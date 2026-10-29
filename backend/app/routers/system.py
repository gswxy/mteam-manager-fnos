"""系统路由：健康检查、事件、登录会话。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import __version__
from ..database import db
from ..security import current_user, issue_ticket, user_from_ticket
from ..services.scheduler import scheduler as _scheduler_mod

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    """无认证健康检查（生命周期监控用）。"""
    return {"ok": True, "version": __version__,
            "scheduler_running": _scheduler_mod.running}


@router.get("/me")
def me(request: Request, user: dict = Depends(current_user)):
    ticket = issue_ticket(user)
    return {"user": user, "ticket": ticket}


@router.get("/events")
def events(level: str = "all", limit: int = 100, user: dict = Depends(current_user)):
    where, params = "", []
    if level != "all":
        where = "WHERE level=?"
        params = [level]
    params.append(min(limit, 500))
    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM event_logs {where} ORDER BY id DESC LIMIT ?", params).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.get("/dashboard")
def dashboard(user: dict = Depends(current_user)):
    """总览统计（账号中心首页）。"""
    with db() as conn:
        accounts = conn.execute(
            "SELECT id,name,username,is_default,enabled,uploaded,downloaded,share_rate,"
            "bonus,seeding_count,seeding_size,last_sync,last_error FROM accounts "
            "ORDER BY is_default DESC, id").fetchall()
        tasks = conn.execute(
            "SELECT status, source, COUNT(*) n, COALESCE(SUM(size),0) sz "
            "FROM download_tasks WHERE removed_at IS NULL GROUP BY status, source").fetchall()
    return {"accounts": [dict(a) for a in accounts],
            "tasks": [dict(t) for t in tasks]}
