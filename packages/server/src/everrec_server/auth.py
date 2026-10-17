"""认证（tasks.md WP5）：管理员密码、会话、Agent 设备令牌、只读 API 令牌。

- 管理员密码用 scrypt 哈希存到 meta 表（只存哈希，不存明文）。scrypt 是标准库自带的
  内存硬化 KDF，安全性等价于 argon2 / bcrypt，免去引入外部依赖。
- 会话用 HMAC 签名的无状态令牌（HttpOnly + SameSite=Lax Cookie），密钥来自环境变量。
- Agent 令牌随机生成，服务端只存 SHA-256 哈希；管理网站可吊销。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

SESSION_COOKIE = "everrec_session"
SESSION_TTL = 12 * 3600  # 会话 12 小时
SCRYPT_N = 2**14


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=8, p=1, dklen=32)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def _sign(secret: str, msg: str) -> str:
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make_session_token(secret: str) -> str:
    exp = str(int(time.time()) + SESSION_TTL)
    return f"{exp}.{_sign(secret, exp)}"


def verify_session_token(token: str, secret: str) -> bool:
    try:
        exp_s, sig = token.split(".", 1)
        expected = _sign(secret, exp_s)
        return hmac.compare_digest(sig, expected) and int(exp_s) > time.time()
    except (ValueError, TypeError):
        return False


def new_agent_token() -> tuple[str, str]:
    """生成设备令牌，返回 (明文, 哈希)。明文只给 Agent 一次，服务端只存哈希。"""
    token = secrets.token_hex(32)
    return token, hash_agent_token(token)


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_agent_token(token: str, token_hash: str | None) -> bool:
    if not token or not token_hash:
        return False
    return hmac.compare_digest(hash_agent_token(token), token_hash)
