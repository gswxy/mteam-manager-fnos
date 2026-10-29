"""自动下载引擎。

安全顺序（每次命中后重新检查）：
1. 时间窗口与全局开关
2. 每日任务数 / 最大同时下载数 / 空间预算
3. 规则匹配（类别、画质、大小、免费、做种人数、活跃度）
4. 重复检查（本地 + 站点历史 + 下载中心）
5. 促销状态重新确认（以 torrent/detail 为准）
6. 分享率风险预测（非免费时不得超过保护阈值）

规则预览：同一路径 dry_run=True，不产生任何写操作。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from ..database import add_event, db, get_setting
from .download_manager import manager
from .mteam import FREE_DISCOUNTS, MTeamError
from .risk import predict

logger = logging.getLogger(__name__)

QUALITY_PATTERNS = {
    "4k": ("2160p", "4K"),
    "1080p": ("1080p", "1080i"),
    "720p": ("720p",),
    "any": (),
}


def auto_settings() -> dict:
    return get_setting("auto_download", {}) or {}


def _in_time_window(cfg: dict) -> bool:
    window = (cfg.get("time_window") or "").strip()
    if not window or "-" not in window:
        return True
    try:
        start_s, end_s = window.split("-", 1)
        now = datetime.now().time()
        start = datetime.strptime(start_s.strip(), "%H:%M").time()
        end = datetime.strptime(end_s.strip(), "%H:%M").time()
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end  # 跨天窗口
    except ValueError:
        return True


def _quality_ok(name: str, qualities: list[str]) -> bool:
    if not qualities or "any" in qualities:
        return True
    low = name.lower()
    for q in qualities:
        for pat in QUALITY_PATTERNS.get(q, (q.lower(),)):
            if pat.lower() in low:
                return True
    return False


def _rule_category_prefixes(categories: list) -> list[str]:
    """UI 类别（movie 等）-> M-Team 分类码前缀（401 等）。"""
    from .mteam import UI_CATEGORY_MAP
    prefixes = []
    for c in categories or []:
        c = str(c)
        if c.isdigit():
            prefixes.append(c[:3])
        else:
            prefixes.extend(UI_CATEGORY_MAP.get(c, []))
    return prefixes


def match_rule(torrent: dict, rule: dict) -> str | None:
    """返回命中原因；未命中返回 None。纯函数，供单测。"""
    cfg = rule.get("config") if isinstance(rule.get("config"), dict) else {}
    if isinstance(rule.get("config"), str):
        import json
        cfg = json.loads(rule["config"] or "{}")
    name = torrent["name"]
    size = torrent["size"]

    prefixes = _rule_category_prefixes(cfg.get("categories"))
    if prefixes:
        cat = (torrent.get("category") or "")
        if not any(cat.startswith(p) for p in prefixes):
            return None

    if not _quality_ok(name, cfg.get("qualities") or []):
        return None

    min_size = float(cfg.get("min_size_gb") or 0) * 1024**3
    max_size = float(cfg.get("max_size_gb") or 0) * 1024**3
    if min_size and size < min_size:
        return None
    if max_size and size > max_size:
        return None

    promo_mode = cfg.get("promo_mode") or "free_first"
    is_free = torrent["discount"] in FREE_DISCOUNTS
    if promo_mode == "free_only" and not is_free:
        return None
    if promo_mode == "free_first":
        # 非免费种子仅在「偏好下载」规则且明确允许时考虑
        if not is_free and not cfg.get("allow_paid"):
            return None

    min_seeders = int(cfg.get("min_seeders") or 0)
    if min_seeders and torrent["seeders"] < min_seeders:
        return None

    min_leechers = int(cfg.get("min_leechers") or 0)
    if min_leechers and torrent["leechers"] < min_leechers:
        return None

    keywords = cfg.get("keywords") or []
    if keywords:
        low = name.lower()
        if not any(k.lower() in low for k in keywords):
            return None
    excludes = cfg.get("exclude_keywords") or []
    if excludes:
        low = name.lower()
        if any(k.lower() in low for k in excludes):
            return None

    reasons = []
    if is_free:
        reasons.append("免费")
    if torrent["discount"] in ("_2X", "_2X_FREE", "_2X_PERCENT_50"):
        reasons.append("双倍上传")
    if rule.get("kind") == "seeding":
        reasons.append(f"保种（做种 {torrent['seeders']} 人）")
    else:
        reasons.append(f"偏好匹配（做种 {torrent['seeders']} 人）")
    return "、".join(reasons)


def _budget(cfg: dict) -> dict:
    return {
        "daily_max": int(cfg.get("daily_max") or 0),
        "max_concurrent": int(cfg.get("max_concurrent") or 0),
        "space_budget_gb": float(cfg.get("space_budget_gb") or 0),
    }


def budget_state(cfg: dict) -> dict:
    """统计当前预算占用。"""
    b = _budget(cfg)
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as conn:
        added_today = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(size),0) sz FROM download_tasks "
            "WHERE source='auto' AND date(added_at)=?", (today,)).fetchone()
        active = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(size),0) sz FROM download_tasks "
            "WHERE source='auto' AND removed_at IS NULL "
            "AND status IN ('pending','pushing','waiting','downloading','paused','checking')"
        ).fetchone()
        total_auto = conn.execute(
            "SELECT COALESCE(SUM(size),0) sz FROM download_tasks "
            "WHERE source='auto' AND removed_at IS NULL").fetchone()
    return {
        "added_today": added_today["n"],
        "added_today_size": added_today["sz"],
        "active_count": active["n"],
        "total_auto_size": total_auto["sz"],
        "daily_max": b["daily_max"],
        "max_concurrent": b["max_concurrent"],
        "space_budget_bytes": int(b["space_budget_gb"] * 1024**3),
    }


def budget_allows(cfg: dict) -> tuple[bool, str]:
    st = budget_state(cfg)
    b = _budget(cfg)
    if b["daily_max"] and st["added_today"] >= b["daily_max"]:
        return False, f"今日已添加 {st['added_today']} 个自动任务（上限 {b['daily_max']}）"
    if b["max_concurrent"] and st["active_count"] >= b["max_concurrent"]:
        return False, f"下载中任务已达上限（{st['active_count']}/{b['max_concurrent']}）"
    if b["space_budget_gb"]:
        used = st["total_auto_size"]
        if used >= b["space_budget_gb"] * 1024**3:
            return False, "自动下载空间预算已用尽，等待清理释放"
    # NAS 最低剩余空间
    min_free_gb = float(cfg.get("min_free_gb") or 0)
    if min_free_gb:
        from .download_manager import manager as mgr
        ap = get_setting("auto_paths") or {}
        root = ap.get("root") or ""
        if root:
            free = mgr._sync_free_space_cached(root)
            if free is not None and free < min_free_gb * 1024**3:
                return False, f"NAS 剩余空间不足（{free / 1024**3:.0f}GB < {min_free_gb}GB）"
    return True, "ok"


async def run_scan(dry_run: bool = False, max_add: int | None = None) -> dict:
    """执行一轮自动下载扫描。dry_run=True 为规则预览。"""
    cfg = auto_settings()
    result = {"checked": 0, "matched": [], "added": 0, "skipped": [], "stopped_reason": None}
    if not cfg.get("enabled") and not dry_run:
        result["stopped_reason"] = "自动下载未启用"
        return result
    if not dry_run and not _in_time_window(cfg):
        result["stopped_reason"] = "不在运行时间段内"
        return result

    with db() as conn:
        rules = conn.execute(
            "SELECT * FROM auto_rules WHERE enabled=1 ORDER BY id").fetchall()
    if not rules:
        result["stopped_reason"] = "没有启用的规则"
        return result

    account_id = manager.default_account_id()
    if account_id is None:
        result["stopped_reason"] = "未配置 M-Team 账号"
        return result

    ok, why = budget_allows(cfg)
    if not ok and not dry_run:
        result["stopped_reason"] = why
        add_event("warn", "auto_download", f"自动下载停止：{why}")
        return result

    mt = manager.mteam(account_id)
    # 拉取最新数据（含免费）
    from .discover import fetch_latest
    try:
        await fetch_latest(mt, pages=int(cfg.get("scan_pages") or 2))
    except MTeamError as e:
        result["stopped_reason"] = f"M-Team 接口错误：{e}"
        return result

    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM torrents ORDER BY datetime(created_date) DESC LIMIT 2000"
        ).fetchall()

    import json as _j
    protection = get_setting("account_protection") or {}
    target_rate = float(protection.get("min_share_rate") or 2.0)
    added = 0
    for row in rows:
        t = dict(row)
        try:
            t["labels"] = _j.loads(row["labels"]) if row["labels"] else []
        except (TypeError, ValueError):
            t["labels"] = []
        if t["discount"] not in FREE_DISCOUNTS and t["discount"] not in ("PERCENT_50", "_2X_PERCENT_50", "_2X", "NORMAL"):
            continue
        for rule in rules:
            rule_d = dict(rule)
            rule_d["config"] = _j.loads(rule_d["config"] or "{}")
            reason = match_rule(t, rule_d)
            if not reason:
                continue
            result["checked"] += 1
            entry = {
                "torrent_id": t["id"], "name": t["name"], "size": t["size"],
                "discount": t["discount"], "seeders": t["seeders"],
                "rule": rule_d["name"], "reason": reason,
            }
            # 分享率风险门（非免费）
            if t["discount"] not in FREE_DISCOUNTS:
                pred = predict(account_id, t["size"], t["discount"])
                entry["risk"] = pred
                if pred["level"] == "danger":
                    entry["action"] = "skip"
                    entry["action_reason"] = "分享率风险过高"
                    result["skipped"].append(entry)
                    break
            if dry_run:
                entry["action"] = "would_add"
                result["matched"].append(entry)
            else:
                res = await manager.add_torrent(
                    t["id"], source="auto",
                    category=_category_of(t["category"]),
                    account_id=account_id, rule_id=rule_d["id"],
                    rule_name=rule_d["name"], hit_reason=reason)
                if res.get("added"):
                    entry["action"] = "added"
                    result["matched"].append(entry)
                    result["added"] += 1
                    added += 1
                    if max_add and added >= max_add:
                        return result
                    ok, why = budget_allows(cfg)
                    if not ok:
                        result["stopped_reason"] = why
                        return result
                else:
                    entry["action"] = "skip"
                    entry["action_reason"] = res.get("reason")
                    result["skipped"].append(entry)
            break
    return result


def _category_of(mt_cat: str) -> str:
    """M-Team 分类码 -> 手动/自动目录类别。"""
    from .mteam import MT_CATEGORY_GROUPS
    cat = mt_cat or ""
    for g in MT_CATEGORY_GROUPS:
        if any(cat.startswith(code) for code in g["codes"]):
            if g["key"] == "tv":
                return "tv"
            if g["key"] in ("doc", "music", "sport", "game", "other"):
                return "other"
            return g["key"]
    return "other"
