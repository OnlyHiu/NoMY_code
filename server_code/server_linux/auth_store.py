# -*- coding: utf-8 -*-
"""账号认证存储（服务管理后台）

纯标准库实现，供 distribute_server（HTTP 路由）与 distribute_server_gui（管理界面）共用。

- UserStore    账号存储：PBKDF2-HMAC-SHA256 加盐哈希，绝不存明文密码
- SessionStore 会话存储：随机令牌 + 7 天滑动过期，持久化到磁盘（服务重启不掉线）
- LoginGuard   简单防爆破：同 IP 5 分钟窗口内登录失败达上限则临时拒绝

数据文件位于 server/server_data/ 下：users.json / sessions.json / server_config.json
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import threading
import time

from mail_service import validate_email, gen_code
from security import protect_secret, unprotect_secret

logger = logging.getLogger("auth_store")


def _data_root() -> str:
    """打包 exe（Nuitka onefile）时 __file__ 在临时解包目录，
    账号/会话数据需要持久化，改用 exe 所在目录。"""
    if getattr(sys, "frozen", False) or globals().get("__compiled__"):
        for p in (sys.argv[0], sys.executable):
            if p:
                return os.path.dirname(os.path.abspath(p))
    return os.path.dirname(os.path.abspath(__file__))


DATA_DIR = os.path.join(_data_root(), "server_data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
CONFIG_FILE = os.path.join(DATA_DIR, "server_config.json")

PBKDF2_ITERATIONS = 120_000
SESSION_TTL = 7 * 24 * 3600          # 令牌有效期：7 天（滑动）
SESSION_ABSOLUTE_TTL = 30 * 24 * 3600  # 令牌绝对寿命上限：30 天（滑动续期不可超越）
SESSION_PERSIST_INTERVAL = 60        # 滑动续期写盘节流：最多每 60 秒落盘一次
GUARD_WINDOW = 300                    # 防爆破统计窗口：5 分钟
GUARD_MAX_FAILS = 10                  # 窗口内最大失败次数

USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fa5]{2,32}$", re.UNICODE)

# server_config.json 读-改-写互斥（L5：防并发丢更新）
_CONFIG_LOCK = threading.Lock()


def _now() -> int:
    return int(time.time())


def _hash_token(token: str) -> str:
    """会话令牌的 SHA-256 摘要，用作 sessions.json 的键（不落明文）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ts(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    ).hex()


def validate_username(username: str) -> str | None:
    """返回错误信息，None 表示合法。"""
    if not username or not USERNAME_RE.match(username):
        return "用户名须为 2-32 位字母/数字/下划线/中文"
    return None


def validate_password(password: str) -> str | None:
    """密码策略（L2）：≥8 位，且至少包含字母/数字/符号中的两类。"""
    if not password or len(password) < 8:
        return "密码长度至少 8 位"
    if len(password) > 64:
        return "密码长度不能超过 64 位"
    classes = sum([
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"\d", password)),
        bool(re.search(r"[^\w]", password)),
    ])
    if classes < 2:
        return "密码至少包含字母、数字、符号中的两类"
    return None


class UserStore:
    """账号存储与校验（SQLite 后端，首次启动自动迁移 users.json）。

    用户带 8 位 ID（00000001 起）与唯一绑定邮箱。
    """

    def __init__(self, users_file: str = USERS_FILE, db=None):
        self._legacy_file = users_file
        self._db = db if db is not None else __import__("db").DB
        self._create_lock = threading.Lock()   # L4：ID 分配 + 插入原子化
        self._migrate_legacy_json()
        self.allow_registration = self._load_registration_flag()

    # ── 服务器配置（注册开关 / SMTP） ───────────────────
    @staticmethod
    def _read_server_config() -> dict:
        return _load_json(CONFIG_FILE, {})

    @staticmethod
    def _write_server_config(patch: dict):
        with _CONFIG_LOCK:
            cfg = _load_json(CONFIG_FILE, {})
            cfg.update(patch)
            _save_json(CONFIG_FILE, cfg)

    def _load_registration_flag(self) -> bool:
        return bool(self._read_server_config().get("allow_registration", True))

    def set_allow_registration(self, enabled: bool) -> None:
        self.allow_registration = bool(enabled)
        self._write_server_config({"allow_registration": self.allow_registration})
        logger.info("开放注册开关: %s", self.allow_registration)

    def get_register_invite_code(self) -> str:
        return str(self._read_server_config().get("register_invite_code", "") or "")

    def set_register_invite_code(self, code: str) -> None:
        self._write_server_config({"register_invite_code": (code or "").strip()})
        logger.info("注册邀请码已更新: %s", "已设置" if code else "已清空")

    def get_smtp_config(self) -> dict:
        cfg = self._read_server_config().get("smtp", {})
        # M8：授权码落盘加密，读取时解密；环境变量 NOMY_SMTP_PASSWORD 优先
        password = ""
        try:
            password = unprotect_secret(cfg.get("password", ""))
        except Exception as e:
            logger.error("SMTP 授权码解密失败（密钥文件被更换？）: %s", e)
        password = os.environ.get("NOMY_SMTP_PASSWORD") or password
        return {
            "host": cfg.get("host", ""),
            "port": cfg.get("port", 465),
            "ssl": bool(cfg.get("ssl", True)),
            "user": cfg.get("user", ""),
            "password": password,
            "sender_name": cfg.get("sender_name", "NoMY 服务管理后台"),
        }

    def set_smtp_config(self, smtp: dict) -> None:
        password = smtp.get("password", "") or ""
        self._write_server_config({"smtp": {
            "host": smtp.get("host", ""),
            "port": int(smtp.get("port") or 465),
            "ssl": bool(smtp.get("ssl", True)),
            "user": smtp.get("user", ""),
            "password": protect_secret(password) if password else "",
            "sender_name": smtp.get("sender_name", ""),
        }})
        logger.info("SMTP 配置已更新")

    def _migrate_legacy_json(self):
        """users.json（旧版 JSON 存储）一次性导入 SQLite。"""
        legacy = _load_json(self._legacy_file, None)
        if not legacy or not isinstance(legacy, dict):
            return
        imported = 0
        for name, u in legacy.items():
            if not isinstance(u, dict):
                continue
            uid = u.get("id") or self._db.next_user_id()
            ok = self._db.insert_user(
                uid, name, u.get("email", ""), u.get("salt", ""),
                u.get("password_hash", ""), u.get("created_at") or _now())
            if ok:
                self._db.update_user(uid, last_login_at=u.get("last_login_at") or 0,
                                     last_login_ip=u.get("last_login_ip") or "",
                                     disabled=1 if u.get("disabled") else 0)
                imported += 1
        if imported:
            logger.info("已从 users.json 迁移 %d 个账号到 SQLite", imported)
        try:
            os.replace(self._legacy_file, self._legacy_file + ".migrated")
        except OSError:
            pass

    # ── 账号管理 ────────────────────────────────────────
    def create_user(self, username: str, password: str, email: str = "") -> tuple[bool, str]:
        err = validate_username(username) or validate_password(password)
        if err:
            return False, err
        email = (email or "").strip().lower()
        if email:
            err = validate_email(email)
            if err:
                return False, err
        if self._db.get_user_by_username(username):
            return False, "用户名已存在"
        if email and self._db.get_user_by_email(email):
            # M4：中性文案，不确认邮箱是否已注册（防邮箱枚举）
            return False, "该邮箱不可用，请更换"
        with self._create_lock:
            uid = self._db.next_user_id()
            salt = secrets.token_bytes(16)
            ok = self._db.insert_user(uid, username, email, salt.hex(),
                                      hash_password(password, salt), _now())
        if not ok:
            return False, "用户名或邮箱已被占用"
        logger.info("创建账号: %s (id=%s)", username, uid)
        return True, ""

    def verify_user(self, username: str, password: str) -> tuple[bool, str]:
        user = self._db.get_user_by_username(username)
        if not user:
            # 与"密码错误"返回一致，避免账号枚举
            hash_password(password, secrets.token_bytes(16))
            return False, "用户名或密码错误"
        if user.get("disabled"):
            return False, "账号已被禁用"
        salt = bytes.fromhex(user["salt"])
        # M3：常数时间比较，防计时侧信道
        if not hmac.compare_digest(hash_password(password, salt), user["password_hash"]):
            return False, "用户名或密码错误"
        return True, ""

    def delete_user(self, username: str) -> tuple[bool, str]:
        user = self._db.get_user_by_username(username)
        if not user:
            return False, "用户不存在"
        self._db.delete_user(user["id"])
        logger.info("删除账号: %s", username)
        return True, ""

    def reset_password(self, username: str, new_password: str) -> tuple[bool, str]:
        err = validate_password(new_password)
        if err:
            return False, err
        user = self._db.get_user_by_username(username)
        if not user:
            return False, "用户不存在"
        salt = secrets.token_bytes(16)
        self._db.update_user(user["id"], salt=salt.hex(),
                             password_hash=hash_password(new_password, salt))
        logger.info("重置密码: %s", username)
        return True, ""

    def change_password(self, username: str, old_password: str,
                        new_password: str) -> tuple[bool, str]:
        """自助修改密码：需验证旧密码。"""
        err = validate_password(new_password)
        if err:
            return False, f"新密码不合法：{err}"
        user = self._db.get_user_by_username(username)
        if not user:
            return False, "用户不存在"
        if user.get("disabled"):
            return False, "账号已被禁用"
        salt = bytes.fromhex(user["salt"])
        # M3：常数时间比较
        if not hmac.compare_digest(hash_password(old_password, salt), user["password_hash"]):
            return False, "旧密码错误"
        new_salt = secrets.token_bytes(16)
        self._db.update_user(user["id"], salt=new_salt.hex(),
                             password_hash=hash_password(new_password, new_salt))
        logger.info("用户自助修改密码: %s", username)
        return True, ""

    def set_disabled(self, username: str, disabled: bool) -> tuple[bool, str]:
        user = self._db.get_user_by_username(username)
        if not user:
            return False, "用户不存在"
        self._db.update_user(user["id"], disabled=1 if disabled else 0)
        logger.info("%s 账号: %s", "禁用" if disabled else "启用", username)
        return True, ""

    def bind_email(self, username: str, email: str) -> tuple[bool, str]:
        err = validate_email(email)
        if err:
            return False, err
        user = self._db.get_user_by_username(username)
        if not user:
            return False, "用户不存在"
        other = self._db.get_user_by_email(email)
        if other and other["id"] != user["id"]:
            return False, "该邮箱已被其他账号绑定"
        self._db.update_user(user["id"], email=email)
        logger.info("绑定邮箱: %s <- %s", username, email)
        return True, ""

    def record_login(self, username: str, ip: str) -> None:
        user = self._db.get_user_by_username(username)
        if user:
            self._db.update_user(user["id"], last_login_at=_now(), last_login_ip=ip)

    def get_user_info(self, username: str) -> dict:
        """返回 {id, email} 供登录/验证响应携带。"""
        user = self._db.get_user_by_username(username) or {}
        return {"id": user.get("id", ""), "email": user.get("email") or ""}

    def find_by_email(self, email: str) -> str | None:
        user = self._db.get_user_by_email(email)
        return user["username"] if user else None

    def list_users(self) -> list[dict]:
        return [
            {
                "id": u.get("id", ""),
                "username": u.get("username", ""),
                "email": u.get("email") or "-",
                "created_at": _ts(u.get("created_at", 0)),
                "last_login_at": _ts(u.get("last_login_at", 0)),
                "last_login_ip": u.get("last_login_ip") or "-",
                "disabled": bool(u.get("disabled")),
            }
            for u in self._db.all_users()
        ]


class SessionStore:
    """令牌会话存储（持久化，滑动过期）。"""

    def __init__(self, sessions_file: str = SESSIONS_FILE, ttl: int = SESSION_TTL):
        self._path = sessions_file
        self._ttl = ttl
        self._lock = threading.Lock()
        self._last_persist = 0
        # {token哈希: {username, created_at, expires_at, absolute_expires_at, ip}}
        self._sessions: dict = _load_json(sessions_file, {})
        self.purge_expired()

    def _abs_expiry(self, session: dict) -> int:
        """绝对过期时间（旧数据无该字段时按创建时间推算）。"""
        return session.get("absolute_expires_at") or (session["created_at"] + SESSION_ABSOLUTE_TTL)

    def _persist_locked(self, force: bool = False) -> None:
        """写盘节流（M6）：滑动续期不逐次落盘，创建/删除类操作强制落盘。"""
        now = _now()
        if force or now - self._last_persist >= SESSION_PERSIST_INTERVAL:
            _save_json(self._path, self._sessions)
            self._last_persist = now

    def purge_expired(self) -> None:
        now = _now()
        with self._lock:
            expired = [t for t, s in self._sessions.items()
                       if s["expires_at"] < now or self._abs_expiry(s) < now]
            for t in expired:
                del self._sessions[t]
            if expired:
                self._persist_locked(force=True)
                logger.info("清理过期会话 %d 个", len(expired))

    def create_session(self, username: str, ip: str) -> dict:
        now = _now()
        token = secrets.token_hex(32)
        key = _hash_token(token)   # 磁盘只存令牌哈希，防止文件泄露即冒用
        with self._lock:
            self._sessions[key] = {
                "username": username,
                "created_at": now,
                "expires_at": now + self._ttl,
                # M1：绝对寿命上限，滑动续期不可超越
                "absolute_expires_at": now + SESSION_ABSOLUTE_TTL,
                "ip": ip,
            }
            # 单用户最多保留 5 个会话（多设备），超出挤掉最早的
            own = sorted(
                [(t, s["created_at"]) for t, s in self._sessions.items()
                 if s["username"] == username],
                key=lambda x: x[1],
            )
            for t, _ in own[:-5]:
                del self._sessions[t]
            self._persist_locked(force=True)
        return {"token": token, "expires_at": now + self._ttl}

    def verify_token(self, token: str) -> dict | None:
        """校验令牌并滑动续期。有效返回 {username, expires_at}，无效返回 None。"""
        if not token:
            return None
        now = _now()
        key = _hash_token(token)
        with self._lock:
            session = self._sessions.get(key)
            if not session:
                return None
            abs_exp = self._abs_expiry(session)
            if session["expires_at"] < now or abs_exp < now:
                self._sessions.pop(key, None)
                self._persist_locked(force=True)
                return None
            # 滑动续期封顶到绝对过期时间
            session["expires_at"] = min(now + self._ttl, abs_exp)
            self._persist_locked()   # M6：节流写盘
            return {"username": session["username"], "expires_at": session["expires_at"]}

    def revoke_token(self, token: str) -> bool:
        with self._lock:
            key = _hash_token(token)
            if key in self._sessions:
                del self._sessions[key]
                self._persist_locked(force=True)
                return True
        return False

    def revoke_user_tokens(self, username: str, except_token: str = None) -> int:
        """注销某用户的会话；except_token 用于改密后保留当前会话。"""
        except_key = _hash_token(except_token) if except_token else None
        with self._lock:
            keys = [k for k, s in self._sessions.items()
                    if s["username"] == username and k != except_key]
            for k in keys:
                del self._sessions[k]
            if keys:
                self._persist_locked(force=True)
        return len(keys)

    def list_sessions(self) -> list[dict]:
        now = _now()
        with self._lock:
            return [
                {
                    "username": s["username"],
                    "ip": s.get("ip") or "-",
                    "created_at": _ts(s["created_at"]),
                    "expires_at": _ts(s["expires_at"]),
                    "valid": s["expires_at"] >= now,
                }
                for s in sorted(self._sessions.values(),
                                key=lambda x: x["created_at"], reverse=True)
            ]


class LoginGuard:
    """同 IP 登录失败速率限制（内存态）。"""

    def __init__(self, window: int = GUARD_WINDOW, max_fails: int = GUARD_MAX_FAILS):
        self._window = window
        self._max = max_fails
        self._fails: dict[str, list[int]] = {}
        self._lock = threading.Lock()

    def check(self, ip: str) -> bool:
        """False = 已达上限，应拒绝本次尝试。"""
        now = _now()
        with self._lock:
            recent = [t for t in self._fails.get(ip, []) if now - t < self._window]
            self._fails[ip] = recent
            return len(recent) < self._max

    def record_fail(self, ip: str) -> None:
        now = _now()
        with self._lock:
            self._fails.setdefault(ip, []).append(now)

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)


class CodeStore:
    """邮箱验证码（内存态）：10 分钟有效，同邮箱同用途 60 秒冷却，
    连续输错 5 次作废（防验证码在线爆破）。"""

    MAX_VERIFY_FAILS = 5

    def __init__(self, ttl: int = 600, cooldown: int = 60):
        self._ttl = ttl
        self._cooldown = cooldown
        self._codes: dict = {}   # key: f"{purpose}:{email}" -> {code, sent_at, expires_at, fails}
        self._lock = threading.Lock()

    def issue(self, email: str, purpose: str) -> tuple[bool, str, str]:
        """返回 (ok, err, code)。"""
        now = _now()
        key = f"{purpose}:{email}"
        with self._lock:
            entry = self._codes.get(key)
            if entry and now - entry["sent_at"] < self._cooldown:
                return False, "发送过于频繁，请稍后再试", ""
            code = gen_code()
            self._codes[key] = {"code": code, "sent_at": now,
                                "expires_at": now + self._ttl, "fails": 0}
        return True, "", code

    def verify(self, email: str, purpose: str, code: str) -> tuple[bool, str]:
        now = _now()
        key = f"{purpose}:{email}"
        with self._lock:
            entry = self._codes.get(key)
            if not entry:
                return False, "请先获取验证码"
            if entry["expires_at"] < now:
                self._codes.pop(key, None)
                return False, "验证码已过期，请重新获取"
            if not hmac.compare_digest(entry["code"], (code or "").strip()):
                entry["fails"] = entry.get("fails", 0) + 1
                if entry["fails"] >= self.MAX_VERIFY_FAILS:
                    self._codes.pop(key, None)
                    return False, "错误次数过多，请重新获取验证码"
                return False, "验证码错误"
            self._codes.pop(key, None)   # 一次性使用
        return True, ""

    def reset_cooldown(self, email: str, purpose: str):
        with self._lock:
            self._codes.pop(f"{purpose}:{email}", None)


# 模块级单例（HTTP 服务与 GUI 共用同一份数据）
USER_STORE = UserStore()
SESSION_STORE = SessionStore()
LOGIN_GUARD = LoginGuard()
CODE_STORE = CodeStore()
