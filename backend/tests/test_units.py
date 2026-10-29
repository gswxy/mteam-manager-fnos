"""纯逻辑单元测试：解析、聚合、规则、清理候选、风险、路径、加密。"""
from __future__ import annotations

import json

from app.services import auto_cleanup, auto_download, discover, paths, risk
from app.services.mteam import (FREE_DISCOUNTS, merge_discount, parse_flex_time,
                                parse_torrent)
from app.utils.crypto import decrypt, encrypt, mask


# ---------- M-Team 解析 ----------

def test_parse_torrent_basic():
    raw = {
        "id": "1", "name": "Movie.2160p", "smallDescr": "电影X", "category": "401",
        "size": 123, "status": {"seeders": 5, "leechers": 2, "timesCompleted": 9,
                                "discount": "FREE"},
        "createdDate": "2026-10-01 10:00:00", "douban": "123", "doubanRating": 8.0,
        "imageList": ["https://x/img.jpg"],
    }
    t = parse_torrent(raw)
    assert t["id"] == "1" and t["seeders"] == 5
    assert t["discount"] == "FREE" and t["is_free"]
    assert t["poster"] == "https://x/img.jpg"
    assert t["douban_rating"] == 8.0


def test_merge_discount_promotion_rule_wins():
    status = {"discount": "NORMAL",
              "promotionRule": {"discount": "_2X_FREE", "endTime": None}}
    discount, _ = merge_discount(status)
    assert discount == "_2X_FREE" and discount in FREE_DISCOUNTS


def test_merge_discount_keeps_better_local():
    status = {"discount": "FREE", "promotionRule": {"discount": "_2X", "endTime": None}}
    discount, _ = merge_discount(status)
    assert discount == "FREE"


def test_parse_flex_time_formats():
    assert parse_flex_time("2026-10-01 10:00:00") == "2026-10-01 10:00:00"
    assert parse_flex_time(1793000000000).startswith("20")
    assert parse_flex_time(None) is None


# ---------- 影视聚合 ----------

def test_aggregate_by_douban():
    rows = [
        {"id": "a", "name": "Movie.2160p", "small_descr": "电影A", "category": "401",
         "size": 1, "seeders": 5, "leechers": 1, "times_completed": 2, "discount": "FREE",
         "discount_end": None, "douban_id": "db1", "douban_rating": 8.0, "imdb_id": None,
         "imdb_rating": None, "poster": None, "created_date": "2026-10-01 00:00:00",
         "labels": None},
        {"id": "b", "name": "Movie.1080p", "small_descr": "电影A", "category": "401",
         "size": 2, "seeders": 9, "leechers": 0, "times_completed": 3, "discount": "NORMAL",
         "discount_end": None, "douban_id": "db1", "douban_rating": 8.0, "imdb_id": None,
         "imdb_rating": None, "poster": None, "created_date": "2026-10-02 00:00:00",
         "labels": None},
    ]
    groups = discover.aggregate(rows)
    assert len(groups) == 1
    g = groups[0]
    assert g["key"] == "db:db1"
    assert g["version_count"] == 2
    assert g["seeders_total"] == 14
    assert g["has_free"] is True


def test_normalize_title():
    assert "the movie" in discover.normalize_title("The.Movie.2023.2160p.WEB-DL")
    assert discover.normalize_title("Show.S02E01.1080p") == "show"


# ---------- 自动下载规则 ----------

def _torrent(discount="FREE", size=10 * 1024**3, seeders=10, leechers=5,
             name="Movie.2160p.WEB-DL", category="401"):
    return {"id": "x", "name": name, "size": size, "seeders": seeders,
            "leechers": leechers, "discount": discount, "category": category,
            "discount_end": None}


def _rule(**cfg):
    base = {"name": "r1", "kind": "preference",
            "config": {"categories": ["movie"], "qualities": ["4k"],
                       "promo_mode": "free_only", "min_size_gb": 1, "max_size_gb": 50,
                       "min_seeders": 2}}
    base["config"].update(cfg)
    return base


def test_match_rule_hit():
    assert auto_download.match_rule(_torrent(), _rule()) is not None


def test_match_rule_reject_non_free():
    assert auto_download.match_rule(_torrent(discount="NORMAL"), _rule()) is None


def test_match_rule_reject_quality():
    assert auto_download.match_rule(_torrent(name="Movie.1080p"), _rule()) is None


def test_match_rule_reject_size():
    assert auto_download.match_rule(_torrent(size=100 * 1024**3), _rule()) is None


def test_match_rule_reject_seeders():
    assert auto_download.match_rule(_torrent(seeders=1), _rule()) is None


def test_match_rule_keywords():
    r = _rule(keywords=["HDR"], promo_mode="any")
    assert auto_download.match_rule(_torrent(name="Movie.2160p.HDR", discount="NORMAL"), r)
    assert not auto_download.match_rule(_torrent(name="Movie.2160p"), r)


def test_time_window():
    cfg = {"time_window": "00:00-23:59"}
    assert auto_download._in_time_window(cfg) is True
    assert auto_download._in_time_window({}) is True
    assert auto_download._in_time_window({"time_window": "bad"}) is True


# ---------- 清理候选 ----------

def _task(source="auto", status="seeding", protected=0, age_days=40,
          ratio=2.0, labels=None, completed="2026-09-01 00:00:00"):
    return {"id": 1, "torrent_name": "t", "size": 1, "source": source, "status": status,
            "protected": protected, "completed_at": completed, "added_at": completed,
            "ratio": ratio, "labels": json.dumps(labels or []), "up_speed": 0,
            "meta": None}


def test_cleanup_candidate_rules(fresh_db):
    from app.database import set_setting
    set_setting("auto_cleanup", {"retain_days": 30, "min_seed_days": 7, "min_ratio": 1.0})
    import datetime
    now = datetime.datetime(2026, 10, 20)
    # 未到期
    c = auto_cleanup.cleanup_candidates.__wrapped__ if hasattr(
        auto_cleanup.cleanup_candidates, "__wrapped__") else None
    # 通过数据库路径验证：插入任务
    from app.database import db
    with db() as conn:
        conn.execute(
            """INSERT INTO download_tasks (account_id,torrent_id,torrent_name,size,source,
               save_path,status,protected,completed_at,added_at,ratio)
               VALUES (1,'t1','T1',100,'auto','/vol1/x','seeding',0,
               '2026-09-01 00:00:00','2026-09-01 00:00:00',2.0)""")
        conn.execute(
            """INSERT INTO download_tasks (account_id,torrent_id,torrent_name,size,source,
               save_path,status,protected,completed_at,added_at,ratio)
               VALUES (1,'t2','T2',100,'auto','/vol1/x','seeding',1,
               '2026-09-01 00:00:00','2026-09-01 00:00:00',2.0)""")
        conn.execute(
            """INSERT INTO download_tasks (account_id,torrent_id,torrent_name,size,source,
               save_path,status,protected,completed_at,added_at,ratio)
               VALUES (1,'t3','T3',100,'auto','/vol1/x','downloading',0,
               '2026-09-01 00:00:00','2026-09-01 00:00:00',2.0)""")
        conn.execute(
            """INSERT INTO download_tasks (account_id,torrent_id,torrent_name,size,source,
               save_path,status,protected,completed_at,added_at,ratio)
               VALUES (1,'t4','T4',100,'auto','/vol1/x','seeding',0,
               '2026-09-01 00:00:00','2026-09-01 00:00:00',0.3)""")
        conn.execute(
            """INSERT INTO download_tasks (account_id,torrent_id,torrent_name,size,source,
               save_path,status,protected,completed_at,added_at,ratio,labels)
               VALUES (1,'t5','T5',100,'auto','/vol1/x','seeding',0,
               '2026-09-01 00:00:00','2026-09-01 00:00:00',5.0,'["HR"]')""")
    cands = {c["torrent_name"]: c for c in auto_cleanup.cleanup_candidates(now)}
    assert cands["T1"]["decision"] == "delete"      # 满足全部条件
    assert cands["T2"]["decision"] == "keep"        # 永久保护
    assert cands["T3"]["decision"] == "keep"        # 未完成
    assert cands["T4"]["decision"] == "keep"        # 分享率不足
    assert cands["T5"]["decision"] == "keep"        # HR 标记


def test_hr_flag_detection():
    assert auto_cleanup.is_hr_flagged({"labels": json.dumps(["HR"])})
    assert auto_cleanup.is_hr_flagged({"labels": json.dumps(["h&r"])})
    assert not auto_cleanup.is_hr_flagged({"labels": json.dumps(["FREE"])})
    assert not auto_cleanup.is_hr_flagged({"labels": None})


# ---------- 风险 ----------

def test_predict_free_is_safe(fresh_db):
    from app.database import db
    with db() as conn:
        conn.execute(
            "INSERT INTO accounts (id,name,api_key_enc,uploaded,downloaded,share_rate) "
            "VALUES (1,'a','enc',?,?,?)", (10**12, 10**11, 10.0))
    p = risk.predict(1, 10 * 1024**3, "FREE")
    assert p["billed_download"] == 0 and p["level"] == "safe"


def test_predict_paid_danger(fresh_db):
    from app.database import db
    with db() as conn:
        conn.execute(
            "INSERT INTO accounts (id,name,api_key_enc,uploaded,downloaded,share_rate) "
            "VALUES (1,'a','enc',?,?,?)", (10**11, 10**11, 1.0))
    p = risk.predict(1, 50 * 1024**3, "NORMAL")
    assert p["billed_download"] == 50 * 1024**3
    assert p["level"] == "danger"


def test_predict_half_discount():
    p = risk.predict(1, 10 * 1024**3, "PERCENT_50")
    assert p["billed_download"] == 5 * 1024**3


# ---------- 路径安全 ----------

def test_safe_paths():
    assert paths.is_safe_path("/vol1/downloads")
    assert paths.is_safe_path("/vol2/PT/电影")
    assert not paths.is_safe_path("/etc/passwd")
    assert not paths.is_safe_path("/vol1/../etc")
    assert not paths.is_safe_path("vol1/x")
    assert not paths.is_safe_path("")


def test_within_allowed():
    assert paths.within_allowed("/vol1/a/b", ["/vol1/a"])
    assert not paths.within_allowed("/vol2/a", ["/vol1/a"])


# ---------- 加密 ----------

def test_encrypt_roundtrip():
    token = encrypt("my-secret-key")
    assert token != "my-secret-key" and token.startswith("v1:")
    assert decrypt(token) == "my-secret-key"


def test_mask():
    assert mask("abcdefgh", 2) == "ab****gh"
    assert mask("") == ""
