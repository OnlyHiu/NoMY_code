# -*- coding: utf-8 -*-
"""聊天 / 好友 / 朋友圈 HTTP API（供 distribute_server 挂载）

鉴权：除文件取回外均需 Bearer 形式的 token（JSON 字段 token）。
图片存储：server_data/uploads/{uid}/（聊天）、server_data/moments/{uid}/（朋友圈），
均按用户 ID 建文件夹。客户端上传 JSON base64，服务端落盘。
"""
import base64
import logging
import os
import re
import time

from auth_store import DATA_DIR, SESSION_STORE, USER_STORE
from security import sniff_image_ext
from db import DB

logger = logging.getLogger("chat_api")

SAFE_REL = re.compile(r"^(uploads|moments)/[\w-]+/[\w.\-]+$")
MAX_IMG_BYTES = 2 * 1024 * 1024  # 单图上限 2MB


def _auth(token: str):
    """校验令牌 → 返回 {uid, username}；无效返回 None。"""
    info = SESSION_STORE.verify_token(token or "")
    if not info:
        return None
    username = info["username"]
    uid = USER_STORE.get_user_info(username).get("id", "")
    if not uid:
        return None
    DB.touch_online(uid)
    return {"uid": uid, "username": username}


def _user_brief(uid: str) -> dict:
    user = DB.get_user(uid) or {}
    return {"id": user.get("id", ""), "username": user.get("username", "未知"),
            "display_name": user.get("display_name") or user.get("username", "未知"),
            "avatar": user.get("avatar") or "",
            "online": DB.is_online(uid)}


def _resolve_target(target: str) -> str | None:
    """好友添加目标：8 位 ID 或用户名。"""
    target = (target or "").strip()
    if re.fullmatch(r"\d{8}", target):
        return target if DB.get_user(target) else None
    user = DB.get_user_by_username(target)
    return user["id"] if user else None


def _safe_path(rel: str) -> str | None:
    if not SAFE_REL.match(rel):
        return None
    root = os.path.abspath(DATA_DIR)
    full = os.path.abspath(os.path.join(root, rel.replace("/", os.sep)))
    if not full.startswith(root + os.sep):
        return None
    return full


def _save_upload(uid: str, folder: str, ext: str, data: bytes,
                 name: str = None) -> str | None:
    ext = re.sub(r"[^a-z0-9]", "", ext.lower())[:5] or "jpg"
    if folder == "chat":
        rel_dir = f"uploads/{uid}"
    elif folder == "uploads":
        rel_dir = f"uploads/{uid}"
    else:
        rel_dir = f"moments/{uid}"
    full_dir = os.path.join(DATA_DIR, rel_dir.replace("/", os.sep))
    os.makedirs(full_dir, exist_ok=True)
    if name:
        name = f"{name}.{ext}"
    else:
        # 16 字节随机熵，防止文件名被枚举猜测
        name = f"{int(time.time() * 1000)}_{base64.b16encode(os.urandom(16)).decode()}.{ext}"
    with open(os.path.join(full_dir, name), "wb") as f:
        f.write(data)
    return f"{rel_dir}/{name}"


def _file_readable(owner_uid: str, me_uid: str) -> bool:
    """文件属主校验：本人、好友、共同群成员（群图片）、
    或好友请求双方（请求列表头像）可读。"""
    if owner_uid == me_uid:
        return True
    if owner_uid in DB.friend_ids(me_uid):
        return True
    # 好友请求双方（待处理请求的头像展示）
    if owner_uid in DB.pending_requesters(me_uid) \
            or me_uid in DB.pending_requesters(owner_uid):
        return True
    return bool(set(DB.group_ids(owner_uid)) & set(DB.group_ids(me_uid)))


def _chat_image_readable(rel: str, me_uid: str) -> bool:
    """聊天图片按「消息收发关系」授权（M7）：
    仅图片消息的发送方、私聊接收方、目标群成员可读，
    防止好友借路径读取对方与他人的私聊图片。"""
    for m in DB.find_messages_by_content(rel):
        if me_uid == m["sender_id"]:
            return True
        recv = m["receiver_id"]
        if recv == me_uid:
            return True
        if recv.startswith("G"):
            try:
                if DB.is_group_member(int(recv[1:]), me_uid):
                    return True
            except ValueError:
                pass
    return False


# ── POST 路由 ───────────────────────────────────────────
def handle_post(path: str, body: dict):
    """返回 (handled, code, payload)。payload 为 dict；文件服务在 GET 中处理。"""
    token = str(body.get("token", ""))
    me = _auth(token)
    if me is None:
        return True, 401, {"error": "登录已过期，请重新登录"}
    uid, username = me["uid"], me["username"]

    if path == "/api/profile/update":
        display_name = str(body.get("display_name", "")).strip()
        avatar_b64 = str(body.get("avatar_b64", ""))
        fields = {}
        if display_name:
            if len(display_name) > 24:
                return True, 400, {"error": "显示名最长 24 个字符"}
            fields["display_name"] = display_name
        if avatar_b64:
            try:
                data = base64.b64decode(avatar_b64)
            except Exception:
                return True, 400, {"error": "头像数据无效"}
            ext = sniff_image_ext(data) or ""
            if not ext:
                return True, 400, {"error": "头像仅支持 PNG/JPEG/WebP/BMP 图片"}
            if len(data) > 200 * 1024:
                return True, 400, {"error": "头像需在 200KB 以内"}
            rel = _save_upload(uid, "uploads", ext, data, name="avatar")
            if not rel:
                return True, 400, {"error": "头像保存失败"}
            fields["avatar"] = rel
        if not fields:
            return True, 400, {"error": "没有要更新的内容"}
        DB.update_user(uid, **fields)
        logger.info("资料更新: %s %s", uid, list(fields.keys()))
        return True, 200, {"ok": True, "display_name": fields.get(
            "display_name", ""), "avatar": fields.get("avatar", "")}

    if path == "/api/friends/remark":
        target = str(body.get("target_id", ""))
        remark = str(body.get("remark", "")).strip()[:24]
        if not DB.get_user(target):
            return True, 404, {"error": "用户不存在"}
        okr = DB.update_remark(uid, target, remark)
        if not okr:
            return True, 400, {"error": "仅好友可以设置备注"}
        return True, 200, {"ok": True}

    if path == "/api/chat/send":
        receiver = str(body.get("receiver_id", ""))
        mtype = str(body.get("type", "text"))
        content = str(body.get("content", ""))
        if mtype not in ("text", "image"):
            return True, 400, {"error": "不支持的消息类型"}
        if not content:
            return True, 400, {"error": "消息内容为空"}
        if receiver.startswith("G"):
            try:
                gid = int(receiver[1:])
            except ValueError:
                return True, 400, {"error": "接收者不存在"}
            if not DB.is_group_member(gid, uid):
                return True, 403, {"error": "你不是该群成员"}
        else:
            if not DB.get_user(receiver):
                return True, 400, {"error": "接收者不存在"}
            if receiver not in DB.friend_ids(uid):
                return True, 403, {"error": "仅好友之间可以发送消息"}
        msg_id = DB.add_message(uid, receiver, mtype, content)
        return True, 200, {"ok": True, "id": msg_id}

    if path == "/api/chat/upload":
        receiver = str(body.get("receiver_id", ""))
        data_b64 = str(body.get("data", ""))
        ext = str(body.get("ext", "jpg"))
        if receiver.startswith("G"):
            try:
                gid = int(receiver[1:])
            except ValueError:
                return True, 400, {"error": "接收者不存在"}
            if not DB.is_group_member(gid, uid):
                return True, 403, {"error": "你不是该群成员"}
        elif receiver not in DB.friend_ids(uid):
            return True, 403, {"error": "仅好友之间可以发送图片"}
        try:
            data = base64.b64decode(data_b64)
        except Exception:
            return True, 400, {"error": "图片数据无效"}
        if not data or len(data) > MAX_IMG_BYTES:
            return True, 400, {"error": f"图片大小需在 2MB 以内"}
        ext = sniff_image_ext(data) or ""
        if not ext:
            return True, 400, {"error": "仅支持 PNG/JPEG/GIF/WebP/BMP 图片"}
        rel = _save_upload(uid, "chat", ext, data)
        if not rel:
            return True, 400, {"error": "保存失败"}
        return True, 200, {"ok": True, "path": rel}

    if path == "/api/friends/add":
        target = _resolve_target(str(body.get("target", "")))
        if not target:
            return True, 404, {"error": "用户不存在"}
        ok, msg = DB.add_friend_request(uid, target)
        if not ok:
            return True, 400, {"error": msg}
        if msg == "matched":
            logger.info("互加自动成为好友: %s <-> %s", uid, target)
        return True, 200, {"ok": True, "target_id": target, "status": "matched" if msg == "matched" else "pending"}

    if path == "/api/friends/accept":
        target = str(body.get("target_id", ""))
        if DB.accept_friend(target, uid):
            return True, 200, {"ok": True}
        return True, 400, {"error": "没有待处理的请求"}

    if path == "/api/friends/reject":
        target = str(body.get("target_id", ""))
        DB.reject_friend(target, uid)
        return True, 200, {"ok": True}

    if path == "/api/friends/remove":
        target = str(body.get("target_id", ""))
        DB.remove_friend(uid, target)
        return True, 200, {"ok": True}

    if path == "/api/groups/create":
        name = str(body.get("name", "")).strip()[:30] or "未命名群聊"
        member_ids = body.get("member_ids") or []
        valid = [m for m in member_ids
                 if m in DB.friend_ids(uid) or m == uid]
        gid = DB.create_group(uid, name, valid)
        logger.info("建群: %s (%s) 成员 %d", name, gid, len(valid) + 1)
        return True, 200, {"ok": True, "gid": str(gid)}

    if path == "/api/groups/invite":
        gid = int(str(body.get("gid", "0") or 0))
        g = DB.group_info(gid)
        if not g:
            return True, 404, {"error": "群不存在"}
        if g["owner_id"] != uid:
            return True, 403, {"error": "仅群主可以邀请成员"}
        valid = [m for m in (body.get("member_ids") or [])
                 if m in DB.friend_ids(uid)]
        n = DB.add_group_members(gid, valid)
        return True, 200, {"ok": True, "added": n}

    if path == "/api/groups/quit":
        gid = int(str(body.get("gid", "0") or 0))
        result = DB.quit_group(gid, uid)
        if not result:
            return True, 404, {"error": "群不存在"}
        return True, 200, {"ok": True, "result": result}

    if path == "/api/moments/publish":
        text = str(body.get("text", "")).strip()
        images = body.get("images") or []
        if not text and not images:
            return True, 400, {"error": "内容不能为空"}
        if not isinstance(images, list) or len(images) > 9:
            return True, 400, {"error": "图片数量需在 9 张以内"}
        safe_imgs = []
        for rel in images:
            rel = str(rel)
            # 仅允许引用自己上传的朋友圈图片，防止盗用他人图片路径
            if not rel.startswith(f"moments/{uid}/") or not _safe_path(rel):
                return True, 400, {"error": "图片路径无效"}
            safe_imgs.append(rel)
        moment_id = DB.add_moment(uid, text, safe_imgs)
        return True, 200, {"ok": True, "id": moment_id}

    if path == "/api/moments/upload":
        data_b64 = str(body.get("data", ""))
        ext = str(body.get("ext", "jpg"))
        try:
            data = base64.b64decode(data_b64)
        except Exception:
            return True, 400, {"error": "图片数据无效"}
        if not data or len(data) > MAX_IMG_BYTES:
            return True, 400, {"error": "图片大小需在 2MB 以内"}
        ext = sniff_image_ext(data) or ""
        if not ext:
            return True, 400, {"error": "仅支持 PNG/JPEG/GIF/WebP/BMP 图片"}
        rel = _save_upload(uid, "moments", ext, data)
        if not rel:
            return True, 400, {"error": "保存失败"}
        return True, 200, {"ok": True, "path": rel}

    if path == "/api/moments/delete":
        moment_id = int(body.get("moment_id", 0) or 0)
        DB.delete_moment(uid, moment_id)
        return True, 200, {"ok": True}

    return False, 0, None


# ── GET 路由 ────────────────────────────────────────────
def handle_get(path: str, query: dict, send_file) -> bool:
    """send_file(full_path, size) 用于二进制文件响应。返回是否已处理。"""
    token = (query.get("token") or [""])[0]
    me = _auth(token)
    if me is None:
        return False
    uid = me["uid"]

    if path == "/api/chat/poll":
        try:
            since = int((query.get("since") or ["0"])[0])
        except ValueError:
            since = 0
        msgs = DB.messages_since(uid, since)
        for m in msgs:
            m["sender_username"] = (DB.get_user(m["sender_id"]) or {}).get(
                "username", m["sender_id"])
        requests = [{"id": r, **_user_brief(r)} for r in DB.pending_requesters(uid)]
        return_payload = {"messages": msgs, "friend_requests": requests}
        _emit_json(send_file, return_payload)
        return True

    if path == "/api/friends/list":
        friends = []
        remark_map = {r["friend_id"]: r["remark"] for r in DB.friend_rows(uid)}
        for fid in DB.friend_ids(uid):
            brief = _user_brief(fid)
            brief["remark"] = remark_map.get(fid, "")
            last = DB.last_message(uid, fid)
            friends.append({**brief, "last": last})
        friends.sort(key=lambda f: -(f["last"] or {}).get("created_at", 0))
        _emit_json(send_file, {"friends": friends})
        return True

    if path == "/api/friends/requests":
        reqs = [{**_user_brief(r)} for r in DB.pending_requesters(uid)]
        _emit_json(send_file, {"requests": reqs})
        return True

    if path == "/api/groups/list":
        groups = []
        for gid in DB.group_ids(uid):
            info = DB.group_info(int(gid)) or {}
            members = [_user_brief(m) for m in info.get("members", [])]
            groups.append({"gid": str(gid), "name": info.get("name", ""),
                           "owner_id": info.get("owner_id", ""),
                           "members": members})
        _emit_json(send_file, {"groups": groups})
        return True

    if path == "/api/moments/feed":
        feed = DB.moments_feed(uid)
        for m in feed:
            m["author"] = m.get("display_name") or m.get("username", "")
            m["images"] = [f"/api/files?p={p}&token={token}" for p in m.get("images", [])]
        _emit_json(send_file, {"feed": feed})
        return True

    if path == "/api/files":
        rel = (query.get("p") or [""])[0]
        full = _safe_path(rel)
        if not full or not os.path.isfile(full):
            _emit_json(send_file, {"error": "文件不存在"}, 404)
            return True
        # M7：按文件类型分层授权，防横向越权读取他人图片
        owner_uid = rel.split("/")[1] if "/" in rel else ""
        fname = rel.rsplit("/", 1)[-1].lower()
        if rel.startswith("moments/"):
            # 朋友圈图片：仅本人与好友可见（与 moments feed 范围一致）
            allowed = owner_uid == uid or owner_uid in DB.friend_ids(uid)
        elif fname.startswith("avatar."):
            # 头像：好友/待处理请求双方/共同群成员可读（列表展示需要）
            allowed = _file_readable(owner_uid, uid)
        else:
            # 聊天图片：按消息收发关系授权
            allowed = _chat_image_readable(rel, uid)
        if not allowed:
            _emit_json(send_file, {"error": "无权访问该文件"}, 403)
            return True
        with open(full, "rb") as f:
            data = f.read()
        send_file(full, data)
        return True

    return False


def _emit_json(send_file, payload: dict, code: int = 200):
    """把 JSON 响应交给 handler 的文件发送通道（统一出口）。"""
    import json
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    send_file(None, body, json_mode=True, status=code)
