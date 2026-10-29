"""影视发现路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..database import db
from ..security import current_user
from ..services import discover
from ..services.download_manager import manager
from ..services.mteam import MTeamError
from ..services.risk import predict

router = APIRouter(prefix="/api/discover", tags=["discover"], dependencies=[Depends(current_user)])


@router.get("")
def browse(tab: str = Query("latest", pattern="^(latest|hot|free|rating)$"),
           category: str = Query("all"),
           page: int = Query(1, ge=1), page_size: int = Query(24, ge=6, le=60)):
    return discover.browse(tab, category, page, page_size)


@router.get("/search")
async def search(q: str = Query("", max_length=120),
                 category: str = "all",
                 discount: str | None = Query(None,
                     pattern="^(FREE|PERCENT_50|_2X_FREE|_2X_PERCENT_50|_2X)$"),
                 page: int = 1, page_size: int = 24):
    account_id = manager.default_account_id()
    if account_id is None:
        raise HTTPException(400, "请先在「设置 → 馒头账号」中添加账号")
    categories = None
    if category != "all":
        from ..services.mteam import UI_CATEGORY_MAP
        categories = UI_CATEGORY_MAP.get(category)
    mt = manager.mteam(account_id)
    try:
        await discover.search_and_cache(mt, q, categories=categories, discount=discount)
    except MTeamError as e:
        raise HTTPException(502, f"M-Team 搜索失败：{e}")
    data = discover.browse("latest", category, page, page_size)
    data["keyword"] = q
    return data


@router.get("/refresh")
async def refresh_latest():
    """抓取最新资源入缓存。"""
    account_id = manager.default_account_id()
    if account_id is None:
        raise HTTPException(400, "请先添加 M-Team 账号")
    mt = manager.mteam(account_id)
    try:
        n = await discover.fetch_latest(mt, pages=2)
    except MTeamError as e:
        raise HTTPException(502, f"M-Team 接口失败：{e}")
    return {"ok": True, "fetched": n}


@router.get("/media/{key}")
def media_detail(key: str):
    item = discover.media_detail(key)
    if item is None:
        raise HTTPException(404, "条目不存在或缓存已过期")
    return item


@router.get("/torrent/{torrent_id}")
async def torrent_detail(torrent_id: str):
    """种子详情（含 MediaInfo 与文件列表，实时接口）。"""
    account_id = manager.default_account_id()
    if account_id is None:
        raise HTTPException(400, "请先添加 M-Team 账号")
    mt = manager.mteam(account_id)
    try:
        t = await mt.torrent_detail(torrent_id)
    except MTeamError as e:
        raise HTTPException(502, f"M-Team 接口失败：{e}")
    t.pop("raw", None)
    with db() as conn:
        fav = conn.execute(
            "SELECT 1 FROM favorites WHERE torrent_id=? AND account_id=?",
            (torrent_id, account_id)).fetchone()
    t["favorited"] = bool(fav)
    t["risk"] = predict(account_id, t["size"], t["discount"])
    return t


class DownloadIn(BaseModel):
    category: str = "movie"


@router.post("/torrent/{torrent_id}/download")
async def download_torrent(torrent_id: str, body: DownloadIn):
    res = await manager.add_torrent(torrent_id, source="manual", category=body.category)
    if not res.get("added"):
        raise HTTPException(400, res.get("reason") or "添加失败")
    return res


@router.post("/torrent/{torrent_id}/favorite")
async def toggle_favorite(torrent_id: str):
    account_id = manager.default_account_id()
    if account_id is None:
        raise HTTPException(400, "请先添加 M-Team 账号")
    mt = manager.mteam(account_id)
    with db() as conn:
        fav = conn.execute("SELECT 1 FROM favorites WHERE torrent_id=? AND account_id=?",
                           (torrent_id, account_id)).fetchone()
    make = fav is None
    try:
        await mt.collection_toggle(torrent_id, make)
    except MTeamError as e:
        raise HTTPException(502, f"收藏操作失败：{e}")
    with db() as conn:
        if make:
            conn.execute("INSERT OR IGNORE INTO favorites (account_id, torrent_id) VALUES (?,?)",
                         (account_id, torrent_id))
        else:
            conn.execute("DELETE FROM favorites WHERE account_id=? AND torrent_id=?",
                         (account_id, torrent_id))
    return {"favorited": make}
