"""服务端密钥加密存储。

API Key / NAS 密码加密后落盘：密钥文件位于数据目录（权限 0600），
采用 SHA-256 CTR 型密钥流 + HMAC-SHA256 完整性校验（标准构造，无第三方依赖）。
目的：避免明文泄露到磁盘、日志或备份；不防御持有 root 权限的攻击者。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from ..config import settings

_KEY_CACHE: bytes | None = None


def _key_path() -> Path:
    return settings.data_dir / "secret.key"


def _load_key() -> bytes:
    global _KEY_CACHE
    if _KEY_CACHE is not None:
        return _KEY_CACHE
    path = _key_path()
    if path.exists():
        key = bytes.fromhex(path.read_text(encoding="utf-8").strip())
    else:
        key = secrets.token_bytes(32)
        path.write_text(key.hex(), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    _KEY_CACHE = key
    return key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    key = _load_key()
    nonce = secrets.token_bytes(16)
    data = plaintext.encode("utf-8")
    stream = _keystream(key, nonce, len(data))
    cipher = bytes(a ^ b for a, b in zip(data, stream))
    mac = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    return "v1:" + base64.urlsafe_b64encode(nonce + mac + cipher).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    if not token.startswith("v1:"):
        return token  # 兼容历史明文
    key = _load_key()
    raw = base64.urlsafe_b64decode(token[3:])
    nonce, mac, cipher = raw[:16], raw[16:48], raw[48:]
    expect = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expect):
        raise ValueError("密文校验失败（密钥文件可能被更换）")
    stream = _keystream(key, nonce, len(cipher))
    return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")


def mask(value: str, keep: int = 4) -> str:
    """日志/接口脱敏。"""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep * 2) + value[-keep:]
