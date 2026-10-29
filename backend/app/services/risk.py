"""分享率风险计算。

原则：只做风险提示与保守预测，不承诺提高分享率，不伪造任何数据。
计量下载量规则（M-Team 官方）：
- FREE/_2X_FREE：不计量下载
- PERCENT_50/_2X_PERCENT_50：按 50% 计量
- NORMAL/_2X：按 100% 计量
"""
from __future__ import annotations

from ..database import db, get_setting

DISCOUNT_FACTOR = {
    "FREE": 0.0, "_2X_FREE": 0.0,
    "PERCENT_50": 0.5, "_2X_PERCENT_50": 0.5,
    "NORMAL": 1.0, "_2X": 1.0,
}


def account_stats(account_id: int) -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT uploaded, downloaded, share_rate FROM accounts WHERE id=?",
            (account_id,)).fetchone()
    if row is None:
        return {"uploaded": 0, "downloaded": 0, "share_rate": 0.0}
    return dict(row)


def predict(account_id: int, size: int, discount: str, upload_factor: float = 1.0,
            expected_upload_ratio: float = 0.8) -> dict:
    """预测新增任务对账号分享率的影响。

    expected_upload_ratio：对该任务生命周期内上传量/体积 的保守估计（默认 0.8，
    即认为平均可上传 80% 体积；免费种子不产生计量下载）。
    """
    stats = account_stats(account_id)
    factor = DISCOUNT_FACTOR.get(discount, 1.0)
    billed_download = int(size * factor)
    if factor == 0.0:
        # 免费下载：只可能带来上传收益
        est_upload = int(size * expected_upload_ratio)
        new_up, new_down = stats["uploaded"] + est_upload, stats["downloaded"]
    else:
        est_upload = int(size * expected_upload_ratio * upload_factor)
        new_up, new_down = stats["uploaded"] + est_upload, stats["downloaded"] + billed_download
    new_rate = round(new_up / new_down, 2) if new_down > 0 else float("inf")
    target = float((get_setting("account_protection") or {}).get("min_share_rate") or 2.0)
    current_rate = stats["share_rate"] or (stats["uploaded"] / stats["downloaded"]
                                           if stats["downloaded"] else 0)
    level = "safe"
    if factor > 0:
        if new_rate < 1.0:
            level = "danger"
        elif new_rate < target:
            level = "warn"
    return {
        "billed_download": billed_download,
        "estimated_upload": est_upload,
        "current_rate": round(current_rate, 2),
        "predicted_rate": None if new_rate == float("inf") else new_rate,
        "target_rate": target,
        "level": level,
        "message": {
            "safe": "预计分享率保持在目标之上",
            "warn": "下载后分享率可能低于保护目标，建议选择免费资源",
            "danger": "下载后分享率可能跌破 1.0，风险高",
        }[level],
    }


def seeding_quality(account_id: int) -> dict:
    """保种质量：做种数量/体积来自账号同步与本地任务表。"""
    with db() as conn:
        row = conn.execute("SELECT seeding_count, seeding_size FROM accounts WHERE id=?",
                           (account_id,)).fetchone()
        tasks = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(size),0) sz FROM download_tasks "
            "WHERE removed_at IS NULL AND status IN ('seeding','completed')").fetchone()
    return {
        "seeding_count": (row["seeding_count"] if row else 0) or tasks["n"],
        "seeding_size": (row["seeding_size"] if row else 0) or tasks["sz"],
        "local_seeding_count": tasks["n"],
        "local_seeding_size": tasks["sz"],
    }
