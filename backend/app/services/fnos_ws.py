"""飞牛 fnOS 下载中心 WebSocket 客户端。

协议（2026-10 在 fnOS 最新版真机验证）：
- 端点 ws://127.0.0.1:5666/websocket（应用运行于 NAS 内）
- 登录 user.login：{reqid, req, user, password, deviceName, deviceType:"pc", stay:true, did:uuid}
  成功响应字段在顶层：uid / admin / token / longToken / secret / backId / machineId
- 业务请求需 HMAC-SHA256 签名：发送帧 = base64(hmac(key=b64decode(secret), msg=json)) + json
- 响应 {result:"succ|fail", errno, reqid, data}；业务数据可能位于
  data.block.data（JSON 字符串，>1MiB 分块，同 reqid 多帧按 offset 拼接）、data.rsp 或 data.body
- 登录失败 errno 8192；未认证业务调用 errno 65534

协议格式另见 trim-cli 参考文档（appcgi.downloadcenter.* 端点）。
本模块为独立实现，未复用任何第三方二进制。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any

import websockets

logger = logging.getLogger(__name__)

LOGIN_ERRNO = 8192
AUTH_REQUIRED = 65534

# 任务状态字段（task.query 返回）中的 state 为数字位掩码语义；
# 依据真机样本与下载中心 UI 枚举归纳，未知值一律归为 unknown，绝不臆断。
STATE_LABELS = {
    0: "waiting",
    1: "downloading",
    2: "paused",
    3: "error",
    4: "finished",
    5: "seeding",
    6: "removed",
    7: "checking",
    8: "stopped",
}


class FnOSWsError(Exception):
    def __init__(self, message: str, errno: int | None = None):
        super().__init__(message)
        self.errno = errno


class FnOSSession:
    """长连接会话：自动登录、签名、按 reqid 关联响应、断线重连。"""

    def __init__(self, url: str, username: str, password: str):
        self.url = url
        self.username = username
        self.password = password
        self._ws = None
        self._secret: bytes | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._blocks: dict[str, dict[int, str]] = {}
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self.last_error: str | None = None

    # ---------------- 生命周期 ----------------

    async def _ensure_connected(self) -> None:
        if self._closed:
            raise FnOSWsError("会话已关闭")
        if self._ws is not None and self._secret is not None:
            return
        async with self._lock:
            if self._ws is not None and self._secret is not None:
                return
            await self._connect_login()

    async def _connect_login(self) -> None:
        try:
            self._ws = await websockets.connect(
                # fnOS 网关不响应库层 ping（真机验证），必须禁用，否则约 20s 后被库判定超时断开
                self.url, open_timeout=10, max_size=None, ping_interval=None,
            )
        except Exception as e:
            self.last_error = f"无法连接下载中心（{self.url}）：{type(e).__name__}"
            raise FnOSWsError(self.last_error) from e
        login = {
            "reqid": uuid.uuid4().hex[:16],
            "req": "user.login",
            "user": self.username,
            "password": self.password,
            "deviceName": "M-Team-Manager",
            "deviceType": "pc",
            "stay": True,
            "did": uuid.uuid4().hex.upper(),
        }
        await self._ws.send(json.dumps(login))
        raw = await asyncio.wait_for(self._ws.recv(), 10)
        resp = json.loads(raw)
        if resp.get("result") != "succ":
            errno = resp.get("errno")
            self.last_error = "fnOS 账号或密码错误" if errno == LOGIN_ERRNO else f"登录失败 errno={errno}"
            await self._ws.close()
            self._ws = None
            raise FnOSWsError(self.last_error, errno)
        secret = resp.get("secret")
        if not secret:
            raise FnOSWsError("登录响应缺少 secret，接口不兼容")
        try:
            self._secret = base64.b64decode(secret)
        except Exception as e:
            raise FnOSWsError("secret 解码失败，接口不兼容") from e
        self.last_error = None
        if self._reader is None or self._reader.done():
            self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        """分发响应帧；连接断开时让所有 pending 失败。"""
        try:
            assert self._ws is not None
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                reqid = msg.get("reqid")
                if not reqid:
                    continue
                fut = self._pending.get(reqid)
                if fut is None or fut.done():
                    continue
                data = msg.get("data")
                if isinstance(data, dict) and isinstance(data.get("block"), dict):
                    blk = data["block"]
                    if "data" in blk:
                        parts = self._blocks.setdefault(reqid, {})
                        parts[int(blk.get("offset") or 0)] = blk["data"]
                        total = int(blk.get("total_size") or 0)
                        if total and sum(len(p) for p in parts.values()) >= total:
                            payload = "".join(parts[k] for k in sorted(parts))
                            self._blocks.pop(reqid, None)
                            fut.set_result({"result": msg.get("result"), "payload": payload})
                        continue
                fut.set_result({"result": msg.get("result"), "payload": data, "msg": msg})
        except Exception as e:
            err = FnOSWsError(f"下载中心连接断开：{type(e).__name__}")
            self.last_error = err.args[0]
        finally:
            self._secret = None
            self._ws = None
            for reqid, fut in list(self._pending.items()):
                if not fut.done():
                    fut.set_exception(FnOSWsError("连接已断开"))
            self._pending.clear()
            self._blocks.clear()

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._reader is not None:
            self._reader.cancel()

    # ---------------- 调用 ----------------

    async def call(self, req: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        await self._ensure_connected()
        assert self._ws is not None and self._secret is not None
        reqid = uuid.uuid4().hex[:16]
        msg: dict[str, Any] = {"reqid": reqid, "req": req}
        msg.update(params or {})
        body = json.dumps(msg, ensure_ascii=False)
        sig = base64.b64encode(
            hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).digest()
        ).decode()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[reqid] = fut
        try:
            await self._ws.send(sig + body)
            result = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise FnOSWsError(f"下载中心请求超时：{req}", errno=-1)
        finally:
            self._pending.pop(reqid, None)
            self._blocks.pop(reqid, None)

        if result.get("result") != "succ":
            msg_full = result.get("msg") or {}
            errno = msg_full.get("errno")
            raise FnOSWsError(f"下载中心返回失败 errno={errno} req={req}", errno=errno)
        payload = result.get("payload")
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except (TypeError, ValueError):
                return payload
        if isinstance(payload, dict):
            for key in ("rsp", "body"):
                if key in payload and payload.get(key) is not None:
                    return payload[key]
        return payload


# ---------------- 下载中心高层接口 ----------------

class FnOSDownloader:
    """飞牛下载中心适配层：全部异常向上透出，由调用方决定降级行为。"""

    def __init__(self, session: FnOSSession):
        self.session = session

    async def _call(self, req: str, params: dict | None = None, timeout: float = 30.0):
        return await self.session.call(req, params, timeout)

    async def ping(self) -> dict:
        """连通性与版本检测。"""
        started = time.monotonic()
        stat = await self._call("appcgi.downloadcenter.stat.all", timeout=10)
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "stat": stat,
        }

    async def list_tasks(self, init_flag: bool = False) -> dict:
        # 真机验证：init_flag=True 仅在连接后首次同步时使用（可能耗时 40s+），
        # 之后一律 False（瞬时返回）；高频调用会触发服务端短时冷却（约 1-3 分钟），
        # 因此本应用以低频轮询 + 长连接方式访问。
        data = await self._call("appcgi.downloadcenter.task.query",
                                {"init_flag": init_flag, "state_filter": 65535},
                                timeout=70 if init_flag else 30)
        return data or {}

    async def task_info(self, task_id: int) -> dict:
        data = await self._call("appcgi.downloadcenter.task.getInfo", {"id": int(task_id)})
        return data or {}

    async def task_files(self, task_id: int) -> list:
        data = await self._call("appcgi.downloadcenter.task.getFiles",
                                {"id": int(task_id), "mode": 1})
        if isinstance(data, list):
            return data
        return (data or {}).get("files") or []

    async def add_by_paths(self, torrent_paths: list[str], save_dir: str) -> list[int]:
        data = await self._call("appcgi.downloadcenter.task.addPaths",
                                {"paths": torrent_paths, "save_dir": save_dir}, timeout=60)
        ids = (data or {}).get("ids") or []
        errs = (data or {}).get("errs") or []
        if errs and not ids:
            raise FnOSWsError(f"添加任务失败：{errs}")
        return [int(i) for i in ids]

    async def add_by_uris(self, uris: list[str], save_dir: str) -> list[int]:
        data = await self._call("appcgi.downloadcenter.task.addUris",
                                {"uris": uris, "save_dir": save_dir}, timeout=60)
        ids = (data or {}).get("ids") or []
        errs = (data or {}).get("errs") or []
        if errs and not ids:
            raise FnOSWsError(f"添加任务失败：{errs}")
        return [int(i) for i in ids]

    async def pause(self, task_ids: list[int]) -> None:
        await self._call("appcgi.downloadcenter.task.pause", {"ids": [int(i) for i in task_ids]})

    async def resume(self, task_ids: list[int]) -> None:
        await self._call("appcgi.downloadcenter.task.resume", {"ids": [int(i) for i in task_ids]})

    async def delete(self, task_ids: list[int], delete_files: bool = False) -> None:
        """删除任务；是否同时删除数据由调用方明确决定（自动清理=true，仅移除=false）。"""
        await self._call("appcgi.downloadcenter.task.delete",
                         {"ids": [int(i) for i in task_ids],
                          "delete_files": bool(delete_files)}, timeout=60)

    async def free_space(self, dir_path: str) -> int | None:
        try:
            # 真机验证：直接返回剩余字节数（整数）
            data = await self._call("appcgi.downloadcenter.util.getDirFreeSpace",
                                    {"dir": dir_path}, timeout=15)
            if isinstance(data, dict):
                for key in ("free", "free_size", "freeSpace", "size"):
                    if key in data:
                        return int(data[key])
            if isinstance(data, (int, float)):
                return int(data)
        except FnOSWsError:
            return None
        return None

    async def default_save_dir(self) -> str | None:
        try:
            data = await self._call("appcgi.downloadcenter.config.getDefaultSaveDir", timeout=10)
            if isinstance(data, str):
                return data
            if isinstance(data, dict):
                for key in ("dir", "save_dir", "path", "defaultSaveDir"):
                    if key in data:
                        return data[key]
        except FnOSWsError:
            return None
        return None


def normalize_state(task: dict) -> str:
    """下载中心任务 state -> 应用内状态。

    真机样本中 state 为数字；不同引擎（aria2/qbittorrent）字段可能不同。
    未知值返回 unknown，由上层结合进度等字段推断，绝不臆断为已完成。
    """
    raw = task.get("state")
    if isinstance(raw, str):
        low = raw.lower()
        mapping = {
            "downloading": "downloading", "active": "downloading",
            "paused": "paused", "pauseddl": "paused", "stopped": "paused",
            "waiting": "waiting", "queueddl": "waiting", "stalleddl": "waiting",
            "metadl": "waiting", "allocating": "waiting",
            "error": "error", "fail": "error",
            "finished": "seeding", "completed": "seeding", "complete": "seeding",
            "uploading": "seeding", "seeding": "seeding",
            "stoppedup": "seeding", "forcedup": "seeding", "stalledup": "seeding",
            "checkingdl": "checking", "checkingresumedata": "checking", "moving": "checking",
        }
        return mapping.get(low, "unknown")
    if isinstance(raw, bool):
        return "finished" if raw else "downloading"
    if isinstance(raw, (int, float)):
        return STATE_LABELS.get(int(raw), "unknown")
    return "unknown"
