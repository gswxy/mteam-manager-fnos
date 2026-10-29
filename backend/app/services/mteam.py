"""M-Team API 客户端。

接口规格来自官方 wiki (https://wiki.m-team.cc/api)，实现参考开源项目
M-Team-Helper backend/services/scraper.py（作者 spellyaohui，MIT），
本文件为独立重写，仅沿用其已验证的接口行为：
- 全部端点 POST；search/profile 为 JSON body，detail/genDlToken/collection 为表单
- 鉴权头 x-api-key；响应信封 code 为字符串 "0" 表示成功
- 种子做种数/促销在 status 子对象；全站促销 promotionRule 需与单种促销取更优
- discountEndTime / createdDate 兼容毫秒时间戳与 ISO 字符串
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DISCOUNT_PRIORITY = {
    "FREE": 5, "_2X_FREE": 4, "PERCENT_50": 3, "_2X_PERCENT_50": 2, "_2X": 1, "NORMAL": 0,
}
FREE_DISCOUNTS = {"FREE", "_2X_FREE"}
DISCOUNT_2X = {"_2X", "_2X_FREE", "_2X_PERCENT_50"}

# M-Team 站点分类（categoryList 返回的一级分类；子分类 4xx/6xx 前缀对应）
CATEGORY_PREFIXES = {
    "401": "movie", "402": "tv", "403": "anime", "404": "show",
    "405": "music", "406": "sport", "407": "doc", "408": "edu",
    "409": "software", "410": "game", "411": "eBook",
    "412": "ai", "413": "course", "414": "other",
}
# 界面一级分类 -> M-Team 分类码（影视相关）
UI_CATEGORY_MAP = {
    "movie": ["401"],
    "tv": ["402"],
    "anime": ["403"],
    "show": ["404"],  # 综艺
}


class MTeamError(Exception):
    def __init__(self, message: str, errno: str = ""):
        super().__init__(message)
        self.errno = errno


@dataclass
class RateLimiter:
    """全站限速：M-Team 官方限制约 100 次/5 分钟，默认按 4s 间隔保守执行。"""
    min_interval: float = 4.0
    _last: float = field(default_factory=lambda: 0.0)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last + self.min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


def parse_flex_time(value: Any) -> str | None:
    """毫秒时间戳或 ISO 字符串 -> 'YYYY-MM-DD HH:MM:SS'（本地时间）。"""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            ts = int(float(value))
            if ts > 10**10:  # 毫秒
                ts = ts / 1000
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        ts = float(value)
        if ts > 10**10:
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value)[:19], fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return str(value)[:19]


def merge_discount(status: dict) -> tuple[str, str | None]:
    """合并种子自身促销与全站促销，返回 (discount, discount_end)。"""
    st = status or {}
    discount = st.get("discount") or "NORMAL"
    promo = st.get("promotionRule") or {}
    p_discount = promo.get("discount") or "NORMAL"
    if DISCOUNT_PRIORITY.get(p_discount, 0) > DISCOUNT_PRIORITY.get(discount, 0):
        discount = p_discount
    end = parse_flex_time(promo.get("endTime")) or parse_flex_time(st.get("discountEndTime"))
    return discount, end


def parse_torrent(raw: dict) -> dict:
    """M-Team 种子对象 -> 应用内统一结构。"""
    status = raw.get("status") or {}
    discount, discount_end = merge_discount(status)
    image_list = raw.get("imageList") or []
    poster = None
    for img in image_list:
        # 真机验证：imageList 可能是字符串数组或 {imgUrl} 对象数组
        img_url = img.get("imgUrl") if isinstance(img, dict) else (img if isinstance(img, str) else None)
        if not img_url:
            continue
        if any(k in img_url for k in (".jpg", ".jpeg", ".png", "doubanio", "img9", "img1", "img2", "img3", "/img/")):
            poster = img_url
            break
    if poster is None and image_list:
        first = image_list[0]
        poster = first.get("imgUrl") if isinstance(first, dict) else (first if isinstance(first, str) else None)

    return {
        "id": str(raw.get("id") or ""),
        "name": raw.get("name") or "",
        "small_descr": raw.get("smallDescr") or "",
        "category": raw.get("category") or "",
        "size": int(raw.get("size") or 0),
        "seeders": int(status.get("seeders") or 0),
        "leechers": int(status.get("leechers") or 0),
        "times_completed": int(status.get("timesCompleted") or 0),
        "discount": discount,
        "discount_end": discount_end,
        "douban_id": str(raw.get("douban") or "") or None,
        "douban_rating": _float(raw.get("doubanRating")),
        "imdb_id": raw.get("imdb") or None,
        "imdb_rating": _float(raw.get("imdbRating")),
        "poster": poster,
        "created_date": parse_flex_time(raw.get("createdDate")),
        "labels": raw.get("labelsNew") or [],
        "is_free": discount in FREE_DISCOUNTS,
        "is_2x": discount in DISCOUNT_2X,
        "raw": raw,
    }


def _float(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


class MTeamClient:
    """单账号客户端。凭据仅在服务端内存中使用，不写日志。"""

    def __init__(self, api_key: str, api_url: str = "https://api.m-team.cc",
                 limiter: RateLimiter | None = None, timeout: float = 25.0):
        self.api_key = api_key
        self.base = (api_url or "https://api.m-team.cc").rstrip("/")
        if not self.base.endswith("/api"):
            self.base = self.base + "/api"
        self.limiter = limiter or RateLimiter()
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"x-api-key": self.api_key, "Accept": "application/json"},
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request(self, path: str, payload: dict | None = None,
                       form: dict | None = None, skip_limit: bool = False) -> Any:
        if not skip_limit:
            await self.limiter.acquire()
        client = await self._http()
        try:
            if form is not None:
                resp = await client.post(f"{self.base}/{path}", data=form)
            else:
                # M-Team 要求 JSON 以原始字符串方式发送
                resp = await client.post(
                    f"{self.base}/{path}",
                    content=json.dumps(payload or {}, ensure_ascii=False),
                    headers={"Content-Type": "application/json"},
                )
        except httpx.HTTPError as e:
            raise MTeamError(f"网络请求失败：{type(e).__name__}") from e
        if resp.status_code != 200:
            raise MTeamError(f"HTTP {resp.status_code}", errno=str(resp.status_code))
        try:
            result = resp.json()
        except ValueError as e:
            raise MTeamError("响应不是有效 JSON") from e
        if str(result.get("code")) != "0":
            raise MTeamError(result.get("message") or "接口返回失败",
                             errno=str(result.get("code")))
        return result.get("data")

    # ---------- 端点 ----------

    async def member_profile(self) -> dict:
        data = await self._request("member/profile")
        mc = data.get("memberCount") or {}
        ms = data.get("memberStatus") or {}
        return {
            "uid": str(data.get("id") or ""),
            "username": data.get("username") or "",
            "email": data.get("email") or "",
            "uploaded": _int(mc.get("uploaded")),
            "downloaded": _int(mc.get("downloaded")),
            "bonus": _float(mc.get("bonus")) or 0.0,
            "share_rate": _float(mc.get("shareRate")) or 0.0,
            "vip": bool(ms.get("vip")),
            "warned": bool(ms.get("warned")),
            "leech_warn": bool(ms.get("leechWarn")),
            "raw": data,
        }

    async def torrent_search(self, *, page: int = 1, page_size: int = 100,
                             mode: str = "normal", keyword: str | None = None,
                             categories: list[str] | None = None,
                             discount: str | None = None,
                             sort_field: str = "CREATED_DATE",
                             sort_direction: str = "DESC") -> dict:
        payload: dict[str, Any] = {
            "pageNumber": page, "pageSize": page_size, "mode": mode,
            "sortField": sort_field, "sortDirection": sort_direction,
        }
        if keyword:
            payload["keyword"] = keyword
        if categories:
            payload["categories"] = categories
        if discount:
            payload["discount"] = discount
        data = await self._request("torrent/search", payload)
        items = data.get("data") or []
        return {"total": int(data.get("total") or 0),
                "torrents": [parse_torrent(t) for t in items]}

    async def torrent_detail(self, torrent_id: str) -> dict:
        data = await self._request("torrent/detail", form={"id": torrent_id})
        t = parse_torrent(data)
        t["descr"] = _strip_html(data.get("descr") or "")
        t["mediainfo"] = data.get("mediainfo") or ""
        return t

    async def gen_dl_token(self, torrent_id: str) -> str:
        data = await self._request("torrent/genDlToken", form={"id": torrent_id})
        if not isinstance(data, str) or not data:
            raise MTeamError("获取下载令牌失败")
        return data

    async def download_torrent_file(self, torrent_id: str) -> bytes:
        url = await self.gen_dl_token(torrent_id)
        await self.limiter.acquire()
        client = await self._http()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            body = resp.content
        except httpx.HTTPError as e:
            raise MTeamError(f"下载种子文件失败：{type(e).__name__}") from e
        if not body.startswith(b"d"):  # bencode 字典以 d 开头
            raise MTeamError("种子文件内容无效")
        return body

    async def query_history(self, torrent_ids: list[str]) -> dict[str, bool]:
        """查询本账号是否下载过这些种子，返回 {tid: downloaded}"""
        if not torrent_ids:
            return {}
        data = await self._request("tracker/queryHistory",
                                   {"tids": torrent_ids, "_timestamp": int(time.time() * 1000)})
        mapping = (data or {}).get("historyMap") or {}
        return {str(k): bool(v) for k, v in mapping.items()}

    async def category_list(self) -> list[dict]:
        data = await self._request("torrent/categoryList")
        return data if isinstance(data, list) else []

    async def collection_toggle(self, torrent_id: str, make: bool) -> None:
        await self._request("torrent/collection",
                            form={"id": torrent_id, "make": "true" if make else "false"})

    async def seed_watching(self) -> dict:
        """做种列表（保种质量指标数据源之一）。"""
        data = await self._request("personal/seedWatch")
        return data if isinstance(data, dict) else {}


def _int(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    text = _HTML_TAG.sub(" ", text)
    return re.sub(r"\s{2,}", " ", text).strip()
