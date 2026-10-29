"""路径安全与目录浏览（应用运行于 NAS 内，直接检查本地文件系统）。"""
from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path

# 仅允许 /volN/... 形式的绝对路径，杜绝路径穿越与越界
VOL_PATH = re.compile(r"^/vol\d+(?:/[^/\\]+)*/?$")


def is_safe_path(path: str) -> bool:
    if not path or not VOL_PATH.match(path):
        return False
    return ".." not in path.split("/")


def resolve_real(path: str) -> str:
    """解析软链接后的真实路径，用于防止软链越界。

    路径存在时用文件系统真实解析（NAS/Linux 上可检出软链逃逸）；
    不存在时退化为 posix 规范化（保持 /vol 前缀，跨平台可测）。
    """
    if os.path.exists(path):
        return os.path.realpath(path).replace("\\", "/")
    return posixpath.normpath(path)


def within_allowed(path: str, roots: list[str]) -> bool:
    real = resolve_real(path)
    return any(real == r or real.startswith(r.rstrip("/") + "/") for r in roots)


def validate_dir(path: str, *, must_exist: bool = True, writable: bool = True) -> tuple[bool, str]:
    """校验下载目录：格式合法、存在、无越界软链、可写。"""
    if not is_safe_path(path):
        return False, "路径必须形如 /vol1/...，且不允许包含 .."
    p = Path(path)
    if must_exist and not p.is_dir():
        return False, "目录不存在"
    real = resolve_real(path)
    # 软链越界检查：仅当路径真实存在（POSIX 解析有效）时执行
    if os.path.exists(path) and os.name == "posix" and not real.startswith("/vol"):
        return False, "目录解析后不在存储卷内（软链越界）"
    if not real.startswith("/vol"):
        return False, "目录解析失败"
    if writable:
        try:
            probe = Path(real) / ".mtm_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as e:
            return False, f"目录不可写：{e.strerror or e}"
    return True, "ok"


def list_volumes() -> list[dict]:
    out = []
    for name in sorted(os.listdir("/")):
        if re.fullmatch(r"vol\d+", name):
            p = Path("/") / name
            if p.is_dir():
                out.append({"name": f"/{name}", "path": f"/{name}", "dir": True})
    return out


def list_dir(path: str) -> list[dict]:
    """列出目录内容（文件夹选择器数据源），只返回目录。"""
    if not is_safe_path(path):
        return []
    p = Path(resolve_real(path))
    if not p.is_dir():
        return []
    items: list[dict] = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if child.is_dir() and not child.name.startswith(".") and not child.is_symlink():
                items.append({"name": child.name, "path": str(child), "dir": True})
    except PermissionError:
        return []
    return items[:500]


def ensure_category_dir(root: str, category: str, enabled: bool) -> str:
    """自动下载按类别建子目录。"""
    if not enabled:
        return root
    sub = {"movie": "电影", "tv": "电视剧", "anime": "动漫", "show": "综艺"}.get(category)
    if not sub:
        return root
    target = str(Path(root) / sub)
    os.makedirs(target, exist_ok=True)
    return target
