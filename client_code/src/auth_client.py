# -*- coding: utf-8 -*-
"""账号认证客户端（服务管理后台 /api/auth/* 的访问封装）

纯标准库实现，base_url 默认取 src.UPDATE_SERVER
（即 Config/Version.ini 的 VERSION_NEW_SERVER，认证与更新同址）。
所有 JSON / 文件请求走 http.client 连接池，自动 keep-alive，
避免 2 秒一次轮询时反复 TCP/TLS 握手带来的额外延时。
"""
import json
import socket
import threading
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlsplit

from src import net_config

TIMEOUT = net_config.TIMEOUT

ERR_NETWORK = "无法连接认证服务器，请检查网络或服务器状态"

_POOL_LOCK = threading.Lock()
_POOL: dict[tuple, list] = {}
_POOL_MAX = 6


def _acquire(host: str, port: int, is_https: bool, timeout: int):
    key = (host, port, is_https)
    with _POOL_LOCK:
        conns = _POOL.get(key)
        while conns:
            c = conns.pop()
            try:
                if c.sock is not None:
                    return c
            except Exception:
                try:
                    c.close()
                except Exception:
                    pass
    if is_https:
        return HTTPSConnection(host, port, timeout=timeout)
    return HTTPConnection(host, port, timeout=timeout)


def _release(conn, host: str, port: int, is_https: bool):
    key = (host, port, is_https)
    with _POOL_LOCK:
        conns = _POOL.setdefault(key, [])
        if len(conns) >= _POOL_MAX:
            try:
                conn.close()
            except Exception:
                pass
            return
        conns.append(conn)


def _do_request(method: str, url: str, body: bytes | None,
                headers: dict, timeout: int) -> tuple[int, bytes]:
    sp = urlsplit(url)
    host = sp.hostname or ""
    port = sp.port or (443 if sp.scheme == "https" else 80)
    is_https = sp.scheme == "https"
    path = sp.path or "/"
    if sp.query:
        path += "?" + sp.query

    last_err: Exception | None = None
    for _ in range(2):
        conn = _acquire(host, port, is_https, timeout)
        try:
            hdrs = dict(headers)
            hdrs["Host"] = sp.netloc
            hdrs["Connection"] = "keep-alive"
            hdrs["Accept-Encoding"] = "identity"
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            status = resp.status
            close_hdr = resp.getheader("Connection", "").lower() == "close"
            try:
                if close_hdr:
                    conn.close()
                else:
                    _release(conn, host, port, is_https)
            except Exception:
                pass
            return status, data
        except (OSError, socket.error, TimeoutError) as e:
            last_err = e
            try:
                conn.close()
            except Exception:
                pass
            continue
        except Exception as e:
            last_err = e
            try:
                conn.close()
            except Exception:
                pass
            break
    if last_err is None:
        last_err = RuntimeError("request failed")
    raise last_err


def _post_json(base_url: str, path: str, payload: dict,
               timeout: int = TIMEOUT) -> tuple[bool, dict]:
    url = base_url.rstrip("/") + path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    try:
        status, data = _do_request("POST", url, body, headers, timeout)
    except Exception:
        return False, {"error": ERR_NETWORK, "network": True}
    if 200 <= status < 300:
        try:
            return True, json.loads(data.decode("utf-8"))
        except Exception as e:
            return False, {"error": f"响应解析失败: {e}"}
    try:
        return False, json.loads(data.decode("utf-8"))
    except Exception:
        return False, {"error": f"服务器错误 (HTTP {status})"}


def _get_json(url: str, timeout: int = TIMEOUT) -> tuple[bool, dict]:
    try:
        status, data = _do_request("GET", url, None,
                                   {"Accept": "application/json"}, timeout)
    except Exception:
        return False, {"error": ERR_NETWORK, "network": True}
    if 200 <= status < 300:
        try:
            return True, json.loads(data.decode("utf-8"))
        except Exception as e:
            return False, {"error": f"响应解析失败: {e}"}
    try:
        return False, json.loads(data.decode("utf-8"))
    except Exception:
        return False, {"error": f"服务器错误 (HTTP {status})"}


def register(base_url: str, username: str, password: str,
             email: str, code: str, invite_code: str = "") -> tuple[bool, dict]:
    return _post_json(base_url, "/api/auth/register",
                      {"username": username, "password": password,
                       "email": email, "code": code, "invite_code": invite_code})


def send_code(base_url: str, email: str, purpose: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/auth/send_code",
                      {"email": email, "purpose": purpose})


def reset_password(base_url: str, email: str, code: str,
                   new_password: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/auth/reset_password",
                      {"email": email, "code": code, "new_password": new_password})


def login(base_url: str, username: str, password: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/auth/login",
                      {"username": username, "password": password})


def verify(base_url: str, token: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/auth/verify", {"token": token})


def logout(base_url: str, token: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/auth/logout", {"token": token}, timeout=5)


def change_password(base_url: str, token: str, old_password: str,
                    new_password: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/auth/change_password",
                      {"token": token, "old_password": old_password,
                       "new_password": new_password})


def chat_send(base_url: str, token: str, receiver_id: str,
              mtype: str, content: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/chat/send",
                      {"token": token, "receiver_id": receiver_id,
                       "type": mtype, "content": content})


def chat_poll(base_url: str, token: str, since_id: int) -> tuple[bool, dict]:
    url = (base_url.rstrip("/") + f"/api/chat/poll?token={token}&since={since_id}")
    return _get_json(url)


def chat_upload(base_url: str, token: str, receiver_id: str,
                data: bytes, ext: str) -> tuple[bool, dict]:
    import base64 as _b64
    return _post_json(base_url, "/api/chat/upload",
                      {"token": token, "receiver_id": receiver_id,
                       "data": _b64.b64encode(data).decode(), "ext": ext})


def friends_add(base_url: str, token: str, target: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/friends/add",
                      {"token": token, "target": target})


def friends_accept(base_url: str, token: str, target_id: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/friends/accept",
                      {"token": token, "target_id": target_id})


def friends_reject(base_url: str, token: str, target_id: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/friends/reject",
                      {"token": token, "target_id": target_id})


def friends_remove(base_url: str, token: str, target_id: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/friends/remove",
                      {"token": token, "target_id": target_id})


def profile_update(base_url: str, token: str, display_name: str = "",
                   avatar_b64: str = "") -> tuple[bool, dict]:
    return _post_json(base_url, "/api/profile/update",
                      {"token": token, "display_name": display_name,
                       "avatar_b64": avatar_b64})


def friends_remark(base_url: str, token: str, target_id: str,
                   remark: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/friends/remark",
                      {"token": token, "target_id": target_id, "remark": remark})


def groups_create(base_url: str, token: str, name: str,
                  member_ids: list) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/groups/create",
                      {"token": token, "name": name, "member_ids": member_ids})


def groups_list(base_url: str, token: str) -> tuple[bool, dict]:
    return _get_json(base_url.rstrip("/") + f"/api/groups/list?token={token}")


def groups_invite(base_url: str, token: str, gid: str,
                  member_ids: list) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/groups/invite",
                      {"token": token, "gid": gid, "member_ids": member_ids})


def groups_quit(base_url: str, token: str, gid: str) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/groups/quit",
                      {"token": token, "gid": gid})


def friends_list(base_url: str, token: str) -> tuple[bool, dict]:
    return _get_json(base_url.rstrip("/") + f"/api/friends/list?token={token}")


def moments_feed(base_url: str, token: str) -> tuple[bool, dict]:
    return _get_json(base_url.rstrip("/") + f"/api/moments/feed?token={token}")


def moments_publish(base_url: str, token: str, text: str,
                    images: list) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/moments/publish",
                      {"token": token, "text": text, "images": images})


def moments_upload(base_url: str, token: str, data: bytes,
                   ext: str) -> tuple[bool, dict]:
    import base64 as _b64
    return _post_json(base_url, "/api/moments/upload",
                      {"token": token, "data": _b64.b64encode(data).decode(),
                       "ext": ext})


def moments_delete(base_url: str, token: str, moment_id: int) -> tuple[bool, dict]:
    return _post_json(base_url, "/api/moments/delete",
                      {"token": token, "moment_id": moment_id})


def download_bytes(url: str, timeout: int = 10,
                   max_bytes: int = 20 * 1024 * 1024) -> bytes | None:
    """下载文件（聊天/朋友圈图片）。失败或超过 max_bytes 返回 None。"""
    try:
        status, data = _do_request("GET", url, None, {}, timeout)
    except Exception:
        return None
    if 200 <= status < 300:
        if len(data) > max_bytes:
            return None
        return data
    return None