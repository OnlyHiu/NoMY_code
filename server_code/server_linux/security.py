# -*- coding: utf-8 -*-
"""服务端安全防护

- RateLimiter：滑动窗口限流（按 IP + 通道），防暴力刷接口
- 邀请码注册：server_config.json 的 register_invite_code 非空时启用
- 图片魔数校验：拦截伪装成图片的恶意文件
- protect_secret/unprotect_secret：敏感配置（SMTP 授权码等）落盘加密，
  基于 HMAC-SHA256 计数器模式流密码 + HMAC 认证标签（encrypt-then-MAC），
  密钥存于 server_data/secret.key（0600），支持旧版明文兼容读取
"""
import base64
import hashlib
import hmac
import os
import secrets as _secrets
import sys
import threading
import time

GLOBAL_LIMIT = 120      # 每个 IP 每分钟普通请求数
AUTH_LIMIT = 20         # 每个 IP 每分钟认证类请求数
WINDOW = 60

MAGIC = (
    (b"\x89PNG", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),   # 需再校验 8..12 字节为 WEBP
    (b"BM", "bmp"),
)


def sniff_image_ext(data: bytes) -> str | None:
    """按魔数判断图片类型；非图片返回 None。"""
    for magic, ext in MAGIC:
        if data.startswith(magic):
            if magic == b"RIFF":
                if len(data) >= 12 and data[8:12] == b"WEBP":
                    return "webp"
                return None
            return ext
    return None


class RateLimiter:
    """滑动窗口限流器（内存态）。"""

    def __init__(self):
        self._hits: dict = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: int = WINDOW) -> bool:
        now = time.time()
        with self._lock:
            recent = [t for t in self._hits.get(key, []) if now - t < window]
            if len(recent) >= limit:
                self._hits[key] = recent
                return False
            recent.append(now)
            self._hits[key] = recent
            # 防内存膨胀
            if len(self._hits) > 4096:
                cutoff = now - window
                self._hits = {k: [t for t in v if t > cutoff]
                              for k, v in self._hits.items() if v}
        return True


SECURITY = RateLimiter()


# ── 敏感配置落盘加密（M8：SMTP 授权码不再明文存储） ─────────
def _data_root() -> str:
    """打包 exe 时 __file__ 在临时解包目录，密钥需持久化到 exe 所在目录。"""
    if getattr(sys, "frozen", False) or globals().get("__compiled__"):
        for p in (sys.argv[0], sys.executable):
            if p:
                return os.path.dirname(os.path.abspath(p))
    return os.path.dirname(os.path.abspath(__file__))


_KEY_FILE = os.path.join(_data_root(), "server_data", "secret.key")
_KEY_CACHE: bytes | None = None
_KEY_LOCK = threading.Lock()


def _load_key() -> bytes:
    """读取/生成本地 32 字节密钥（文件权限 0600）。"""
    global _KEY_CACHE
    with _KEY_LOCK:
        if _KEY_CACHE:
            return _KEY_CACHE
        try:
            with open(_KEY_FILE, "rb") as f:
                key = f.read()
        except OSError:
            key = _secrets.token_bytes(32)
            os.makedirs(os.path.dirname(_KEY_FILE), exist_ok=True)
            fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
        if len(key) < 32:
            key = hashlib.sha256(key).digest()
        _KEY_CACHE = key[:32]
        return _KEY_CACHE


def _keystream(key: bytes, n: int) -> bytes:
    """HMAC-SHA256 计数器模式生成密钥流。"""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:n])


def protect_secret(plain: str) -> str:
    """加密敏感字符串。返回 enc:v1:... 形式（含认证标签，防篡改）。"""
    if not plain:
        return ""
    key = _load_key()
    data = plain.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(data, _keystream(key, len(data))))
    tag = hmac.new(key, ct, hashlib.sha256).digest()
    return "enc:v1:" + base64.b64encode(tag + ct).decode("ascii")


def unprotect_secret(stored: str) -> str:
    """解密 protect_secret 的输出；旧版明文原样返回（兼容迁移）。"""
    if not stored:
        return ""
    if not stored.startswith("enc:v1:"):
        return stored
    raw = base64.b64decode(stored[7:])
    tag, ct = raw[:32], raw[32:]
    key = _load_key()
    if not hmac.compare_digest(tag, hmac.new(key, ct, hashlib.sha256).digest()):
        raise ValueError("secret auth tag mismatch (key changed or data tampered)")
    pt = bytes(a ^ b for a, b in zip(ct, _keystream(key, len(ct))))
    return pt.decode("utf-8")
