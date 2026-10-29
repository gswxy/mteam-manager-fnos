"""API 集成测试：模拟 M-Team HTTP 服务 + FastAPI TestClient 全链路。"""
from __future__ import annotations

import json

from app.database import db, set_setting
from app.utils.crypto import encrypt

ACCOUNT = {"name": "主账号", "api_key": "test-api-key", "api_url": "https://api.m-team.cc"}


def _setup_account(client):
    r = client.post("/api/accounts", json=ACCOUNT)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _setup_fnos(client, ws_url):
    r = client.put("/api/settings/fnos", json={
        "ws_url": ws_url, "username": "nas-user", "password": "nas-pass"})
    assert r.status_code == 200


def test_health_no_auth(client=1):
    pass


def test_account_crud_and_validation(api_client, mock_mteam):
    aid = _setup_account(api_client)
    items = api_client.get("/api/accounts").json()["items"]
    assert items[0]["username"] == "tester"
    assert "api_key" not in items[0]                     # 明文 key 不回传
    assert items[0]["api_key_masked"].startswith("test")  # 只有脱敏指纹
    # 无效 key 被拒绝
    r = api_client.post("/api/accounts", json={**ACCOUNT, "name": "bad", "api_key": "invalid-key!!"})
    assert r.status_code == 400
    # 同步
    r = api_client.post(f"/api/accounts/{aid}/sync")
    assert r.json()["ok"] and r.json()["username"] == "tester"


def test_discover_search_and_aggregation(api_client, mock_mteam):
    _setup_account(api_client)
    r = api_client.get("/api/discover/search", params={"q": "Movie"})
    assert r.status_code == 200
    d = r.json()
    assert d["total"] >= 1
    # 1001 + 1002 同豆瓣 ID，聚合为一个条目
    titles = [m["title"] for m in d["items"]]
    assert any("测试电影A" in t for t in titles)
    item = next(m for m in d["items"] if "测试电影A" in m["title"])
    assert item["version_count"] == 2
    assert item["douban_rating"] == 8.4


def test_torrent_detail_and_risk(api_client, mock_mteam):
    _setup_account(api_client)
    r = api_client.get("/api/discover/torrent/1001")
    assert r.status_code == 200
    d = r.json()
    assert d["mediainfo"].startswith("General")
    assert d["risk"]["level"] == "safe"   # FREE
    r2 = api_client.get("/api/discover/torrent/1002")
    assert r2.json()["risk"]["billed_download"] > 0  # NORMAL 计量


def test_manual_download_dedup(api_client, mock_mteam, mock_fnos, monkeypatch):
    mock, url = mock_fnos
    _setup_account(api_client)
    _setup_fnos(api_client, url)
    set_setting("manual_paths", {"movie": "/vol1/movies", "other": "/vol1/other"})
    monkeypatch.setattr("app.services.download_manager.validate_dir", lambda *a, **k: (True, "ok"))
    r = api_client.post("/api/discover/torrent/1001/download", json={"category": "movie"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] is True
    # 重复添加被拒绝
    r2 = api_client.post("/api/discover/torrent/1001/download", json={"category": "movie"})
    assert r2.status_code == 400 and "已存在" in r2.json()["detail"]
    # 站点历史已下载（1002 在 historyMap 中为 True）
    r3 = api_client.post("/api/discover/torrent/1002/download", json={"category": "movie"})
    assert r3.status_code == 400 and "下载记录" in r3.json()["detail"]


def test_status_sync_and_actions(api_client, mock_mteam, mock_fnos, monkeypatch):
    import asyncio
    from app.services.download_manager import manager
    mock, url = mock_fnos
    _setup_account(api_client)
    _setup_fnos(api_client, url)
    set_setting("manual_paths", {"movie": "/vol1/movies", "other": "/vol1/other"})
    monkeypatch.setattr("app.services.download_manager.validate_dir", lambda *a, **k: (True, "ok"))
    added = api_client.post("/api/discover/torrent/1001/download",
                            json={"category": "movie"}).json()
    db_id = added["task_db_id"]
    fnos_id = added["fnos_task_id"]
    # 完成任务后同步
    mock.complete(fnos_id, ratio=1.5)
    res = asyncio.run(manager.sync_status())
    assert res["ok"]
    tasks = api_client.get("/api/downloads").json()["items"]
    t = next(t for t in tasks if t["id"] == db_id)
    assert t["status"] == "seeding"
    assert t["progress"] == 1.0
    assert t["ratio"] == 1.5
    # 保护与取消保护
    assert api_client.post(f"/api/downloads/{db_id}/protect").status_code == 200
    assert api_client.post(f"/api/downloads/{db_id}/pause").status_code == 200
    assert api_client.post(f"/api/downloads/{db_id}/resume").status_code == 200
    # 删除任务（不删文件）
    assert api_client.post(f"/api/downloads/{db_id}/delete",
                           json={"delete_files": False}).status_code == 200
    assert fnos_id not in mock.tasks
    history = api_client.get("/api/downloads/history").json()["items"]
    assert any(h["id"] == db_id for h in history)


def test_auto_download_flow(api_client, mock_mteam, mock_fnos, monkeypatch):
    import asyncio
    from app.services import auto_download
    mock, url = mock_fnos
    _setup_account(api_client)
    _setup_fnos(api_client, url)
    set_setting("manual_paths", {"movie": "/vol1/movies", "other": "/vol1/other"})
    set_setting("auto_paths", {"root": "/vol1/auto", "by_category": True})
    monkeypatch.setattr("app.services.download_manager.validate_dir", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr("app.services.download_manager.ensure_category_dir",
                        lambda root, cat, enabled: root)
    # 规则：电影 + 4K + 仅免费
    r = api_client.post("/api/settings/rules", json={
        "name": "免费4K电影", "kind": "free", "enabled": True,
        "categories": ["movie"], "qualities": ["4k"], "promo_mode": "free_only",
        "min_size_gb": 1, "max_size_gb": 50, "min_seeders": 2,
        "allow_paid": False, "min_leechers": 0, "keywords": [], "exclude_keywords": []})
    assert r.status_code == 201
    set_setting("auto_download", {"enabled": True, "daily_max": 5, "max_concurrent": 3,
                                  "space_budget_gb": 1000, "min_free_gb": 0})
    # 预览：1001 命中
    preview = asyncio.run(auto_download.run_scan(dry_run=True))
    assert any(m["torrent_id"] == "1001" for m in preview["matched"])
    # 真实执行：添加成功
    result = asyncio.run(auto_download.run_scan())
    assert result["added"] == 1
    tasks = api_client.get("/api/downloads?filter=auto").json()["items"]
    assert tasks and tasks[0]["source"] == "auto"
    assert tasks[0]["rule_name"] == "免费4K电影"
    assert "免费" in tasks[0]["hit_reason"]
    # 再次执行：去重，不重复添加
    result2 = asyncio.run(auto_download.run_scan())
    assert result2["added"] == 0


def test_auto_cleanup_protections(api_client, mock_mteam, mock_fnos, monkeypatch):
    import asyncio
    import datetime
    from app.services import auto_cleanup, auto_download
    mock, url = mock_fnos
    _setup_account(api_client)
    _setup_fnos(api_client, url)
    set_setting("manual_paths", {"movie": "/vol1/movies", "other": "/vol1/other"})
    set_setting("auto_paths", {"root": "/vol1/auto", "by_category": True})
    monkeypatch.setattr("app.services.download_manager.validate_dir", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr("app.services.download_manager.ensure_category_dir",
                        lambda root, cat, enabled: root)
    set_setting("auto_download", {"enabled": True, "daily_max": 5, "max_concurrent": 3,
                                  "space_budget_gb": 0, "min_free_gb": 0})
    api_client.post("/api/settings/rules", json={
        "name": "免费4K电影", "kind": "free", "enabled": True,
        "categories": ["movie"], "qualities": ["4k"], "promo_mode": "free_only",
        "min_size_gb": 1, "max_size_gb": 50, "min_seeders": 2, "allow_paid": False,
        "min_leechers": 0, "keywords": [], "exclude_keywords": []})
    asyncio.run(auto_download.run_scan())
    tasks = api_client.get("/api/downloads?filter=auto").json()["items"]
    assert tasks
    db_id = tasks[0]["id"]
    # 手动添加一个任务作对照（不应被清理）
    set_setting("manual_paths", {"movie": "/vol1/movies", "other": "/vol1/other"})
    api_client.post("/api/discover/torrent/1003/download", json={"category": "movie"})
    # 置为完成且满足保留条件
    with db() as conn:
        conn.execute(
            "UPDATE download_tasks SET status='seeding', progress=1.0, ratio=2.0, "
            "completed_at='2020-01-01 00:00:00' WHERE id=?", (db_id,))
    set_setting("auto_cleanup", {"enabled": True, "retain_days": 30, "min_seed_days": 7,
                                 "min_ratio": 1.0, "quota_gb": 0, "min_free_gb": 0,
                                 "min_idle_hours": 0, "unconfirmed_deletable": False})
    # 保护后不清理
    api_client.post(f"/api/downloads/{db_id}/protect")
    result = asyncio.run(auto_cleanup.run_cleanup())
    assert result["deleted"] == []
    # 取消保护后清理
    api_client.post(f"/api/downloads/{db_id}/unprotect")
    result2 = asyncio.run(auto_cleanup.run_cleanup())
    assert len(result2["deleted"]) == 1
    # 手动任务仍在
    remain = api_client.get("/api/downloads").json()["items"]
    assert any(t["id"] != db_id for t in remain)
    logs = api_client.get("/api/downloads/history").json()["cleanup_logs"]
    assert logs and logs[0]["freed_bytes"] >= 0


def test_rules_crud_and_settings(api_client):
    r = api_client.post("/api/settings/rules", json={
        "name": "r", "kind": "free", "enabled": False, "categories": [],
        "qualities": [], "min_size_gb": 0, "max_size_gb": 0, "promo_mode": "free_only",
        "allow_paid": False, "min_seeders": 0, "min_leechers": 0, "keywords": [],
        "exclude_keywords": []})
    rid = r.json()["id"]
    assert api_client.get("/api/settings/rules").json()["items"][0]["name"] == "r"
    r2 = api_client.put(f"/api/settings/rules/{rid}", json={
        "name": "r2", "kind": "preference", "enabled": True, "categories": ["tv"],
        "qualities": ["1080p"], "min_size_gb": 1, "max_size_gb": 20,
        "promo_mode": "any", "allow_paid": True, "min_seeders": 1, "min_leechers": 1,
        "keywords": ["a"], "exclude_keywords": ["b"]})
    assert r2.status_code == 200
    assert api_client.delete(f"/api/settings/rules/{rid}").status_code == 200


def test_path_validation_rejects_unsafe(api_client):
    # 非法路径被拒（validate_dir 在此环境真实执行 /vol 检查 -> 不存在 -> 400）
    r = api_client.put("/api/settings/manual-paths", json={
        "movie": "/etc/passwd", "tv": "", "anime": "", "show": "", "other": ""})
    assert r.status_code == 400
    assert "路径必须形如" in r.json()["detail"] or "不存在" in r.json()["detail"]


def test_api_key_encrypted_at_rest(api_client, mock_mteam):
    _setup_account(api_client)
    with db() as conn:
        row = conn.execute("SELECT api_key_enc FROM accounts WHERE id=1").fetchone()
    stored = row["api_key_enc"]
    assert stored.startswith("v1:")
    assert "test-api-key" not in stored
    assert decrypt(stored) == "test-api-key"


def test_db_migration_idempotent(tmp_path, monkeypatch):
    from app import database
    monkeypatch.setattr(database.settings, "db_path", tmp_path / "m.db")
    database.init_db()
    database.init_db()  # 二次初始化不报错
    with database.db() as conn:
        v = conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()["v"]
        assert v == 1


from app.utils.crypto import decrypt  # noqa: E402
