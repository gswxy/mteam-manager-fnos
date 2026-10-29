"""设置路由：馒头账号全局 / 飞牛下载中心 / 目录 / 自动下载规则 / 自动删除 / 关于。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import APP_NAME, APP_NAME_EN, AUTHOR, AUTHOR_URL, REPO_URL, __version__
from ..config import settings as app_settings
from ..database import db, get_setting, set_setting
from ..security import current_user
from ..services import auto_cleanup, auto_download, paths
from ..services.download_manager import manager
from ..services.mteam import MTeamError
from ..utils.crypto import decrypt, encrypt, mask

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(current_user)])


# ---------- A. 馒头账号全局 ----------

class MTeamGlobalIn(BaseModel):
    request_interval: float = Field(default=4, ge=1, le=60)
    account_sync_interval: int = Field(default=600, ge=60, le=86400)
    proxy: str = Field(default="", max_length=200)


@router.get("/mteam")
def get_mteam():
    cfg = get_setting("mteam_global", {}) or {}
    return {"request_interval": cfg.get("request_interval", 4),
            "account_sync_interval": cfg.get("account_sync_interval", 600),
            "proxy": cfg.get("proxy", "")}


@router.put("/mteam")
def put_mteam(body: MTeamGlobalIn):
    set_setting("mteam_global", body.model_dump())
    return {"ok": True}


# ---------- B. 飞牛下载中心 ----------

class FnOSIn(BaseModel):
    ws_url: str = Field(default="ws://127.0.0.1:5666/websocket")
    username: str = Field(min_length=1, max_length=64)
    password: str | None = None  # 为空表示不修改


@router.get("/fnos")
def get_fnos():
    cfg = get_setting("fnos_downloader", {}) or {}
    return {"ws_url": cfg.get("ws_url") or app_settings.fnos_ws_default,
            "username": cfg.get("username") or "",
            "password_masked": mask(decrypt(cfg.get("password_enc") or ""), 2) if cfg.get("password_enc") else "",
            "configured": bool(cfg.get("username") and cfg.get("password_enc"))}


@router.put("/fnos")
def put_fnos(body: FnOSIn):
    cfg = get_setting("fnos_downloader", {}) or {}
    cfg["ws_url"] = body.ws_url
    cfg["username"] = body.username
    if body.password:
        cfg["password_enc"] = encrypt(body.password)
    set_setting("fnos_downloader", cfg)
    return {"ok": True}


@router.post("/fnos/test")
async def test_fnos():
    try:
        dl = manager.downloader()
        ping = await dl.ping()
        default_dir = await dl.default_save_dir()
        return {"ok": True, "latency_ms": ping["latency_ms"],
                "default_save_dir": default_dir,
                "stat": ping.get("stat")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------- C/D. 目录 ----------

class PathsIn(BaseModel):
    movie: str = ""
    tv: str = ""
    anime: str = ""
    show: str = ""
    other: str = ""


@router.get("/manual-paths")
def get_manual_paths():
    return get_setting("manual_paths", {}) or {"movie": "", "tv": "", "anime": "", "show": "", "other": ""}


@router.put("/manual-paths")
def put_manual_paths(body: PathsIn):
    invalid = []
    for cat, p in body.model_dump().items():
        if p:
            ok, msg = paths.validate_dir(p)
            if not ok:
                invalid.append(f"{cat}: {msg}")
    if invalid:
        raise HTTPException(400, "；".join(invalid))
    set_setting("manual_paths", body.model_dump())
    return {"ok": True}


class AutoPathsIn(BaseModel):
    root: str = ""
    by_category: bool = True


@router.get("/auto-paths")
def get_auto_paths():
    return get_setting("auto_paths", {}) or {"root": "", "by_category": True}


@router.put("/auto-paths")
def put_auto_paths(body: AutoPathsIn):
    if body.root:
        ok, msg = paths.validate_dir(body.root)
        if not ok:
            raise HTTPException(400, f"自动下载根目录无效：{msg}")
    set_setting("auto_paths", body.model_dump())
    return {"ok": True}


@router.get("/fs/volumes")
def fs_volumes():
    return {"items": paths.list_volumes()}


@router.get("/fs/list")
def fs_list(path: str = "/"):
    return {"items": paths.list_dir(path)}


# ---------- E. 自动下载规则 ----------

class AutoCfgIn(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    scan_pages: int = Field(default=2, ge=1, le=10)
    daily_max: int = Field(default=10, ge=0, le=500)
    max_concurrent: int = Field(default=3, ge=0, le=100)
    space_budget_gb: float = Field(default=200, ge=0, le=100000)
    min_free_gb: float = Field(default=20, ge=0, le=100000)
    time_window: str = ""


@router.get("/auto-download")
def get_auto_download():
    return get_setting("auto_download", {}) or AutoCfgIn().model_dump()


@router.put("/auto-download")
def put_auto_download(body: AutoCfgIn):
    set_setting("auto_download", body.model_dump())
    from ..services.scheduler import reschedule_from_settings
    reschedule_from_settings()
    return {"ok": True}


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    kind: str = Field(default="preference", pattern="^(preference|free|seeding)$")
    enabled: bool = False
    categories: list[str] = []
    qualities: list[str] = []
    min_size_gb: float = 0
    max_size_gb: float = 0
    promo_mode: str = Field(default="free_first", pattern="^(free_first|free_only|any)$")
    allow_paid: bool = False
    min_seeders: int = 0
    min_leechers: int = 0
    keywords: list[str] = []
    exclude_keywords: list[str] = []


@router.get("/rules")
def list_rules():
    with db() as conn:
        rows = conn.execute("SELECT * FROM auto_rules ORDER BY id").fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["config"] = json.loads(d.get("config") or "{}")
        items.append(d)
    return {"items": items}


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn):
    cfg = body.model_dump(exclude={"name", "kind", "enabled"})
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO auto_rules (name, kind, enabled, config) VALUES (?,?,?,?)",
            (body.name, body.kind, 1 if body.enabled else 0,
             json.dumps(cfg, ensure_ascii=False)))
        return {"id": cur.lastrowid}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, body: RuleIn):
    cfg = body.model_dump(exclude={"name", "kind", "enabled"})
    with db() as conn:
        row = conn.execute("SELECT id FROM auto_rules WHERE id=?", (rule_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "规则不存在")
        conn.execute(
            "UPDATE auto_rules SET name=?, kind=?, enabled=?, config=?, "
            "updated_at=datetime('now') WHERE id=?",
            (body.name, body.kind, 1 if body.enabled else 0,
             json.dumps(cfg, ensure_ascii=False), rule_id))
    return {"ok": True}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int):
    with db() as conn:
        conn.execute("DELETE FROM auto_rules WHERE id=?", (rule_id,))
    return {"ok": True}


@router.post("/auto-download/preview")
async def preview_auto():
    """规则预览：不产生任何下载。"""
    return await auto_download.run_scan(dry_run=True)


@router.post("/auto-download/run")
async def run_auto_once():
    cfg = get_setting("auto_download") or {}
    if not cfg.get("enabled"):
        raise HTTPException(400, "自动下载未启用")
    return await auto_download.run_scan()


# ---------- F. 自动删除 ----------

class CleanupCfgIn(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=10, le=1440)
    retain_days: int = Field(default=30, ge=0, le=3650)
    min_seed_days: float = Field(default=7, ge=0, le=3650)
    min_ratio: float = Field(default=1.0, ge=0, le=50)
    min_idle_hours: float = Field(default=0, ge=0, le=720)
    quota_gb: float = Field(default=0, ge=0, le=100000)
    min_free_gb: float = Field(default=0, ge=0, le=100000)
    unconfirmed_deletable: bool = False  # 默认：无法确认义务时禁止删除


@router.get("/auto-cleanup")
def get_cleanup():
    return get_setting("auto_cleanup", {}) or CleanupCfgIn().model_dump()


@router.put("/auto-cleanup")
def put_cleanup(body: CleanupCfgIn):
    set_setting("auto_cleanup", body.model_dump())
    from ..services.scheduler import reschedule_from_settings
    reschedule_from_settings()
    return {"ok": True}


@router.post("/auto-cleanup/preview")
async def preview_cleanup():
    return await auto_cleanup.run_cleanup(dry_run=True)


@router.post("/auto-cleanup/run")
async def run_cleanup_once():
    cfg = get_setting("auto_cleanup") or {}
    if not cfg.get("enabled"):
        raise HTTPException(400, "自动清理未启用")
    return await auto_cleanup.run_cleanup()


# ---------- 关于 / 系统 ----------

@router.get("/about")
def about():
    return {
        "app_name": APP_NAME, "app_name_en": APP_NAME_EN,
        "author": AUTHOR, "author_url": AUTHOR_URL, "repo_url": REPO_URL,
        "version": app_settings.version,
        "license": "MIT",
        "third_party": [
            {"name": "M-Team-Helper", "author": "spellyaohui",
             "url": "https://github.com/spellyaohui/M-Team-Helper", "license": "MIT",
             "usage": "M-Team 接口行为参考（独立重写）"},
            {"name": "python-build-standalone", "author": "astral-sh",
             "url": "https://github.com/astral-sh/python-standalone", "license": "MIT",
             "usage": "FPK 内嵌 Python 运行时"},
            {"name": "FastAPI / SQLAlchemy / APScheduler / httpx / websockets",
             "author": "各开源作者", "url": "https://pypi.org", "license": "BSD/MIT/Apache-2.0",
             "usage": "后端依赖"},
            {"name": "React / Vite / TypeScript", "author": "Meta / Vite 团队",
             "url": "https://react.dev", "license": "MIT", "usage": "前端框架"},
        ],
    }
