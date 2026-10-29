"""馒头管家 FastAPI 主程序。

部署形态：fnOS 统一网关（Unix Socket）+ 静态前端托管；
开发形态：MTM_DEV=1 时监听 TCP 端口（默认 8686）。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .config import settings
from .database import add_event, init_db
from .routers import accounts, discover, downloads, settings as settings_router, system
from .services import download_manager
from .services.scheduler import scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("mteam-manager")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _import_wizard_setup()
    scheduler.start()
    add_event("info", "system", f"馒头管家启动（v{__version__}）")
    logger.info("mteam-manager v%s started, data=%s", __version__, settings.data_dir)
    yield
    from .services.scheduler import stop as stop_scheduler
    stop_scheduler()
    await download_manager.manager.close()
    logger.info("mteam-manager stopped")


def _import_wizard_setup() -> None:
    """安装向导收集的下载中心凭据导入设置表（仅首次）。"""
    import json as _json
    from .database import get_setting, set_setting
    from .utils.crypto import encrypt
    setup_file = settings.data_dir / "fnos-setup.json"
    if not setup_file.exists():
        return
    if get_setting("fnos_downloader"):
        return
    try:
        data = _json.loads(setup_file.read_text(encoding="utf-8"))
        if data.get("username"):
            set_setting("fnos_downloader", {
                "ws_url": data.get("ws_url") or settings.fnos_ws_default,
                "username": data["username"],
                "password_enc": encrypt(data.get("password") or ""),
            })
            logger.info("imported fnOS downloader setup from install wizard")
    except (OSError, ValueError):
        logger.warning("failed to import fnos-setup.json", exc_info=True)


app = FastAPI(title="馒头管家 M-Team Manager", version=__version__,
              lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

# fnOS 统一网关可能带 /app/mteam-manager 前缀转发；剥离前缀使应用在
# 网关模式与直接访问模式下均可用（前端资源为相对路径，两种模式通用）
GATEWAY_PREFIX = "/app/mteam-manager"


@app.middleware("http")
async def strip_gateway_prefix(request: Request, call_next):
    path = request.scope.get("path", "")
    if path.startswith(GATEWAY_PREFIX):
        request.scope["path"] = path[len(GATEWAY_PREFIX):] or "/"
    return await call_next(request)


app.include_router(system.router)
app.include_router(accounts.router)
app.include_router(discover.router)
app.include_router(downloads.router)
app.include_router(settings_router.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


STATIC_DIR = Path(os.environ.get("MTM_STATIC_DIR",
                  Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"))


@app.get("/", include_in_schema=False)
async def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"app": "馒头管家", "version": __version__,
                         "hint": "前端资源未构建，API 正常运行"})


@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str):
    """SPA 回退：静态资源直出，其余返回 index.html。"""
    target = (STATIC_DIR / full_path).resolve()
    try:
        target.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    if target.is_file():
        return FileResponse(target)
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


def main() -> None:  # pragma: no cover - 入口
    import uvicorn
    if settings.unix_socket:
        sock_path = settings.unix_socket
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        uvicorn.run(app, uds=sock_path, log_level="info",
                    timeout_keep_alive=75, access_log=False)
    else:
        port = int(os.environ.get("MTM_PORT", 8686))
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info",
                    timeout_keep_alive=75, access_log=False)


if __name__ == "__main__":
    main()
