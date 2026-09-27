# -*- coding: utf-8 -*-
"""NoMY — 服务管理后台（独立脚本）

把本机作为服务端：向局域网提供账号注册/登录校验（/api/auth/*），
以及版本检查与安装包下载（/api/update/*）。

用法：
    python distribute_server.py --package "C:\\path\\to\\setup.exe"
    python distribute_server.py --package setup.exe --port 18888
    python distribute_server.py --config server.json

接口（GET）：
    /                     → 静态信息展示页（server/web/index.html：软件信息 + 下载按钮）
    /api/update/check     → JSON  { version, info, package, package_size, sha256, ... }
    /api/update/download   → 二进制流（application/octet-stream）
    /health                → {"ok": true, "auth": true}

接口（POST，JSON 请求体）：
    /api/auth/register     {username, password}        → 201 {ok, username}
    /api/auth/login        {username, password}        → 200 {ok, token, username, expires_at}
    /api/auth/verify       {token}                     → 200 {ok, username, expires_at}
    /api/auth/logout       {token}                     → 200 {ok}
    /api/auth/change_password {token, old_password, new_password}
                                                       → 200 {ok}（注销该用户其他会话）

依赖：仅 Python 3.10+ 标准库（无 PySide6 / FastAPI / requests）
"""
import argparse
import configparser
import hashlib
import json
import logging
import os
import signal
import socket
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from auth_store import USER_STORE, SESSION_STORE, LOGIN_GUARD, CODE_STORE
from mail_service import send_mail, code_body, validate_email
import chat_api
from security import SECURITY, GLOBAL_LIMIT, AUTH_LIMIT

LOG_FORMAT = "%(asctime)s - %(name)s:[%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT,
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("distribute_server")

DEFAULT_PORT = 18888
DEFAULT_HOST = "0.0.0.0"
DEFAULT_VERSION_INI = "./Config/Version.ini"
CHUNK_SIZE = 64 * 1024
# 静态网页目录（信息展示页：软件信息 + 下载入口）
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

AUTH_ENABLED = True  # 可由配置文件 "auth_enabled": false 关闭认证接口


# ── 工具函数 ──────────────────────────────────────────────
def get_lan_ips() -> list[str]:
    """本机局域网 IP 列表（排除 127.x、IPv6、link-local）。"""
    ips: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ":" not in ip and ip not in ips:
                ips.append(ip)
        finally:
            s.close()
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if (not ip.startswith("127.") and ":" not in ip
                    and not ip.startswith("169.254.") and ip not in ips):
                ips.append(ip)
    except Exception:
        pass
    return ips


def _frozen_exe_dirs() -> list[str]:
    """打包运行（Nuitka/PyInstaller）时 exe 所在目录候选。

    onefile 模式下 sys.executable 可能指向临时解包目录，
    因此同时考虑 sys.argv[0]（用户实际启动的 exe 路径）。
    """
    if not (getattr(sys, "frozen", False) or globals().get("__compiled__")):
        return []
    dirs: list[str] = []
    for p in (sys.argv[0], sys.executable):
        d = os.path.dirname(os.path.abspath(p))
        if d and d not in dirs:
            dirs.append(d)
    return dirs


def load_version_info(ini_path: str = DEFAULT_VERSION_INI) -> dict:
    if not os.path.isfile(ini_path) and ini_path == DEFAULT_VERSION_INI:
        # 打包为 exe 后 "./Config" 相对 cwd 不可靠，回退到 exe 所在目录查找
        for d in _frozen_exe_dirs():
            for cand in (os.path.join(d, "Config", "Version.ini"),
                         os.path.join(d, "Version.ini")):
                if os.path.isfile(cand):
                    ini_path = cand
                    break
            if os.path.isfile(ini_path):
                break
    if not os.path.isfile(ini_path):
        logger.warning("Version.ini 不存在: %s", ini_path)
        return {"version": "", "info": "", "new": "", "log": ""}
    cfg = configparser.ConfigParser()
    try:
        cfg.read(ini_path, encoding="utf-8")
    except Exception as e:
        logger.warning("读 Version.ini 失败: %s", e)
        return {"version": "", "info": "", "new": "", "log": ""}
    if "VERSION" not in cfg:
        return {"version": "", "info": "", "new": "", "log": ""}
    s = cfg["VERSION"]
    return {
        "version": s.get("VERSION", "").strip(),
        "info":    s.get("VERSION_INFO", "").strip(),
        "new":     s.get("VERSION_NEW", "").strip(),
        "log":     s.get("VERSION_LOG", "").strip(),
    }


def _ip_allowed(client_ip: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    try:
        import ipaddress
        addr = ipaddress.ip_address(client_ip)
        for sn in allowed:
            try:
                if addr in ipaddress.ip_network(sn, strict=False):
                    return True
            except ValueError:
                continue
    except Exception:
        # 子网配置非法时拒绝访问（fail-closed），而非放行
        return False
    return False


def _parse_subnets(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []


# ── 分发状态 ──────────────────────────────────────────────
class _State:
    """当前分发的安装包 + 版本号（供外部动态设置）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.package_path: str | None = None
        self.package_name: str = ""
        self.package_size: int = 0
        self.package_sha256: str = ""
        self.version_override: str | None = None  # GUI 覆盖（None = 用 Version.ini）
        self.version_ini_path: str = DEFAULT_VERSION_INI

    def set_package(self, path: str) -> None:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"安装包不存在: {path}")
        with self._lock:
            self.package_path = path
            self.package_name = os.path.basename(path)
            self.package_size = os.path.getsize(path)
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            self.package_sha256 = h.hexdigest()

    def set_version_override(self, version: str | None) -> None:
        """设置 /api/update/check 报告的版本号；传 None 或空串则回退 Version.ini。"""
        with self._lock:
            self.version_override = (version or "").strip() or None

    def set_version_ini_path(self, path: str) -> None:
        with self._lock:
            self.version_ini_path = path or DEFAULT_VERSION_INI

    def info_dict(self) -> dict:
        meta = load_version_info(self.version_ini_path)
        with self._lock:
            version = self.version_override or meta["version"]
            return {
                "version":      version,
                "info":         meta["new"] or meta["log"],
                "app_info":     meta["info"],
                "package":      self.package_name,
                "package_size": self.package_size,
                "sha256":       self.package_sha256,
                "has_package":  self.package_path is not None,
            }


STATE = _State()


# ── HTTP 处理器 ────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    allowed_subnets: list[str] = []
    # 读超时：缓解慢速连接（slowloris）占用线程
    timeout = 30

    def log_request(self, code='-', size='-'):
        # 访问日志脱敏：剥离 query string（其中含 token），防止凭证落日志
        self.log_message('"%s" %s', self.requestline.split("?", 1)[0], str(code))

    def log_message(self, fmt, *args):
        logger.info("%s - " + fmt, self.address_string(), *args)

    def do_GET(self):
        if not _ip_allowed(self.client_address[0], self.allowed_subnets):
            self.send_error(403, "Forbidden (not in allowed subnets)")
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        ip = self.client_address[0]
        if not SECURITY.allow(f"all:{ip}", GLOBAL_LIMIT):
            self._json(429, {"error": "请求过于频繁，请稍后再试"})
            return

        if path.startswith(("/api/chat", "/api/friends", "/api/moments", "/api/groups", "/api/files")):
            if not AUTH_ENABLED:
                self._json(503, {"error": "auth disabled"})
                return
            try:
                handled = chat_api.handle_get(path, query, self._send_data)
            except Exception as e:
                logger.error("chat_api GET 异常: %s", e)
                self._json(500, {"error": "服务器内部错误"})
                return
            if handled:
                return
            self.send_error(404, "Not Found")
            return

        if path == "/api/update/check":
            self._handle_check()
        elif path == "/api/update/download" or path.startswith("/api/update/download?"):
            self._handle_download()
        elif path == "/health":
            self._json(200, {"ok": True, "auth": AUTH_ENABLED})
        else:
            self._handle_static(path)

    def _handle_static(self, path: str):
        """静态网页（server/web/ 目录）：信息展示页 / 与下载入口。"""
        if path == "/":
            path = "/index.html"
        rel = os.path.normpath(urllib.parse.unquote(path.lstrip("/")))
        if rel.startswith("..") or os.path.isabs(rel) or rel.startswith("..\\"):
            self.send_error(403, "Forbidden")
            return
        full = os.path.join(WEB_DIR, rel)
        # 目录穿越兜底：解析后必须仍在 WEB_DIR 内
        web_root = os.path.normpath(os.path.abspath(WEB_DIR))
        if not os.path.normpath(os.path.abspath(full)).startswith(web_root + os.sep):
            self.send_error(403, "Forbidden")
            return
        if not os.path.isfile(full):
            self.send_error(404, "Not Found")
            return
        ext = os.path.splitext(full)[1].lower()
        mime = STATIC_MIME.get(ext, "application/octet-stream")
        try:
            with open(full, "rb") as f:
                data = f.read()
        except Exception:
            self.send_error(500, "Read Error")
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not _ip_allowed(self.client_address[0], self.allowed_subnets):
            self.send_error(403, "Forbidden (not in allowed subnets)")
            return
        ip = self.client_address[0]
        if not SECURITY.allow(f"all:{ip}", GLOBAL_LIMIT):
            self._json(429, {"error": "请求过于频繁，请稍后再试"})
            return
        if self.path.startswith("/api/auth") and                 not SECURITY.allow(f"auth:{ip}", AUTH_LIMIT):
            self._json(429, {"error": "操作过于频繁，请稍后再试"})
            return

        if not AUTH_ENABLED:
            self._json(503, {"error": "auth disabled"})
            return

        # 聊天 / 好友 / 朋友圈（大 JSON 体：图片 base64 上传，上限放宽到 4MB）
        if self.path.startswith(("/api/chat", "/api/friends", "/api/moments", "/api/groups", "/api/profile")):
            body = self._read_json(max_bytes=4 * 1024 * 1024)
            if body is None:
                self._json(400, {"error": "invalid json body"})
                return
            try:
                handled, code, payload = chat_api.handle_post(self.path, body)
            except Exception as e:
                logger.error("chat_api POST 异常: %s", e)
                self._json(500, {"error": "服务器内部错误"})
                return
            if handled:
                self._json(code, payload)
                return
            self.send_error(404, "Not Found")
            return

        routes = {
            "/api/auth/register": self._auth_register,
            "/api/auth/login": self._auth_login,
            "/api/auth/verify": self._auth_verify,
            "/api/auth/logout": self._auth_logout,
            "/api/auth/change_password": self._auth_change_password,
            "/api/auth/send_code": self._auth_send_code,
            "/api/auth/reset_password": self._auth_reset_password,
        }
        handler = routes.get(self.path)
        if not handler:
            self.send_error(404, "Not Found")
            return
        body = self._read_json()
        if body is None:
            self._json(400, {"error": "invalid json body"})
            return
        handler(body)

    def _read_json(self, max_bytes: int = 64 * 1024) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > max_bytes:
                return None
            raw = self.rfile.read(length).decode("utf-8")
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    def _send_data(self, full_path, data: bytes, json_mode: bool = False,
                   status: int = 200):
        """统一二进制/JSON 响应出口（供 chat_api 文件与 JSON 回包使用）。"""
        self.send_response(status)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if json_mode:
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
        else:
            ext = (full_path or "").rsplit(".", 1)[-1].lower()
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"
                    }.get(ext, "application/octet-stream")
            self.send_header("Content-Type", mime)
            # M2：URL 中携带 token，禁止缓存防止凭证落盘
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── /api/auth/send_code ────────────────────────────────
    def _auth_send_code(self, body: dict):
        email = str(body.get("email", "")).strip().lower()
        purpose = str(body.get("purpose", ""))
        if purpose not in ("register", "reset"):
            self._json(400, {"error": "无效的验证码用途"})
            return
        err = validate_email(email)
        if err:
            self._json(400, {"error": err})
            return
        # L7：发信限流（单 IP 每小时 10 封 / 全局每小时 100 封），防邮件轰炸
        ip = self.client_address[0]
        if not SECURITY.allow(f"mail:{ip}", 10, 3600) \
                or not SECURITY.allow("mail:global", 100, 3600):
            self._json(429, {"error": "发送过于频繁，请稍后再试"})
            return
        if purpose == "reset" and not USER_STORE.find_by_email(email):
            # M4：统一响应防邮箱枚举——无论邮箱是否绑定账号均返回成功，不发信
            logger.info("找回密码请求未绑定邮箱，静默忽略: %s <- %s", email, ip)
            self._json(200, {"ok": True})
            return
        ok, err, code = CODE_STORE.issue(email, purpose)
        if not ok:
            self._json(429, {"error": err})
            return
        smtp = USER_STORE.get_smtp_config()
        ok_sent, send_err = send_mail(smtp, email,
                                      f"NoMY 验证码（{'注册' if purpose == 'register' else '找回密码'}）",
                                      code_body(code, purpose, 10))
        if not ok_sent:
            CODE_STORE.reset_cooldown(email, purpose)
            self._json(503, {"error": send_err})
            return
        logger.info("验证码已发送: %s (%s) <- %s", email, purpose,
                    self.client_address[0])
        self._json(200, {"ok": True})

    # ── /api/auth/reset_password ───────────────────────────
    def _auth_reset_password(self, body: dict):
        email = str(body.get("email", "")).strip().lower()
        code = str(body.get("code", ""))
        new_password = str(body.get("new_password", ""))
        ok, err = CODE_STORE.verify(email, "reset", code)
        if not ok:
            self._json(400, {"error": err})
            return
        username = USER_STORE.find_by_email(email)
        if not username:
            self._json(400, {"error": "该邮箱未绑定任何账号"})
            return
        ok, err = USER_STORE.reset_password(username, new_password)
        if not ok:
            self._json(400, {"error": err})
            return
        SESSION_STORE.revoke_user_tokens(username)
        logger.info("密码已通过邮箱重置: %s", username)
        self._json(200, {"ok": True})

    # ── /api/auth/register ─────────────────────────────────
    def _auth_register(self, body: dict):
        if not USER_STORE.allow_registration:
            self._json(403, {"error": "服务器已关闭开放注册，请联系管理员创建账号"})
            return
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        email = str(body.get("email", "")).strip().lower()
        code = str(body.get("code", ""))
        invite = str(body.get("invite_code", "")).strip()
        need_invite = USER_STORE.get_register_invite_code()
        if need_invite and invite != need_invite:
            self._json(403, {"error": "注册邀请码错误"})
            return
        if not email:
            self._json(400, {"error": "请填写邮箱并获取验证码"})
            return
        ok, err = CODE_STORE.verify(email, "register", code)
        if not ok:
            self._json(400, {"error": err})
            return
        ok, err = USER_STORE.create_user(username, password, email=email)
        if not ok:
            self._json(400, {"error": err})
            return
        user_info = USER_STORE.get_user_info(username)
        logger.info("注册成功: %s (id=%s, %s) <- %s", username,
                    user_info.get("id"), email, self.client_address[0])
        self._json(201, {"ok": True, "username": username,
                         "user_id": user_info.get("id")})

    # ── /api/auth/login ────────────────────────────────────
    def _auth_login(self, body: dict):
        ip = self.client_address[0]
        if not LOGIN_GUARD.check(ip):
            self._json(429, {"error": "失败次数过多，请 5 分钟后再试"})
            return
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        ok, err = USER_STORE.verify_user(username, password)
        if not ok:
            LOGIN_GUARD.record_fail(ip)
            logger.warning("登录失败: %s (%s) <- %s", username, err, ip)
            self._json(401, {"error": err})
            return
        LOGIN_GUARD.reset(ip)
        session = SESSION_STORE.create_session(username, ip)
        USER_STORE.record_login(username, ip)
        user_info = USER_STORE.get_user_info(username)
        logger.info("登录成功: %s <- %s", username, ip)
        self._json(200, {"ok": True, "token": session["token"],
                         "username": username, "expires_at": session["expires_at"],
                         "user_id": user_info.get("id", ""),
                         "email": user_info.get("email", "")})

    # ── /api/auth/verify ───────────────────────────────────
    def _auth_verify(self, body: dict):
        token = str(body.get("token", ""))
        info = SESSION_STORE.verify_token(token)
        if not info:
            self._json(401, {"error": "登录已过期，请重新登录"})
            return
        user_info = USER_STORE.get_user_info(info["username"])
        self._json(200, {"ok": True, "username": info["username"],
                         "expires_at": info["expires_at"],
                         "user_id": user_info.get("id", ""),
                         "email": user_info.get("email", "")})

    # ── /api/auth/logout ───────────────────────────────────
    def _auth_logout(self, body: dict):
        token = str(body.get("token", ""))
        SESSION_STORE.revoke_token(token)
        self._json(200, {"ok": True})

    # ── /api/auth/change_password ──────────────────────────
    def _auth_change_password(self, body: dict):
        token = str(body.get("token", ""))
        info = SESSION_STORE.verify_token(token)
        if not info:
            self._json(401, {"error": "登录已过期，请重新登录"})
            return
        username = info["username"]
        old_password = str(body.get("old_password", ""))
        new_password = str(body.get("new_password", ""))
        ok, err = USER_STORE.change_password(username, old_password, new_password)
        if not ok:
            self._json(400, {"error": err})
            return
        # 改密后注销该用户全部旧会话（含当前），并为当前设备签发新令牌（M1：令牌轮换）
        revoked = SESSION_STORE.revoke_user_tokens(username)
        new_sess = SESSION_STORE.create_session(username, self.client_address[0])
        logger.info("修改密码: %s（注销旧会话 %d 个，已轮换当前令牌）", username, revoked)
        self._json(200, {"ok": True, "token": new_sess["token"],
                         "expires_at": new_sess["expires_at"]})

    # ── /api/update/check ──────────────────────────────────
    def _handle_check(self):
        info = STATE.info_dict()
        if not info["version"]:
            self._json(503, {"error": "Version.ini not available"})
            return
        self._json(200, info)

    # ── /api/update/download ────────────────────────────────
    def _handle_download(self):
        with STATE._lock:
            path = STATE.package_path
            name = STATE.package_name
            size = STATE.package_size
        if not path or not os.path.isfile(path):
            self._json(404, {"error": "package not set"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        # L6：文件名过滤引号/CRLF，防响应头注入
        safe_name = (name or "package.bin").replace('"', "").replace("\r", "").replace("\n", "")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{safe_name}"')
        self.send_header("X-Package-SHA256", STATE.package_sha256)
        self.end_headers()
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            logger.info("客户端断开: %s", self.address_string())
        logger.info("分发完成: %s (%d B) -> %s",
                    name, size, self.client_address[0])

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ── 配置加载 ──────────────────────────────────────────────
def _load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── 公开 API（供 GUI 等同进程模块调用） ────────────────────────
_G_SERVER_HTTP = None
_G_SERVER_THREAD = None
_G_SERVER_LOCK = threading.Lock()

# M5：并发连接/线程上限（超出等待 5 秒后断开，防连接洪水 DoS）
MAX_CONCURRENT = 200
_CONC = threading.BoundedSemaphore(MAX_CONCURRENT)


class _LimitedServer(ThreadingHTTPServer):
    """ThreadingHTTPServer + 并发线程上限 + 监听队列扩大。"""
    daemon_threads = True
    request_queue_size = 128

    def process_request(self, request, client_address):
        if not _CONC.acquire(timeout=5):
            try:
                request.close()
            except Exception:
                pass
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            _CONC.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            _CONC.release()


def _warn_plaintext(host: str, port: int) -> None:
    """H1：未启用 TLS 时给出显著告警（口令/令牌将以明文传输）。"""
    if host in ("127.0.0.1", "localhost", "::1"):
        return
    logger.warning(
        "=" * 60)
    logger.warning(
        "⚠ 未启用 TLS！服务正以明文 HTTP 监听 %s:%s，口令/令牌可在网络上被嗅探。", host, port)
    logger.warning(
        "  建议：配置 --tls-cert/--tls-key 启用 HTTPS，或置于 TLS 反向代理之后。")
    logger.warning(
        "=" * 60)


def _apply_tls(httpd: ThreadingHTTPServer, tls_cert: str, tls_key: str) -> None:
    """给 HTTP 服务器套上 TLS（https）。证书/私钥为 PEM 格式。"""
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.set_ciphers("HIGH:!aNULL:!MD5")
    except Exception:
        pass
    ctx.load_cert_chain(tls_cert, tls_key)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    logger.info("已启用 HTTPS (TLS)，证书: %s", tls_cert)


def start_server_in_thread(host: str, port: int,
                           allowed_subnets: list[str] | None = None,
                           tls_cert: str = None, tls_key: str = None) -> tuple[bool, str]:
    """在后台线程启动 HTTP 服务（同进程）。返回 (ok, err_msg)。"""
    global _G_SERVER_HTTP, _G_SERVER_THREAD
    with _G_SERVER_LOCK:
        if _G_SERVER_THREAD and _G_SERVER_THREAD.is_alive():
            return True, "已在运行"
        _Handler.allowed_subnets = allowed_subnets or []
        try:
            httpd = _LimitedServer((host, port), _Handler)
        except OSError as e:
            return False, f"启动失败（端口 {port} 已被占用？）: {e}"
        if tls_cert and tls_key:
            try:
                _apply_tls(httpd, tls_cert, tls_key)
            except Exception as e:
                try:
                    httpd.server_close()
                except Exception:
                    pass
                return False, f"加载 TLS 证书失败: {e}"
        else:
            _warn_plaintext(host, port)   # H1：明文监听告警
        _G_SERVER_HTTP = httpd
        _G_SERVER_THREAD = threading.Thread(
            target=httpd.serve_forever, daemon=True, name="update-server"
        )
        _G_SERVER_THREAD.start()
        return True, ""


def stop_server_in_thread() -> bool:
    """停止后台 HTTP 服务。"""
    global _G_SERVER_HTTP, _G_SERVER_THREAD
    with _G_SERVER_LOCK:
        if not _G_SERVER_THREAD:
            return False
        if _G_SERVER_HTTP:
            try:
                _G_SERVER_HTTP.shutdown()
                _G_SERVER_HTTP.server_close()
            except Exception:
                pass
        _G_SERVER_HTTP = None
        _G_SERVER_THREAD = None
        return True


def is_server_running() -> bool:
    return (_G_SERVER_THREAD is not None
            and _G_SERVER_THREAD.is_alive())


def get_access_urls(port: int) -> list[str]:
    """所有可被客户端访问的 URL。"""
    ips = get_lan_ips() or ["127.0.0.1"]
    return [f"http://{ip}:{port}" for ip in ips]


def server_status_text(host: str, port: int) -> str:
    """生成给用户看的横幅文本。"""
    urls = get_access_urls(port)
    info = STATE.info_dict()
    lines = [
        "=" * 64,
        "NoMY — 服务管理后台（账号认证 + 更新分发）",
        "=" * 64,
        f"绑定:        {host}:{port}",
        f"安装包:      {info['package'] or '（未选择）'} "
        f"({(info['package_size'] or 0) / 1024 / 1024:.2f} MB)"
        if info["package_size"] else f"安装包:      {info['package'] or '（未选择）'}",
        f"SHA256:      {info['sha256'] or '（未选择）'}",
        f"版本:        {info['version'] or '（未设置）'}",
        "客户端访问地址:",
        "",
    ]
    lines += [f"  {u}" for u in urls]
    lines += ["", "=" * 64]
    return "\n".join(lines)


# ── 入口 ──────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="NoMY — LAN 更新分发服务器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例：
  %(prog)s --package setup.exe
  %(prog)s --package "C:\\installer\\setup.exe" --port 18888
  %(prog)s --config server.json

server.json 示例：
  {
    "package": "C:\\installer\\setup.exe",
    "port": 18888,
    "host": "0.0.0.0",
    "allowed_subnets": ["192.168.1.0/24"]
  }
""",
    )
    ap.add_argument("--package", "-p", help="安装包路径 (.exe / .msi)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"监听端口 (默认 {DEFAULT_PORT})")
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help=f"监听地址 (默认 {DEFAULT_HOST})")
    ap.add_argument("--config", "-c", help="JSON 配置文件路径")
    ap.add_argument("--version-ini", default=DEFAULT_VERSION_INI,
                    help=f"Version.ini 路径 (默认 {DEFAULT_VERSION_INI})")
    ap.add_argument("--allowed-subnets", default="",
                    help='允许的子网 (CIDR, 逗号分隔)。例: "192.168.1.0/24,10.0.0.0/8"')
    ap.add_argument("--no-registration", action="store_true",
                    help="关闭开放注册（账号只能由管理后台创建）")
    ap.add_argument("--tls-cert", default="",
                    help="TLS 证书 PEM 文件（提供后以 HTTPS 服务）")
    ap.add_argument("--tls-key", default="",
                    help="TLS 私钥 PEM 文件（与 --tls-cert 配套）")
    ap.add_argument("--log-file", help="同时输出日志到此文件")
    ap.add_argument("--print-urls", action="store_true", default=True,
                    help="启动后打印所有可访问的 URL（默认开启）")
    ap.add_argument("--no-print-urls", dest="print_urls",
                    action="store_false", help="不打印 URL")
    args = ap.parse_args()

    if args.log_file:
        fh = logging.FileHandler(args.log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(fh)

    # 合并 CLI + 配置文件
    package_path: str | None = args.package
    port: int = args.port
    host: str = args.host
    allowed_subnets = _parse_subnets(args.allowed_subnets)
    version_ini: str = args.version_ini

    if args.config:
        if not os.path.isfile(args.config):
            print(f"[错误] 配置文件不存在: {args.config}", file=sys.stderr)
            sys.exit(1)
        try:
            cfg = _load_config(args.config)
        except Exception as e:
            print(f"[错误] 配置文件解析失败: {e}", file=sys.stderr)
            sys.exit(1)
        package_path = package_path or cfg.get("package", "")
        try:
            port = int(cfg.get("port", port))
        except (TypeError, ValueError):
            pass
        host = cfg.get("host", host)
        allowed_subnets = _parse_subnets(cfg.get("allowed_subnets", allowed_subnets))
        version_ini = cfg.get("version_ini", version_ini)
        if (v := cfg.get("version_override")):
            STATE.set_version_override(str(v))
        if "auth_enabled" in cfg:
            globals()["AUTH_ENABLED"] = bool(cfg["auth_enabled"])
        if "allow_registration" in cfg:
            USER_STORE.set_allow_registration(bool(cfg["allow_registration"]))

    if args.no_registration:
        USER_STORE.set_allow_registration(False)

    # 安装包为可选（仅更新分发需要）；账号认证不依赖它
    if package_path:
        package_path = str(Path(package_path).expanduser().resolve())
        if not os.path.isfile(package_path):
            print(f"[错误] 安装包不存在: {package_path}", file=sys.stderr)
            sys.exit(1)
        try:
            STATE.set_package(package_path)
        except Exception as e:
            print(f"[错误] {e}", file=sys.stderr)
            sys.exit(1)

    # 启动 HTTP 服务
    _Handler.allowed_subnets = allowed_subnets
    try:
        httpd = _LimitedServer((host, port), _Handler)
    except OSError as e:
        print(f"[错误] 启动失败（端口 {port} 已被占用？）: {e}",
              file=sys.stderr)
        sys.exit(1)
    if (args.tls_cert and args.tls_key):
        try:
            _apply_tls(httpd, args.tls_cert, args.tls_key)
        except Exception as e:
            print(f"[错误] 加载 TLS 证书失败: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.tls_cert or args.tls_key:
        print("[错误] --tls-cert 与 --tls-key 必须同时提供", file=sys.stderr)
        sys.exit(1)
    else:
        _warn_plaintext(host, port)   # H1：明文监听告警

    # 优雅退出
    def _shutdown(signum, frame):
        logger.info("收到信号 %s，正在关闭...", signum)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    # 横幅
    ips = get_lan_ips()
    sep_line = "=" * 64
    logger.info(sep_line)
    logger.info("NoMY — 服务管理后台（账号认证 + 更新分发）")
    logger.info(sep_line)
    logger.info("绑定:        %s:%d", host, port)
    logger.info("安装包:      %s (%.2f MB)", STATE.package_name,
                STATE.package_size / 1024 / 1024)
    logger.info("SHA256:      %s", STATE.package_sha256)
    logger.info("版本:        %s", STATE.info_dict().get("version") or "（未设置）")
    if allowed_subnets:
        logger.info("允许子网:    %s", ", ".join(allowed_subnets))
    else:
        logger.info("允许子网:    (不限制)")
    logger.info("账号认证:    %s", "开启" if AUTH_ENABLED else "关闭")
    logger.info("开放注册:    %s", "开启" if USER_STORE.allow_registration else "关闭")
    logger.info(sep_line)

    if args.print_urls:
        urls = [f"http://{ip}:{port}" for ip in (ips or ["127.0.0.1"])]
        logger.info("其他设备在 设置 → 版本更新 → 设置更新服务器 中填入以下任一地址：")
        for u in urls:
            logger.info("  %s", u)
        logger.info(sep_line)

    logger.info("按 Ctrl+C 停止服务")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        logger.info("已停止")


if __name__ == "__main__":
    main()