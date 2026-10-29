"""APScheduler 调度：账号同步 / 状态同步 / 自动下载 / 自动清理。

异常安全：任何任务失败只记录事件，不影响下一轮；账号或下载器异常时
自动相关任务自动跳过（安全停止）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..database import add_event, db, get_setting
from ..utils.crypto import decrypt
from . import auto_cleanup, auto_download
from .download_manager import manager
from .mteam import MTeamError

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def refresh_accounts() -> None:
    """同步全部启用账号的资料与做种统计。"""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, api_key_enc, api_url FROM accounts WHERE enabled=1").fetchall()
    for row in rows:
        from .mteam import MTeamClient
        client = MTeamClient(decrypt(row["api_key_enc"]), row["api_url"],
                             limiter=manager._limiter_shared())
        try:
            profile = await client.member_profile()
            seeding = {}
            try:
                watching = await client.seed_watching()
                seeding = watching if isinstance(watching, dict) else {}
            except MTeamError:
                pass
            seed_count = seeding.get("count") or seeding.get("total") or 0
            seed_size = seeding.get("size") or seeding.get("totalSize") or 0
            with db() as conn:
                conn.execute(
                    """UPDATE accounts SET uid=?, username=?, uploaded=?, downloaded=?,
                       share_rate=?, bonus=?, seeding_count=?, seeding_size=?, profile_json=?,
                       last_sync=datetime('now'), last_error=NULL WHERE id=?""",
                    (profile["uid"], profile["username"], profile["uploaded"],
                     profile["downloaded"], profile["share_rate"], profile["bonus"],
                     int(seed_count), int(seed_size),
                     json.dumps(profile["raw"], ensure_ascii=False)[:20000],
                     row["id"]))
            if profile["warned"] or profile["leech_warn"]:
                add_event("warn", "account",
                          f"账号 {profile['username']} 存在站方警告（warned/leechWarn），请到站点查看")
        except MTeamError as e:
            with db() as conn:
                conn.execute("UPDATE accounts SET last_error=? WHERE id=?",
                             (str(e), row["id"]))
            add_event("warn", "account", f"账号同步失败：{e}")
        finally:
            await client.close()


async def sync_status() -> None:
    try:
        await manager.sync_status()
    except Exception as e:
        logger.warning("status sync failed: %s", e)


async def auto_download_job() -> None:
    cfg = get_setting("auto_download") or {}
    if not cfg.get("enabled"):
        return
    try:
        await run_with_concurrency_guard(auto_download.run_scan())
    except Exception as e:
        logger.exception("auto download job failed")
        add_event("warn", "auto_download", f"自动下载任务异常：{e}")


async def auto_cleanup_job() -> None:
    cfg = get_setting("auto_cleanup") or {}
    if not cfg.get("enabled"):
        return
    try:
        await run_with_concurrency_guard(auto_cleanup.run_cleanup())
    except Exception as e:
        logger.exception("auto cleanup job failed")
        add_event("warn", "cleanup", f"自动清理任务异常：{e}")


_running = {"auto": False}


async def run_with_concurrency_guard(coro) -> None:
    """防并发重入：上一轮未结束时跳过本轮。"""
    if _running["auto"]:
        add_event("info", "scheduler", "上一轮自动任务仍在执行，本轮跳过")
        return
    _running["auto"] = True
    try:
        await coro
    finally:
        _running["auto"] = False


def start() -> None:
    # 幂等：测试中多个 TestClient 生命周期会反复触发
    if scheduler.running:
        return
    try:
        _start_jobs()
        scheduler.start()
        logger.info("scheduler started")
    except Exception as e:  # 重复启动等环境差异不阻断服务
        logger.warning("scheduler start skipped: %s", e)


def _start_jobs() -> None:
    auto_cfg = get_setting("auto_download") or {}
    cleanup_cfg = get_setting("auto_cleanup") or {}
    scheduler.add_job(refresh_accounts, IntervalTrigger(
        seconds=int((get_setting("mteam_global") or {}).get("account_sync_interval") or 600)),
        id="refresh_accounts", max_instances=1, coalesce=True, replace_existing=True)
    scheduler.add_job(sync_status, IntervalTrigger(seconds=60),
                      id="sync_status", max_instances=1, coalesce=True, replace_existing=True)
    scheduler.add_job(auto_download_job, IntervalTrigger(
        seconds=max(300, int(auto_cfg.get("interval_minutes") or 30) * 60)),
        id="auto_download", max_instances=1, coalesce=True, replace_existing=True)
    scheduler.add_job(auto_cleanup_job, IntervalTrigger(
        seconds=max(600, int(cleanup_cfg.get("interval_minutes") or 60) * 60)),
        id="auto_cleanup", max_instances=1, coalesce=True, replace_existing=True)


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def reschedule_from_settings() -> None:
    """设置变更后热更新间隔。"""
    if not scheduler.running:
        return
    auto_cfg = get_setting("auto_download") or {}
    cleanup_cfg = get_setting("auto_cleanup") or {}
    try:
        scheduler.reschedule_job(
            "auto_download",
            trigger=IntervalTrigger(
                seconds=max(300, int(auto_cfg.get("interval_minutes") or 30) * 60)))
        scheduler.reschedule_job(
            "auto_cleanup",
            trigger=IntervalTrigger(
                seconds=max(600, int(cleanup_cfg.get("interval_minutes") or 60) * 60)))
    except Exception:
        logger.exception("reschedule failed")
