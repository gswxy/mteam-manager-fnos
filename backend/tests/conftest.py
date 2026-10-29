"""测试夹具：隔离数据目录 + 模拟 M-Team HTTP 服务 + 模拟 fnOS WebSocket 下载中心。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 隔离数据目录（必须在导入 app 前 设置）
TEST_DATA = os.path.join(os.path.dirname(__file__), ".test-data")
os.environ["MTM_DATA_DIR"] = TEST_DATA
os.environ["MTM_DEV"] = "1"

from app import database  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """每个测试用独立 SQLite。"""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(database.settings, "db_path", db_path)
    monkeypatch.setattr(database.settings, "data_dir", tmp_path)
    monkeypatch.setattr(database.settings, "torrent_dir", tmp_path / "torrents")
    os.makedirs(tmp_path / "torrents", exist_ok=True)
    # 重置密钥缓存
    import app.utils.crypto as crypto
    monkeypatch.setattr(crypto, "_KEY_CACHE", None)
    # 重置跨测试的单例状态（M-Team 客户端缓存属于不同事件循环）
    from app.services.download_manager import manager
    manager._mt_clients.clear()
    database.init_db()
    yield


# ---------------- 模拟 M-Team HTTP 服务 ----------------

MOCK_TORRENTS = [
    {
        "id": "1001", "name": "The.Movie.2023.2160p.WEB-DL.H265.DTS-HD",
        "smallDescr": "测试电影A 2023", "category": "401", "size": 20 * 1024**3,
        "status": {"seeders": 58, "leechers": 12, "timesCompleted": 300,
                   "discount": "FREE", "discountEndTime": None},
        "createdDate": "2026-10-20 10:00:00",
        "douban": "3521234", "doubanRating": 8.4, "imdb": "tt1234567", "imdbRating": 7.9,
        "imageList": ["https://img.example/p1.jpg"], "labelsNew": [],
    },
    {
        "id": "1002", "name": "The.Movie.2023.1080p.BluRay.x264",
        "smallDescr": "测试电影A 2023", "category": "401", "size": 8 * 1024**3,
        "status": {"seeders": 200, "leechers": 5, "timesCompleted": 900,
                   "discount": "NORMAL", "discountEndTime": None},
        "createdDate": "2026-10-18 09:00:00",
        "douban": "3521234", "doubanRating": 8.4, "imdb": "tt1234567", "imdbRating": 7.9,
        "imageList": [], "labelsNew": [],
    },
    {
        "id": "1003", "name": "Show.Series.S02.2160p.COMPLETE",
        "smallDescr": "测试剧集B 第二季", "category": "402", "size": 60 * 1024**3,
        "status": {"seeders": 10, "leechers": 40, "timesCompleted": 50,
                   "discount": "_2X_FREE", "discountEndTime": int(time.time() * 1000) + 3_600_000},
        "createdDate": 1793000000000,
        "douban": None, "doubanRating": None, "imdb": "tt7654321", "imdbRating": 8.0,
        "imageList": [], "labelsNew": ["HR"],
    },
    {
        "id": "1004", "name": "Anime.Thing.2024.1080p.BDRip",
        "smallDescr": "测试动漫C", "category": "403", "size": 5 * 1024**3,
        "status": {"seeders": 3, "leechers": 2, "timesCompleted": 10, "discount": "NORMAL"},
        "createdDate": "2026-10-25 08:00:00",
        "douban": None, "imdb": None, "imageList": [], "labelsNew": [],
    },
]


class MockMTeamHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get("x-api-key") != "test-api-key":
            return self._send({"code": "403", "message": "KEY_WRONG", "data": None})
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        path = self.path
        if path == "/api/member/profile":
            return self._send({"code": "0", "message": "SUCCESS", "data": {
                "id": "999", "username": "tester", "email": "t@t.cc",
                "memberCount": {"uploaded": 10**12, "downloaded": 10**11,
                                "shareRate": 10.0, "bonus": 5000},
                "memberStatus": {"vip": False, "warned": False, "leechWarn": False},
            }})
        if path == "/api/torrent/search":
            req = json.loads(raw or "{}")
            keyword = req.get("keyword") or ""
            items = [t for t in MOCK_TORRENTS
                     if not keyword or keyword.lower() in t["name"].lower()
                     or keyword in (t.get("smallDescr") or "")]
            return self._send({"code": "0", "message": "SUCCESS",
                               "data": {"total": len(items), "data": items[:req.get("pageSize", 100)]}})
        if path == "/api/torrent/detail":
            tid = raw.split("id=")[-1] if "id=" in raw else ""
            item = next((t for t in MOCK_TORRENTS if t["id"] == tid), None)
            if not item:
                return self._send({"code": "404", "message": "NOT_FOUND", "data": None})
            data = dict(item)
            data["descr"] = "<b>简介</b>测试详情"
            data["mediainfo"] = "General\nName: test.mkv"
            return self._send({"code": "0", "message": "SUCCESS", "data": data})
        if path == "/api/torrent/genDlToken":
            tid = raw.split("id=")[-1] if "id=" in raw else ""
            host, port = self.server.server_address[:2]
            return self._send({"code": "0", "message": "SUCCESS",
                               "data": f"http://{host}:{port}/dl/{tid}.torrent"})
        if path == "/api/tracker/queryHistory":
            req = json.loads(raw or "{}")
            tids = req.get("tids") or []
            return self._send({"code": "0", "message": "SUCCESS",
                               "data": {"historyMap": {t: (t == "1002") for t in tids}}})
        if path == "/api/torrent/categoryList":
            return self._send({"code": "0", "message": "SUCCESS",
                               "data": [{"id": "401", "name": "电影"}, {"id": "402", "name": "剧集"}]})
        if path == "/api/personal/seedWatch":
            return self._send({"code": "0", "message": "SUCCESS",
                               "data": {"count": 6, "size": 12345678}})
        return self._send({"code": "404", "message": "UNKNOWN", "data": None})

    def do_GET(self):
        if self.path.startswith("/dl/"):
            # 合法 bencode 种子
            body = b"d4:infod4:name8:test.dat6:lengthi1024eee"
            self.send_response(200)
            self.send_header("Content-Type", "application/x-bittorrent")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="session")
def mock_mteam():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockMTeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


# ---------------- 模拟 fnOS WebSocket 下载中心 ----------------

class MockFnOSDownloadCenter:
    """实现真实协议的本地下载中心：user.login + HMAC 签名 + block 分块。"""

    def __init__(self):
        self.tasks: dict[int, dict] = {}
        self._seq = 0
        self.password = "nas-pass"
        self.username = "nas-user"
        self.deleted: list[tuple[list[int], bool]] = []

    async def handler(self, ws):
        secret_b64 = None
        try:
            async for raw in ws:
                # 登录帧是纯 JSON；业务帧是 <base64签名><json>（base64 不含 '{'）
                idx = raw.find("{")
                try:
                    msg = json.loads(raw) if idx == 0 else json.loads(raw[idx:])
                except (TypeError, ValueError):
                    continue
                if idx > 0:
                    sig, body = raw[:idx], raw[idx:]
                    if secret_b64 is None:
                        await ws.send(json.dumps({"reqid": msg.get("reqid"), "result": "fail",
                                                  "errno": 65534}))
                        continue
                    expect = base64.b64encode(
                        hmac.new(base64.b64decode(secret_b64), body.encode(),
                                 hashlib.sha256).digest()).decode()
                    if not hmac.compare_digest(sig, expect):
                        await ws.send(json.dumps({"reqid": msg.get("reqid"), "result": "fail",
                                                  "errno": 65535}))
                        continue
                if msg.get("req") == "user.login":
                    if msg.get("user") == self.username and msg.get("password") == self.password:
                        secret_b64 = base64.b64encode(secrets.token_bytes(32)).decode()
                        await ws.send(json.dumps({
                            "reqid": msg["reqid"], "result": "succ",
                            "uid": 1000, "admin": True, "token": "tk", "longToken": "lt",
                            "secret": secret_b64, "backId": "bk", "machineId": "m1",
                        }))
                    else:
                        await ws.send(json.dumps({"reqid": msg["reqid"], "result": "fail",
                                                  "errno": 8192}))
                    continue
                # 已登录但走未签名路径（不可能到达：上面 idx==0 时 secret 未检查）
                if secret_b64 is None:
                    await ws.send(json.dumps({"reqid": msg.get("reqid"), "result": "fail",
                                              "errno": 65534}))
                    continue
                await self._route(ws, msg)
        except Exception:
            pass

    async def _route(self, ws, msg):
        req, reqid = msg["req"], msg["reqid"]
        if req == "appcgi.downloadcenter.task.addPaths":
            paths = msg.get("paths") or []
            ids = []
            for p in paths:
                self._seq += 1
                tid = self._seq
                self.tasks[tid] = {"id": tid, "name": f"task-{tid}",
                                   "type": "qbittorrent", "state": "downloading",
                                   "total_size": 1024, "size": 0, "dl_speed": 100,
                                   "up_speed": 0, "save_dir": msg.get("save_dir"),
                                   "dl_total_size": 0, "up_total_size": 0, "addition_time": int(time.time())}
                ids.append(tid)
            return await self._ok(ws, reqid, {"ids": ids, "errs": [0] * len(ids)})
        if req == "appcgi.downloadcenter.task.query":
            tasks = list(self.tasks.values())
            if msg.get("ids"):
                ids = set(msg["ids"])
                tasks = [t for t in tasks if t["id"] in ids]
            return await self._ok_block(ws, reqid, {"tasks": tasks,
                                                    "stats": {"all": len(tasks)}})
        if req == "appcgi.downloadcenter.task.pause":
            for i in msg["ids"]:
                if i in self.tasks:
                    self.tasks[i]["state"] = "paused"
            return await self._ok(ws, reqid, "ok")
        if req == "appcgi.downloadcenter.task.resume":
            for i in msg["ids"]:
                if i in self.tasks:
                    self.tasks[i]["state"] = "downloading"
            return await self._ok(ws, reqid, "ok")
        if req == "appcgi.downloadcenter.task.delete":
            ids = msg.get("ids") or []
            self.deleted.append((ids, bool(msg.get("delete_files"))))
            for i in ids:
                self.tasks.pop(i, None)
            return await self._ok(ws, reqid, "ok")
        if req == "appcgi.downloadcenter.stat.all":
            return await self._ok(ws, reqid, {"dl_speed": 0, "up_speed": 0,
                                              "dl_total_size": 0, "up_total_size": 0})
        if req == "appcgi.downloadcenter.util.getDirFreeSpace":
            return await self._ok(ws, reqid, 999 * 1024**3)
        await self._ok(ws, reqid, {})

    async def _ok(self, ws, reqid, data):
        await ws.send(json.dumps({"reqid": reqid, "result": "succ", "data": data}))

    async def _ok_block(self, ws, reqid, data):
        payload = json.dumps(data, ensure_ascii=False)
        await ws.send(json.dumps({"reqid": reqid, "result": "succ", "data": {
            "block": {"id": "b1", "total_size": len(payload.encode()), "offset": 0,
                      "data": payload}}}))

    # 让部分任务进入完成态（测试同步）
    def complete(self, tid: int, ratio: float = 1.5):
        t = self.tasks[tid]
        t.update(state="stalledUP", size=t["total_size"], dl_total_size=t["total_size"],
                 up_total_size=int(t["total_size"] * ratio))


@pytest.fixture
def mock_fnos():
    """线程化模拟下载中心：专属事件循环持续服务，供同步/异步测试共用。"""
    import threading
    import websockets

    mock = MockFnOSDownloadCenter()
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    state: dict = {}

    async def _serve():
        async with websockets.serve(mock.handler, "127.0.0.1", 0,
                                    max_size=None, ping_interval=None) as server:
            state["port"] = server.sockets[0].getsockname()[1]
            ready.set()
            await state["stop_event"].wait()

    async def _make_stop_event():
        state["stop_event"] = asyncio.Event()

    loop.run_until_complete(_make_stop_event())

    def _run():
        loop.run_until_complete(_serve())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert ready.wait(5)
    yield mock, f"ws://127.0.0.1:{state['port']}/websocket"
    loop.call_soon_threadsafe(state["stop_event"].set)
    thread.join(timeout=8)


# ---------------- FastAPI 测试客户端 ----------------

@pytest.fixture
def api_client(mock_mteam):
    from fastapi.testclient import TestClient
    from app.main import app
    import app.services.download_manager as dm

    # M-Team 客户端默认指向模拟服务
    from app.services.mteam import MTeamClient, RateLimiter
    original_init = MTeamClient.__init__

    def patched_init(self, api_key, api_url="https://api.m-team.cc", limiter=None, timeout=25.0):
        original_init(self, api_key, mock_mteam, limiter or RateLimiter(min_interval=0.01),
                      timeout=timeout)

    MTeamClient.__init__ = patched_init
    try:
        with TestClient(app) as client:
            yield client
    finally:
        MTeamClient.__init__ = original_init
