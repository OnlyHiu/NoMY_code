# -*- coding: utf-8 -*-
"""服务端 SQLite 数据层

存储：用户、好友关系、聊天消息、朋友圈。线程安全（单连接 + 锁）。
数据库文件：server/server_data/noon.db
"""
import logging
import os
import sqlite3
import sys
import threading
import time

logger = logging.getLogger("server.db")


def _data_root() -> str:
    """打包 exe（Nuitka onefile）时 __file__ 在临时解包目录，
    数据库需要持久化，改用 exe 所在目录。"""
    if getattr(sys, "frozen", False) or globals().get("__compiled__"):
        for p in (sys.argv[0], sys.executable):
            if p:
                return os.path.dirname(os.path.abspath(p))
    return os.path.dirname(os.path.abspath(__file__))


DATA_DIR = os.path.join(_data_root(), "server_data")
DB_FILE = os.path.join(DATA_DIR, "noon.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT UNIQUE,
    salt          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    disabled      INTEGER DEFAULT 0,
    created_at    INTEGER,
    last_login_at INTEGER DEFAULT 0,
    last_login_ip TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS friends (
    owner_id   TEXT NOT NULL,
    friend_id  TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',   -- pending / accepted
    created_at INTEGER,
    PRIMARY KEY (owner_id, friend_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id  TEXT NOT NULL,
    receiver_id TEXT NOT NULL,
    type       TEXT DEFAULT 'text',               -- text / image
    content    TEXT,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_messages_id ON messages(id);
CREATE TABLE IF NOT EXISTS moments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    text       TEXT DEFAULT '',
    created_at INTEGER
);
CREATE TABLE IF NOT EXISTS moment_images (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    moment_id INTEGER NOT NULL,
    path      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    owner_id   TEXT NOT NULL,
    created_at INTEGER
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL,
    user_id  TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS last_seen (
    user_id TEXT PRIMARY KEY,
    ts      INTEGER
);
"""

ONLINE_WINDOW = 60  # 秒：60s 内有轮询视为在线


class Database:
    """SQLite 封装（单连接 + 互斥锁，适配 ThreadingHTTPServer 多线程）。"""

    def __init__(self, path: str = DB_FILE):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            for alter in ("ALTER TABLE users ADD COLUMN display_name TEXT",
                          "ALTER TABLE users ADD COLUMN avatar TEXT",
                          "ALTER TABLE friends ADD COLUMN remark TEXT DEFAULT ''"):
                try:
                    self._conn.execute(alter)
                except sqlite3.OperationalError:
                    pass  # 字段已存在
            self._conn.commit()
        logger.info("数据库就绪: %s", path)

    # ── 用户 ────────────────────────────────────────────
    def insert_user(self, uid: str, username: str, email: str,
                    salt_hex: str, password_hash: str, created_at: int) -> bool:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO users (id, username, email, salt, password_hash,"
                    " disabled, created_at) VALUES (?,?,?,?,?,0,?)",
                    (uid, username, email or None, salt_hex, password_hash, created_at))
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_user(self, uid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None

    def all_users(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # 允许动态更新的用户列（防未来调用方传入用户可控键名造成 SQL 注入）
    _USER_COLS = {"display_name", "avatar", "salt", "password_hash",
                  "disabled", "email", "last_login_at", "last_login_ip"}

    def update_user(self, uid: str, **fields) -> bool:
        fields = {k: v for k, v in fields.items() if k in self._USER_COLS}
        if not fields:
            return False
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE users SET {cols} WHERE id=?",
                (*fields.values(), uid))
            self._conn.commit()
        return cur.rowcount > 0

    def delete_user(self, uid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM users WHERE id=?", (uid,))
            self._conn.execute("DELETE FROM friends WHERE owner_id=? OR friend_id=?",
                               (uid, uid))
            self._conn.commit()
        return cur.rowcount > 0

    def next_user_id(self) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(CAST(id AS INTEGER)) AS m FROM users").fetchone()
            m = row["m"] or 0
            return f"{m + 1:08d}"

    # ── 好友关系 ────────────────────────────────────────
    def add_friend_request(self, owner_id: str, friend_id: str) -> tuple[bool, str]:
        """owner 向 friend 发起请求。已接受/已 pending 返回相应错误。"""
        if owner_id == friend_id:
            return False, "不能添加自己"
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM friends WHERE owner_id=? AND friend_id=?",
                (owner_id, friend_id)).fetchone()
            rev = self._conn.execute(
                "SELECT status FROM friends WHERE owner_id=? AND friend_id=?",
                (friend_id, owner_id)).fetchone()
            if rev and rev["status"] == "pending":
                # 对方恰好也请求了我 → 直接纳为好友
                self._set_friend(owner_id, friend_id, "accepted")
                self._set_friend(friend_id, owner_id, "accepted")
                return True, "matched"
            if row and row["status"] == "accepted":
                return False, "已经是好友"
            self._set_friend(owner_id, friend_id, "pending")
        return True, ""

    def _set_friend(self, owner_id: str, friend_id: str, status: str):
        self._conn.execute(
            "INSERT INTO friends (owner_id, friend_id, status, created_at)"
            " VALUES (?,?,?,?) ON CONFLICT(owner_id, friend_id)"
            " DO UPDATE SET status=excluded.status",
            (owner_id, friend_id, status, int(time.time())))

    def accept_friend(self, requester_id: str, uid: str) -> bool:
        """uid 同意 requester 的请求：双向 accepted。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM friends WHERE owner_id=? AND friend_id=?"
                " AND status='pending'", (requester_id, uid)).fetchone()
            if not row:
                return False
            self._set_friend(requester_id, uid, "accepted")
            self._set_friend(uid, requester_id, "accepted")
            self._conn.commit()
        return True

    def reject_friend(self, requester_id: str, uid: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM friends WHERE owner_id=? AND friend_id=?"
                " AND status='pending'", (requester_id, uid))
            self._conn.commit()
        return cur.rowcount > 0

    def remove_friend(self, uid: str, friend_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM friends WHERE (owner_id=? AND friend_id=?)"
                " OR (owner_id=? AND friend_id=?)",
                (uid, friend_id, friend_id, uid))
            self._conn.commit()
        return cur.rowcount > 0

    def friend_rows(self, uid: str) -> list[dict]:
        """[{friend_id, remark}]（按插入时间倒序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT friend_id, COALESCE(remark,'') AS remark FROM friends"
                " WHERE owner_id=? AND status='accepted' ORDER BY created_at DESC",
                (uid,)).fetchall()
        return [dict(r) for r in rows]

    def update_remark(self, owner_id: str, friend_id: str, remark: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE friends SET remark=? WHERE owner_id=? AND friend_id=?",
                (remark, owner_id, friend_id))
            self._conn.commit()
        return cur.rowcount > 0

    def friend_ids(self, uid: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT friend_id FROM friends WHERE owner_id=? AND status='accepted'",
                (uid,)).fetchall()
        return [r["friend_id"] for r in rows]

    def pending_requesters(self, uid: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT owner_id FROM friends WHERE friend_id=? AND status='pending'",
                (uid,)).fetchall()
        return [r["owner_id"] for r in rows]

    # ── 消息 ────────────────────────────────────────────
    def add_message(self, sender_id: str, receiver_id: str,
                    mtype: str, content: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (sender_id, receiver_id, type, content, created_at)"
                " VALUES (?,?,?,?,?)",
                (sender_id, receiver_id, mtype, content, int(time.time())))
            self._conn.commit()
        return cur.lastrowid

    def messages_since(self, uid: str, since_id: int, limit: int = 200) -> list[dict]:
        recv = ["G" + g for g in self.group_ids(uid)] + [uid]
        placeholders = ",".join("?" * len(recv))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM messages WHERE id>? AND"
                f" (sender_id=? OR receiver_id IN ({placeholders}))"
                f" ORDER BY id LIMIT ?",
                (since_id, uid, *recv, limit)).fetchall()
        return [dict(r) for r in rows]

    def last_message(self, uid: str, other_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE"
                " (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)"
                " ORDER BY id DESC LIMIT 1",
                (uid, other_id, other_id, uid)).fetchone()
        return dict(row) if row else None

    def find_messages_by_content(self, content: str) -> list[dict]:
        """按消息内容（图片相对路径）查收发双方，供文件取回按接收关系授权。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT sender_id, receiver_id FROM messages WHERE content=?",
                (content,)).fetchall()
        return [dict(r) for r in rows]

    def unread_count(self, uid: str, other_id: str, since_ts: int = 0) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE sender_id=?"
                " AND receiver_id=? AND created_at>?",
                (other_id, uid, since_ts)).fetchone()
        return row["c"]

    # ── 群聊 ────────────────────────────────────────────
    def create_group(self, owner_id: str, name: str, member_ids: list) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO groups (name, owner_id, created_at) VALUES (?,?,?)",
                (name, owner_id, int(time.time())))
            gid = cur.lastrowid
            ids = {owner_id, *member_ids}
            for m in ids:
                self._conn.execute(
                    "INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?,?)",
                    (gid, m))
            self._conn.commit()
        return gid

    def group_ids(self, uid: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT group_id FROM group_members WHERE user_id=?", (uid,)).fetchall()
        return [str(r["group_id"]) for r in rows]

    def group_info(self, gid: int) -> dict | None:
        with self._lock:
            g = self._conn.execute(
                "SELECT * FROM groups WHERE id=?", (gid,)).fetchone()
            if not g:
                return None
            members = self._conn.execute(
                "SELECT user_id FROM group_members WHERE group_id=?", (gid,)).fetchall()
        return {**dict(g), "members": [m["user_id"] for m in members]}

    def is_group_member(self, gid: int, uid: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
                (gid, uid)).fetchone()
        return bool(row)

    def add_group_members(self, gid: int, ids: list) -> int:
        with self._lock:
            n = 0
            for i in ids:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?,?)",
                    (gid, i))
                n += cur.rowcount
            self._conn.commit()
        return n

    def quit_group(self, gid: int, uid: str) -> str:
        """返回 'quit' / 'disbanded' / ''。"""
        with self._lock:
            g = self._conn.execute(
                "SELECT owner_id FROM groups WHERE id=?", (gid,)).fetchone()
            if not g:
                return ""
            self._conn.execute(
                "DELETE FROM group_members WHERE group_id=? AND user_id=?",
                (gid, uid))
            if g["owner_id"] == uid:
                self._conn.execute("DELETE FROM group_members WHERE group_id=?", (gid,))
                self._conn.execute("DELETE FROM groups WHERE id=?", (gid,))
                self._conn.commit()
                return "disbanded"
            self._conn.commit()
        return "quit"

    # ── 朋友圈 ──────────────────────────────────────────
    def add_moment(self, uid: str, text: str, images: list[str]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO moments (user_id, text, created_at) VALUES (?,?,?)",
                (uid, text, int(time.time())))
            for p in images:
                self._conn.execute(
                    "INSERT INTO moment_images (moment_id, path) VALUES (?,?)",
                    (cur.lastrowid, p))
            self._conn.commit()
        return cur.lastrowid

    def moments_feed(self, uid: str, limit: int = 100) -> list[dict]:
        """好友 + 自己的动态（倒序），附带作者信息与图片。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.*, u.username, u.display_name FROM moments m"
                " JOIN users u ON u.id=m.user_id"
                " WHERE m.user_id=? OR m.user_id IN"
                " (SELECT friend_id FROM friends WHERE owner_id=? AND status='accepted')"
                " ORDER BY m.id DESC LIMIT ?",
                (uid, uid, limit)).fetchall()
            feed = []
            for r in rows:
                imgs = self._conn.execute(
                    "SELECT path FROM moment_images WHERE moment_id=? ORDER BY id",
                    (r["id"],)).fetchall()
                feed.append({**dict(r), "images": [i["path"] for i in imgs]})
        return feed

    def moment_of(self, uid: str, moment_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM moments WHERE id=? AND user_id=?",
                (moment_id, uid)).fetchone()
        return dict(row) if row else None

    def delete_moment(self, uid: str, moment_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM moments WHERE id=? AND user_id=?", (moment_id, uid))
            self._conn.execute(
                "DELETE FROM moment_images WHERE moment_id=?", (moment_id,))
            self._conn.commit()
        return cur.rowcount > 0

    # ── 在线状态 ────────────────────────────────────────
    def touch_online(self, uid: str):
        with self._lock:
            self._conn.execute(
                "INSERT INTO last_seen (user_id, ts) VALUES (?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET ts=excluded.ts",
                (uid, int(time.time())))
            self._conn.commit()

    def is_online(self, uid: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT ts FROM last_seen WHERE user_id=?", (uid,)).fetchone()
        return bool(row) and time.time() - row["ts"] <= ONLINE_WINDOW


# 模块级单例
DB = Database()
