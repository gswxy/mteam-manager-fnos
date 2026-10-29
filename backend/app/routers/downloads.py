"""下载管理路由。"""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..database import db
from ..security import current_user
from ..services.download_manager import manager

router = APIRouter(prefix="/api/downloads", tags=["downloads"], dependencies=[Depends(current_user)])

FILTERS = ("all", "downloading", "completed", "seeding", "failed", "auto", "protected",
           "cleanup", "history")


def _stats() -> dict:
    with db() as conn:
        rows = conn.execute(
            "SELECT status, source, protected, COUNT(*) n, COALESCE(SUM(size),0) sz "
            "FROM download_tasks WHERE removed_at IS NULL GROUP BY status, source, protected"
        ).fetchall()
    out = {"total": 0, "downloading": 0, "seeding": 0, "completed": 0, "failed": 0,
           "waiting": 0, "auto": 0, "manual": 0, "protected": 0, "total_size": 0}
    for r in rows:
        st, n = r["status"], r["n"]
        out["total"] += n
        out["total_size"] += r["sz"]
        if st in ("downloading", "paused", "checking", "pushing", "pending", "lost"):
            out["downloading"] += n
        if st == "waiting":
            out["waiting"] += n
        if st == "seeding":
            out["seeding"] += n
        if st == "completed":
            out["completed"] += n
        if st in ("error", "lost"):
            out["failed"] += n
        out[r["source"]] = out.get(r["source"], 0) + n
        if r["protected"]:
            out["protected"] += n
    return out


@router.get("")
def list_downloads(filter: str = "all", limit: int = 200):
    if filter not in FILTERS:
        raise HTTPException(400, "无效的筛选")
    where, params = ["removed_at IS NULL"], []
    if filter == "downloading":
        where.append("status IN ('pending','pushing','waiting','downloading','paused','checking')")
    elif filter in ("completed", "seeding"):
        where.append("status IN ('completed','seeding')")
    elif filter == "failed":
        where.append("status IN ('error','lost')")
    elif filter == "auto":
        where.append("source='auto'")
    elif filter == "protected":
        where.append("protected=1")
    elif filter == "cleanup":
        where.append("source='auto' AND protected=0")
    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM download_tasks WHERE {' AND '.join(where)} "
            f"ORDER BY datetime(added_at) DESC LIMIT ?", (limit,)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d.get("meta") or "{}")
        items.append(d)
    return {"items": items, "stats": _stats()}


@router.get("/history")
def history(limit: int = 200):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM download_tasks WHERE removed_at IS NOT NULL "
            "ORDER BY datetime(removed_at) DESC LIMIT ?", (limit,)).fetchall()
        logs = conn.execute(
            "SELECT * FROM cleanup_logs ORDER BY id DESC LIMIT 100").fetchall()
    return {"items": [dict(r) for r in rows], "cleanup_logs": [dict(r) for r in logs]}


@router.post("/sync")
async def force_sync():
    res = await manager.sync_status()
    if not res.get("ok"):
        raise HTTPException(502, res.get("error") or "同步失败")
    return res


@router.post("/{task_id}/pause")
async def pause(task_id: int):
    ok, msg = await manager.pause(task_id)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@router.post("/{task_id}/resume")
async def resume(task_id: int):
    ok, msg = await manager.resume(task_id)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


class DeleteIn(BaseModel):
    delete_files: bool = False


@router.post("/{task_id}/delete")
async def delete(task_id: int, body: DeleteIn):
    ok, msg = await manager.remove(task_id, delete_files=body.delete_files)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@router.post("/{task_id}/protect")
async def protect(task_id: int, body: DeleteIn | None = None):
    await manager.set_protected(task_id, True)
    return {"ok": True}


@router.post("/{task_id}/unprotect")
async def unprotect(task_id: int):
    await manager.set_protected(task_id, False)
    return {"ok": True}
