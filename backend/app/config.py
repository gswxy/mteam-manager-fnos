"""应用配置：全部来自环境变量或数据库设置表，不硬编码任何账号信息。"""
from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    """进程级配置。用户级配置（账号、路径、规则）保存在 SQLite settings 表。"""

    def __init__(self) -> None:
        # 数据目录：fnOS 包内为 $TRIM_PKGVAR/data；开发环境为 MTM_DATA_DIR 或 ./data
        pkg_var = os.environ.get("TRIM_PKGVAR")
        if pkg_var:
            self.data_dir = Path(pkg_var) / "data"
        else:
            self.data_dir = Path(os.environ.get("MTM_DATA_DIR", "./data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.data_dir / "mteam_manager.db"
        self.torrent_dir = self.data_dir / "torrents"
        self.torrent_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = self.data_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # fnOS 网关 unix socket（统一网关模式）
        appdest = os.environ.get("TRIM_APPDEST")
        if appdest:
            self.unix_socket = str(Path(appdest) / "app.sock")
        else:
            self.unix_socket = os.environ.get("MTM_UNIX_SOCKET") or None

        # 开发模式：跳过 fnOS 网关可信头校验
        self.dev_mode = _env_bool("MTM_DEV", False)

        # 应用访问密钥（可选，直接访问时的第二道口令）；未配置时仅信任网关头
        self.app_token = os.environ.get("MTM_APP_TOKEN") or ""

        # 默认 M-Team API 地址（可在账号设置中覆盖）
        self.mteam_default_api = os.environ.get("MTM_MTEAM_API", "https://api.m-team.cc")

        # fnOS 下载中心 WebSocket 地址（应用运行在 NAS 内，默认本机）
        self.fnos_ws_default = os.environ.get("MTM_FNOS_WS", "ws://127.0.0.1:5666/websocket")

        self.version = "1.0.0"


settings = Settings()
