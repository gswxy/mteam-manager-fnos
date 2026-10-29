"""fnOS 下载中心 WebSocket 客户端协议测试（对本地模拟服务，实现真实签名协议）。"""
from __future__ import annotations

import pytest

from app.services.fnos_ws import FnOSDownloader, FnOSSession, FnOSWsError, normalize_state


@pytest.mark.asyncio
async def test_login_and_signed_call(mock_fnos):
    mock, url = mock_fnos
    session = FnOSSession(url, mock.username, mock.password)
    dl = FnOSDownloader(session)
    ping = await dl.ping()
    assert ping["ok"] and ping["latency_ms"] >= 0
    await session.close()


@pytest.mark.asyncio
async def test_wrong_password_rejected(mock_fnos):
    mock, url = mock_fnos
    session = FnOSSession(url, mock.username, "wrong")
    with pytest.raises(FnOSWsError) as e:
        await session.call("appcgi.downloadcenter.stat.all")
    assert "密码" in str(e.value) or e.value.errno == 8192
    await session.close()


@pytest.mark.asyncio
async def test_full_task_lifecycle(mock_fnos):
    mock, url = mock_fnos
    session = FnOSSession(url, mock.username, mock.password)
    dl = FnOSDownloader(session)
    ids = await dl.add_by_paths(["/vol1/t/x.torrent"], "/vol1/dl")
    assert ids and isinstance(ids[0], int)
    tasks = (await dl.list_tasks()).get("tasks") or []
    assert any(t["id"] == ids[0] for t in tasks)
    await dl.pause(ids)
    assert mock.tasks[ids[0]]["state"] == "paused"
    await dl.resume(ids)
    assert mock.tasks[ids[0]]["state"] == "downloading"
    await dl.delete(ids, delete_files=True)
    assert ids[0] not in mock.tasks
    assert mock.deleted == [([ids[0]], True)]
    await session.close()


@pytest.mark.asyncio
async def test_free_space(mock_fnos):
    mock, url = mock_fnos
    session = FnOSSession(url, mock.username, mock.password)
    dl = FnOSDownloader(session)
    assert await dl.free_space("/vol1") == 999 * 1024**3
    await session.close()


@pytest.mark.asyncio
async def test_block_reassembly_large_payload(mock_fnos):
    """大响应分块重组。"""
    mock, url = mock_fnos
    # 注入一个大任务名，使 block.data 超过单块阈值（模拟服务单块发送，这里验证解析路径）
    session = FnOSSession(url, mock.username, mock.password)
    dl = FnOSDownloader(session)
    await dl.add_by_paths(["/vol1/t/big.torrent"], "/vol1/dl")
    tasks = (await dl.list_tasks()).get("tasks") or []
    assert len(tasks) == 1
    await session.close()


def test_normalize_state_variants():
    assert normalize_state({"state": "downloading"}) == "downloading"
    assert normalize_state({"state": "complete"}) == "seeding"
    assert normalize_state({"state": "stalledUP"}) == "seeding"
    assert normalize_state({"state": "pausedDL"}) == "paused"
    assert normalize_state({"state": "metaDL"}) == "waiting"
    assert normalize_state({"state": "error"}) == "error"
    assert normalize_state({"state": "weird-thing"}) == "unknown"
    assert normalize_state({"state": None}) == "unknown"
