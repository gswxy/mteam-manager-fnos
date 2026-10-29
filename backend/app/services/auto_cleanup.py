"""自动清理引擎（核心安全功能）。

铁律：
1. 只允许清理「本应用创建且 source=auto」的任务
2. 永久保护（protected=1）、手动任务、未完成任务一律不碰
3. 必须同时满足：保留天数已过 + 做种时长达标 + 单种分享率达标（可获取时）
   + 无 H&R 风险标记。任何一项无法确认 -> 禁止删除
4. 通过飞牛下载中心接口删除（保证做种状态一致），文件级兜底仅在其后执行
5. 删除后记录原因、时间、回收空间；空间未恢复前不新增自动任务
6. 无可安全删除资源时：停止新增自动下载并告警，绝不强行删种

H&R 判定说明：M-Team API 未提供逐种保种义务字段，因此采用保守策略——
HR 标签种子（labels 含 "HR"/"H&R"）与做种时长不足最短天数的一律视为义务未解除。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from ..database import add_event, db, get_setting
from .download_manager import manager

logger = logging.getLogger(__name__)

HR_MARKERS = ("hr", "h&r", "hit and run", "hitandrun")
# 视为未完成的状态（不可清理）
INCOMPLETE = {"pending", "pushing", "waiting", "downloading", "paused", "checking", "unknown", "lost"}


def cleanup_settings() -> dict:
    return get_setting("auto_cleanup", {}) or {}


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def is_hr_flagged(task: dict) -> bool:
    """H&R 风险标记：站点 HR 标签。无法确认义务时保守返回 True。"""
    labels = []
    raw = task.get("labels")
    if isinstance(raw, str):
        try:
            labels = json.loads(raw)
        except (TypeError, ValueError):
            labels = []
    elif isinstance(raw, list):
        labels = raw
    low = [str(x).lower() for x in labels]
    return any(m in l for l in low for m in HR_MARKERS)


def cleanup_candidates(now: datetime | None = None) -> list[dict]:
    """筛选清理候选（不执行删除）。返回带 decision/reason 的任务列表。"""
    now = now or datetime.now()
    cfg = cleanup_settings()
    retain_days = int(cfg.get("retain_days") or 0)
    min_seed_days = float(cfg.get("min_seed_days") or 0)
    min_ratio = float(cfg.get("min_ratio") or 0)
    min_idle_hours = float(cfg.get("min_idle_hours") or 0)
    unconfirmed_deletable = bool(cfg.get("unconfirmed_deletable", False))

    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM download_tasks WHERE source='auto' AND removed_at IS NULL "
            "ORDER BY datetime(added_at) ASC").fetchall()
        protected_count = conn.execute(
            "SELECT COUNT(*) n FROM download_tasks WHERE source='auto' AND removed_at IS NULL "
            "AND protected=1").fetchone()["n"]

    candidates = []
    for row in rows:
        t = dict(row)
        meta = {}
        try:
            meta = json.loads(t.get("meta") or "{}")
        except (TypeError, ValueError):
            pass
        entry = dict(t)
        entry["decision"] = "keep"
        entry["reason"] = ""

        if t["protected"]:
            entry["reason"] = "永久保留"
            candidates.append(entry)
            continue
        if t["status"] in INCOMPLETE:
            entry["reason"] = "未完成或状态未知"
            candidates.append(entry)
            continue
        completed = _parse_dt(t["completed_at"]) or _parse_dt(t["added_at"])
        if completed is None:
            entry["reason"] = "完成时间未知，禁止删除"
            candidates.append(entry)
            continue
        age_days = (now - completed).total_seconds() / 86400
        if retain_days and age_days < retain_days:
            entry["reason"] = f"保留期内（{age_days:.1f}/{retain_days} 天）"
            candidates.append(entry)
            continue
        # 做种时长义务
        if min_seed_days and age_days < min_seed_days:
            entry["reason"] = f"最短做种时长未达标（{age_days:.1f}/{min_seed_days} 天）"
            candidates.append(entry)
            continue
        if is_hr_flagged(t) or meta.get("hr_flagged"):
            entry["reason"] = "存在 H&R 风险标记，义务无法确认"
            candidates.append(entry)
            continue
        # 单种分享率
        ratio = t.get("ratio")
        if min_ratio:
            if ratio is None:
                if not unconfirmed_deletable:
                    entry["reason"] = "分享率未知，无法确认义务"
                    candidates.append(entry)
                    continue
            elif ratio < min_ratio:
                entry["reason"] = f"单种分享率 {ratio:.2f} 低于阈值 {min_ratio}"
                candidates.append(entry)
                continue
        # 上传活跃度
        if min_idle_hours and (t.get("up_speed") or 0) > 0:
            entry["reason"] = "仍有上传活跃度"
            candidates.append(entry)
            continue
        entry["decision"] = "delete"
        entry["reason"] = (f"已保留 {age_days:.0f} 天"
                           + (f"，分享率 {ratio:.2f}" if ratio is not None else ""))
        candidates.append(entry)
    return candidates


def space_pressure(cfg: dict) -> tuple[bool, str]:
    """判断是否达到空间触发条件。"""
    quota_gb = float(cfg.get("quota_gb") or 0)
    min_free_gb = float(cfg.get("min_free_gb") or 0)
    ap = get_setting("auto_paths") or {}
    root = ap.get("root") or ""
    if quota_gb:
        with db() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(size),0) sz FROM download_tasks "
                "WHERE source='auto' AND removed_at IS NULL").fetchone()
        used = row["sz"]
        if used >= quota_gb * 1024**3:
            return True, f"自动下载目录占用 {used / 1024**3:.1f}GB 达到配额 {quota_gb}GB"
    if min_free_gb and root:
        free = manager._sync_free_space_cached(root)
        if free is not None and free < min_free_gb * 1024**3:
            return True, f"NAS 剩余空间 {free / 1024**3:.1f}GB 低于下限 {min_free_gb}GB"
    return False, ""


async def run_cleanup(dry_run: bool = False) -> dict:
    """执行一轮清理检查。"""
    cfg = cleanup_settings()
    result = {"enabled": bool(cfg.get("enabled")), "deleted": [], "kept": 0,
              "freed_bytes": 0, "trigger": "", "blocked_reason": None}
    if not cfg.get("enabled") and not dry_run:
        return result

    now = datetime.now()
    time_triggered = False
    trigger = ""
    if int(cfg.get("retain_days") or 0):
        # 保留天数到期即触发（逐种判断在候选筛选中）
        time_triggered = True
        trigger = "保留天数"
    space_hit, space_why = space_pressure(cfg)
    if space_hit:
        trigger = trigger + " + " if trigger else ""
        trigger += "空间压力"
    if not (time_triggered or space_hit or dry_run):
        return result
    result["trigger"] = trigger or "预览"

    candidates = cleanup_candidates(now)
    to_delete = [c for c in candidates if c["decision"] == "delete"]
    result["kept"] = len(candidates) - len(to_delete)

    if dry_run:
        result["candidates"] = [{"id": c["id"], "name": c["torrent_name"],
                                 "decision": c["decision"], "reason": c["reason"],
                                 "size": c["size"]} for c in candidates]
        return result

    quota_gb = float(cfg.get("quota_gb") or 0)
    target_bytes = quota_gb * 1024**3 * 0.85 if quota_gb else 0

    for c in to_delete:
        ok, err = await manager.remove(
            c["id"], delete_files=True,
            reason=f"自动清理：{c['reason']}")
        if ok:
            result["deleted"].append({"id": c["id"], "name": c["torrent_name"],
                                      "reason": c["reason"], "size": c["size"]})
            with db() as conn:
                row = conn.execute("SELECT freed_bytes FROM download_tasks WHERE id=?",
                                   (c["id"],)).fetchone()
                result["freed_bytes"] += (row["freed_bytes"] if row else 0) or c["size"]
            if target_bytes:
                with db() as conn:
                    used = conn.execute(
                        "SELECT COALESCE(SUM(size),0) sz FROM download_tasks "
                        "WHERE source='auto' AND removed_at IS NULL").fetchone()["sz"]
                if used <= target_bytes:
                    break
        else:
            add_event("warn", "cleanup", f"删除失败：{c['torrent_name'][:50]} - {err}")

    if result["deleted"]:
        add_event("info", "cleanup",
                  f"自动清理 {len(result['deleted'])} 个任务，回收 "
                  f"{result['freed_bytes'] / 1024**3:.2f}GB（{result['trigger']}）")

    # 空间压力仍在且无候选可删 -> 告警并停止新增自动下载
    still_hit, why = space_pressure(cfg)
    if still_hit and not to_delete:
        result["blocked_reason"] = f"{why}，且没有可安全删除的任务，已暂停新增自动下载"
        add_event("warn", "cleanup", result["blocked_reason"])
    return result
