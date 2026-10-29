"""账号中心 / M-Team 账号管理路由。API Key 加密存储且永不回传。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..database import add_event, db, get_setting, set_setting
from ..security import current_user
from ..services.mteam import MTeamClient, MTeamError
from ..services.risk import predict, seeding_quality
from ..utils.crypto import decrypt, encrypt, mask

router = APIRouter(prefix="/api/accounts", tags=["accounts"], dependencies=[Depends(current_user)])


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    api_key: str = Field(min_length=8, max_length=200)
    api_url: str = "https://api.m-team.cc"


class AccountPatch(BaseModel):
    name: str | None = None
    api_key: str | None = None
    api_url: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None


@router.get("")
def list_accounts():
    with db() as conn:
        rows = conn.execute(
            "SELECT id,name,api_url,uid,username,is_default,enabled,uploaded,downloaded,"
            "share_rate,bonus,seeding_count,seeding_size,last_sync,last_error,created_at "
            "FROM accounts ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        with db() as conn:  # 脱敏的 key 指纹
            key_enc = conn.execute("SELECT api_key_enc FROM accounts WHERE id=?",
                                   (r["id"],)).fetchone()["api_key_enc"]
        d["api_key_masked"] = mask(decrypt(key_enc), 4)
        out.append(d)
    return {"items": out}


@router.post("", status_code=201)
async def create_account(body: AccountIn):
    # 先验证再入库
    client = MTeamClient(body.api_key, body.api_url)
    try:
        profile = await client.member_profile()
    except MTeamError as e:
        raise HTTPException(400, f"API 连接测试失败：{e}")
    finally:
        await client.close()
    with db() as conn:
        exists = conn.execute("SELECT COUNT(*) n FROM accounts").fetchone()["n"]
        cur = conn.execute(
            """INSERT INTO accounts (name, api_url, api_key_enc, uid, username,
               is_default, uploaded, downloaded, share_rate, bonus, last_sync)
               VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (body.name, body.api_url, encrypt(body.api_key), profile["uid"],
             profile["username"], 1 if exists == 0 else 0, profile["uploaded"],
             profile["downloaded"], profile["share_rate"], profile["bonus"]))
        new_id = cur.lastrowid
    add_event("info", "account", f"新增账号 {body.name}（{profile['username']}）")
    return {"id": new_id, "username": profile["username"]}


@router.patch("/{account_id}")
async def update_account(account_id: int, body: AccountPatch):
    with db() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE id=?", (account_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "账号不存在")
    sets, params = [], []
    if body.name is not None:
        sets.append("name=?"); params.append(body.name)
    if body.api_url is not None:
        sets.append("api_url=?"); params.append(body.api_url)
    if body.api_key:
        sets.append("api_key_enc=?"); params.append(encrypt(body.api_key))
    if body.enabled is not None:
        sets.append("enabled=?"); params.append(1 if body.enabled else 0)
    if body.is_default:
        with db() as conn:
            conn.execute("UPDATE accounts SET is_default=0")
        sets.append("is_default=1")
    if sets:
        params.append(account_id)
        with db() as conn:
            conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id=?", params)
    return {"ok": True}


@router.delete("/{account_id}")
def delete_account(account_id: int):
    with db() as conn:
        row = conn.execute("SELECT is_default FROM accounts WHERE id=?", (account_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "账号不存在")
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        if row["is_default"]:
            conn.execute(
                "UPDATE accounts SET is_default=1 WHERE id=(SELECT MIN(id) FROM accounts)")
    add_event("info", "account", f"删除账号 #{account_id}")
    return {"ok": True}


@router.post("/{account_id}/test")
async def test_account(account_id: int):
    with db() as conn:
        row = conn.execute("SELECT api_key_enc, api_url FROM accounts WHERE id=?",
                           (account_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "账号不存在")
    client = MTeamClient(decrypt(row["api_key_enc"]), row["api_url"])
    try:
        profile = await client.member_profile()
    except MTeamError as e:
        return {"ok": False, "error": str(e)}
    finally:
        await client.close()
    return {"ok": True, "username": profile["username"],
            "share_rate": profile["share_rate"], "bonus": profile["bonus"]}


@router.post("/{account_id}/sync")
async def sync_account(account_id: int):
    from ..services.scheduler import refresh_accounts
    with db() as conn:
        row = conn.execute("SELECT api_key_enc, api_url FROM accounts WHERE id=?",
                           (account_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "账号不存在")
    client = MTeamClient(decrypt(row["api_key_enc"]), row["api_url"])
    try:
        profile = await client.member_profile()
        with db() as conn:
            conn.execute(
                """UPDATE accounts SET uid=?, username=?, uploaded=?, downloaded=?,
                   share_rate=?, bonus=?, last_sync=datetime('now'), last_error=NULL
                   WHERE id=?""",
                (profile["uid"], profile["username"], profile["uploaded"],
                 profile["downloaded"], profile["share_rate"], profile["bonus"], account_id))
    except MTeamError as e:
        with db() as conn:
            conn.execute("UPDATE accounts SET last_error=? WHERE id=?", (str(e), account_id))
        return {"ok": False, "error": str(e)}
    finally:
        await client.close()
    return {"ok": True, **{k: profile[k] for k in
                           ("username", "uploaded", "downloaded", "share_rate", "bonus")}}


@router.get("/{account_id}/risk")
def account_risk(account_id: int, size: int = 0, discount: str = "NORMAL"):
    """账号风险概览 + 可选的新任务预测。"""
    quality = seeding_quality(account_id)
    out = {"seeding": quality}
    if size > 0:
        out["prediction"] = predict(account_id, size, discount)
    return out


class ProtectionIn(BaseModel):
    min_share_rate: float = Field(default=2.0, ge=0, le=100)


@router.get("/protection")
def get_protection():
    return get_setting("account_protection", {"min_share_rate": 2.0})


@router.put("/protection")
def set_protection(body: ProtectionIn):
    set_setting("account_protection", {"min_share_rate": body.min_share_rate})
    return {"ok": True}
