"""鉴权与密码学工具：口令哈希 + JWT 签发/校验。

为什么手写 JWT：MVP 阶段不想为了一个 HS256 引入 python-jose/passlib 两个依赖，
标准库 hmac + hashlib 就足够，且没有 C 扩展，Windows 上装包更省心。
生产如果要多算法/刷新令牌/黑名单，再换 PyJWT 即可，接口保持不变。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from .config import settings

# ---------------------------------------------------------------- 口令哈希

_PBKDF2_ROUNDS = 120_000


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 加盐哈希，返回 `pbkdf2$rounds$salt$hash` 形式字符串。"""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return "pbkdf2${}${}${}".format(
        _PBKDF2_ROUNDS, salt.hex(), digest.hex()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds_s)
        )
        # 常数时间比较，避免计时攻击
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------- JWT (HS256)

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_access_token(payload: dict[str, Any], ttl_hours: int | None = None) -> str:
    """签发 HS256 JWT。payload 里必须含 sub（用户 id）与 role。"""
    ttl = (ttl_hours if ttl_hours is not None else settings.jwt_ttl_hours) * 3600
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    body = {**payload, "iat": now, "exp": now + ttl}
    seg_header = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    seg_body = _b64url_encode(json.dumps(body, separators=(",", ":")).encode())
    signing_input = f"{seg_header}.{seg_body}".encode("ascii")
    signature = hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{seg_header}.{seg_body}.{_b64url_encode(signature)}"


class TokenError(Exception):
    """令牌无效/过期。"""


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        seg_header, seg_body, seg_sig = token.split(".")
    except ValueError as exc:
        raise TokenError("令牌格式错误") from exc

    signing_input = f"{seg_header}.{seg_body}".encode("ascii")
    expected = hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest()
    try:
        actual = _b64url_decode(seg_sig)
    except Exception as exc:  # noqa: BLE001
        raise TokenError("令牌签名解码失败") from exc
    if not hmac.compare_digest(expected, actual):
        raise TokenError("令牌签名校验失败")

    try:
        payload = json.loads(_b64url_decode(seg_body))
    except Exception as exc:  # noqa: BLE001
        raise TokenError("令牌内容解析失败") from exc

    if int(payload.get("exp", 0)) < int(time.time()):
        raise TokenError("令牌已过期，请重新登录")
    return payload
