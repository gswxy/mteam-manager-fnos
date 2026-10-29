"""影视发现：聚合、榜单、搜索、详情。

聚合键优先级：douban_id > imdb_id > 规范化标题。
评分只取 M-Team 返回的豆瓣/IMDb 分值，缺失即为空，绝不伪造。
榜单基于已抓取样本排序并注明覆盖范围。
"""
from __future__ import annotations

import re
from typing import Any

from ..database import db
from .mteam import (MT_CATEGORY_GROUPS, UI_CATEGORY_MAP, MTeamClient, MTeamError,
                    category_codes_of, parse_torrent)

TITLE_NOISE = re.compile(
    r"[\[\(].*?[\]\)]|[Ｗｗ]ww\..*|【.*?】", re.S)


def normalize_title(name: str) -> str:
    """种子名 -> 影视标题近似键（去版本/画质噪音，取前半段）。"""
    t = TITLE_NOISE.sub(" ", name)
    t = re.sub(r"[._]+", " ", t)
    t = re.sub(
        r"\b(2160p|1080p|720p|4K|WEB-?DL|BluRay|REMUX|x264|x265|H\.?26[45]|HEVC|AVC"
        r"|AAC|DTS|FLAC|Atmos|TrueHD|HDR10?|DV|DoVi|10bit|60fps|Complete|SEASON|S\d{1,2})"
        r".*$", "", t, flags=re.I)
    t = re.split(r"\b19\d{2}\b|\b20\d{2}\b", t)[0]
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t[:80].lower()


def media_key(t: dict) -> str:
    if t.get("douban_id"):
        return f"db:{t['douban_id']}"
    if t.get("imdb_id"):
        return f"im:{t['imdb_id']}"
    return f"ti:{normalize_title(t['name'])}"


def save_torrents(torrents: list[dict]) -> None:
    """缓存种子列表（upsert）。"""
    with db() as conn:
        for t in torrents:
            if not t.get("id"):
                continue
            conn.execute(
                """INSERT INTO torrents (id,name,small_descr,category,size,seeders,leechers,
                     times_completed,discount,discount_end,douban_id,douban_rating,imdb_id,
                     imdb_rating,poster,created_date,labels,extra,last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, small_descr=excluded.small_descr,
                     category=excluded.category, size=excluded.size, seeders=excluded.seeders,
                     leechers=excluded.leechers, times_completed=excluded.times_completed,
                     discount=excluded.discount, discount_end=excluded.discount_end,
                     douban_id=excluded.douban_id, douban_rating=excluded.douban_rating,
                     imdb_id=excluded.imdb_id, imdb_rating=excluded.imdb_rating,
                     poster=excluded.poster, created_date=excluded.created_date,
                     labels=excluded.labels, extra=excluded.extra,
                     last_seen=datetime('now')""",
                (t["id"], t["name"], t.get("small_descr"), t.get("category"), t["size"],
                 t["seeders"], t["leechers"], t["times_completed"], t["discount"],
                 t.get("discount_end"), t.get("douban_id"), t.get("douban_rating"),
                 t.get("imdb_id"), t.get("imdb_rating"), t.get("poster"),
                 t.get("created_date"), _json(t.get("labels")),
                 _json({"status": t.get("raw", {}).get("status")})))


def _json(v) -> str | None:
    import json as _j
    return _j.dumps(v, ensure_ascii=False) if v is not None else None


def _load_labels(v) -> list:
    import json as _j
    try:
        return _j.loads(v) if v else []
    except (TypeError, ValueError):
        return []


def aggregate(rows: list[dict]) -> list[dict]:
    """种子行 -> 聚合影视条目列表（每条含 versions）。"""
    groups: dict[str, dict] = {}
    for r in rows:
        t = dict(r)
        t["labels"] = _load_labels(r["labels"])
        t["is_free"] = t["discount"] in ("FREE", "_2X_FREE")
        t["is_2x"] = t["discount"] in ("_2X", "_2X_FREE", "_2X_PERCENT_50")
        key = media_key(t)
        g = groups.setdefault(key, {
            "key": key,
            "title": _display_title(t),
            "douban_id": t.get("douban_id"),
            "douban_rating": t.get("douban_rating"),
            "imdb_id": t.get("imdb_id"),
            "imdb_rating": t.get("imdb_rating"),
            "poster": t.get("poster"),
            "category": _ui_category(t.get("category") or ""),
            "created_date": None,
            "versions": [],
            "seeders_total": 0,
            "has_free": False,
            "min_size": None,
            "max_size": 0,
        })
        g["versions"].append(t)
        g["seeders_total"] += t["seeders"]
        g["has_free"] = g["has_free"] or t["is_free"]
        g["max_size"] = max(g["max_size"], t["size"])
        g["min_size"] = t["size"] if g["min_size"] is None else min(g["min_size"], t["size"])
        if not g["poster"] and t.get("poster"):
            g["poster"] = t["poster"]
        if not g["douban_rating"] and t.get("douban_rating"):
            g["douban_rating"] = t["douban_rating"]
        cd = t.get("created_date")
        if cd and (not g["created_date"] or cd > g["created_date"]):
            g["created_date"] = cd
        if not g["title"]:
            g["title"] = _display_title(t)
    for g in groups.values():
        g["version_count"] = len(g["versions"])
    return list(groups.values())


def _display_title(t: dict) -> str:
    if t.get("small_descr"):
        return t["small_descr"][:80]
    return normalize_title(t["name"])[:80] or t["name"][:60]


def _ui_category(mt_category: str) -> str:
    """M-Team 分类码 -> 界面分组（与站点分类结构一致）。"""
    cat = mt_category or ""
    for g in MT_CATEGORY_GROUPS:
        if any(cat.startswith(code) for code in g["codes"]):
            return g["key"]
    return "other"


def browse(tab: str, category: str | None, page: int, page_size: int) -> dict:
    """基于本地缓存聚合浏览。

    覆盖范围说明：榜单来自「已抓取种子样本」（fetch_pages 拉取的最新 N 页），
    在响应中显式声明，不冒充全站数据。
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM torrents ORDER BY datetime(created_date) DESC LIMIT 4000"
        ).fetchall()
    items = [dict(r) for r in rows]
    if category and category != "all":
        wanted = category_codes_of(category)
        if wanted:
            items = [t for t in items
                     if any((t.get("category") or "").startswith(w) for w in wanted)]
    groups = aggregate(items)

    if tab == "free":
        groups = [g for g in groups if g["has_free"]]
        groups.sort(key=lambda g: g["created_date"] or "", reverse=True)
    elif tab == "hot":
        groups.sort(key=lambda g: g["seeders_total"], reverse=True)
    elif tab == "rating":
        groups.sort(key=lambda g: (g["douban_rating"] or g["imdb_rating"] or 0), reverse=True)
        groups = [g for g in groups if (g["douban_rating"] or g["imdb_rating"] or 0) > 0]
    else:  # latest
        groups.sort(key=lambda g: g["created_date"] or "", reverse=True)

    total = len(groups)
    start = (page - 1) * page_size
    return {
        "items": groups[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "coverage": "基于应用已抓取的种子样本（含搜索与自动扫描结果）聚合排序，非全站榜单",
    }


def media_detail(key: str) -> dict | None:
    with db() as conn:
        rows = conn.execute("SELECT * FROM torrents").fetchall()
    items = [dict(r) for r in rows]
    groups = aggregate(items)
    for g in groups:
        if g["key"] == key:
            g["versions"].sort(key=lambda t: (not t["is_free"], t["seeders"]), reverse=False)
            return g
    return None


async def search_and_cache(client: MTeamClient, keyword: str,
                           categories: list[str] | None = None,
                           discount: str | None = None,
                           pages: int = 1, page_size: int = 100) -> dict:
    """调用 M-Team 搜索并写入缓存。"""
    result = await client.torrent_search(keyword=keyword or None, categories=categories,
                                         discount=discount, page_size=page_size)
    if result["total"] > page_size and pages > 1:
        for p in range(2, pages + 1):
            more = await client.torrent_search(keyword=keyword or None,
                                               categories=categories, discount=discount,
                                               page=p, page_size=page_size)
            result["torrents"].extend(more["torrents"])
            if len(more["torrents"]) < page_size:
                break
    save_torrents(result["torrents"])
    return result


async def fetch_latest(client: MTeamClient, categories: list[str] | None = None,
                       pages: int = 2, page_size: int = 100) -> int:
    """抓取最新种子入缓存（影视发现与自动规则共用数据源）。"""
    total = 0
    for p in range(1, pages + 1):
        result = await client.torrent_search(categories=categories, page=p, page_size=page_size)
        save_torrents(result["torrents"])
        total += len(result["torrents"])
        if len(result["torrents"]) < page_size:
            break
    return total
