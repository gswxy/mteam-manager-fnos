"""Web 管理端身份认证。

主模式：fnOS 统一网关可信头（X-Trim-Userid / X-Trim-Isadmin / X-Trim-Username）。
网关在转发前已完成 fnOS 登录校验，应用只信任这些头（应用仅监听 Unix Socket，
外部无法伪造直连）。开发模式（MTM_DEV=1）放开限制便于本地调试。
"""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from .config import settings

# 一次性 CSRF/会话票据：进程内存（重启失效可接受，网关会重新注入可信头）
_session_tickets: dict[str, dict] = {}


def current_user(request: Request) -> dict:
    """FastAPI 依赖：解析当前用户。"""
    if settings.dev_mode:
        return {"uid": 0, "username": "dev", "is_admin": True}
    uid = request.headers.get("x-trim-userid")
    username = request.headers.get("x-trim-username")
    is_admin = (request.headers.get("x-trim-isadmin") or "").lower() == "true"
    if uid:
        return {"uid": int(uid), "username": username or f"uid{uid}", "is_admin": is_admin}
    raise HTTPException(status_code=401, detail="未认证：请通过飞牛桌面打开本应用")


def issue_ticket(user: dict) -> str:
    ticket = secrets.token_urlsafe(24)
    _session_tickets[ticket] = user
    if len(_session_tickets) > 1000:  # 简单上限
        _session_tickets.clear()
    return ticket


def user_from_ticket(ticket: str) -> dict | None:
    return _session_tickets.get(ticket)
