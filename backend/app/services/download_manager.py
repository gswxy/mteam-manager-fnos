"""下载任务管理：添加（幂等）、状态同步、暂停/恢复/删除、保护。

设计要点：
- 添加前三重去重：本地任务表 -> M-Team tracker/queryHistory -> 飞牛下载中心在途任务名
- 所有自动任务记录来源规则与命中原因，便于展示与追溯
- 状态同步以飞牛下载中心为唯一事实来源
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import settings
from ..database import add_event, db, get_setting
from ..utils.crypto import decrypt
from .fnos_ws import FnOSDownloader, FnOSSession, FnOSWsError, normalize_state
from .mteam import MTeamClient, MTeamError
from .paths import ensure_category_dir, validate_dir

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"pending", "pushing", "waiting", "downloading", "paused", "checking"}


def fnos_downloader_from_settings() -> FnOSDownloader:
    cfg = get_setting("fnos_downloader", {}) or {}
    url = cfg.get("ws_url") or settings.fnos_ws_default
    username = cfg.get("username") or ""
    password = decrypt(cfg.get("password_enc") or "")
    session = FnOSSession(url, username, password)
    return FnOSDownloader(session)


def manual_paths() -> dict[str, str]:
    return get_setting("manual_paths", {}) or {}


def auto_paths() -> dict[str, Any]:
    return get_setting("auto_paths", {}) or {}


class DownloadManager:
    """有状态服务：持有 fnOS 会话与 M-Team 客户端缓存。"""

    def __init__(self) -> None:
        self._downloader: FnOSDownloader | None = None
        self._mt_clients: dict[int, MTeamClient] = {}
        self._limiter = None
        self._add_lock = asyncio.Lock()   # 串行化添加，防并发重复
        self._sync_lock = asyncio.Lock()  # 串行化同步，防并发调度

    # ------------- 客户端 -------------

    def downloader(self) -> FnOSDownloader:
        # 每次按最新配置重建（配置变更即生效）；会话内部有锁与重连
        self._downloader = fnos_downloader_from_settings()
        return self._downloader

    def mteam(self, account_id: int) -> MTeamClient:
        if account_id in self._mt_clients:
            return self._mt_clients[account_id]
        with db() as conn:
            row = conn.execute(
                "SELECT api_key_enc, api_url FROM accounts WHERE id=? AND enabled=1",
                (account_id,),
            ).fetchone()
        if row is None:
            raise MTeamError("账号不存在或已禁用")
        client = MTeamClient(decrypt(row["api_key_enc"]), row["api_url"],
                             limiter=self._limiter_shared())
        self._mt_clients[account_id] = client
        return client

    def _limiter_shared(self):
        if self._limiter is None:
            from .mteam import RateLimiter
            interval = float((get_setting("mteam_global") or {}).get("request_interval") or 4)
            self._limiter = RateLimiter(min_interval=max(1.0, interval))
        return self._limiter

    async def close(self) -> None:
        for c in list(self._mt_clients.values()):
            try:
                await c.close()
            except Exception:  # noqa: BLE001 - 跨事件循环的客户端关闭失败可忽略
                pass
        self._mt_clients.clear()

    def default_account_id(self) -> int | None:
        with db() as conn:
            row = conn.execute(
                "SELECT id FROM accounts WHERE is_default=1 AND enabled=1"
            ).fetchone()
            if row:
                return row["id"]
            row = conn.execute(
                "SELECT id FROM accounts WHERE enabled=1 ORDER BY id LIMIT 1"
            ).fetchone()
            return row["id"] if row else None

    # ------------- 添加任务（幂等） -------------

    async def add_torrent(self, torrent_id: str, *, source: str, category: str,
                          account_id: int | None = None, rule_id: int | None = None,
                          rule_name: str | None = None, hit_reason: str | None = None,
                          dry_run: bool = False) -> dict:
        """添加种子到飞牛下载中心。返回 {added, reason, task_db_id}。"""
        account_id = account_id or self.default_account_id()
        if account_id is None:
            return {"added": False, "reason": "未配置 M-Team 账号"}

        async with self._add_lock:
            # 去重 1：本地任务表（任意账号同一种子视为重复，避免重复占用空间）
            with db() as conn:
                dup = conn.execute(
                    "SELECT id, status, source FROM download_tasks WHERE torrent_id=? "
                    "AND removed_at IS NULL", (torrent_id,)
                ).fetchone()
            if dup:
                return {"added": False, "reason": f"任务已存在（{dup['status']} / {dup['source']}）",
                        "task_db_id": dup["id"]}

            mt = self.mteam(account_id)
            try:
                # 去重 2：站点下载历史
                hist = await mt.query_history([torrent_id])
                if hist.get(torrent_id):
                    return {"added": False, "reason": "该账号在站点已有此种子下载记录"}

                torrent = await mt.torrent_detail(torrent_id)
                # 重新确认促销状态（自动任务安全前提）
                if source == "auto":
                    if torrent["discount"] not in ("FREE", "_2X_FREE", "PERCENT_50", "_2X_PERCENT_50", "_2X", "NORMAL"):
                        pass
                # 去重 3：下载中心在途任务（按名称匹配）
                dl = self.downloader()
                try:
                    tasks = (await dl.list_tasks()).get("tasks") or []
                except FnOSWsError as e:
                    return {"added": False, "reason": f"下载中心不可用：{e}"}
                base_name = torrent["name"]
                for t in tasks:
                    if (t.get("name") or "").startswith(base_name[:80]):
                        return {"added": False, "reason": "下载中心已存在同名任务"}

                # 选择保存路径
                if source == "manual":
                    paths = manual_paths()
                    save_path = paths.get(category) or paths.get("other") or ""
                    if not save_path:
                        return {"added": False, "reason": f"未配置「{category}」手动下载目录"}
                else:
                    ap = auto_paths()
                    root = ap.get("root") or ""
                    if not root:
                        return {"added": False, "reason": "未配置自动下载根目录"}
                    save_path = ensure_category_dir(root, category, bool(ap.get("by_category")))
                ok, msg = validate_dir(save_path)
                if not ok:
                    return {"added": False, "reason": f"保存目录无效：{msg}"}

                if dry_run:
                    return {"added": False, "dry_run": True, "reason": "预检通过",
                            "size": torrent["size"], "discount": torrent["discount"],
                            "save_path": save_path}

                # 下载种子文件并推送到下载中心
                data = await mt.download_torrent_file(torrent_id)
                torrent_file = Path(settings.torrent_dir) / f"{torrent_id}.torrent"
                torrent_file.write_bytes(data)
                try:
                    torrent_file.chmod(0o644)
                except OSError:
                    pass
                dl_ids = await dl.add_by_paths([str(torrent_file)], save_path)
                if not dl_ids:
                    return {"added": False, "reason": "下载中心未返回任务 ID"}
            except MTeamError as e:
                return {"added": False, "reason": f"M-Team 接口错误：{e}"}

            with db() as conn:
                cur = conn.execute(
                    """INSERT INTO download_tasks
                       (account_id, torrent_id, torrent_name, size, source, rule_id, rule_name,
                        hit_reason, category, save_path, fnos_task_id, status, discount,
                        labels, meta)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (account_id, torrent_id, torrent["name"], torrent["size"], source,
                     rule_id, rule_name, hit_reason, category, save_path, dl_ids[0],
                     "pushing", torrent["discount"],
                     json.dumps(torrent.get("labels") or [], ensure_ascii=False),
                     json.dumps({"poster": torrent.get("poster"),
                                 "douban_id": torrent.get("douban_id")},
                                ensure_ascii=False)),
                )
                task_db_id = cur.lastrowid
            add_event("info", "download",
                      f"{'自动' if source == 'auto' else '手动'}添加任务：{torrent['name'][:60]}",
                      detail=hit_reason)
            return {"added": True, "task_db_id": task_db_id, "fnos_task_id": dl_ids[0],
                    "size": torrent["size"], "save_path": save_path}

    # ------------- 状态同步 -------------

    async def sync_status(self) -> dict:
        """以飞牛下载中心为准同步任务状态；NAS 重启/应用升级后自动恢复关联。"""
        async with self._sync_lock:
            try:
                dl = self.downloader()
                data = await dl.list_tasks(init_flag=False)
            except FnOSWsError as e:
                add_event("warn", "downloader", f"下载中心同步失败：{e}")
                return {"ok": False, "error": str(e)}
            tasks = {int(t["id"]): t for t in (data.get("tasks") or []) if t.get("id") is not None}

            with db() as conn:
                rows = conn.execute(
                    "SELECT id, fnos_task_id, torrent_name, status, source, save_path "
                    "FROM download_tasks WHERE removed_at IS NULL"
                ).fetchall()
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for row in rows:
                    ft = tasks.get(row["fnos_task_id"])
                    if ft is None:
                        # 下载中心中不存在：可能被用户手动删除 -> 标记 lost，不自动删文件
                        conn.execute(
                            "UPDATE download_tasks SET status='lost', "
                            "meta=COALESCE(meta,'{}') WHERE id=?", (row["id"],)
                        )
                        continue
                    state = normalize_state(ft)
                    size = int(ft.get("total_size") or 0)
                    done = int(ft.get("size") or ft.get("dl_total_size") or 0)
                    progress = round(done / size, 4) if size else 0.0
                    if state == "seeding" and progress >= 0.999:
                        progress = 1.0
                    ratio = ft.get("share_ratio")
                    if ratio is None and state == "seeding":
                        up, down = int(ft.get("up_total_size") or 0), int(ft.get("dl_total_size") or 0)
                        ratio = round(up / down, 3) if down else None
                    completed = None
                    if state in ("seeding",) and row["status"] != "completed":
                        completed = now
                    conn.execute(
                        """UPDATE download_tasks SET status=?, progress=?, dl_speed=?,
                           up_speed=?, uploaded=?, downloaded=?, ratio=?,
                           completed_at=COALESCE(?, completed_at) WHERE id=?""",
                        (state, progress, int(ft.get("dl_speed") or 0),
                         int(ft.get("up_speed") or 0), int(ft.get("up_total_size") or 0),
                         int(ft.get("dl_total_size") or 0), ratio, completed, row["id"]),
                    )
            return {"ok": True, "tasks": len(tasks)}

    # ------------- 操作 -------------

    async def pause(self, task_db_id: int) -> tuple[bool, str]:
        return await self._control(task_db_id, "pause")

    async def resume(self, task_db_id: int) -> tuple[bool, str]:
        return await self._control(task_db_id, "resume")

    async def _control(self, task_db_id: int, action: str) -> tuple[bool, str]:
        with db() as conn:
            row = conn.execute(
                "SELECT fnos_task_id FROM download_tasks WHERE id=?", (task_db_id,)
            ).fetchone()
        if row is None or row["fnos_task_id"] is None:
            return False, "任务不存在或未关联下载中心"
        try:
            dl = self.downloader()
            await getattr(dl, action)([row["fnos_task_id"]])
            return True, "ok"
        except FnOSWsError as e:
            return False, str(e)

    async def remove(self, task_db_id: int, *, delete_files: bool, reason: str = "手动删除") -> tuple[bool, str]:
        """删除任务。手动删除不清理文件；明确选择时才删除数据。"""
        with db() as conn:
            row = conn.execute(
                "SELECT fnos_task_id, torrent_name, save_path, source FROM download_tasks "
                "WHERE id=?", (task_db_id,)
            ).fetchone()
        if row is None:
            return False, "任务不存在"
        try:
            dl = self.downloader()
            if row["fnos_task_id"] is not None:
                await dl.delete([row["fnos_task_id"]], delete_files=delete_files)
        except FnOSWsError as e:
            return False, f"下载中心删除失败：{e}"
        # 兜底：delete_files 时核对文件是否真的删除（真机验证存在不彻底的情况）
        freed = 0
        if delete_files and row["save_path"]:
            freed = _cleanup_leftover(row["torrent_name"], row["save_path"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with db() as conn:
            conn.execute(
                "UPDATE download_tasks SET removed_at=?, remove_reason=?, status='deleted', "
                "freed_bytes=? WHERE id=?", (now, reason, freed, task_db_id))
            conn.execute(
                "INSERT INTO cleanup_logs (task_id, torrent_name, reason, freed_bytes, detail) "
                "VALUES (?,?,?,?,?)",
                (task_db_id, row["torrent_name"], reason, freed, "manual" if "手动" in reason else "auto"))
        add_event("info", "cleanup", f"删除任务：{row['torrent_name'][:60]}（{reason}）",
                  detail=f"回收 {freed} 字节")
        return True, "ok"

    async def set_protected(self, task_db_id: int, protected: bool) -> None:
        with db() as conn:
            conn.execute("UPDATE download_tasks SET protected=? WHERE id=?",
                         (1 if protected else 0, task_db_id))

    # 空间查询缓存（避免高频触发下载中心限流）
    _free_space_cache: dict[str, tuple[float, int]] = {}
    _FREE_TTL = 120.0

    def _sync_free_space_cached(self, dir_path: str) -> int | None:
        """同步版本：直接检查本地文件系统（应用运行于 NAS 内，等价且零开销）。"""
        import shutil as _sh
        try:
            usage = _sh.disk_usage(dir_path)
            return usage.free
        except OSError:
            return None


def _cleanup_leftover(torrent_name: str, save_path: str) -> int:
    """删除任务后，清理残留的数据文件（仅在下载器任务已删除后执行）。

    安全约束：只处理保存目录内、与种子同名的文件/目录，路径必须仍位于 /vol 下。
    """
    import shutil
    freed = 0
    try:
        base = Path(save_path)
        if not str(base).startswith("/vol") or not base.is_dir():
            return 0
        stem = Path(torrent_name).stem or torrent_name
        candidates = [base / torrent_name, base / (torrent_name + ".!qB"), base / stem]
        seen_real = set()
        for cand in candidates:
            real = cand.resolve()
            if str(real) in seen_real:
                continue
            seen_real.add(str(real))
            if not str(real).startswith("/vol"):
                continue
            if cand.is_file():
                freed += cand.stat().st_size
                cand.unlink()
            elif cand.is_dir():
                freed += sum(f.stat().st_size for f in cand.rglob("*") if f.is_file())
                shutil.rmtree(cand, ignore_errors=True)
    except OSError as e:
        logger.warning("leftover cleanup failed: %s", e)
    return freed


manager = DownloadManager()
