# -*- coding: utf-8 -*-
"""好友（基于服务器中转的聊天，微信式布局）

- 左侧：好友请求区 + 会话列表（在线状态/未读），顶部添加好友（按 ID 或用户名）
- 右侧：聊天区（自己右侧主题色气泡、对方左侧灰色气泡、图片内联）
- 0.8 秒轮询服务器拉取新消息与好友请求；对方同意后才能互发消息
"""
import html
import logging
import os

from PIL import Image as PILImage
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import (QImage, QPixmap, QTextCursor, QTextImageFormat,
                           QColor, QTextCharFormat, QTextDocument,
                           QTextBlockFormat)
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QStackedWidget,
                               QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, CaptionLabel, FluentIcon as FIF, InfoBar,
                            InfoBarPosition, LineEdit, ListWidget, MessageBoxBase,
                            PrimaryPushButton, PushButton, SubtitleLabel, TextEdit,
                            TitleLabel, ToolButton, StrongBodyLabel)

from src_ui import cfg
from src_ui import RUNTIME
from src import UPDATE_SERVER
from src import auth_client
from src.net_config import POLL_INTERVAL

module_logger = logging.getLogger("flu_widget.friends")

THEME = "#009faa"
BUBBLE_OTHER = "#f0f0f0"
IMG_RETRY_PREFIX = "srvimg-retry:"
IMG_RETRY_TEXT = "[图片加载失败，点击重试]"


def _token() -> str:
    return (cfg.auth_token.value or RUNTIME.get("token") or "").strip()


def _my_uid() -> str:
    return (cfg.auth_user_id.value or RUNTIME.get("user_id") or "").strip()


def _now_str(ts: int) -> str:
    import time
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else ""


def _decode_image(data: bytes) -> QImage | None:
    """解码图片字节流。QImage 不支持的格式回落 PIL 再转 PNG。"""
    if not data:
        return None
    img = QImage.fromData(data)
    if not img.isNull():
        return img
    try:
        import io
        pil = PILImage.open(io.BytesIO(data))
        if pil.mode not in ("RGB", "RGBA"):
            pil = pil.convert("RGB")
        buf = io.BytesIO()
        pil.save(buf, "PNG")
        img2 = QImage.fromData(buf.getvalue())
        if not img2.isNull():
            return img2
    except Exception:
        pass
    return None


class _Task(QThread):
    """后台执行 fn()，结果经信号回 UI。"""
    done = Signal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:
            module_logger.error(f"后台任务异常: {e}")
            self.done.emit(None)


class ChatInput(TextEdit):
    """聊天输入：回车发送，Shift+回车换行。"""
    send_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ChatTextEdit(TextEdit):
    """聊天展示区。PySide6 6.9 移除了 QTextEdit.anchorClicked 信号，
    这里通过 mousePressEvent + anchorAt() 自派发锚点点击事件。"""
    anchor_clicked = Signal(str)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            anchor = self.anchorAt(event.pos())
            if anchor:
                self.anchor_clicked.emit(anchor)
        super().mousePressEvent(event)


class AddFriendDialog(MessageBoxBase):
    """添加好友：按 8 位 ID 或用户名搜索。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("添加好友")
        self.targetEdit = LineEdit()
        self.targetEdit.setPlaceholderText("对方用户ID（8位）或用户名")
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel("对方需要同意后才能成为好友"))
        self.viewLayout.addWidget(self.targetEdit)
        self.yesButton.setText("发送请求")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(420)


class GroupCreateDialog(MessageBoxBase):
    """建群 / 邀请成员：输入群名（可留空）+ 勾选好友。"""

    def __init__(self, friend_names: list, parent=None, title="创建群聊"):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(title)
        self.groupNameEdit = LineEdit()
        self.groupNameEdit.setPlaceholderText("群聊名称")
        from qfluentwidgets import CheckableMenu  # noqa: F401
        self.memberList = ListWidget()
        for name in friend_names:
            from PySide6.QtWidgets import QListWidgetItem as _Item
            it = _Item(name)
            it.setCheckState(Qt.CheckState.Unchecked)
            self.memberList.addItem(it)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel("勾选要加入群聊的好友"))
        self.viewLayout.addWidget(self.memberList)
        self.viewLayout.addWidget(CaptionLabel("群聊名称（仅群主可见改名功能）"))
        self.viewLayout.addWidget(self.groupNameEdit)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(420)
        self.widget.setMinimumHeight(380)


class RemarkDialog(MessageBoxBase):
    """修改好友备注。"""

    def __init__(self, friend: dict, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("修改备注")
        self.remarkEdit = LineEdit()
        self.remarkEdit.setPlaceholderText("备注（留空清除）")
        self.remarkEdit.setText(friend.get("remark") or
                                friend.get("display_name") or "")
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel(f"好友：{friend.get('display_name') or friend.get('username', '')}"))
        self.viewLayout.addWidget(self.remarkEdit)
        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(420)


class FriendsInterface(QWidget):
    """好友主界面。"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.friends: list[dict] = []            # [{id, username, online, last}]
        self.groups: list[dict] = []             # [{gid, name, owner_id, members}]
        self.requests: list[dict] = []           # [{id, username}]
        self.history: dict = {}                  # 会话键(friend_id 或 Ggid) -> [msg...]
        self.unread: dict = {}                   # 会话键 -> n
        self._avatar_cache: dict = {}            # 头像相对路径 -> QIcon
        self.current_chat = ""                   # 当前打开的会话键
        self.last_msg_id = 0
        self._poll_in_flight = False
        self._chat_gen = 0
        

        self._build_ui()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_tick)
        self._poll_timer.start(POLL_INTERVAL)
        self._poll_tick()

    # ── UI ─────────────────────────────────────────────
    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ── 左：会话列表 ──
        left = QFrame()
        left.setFixedWidth(300)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)

        head_row = QHBoxLayout()
        head_row.addWidget(TitleLabel("好友"))
        head_row.addStretch(1)
        self.addBtn = ToolButton(FIF.ADD)
        self.addBtn.setToolTip("添加好友")
        self.addBtn.clicked.connect(self._on_add_friend)
        head_row.addWidget(self.addBtn)
        self.refreshBtn = ToolButton(FIF.SYNC)
        self.refreshBtn.setToolTip("刷新")
        self.refreshBtn.clicked.connect(self._refresh_friends)
        head_row.addWidget(self.refreshBtn)
        ll.addLayout(head_row)

        self.requestsBox = QWidget()
        self.requestsLayout = QVBoxLayout(self.requestsBox)
        self.requestsLayout.setContentsMargins(0, 0, 0, 0)
        self.requestsLayout.setSpacing(4)
        self.requestsBox.setMaximumHeight(110)
        ll.addWidget(self.requestsBox)

        # ── 好友分区（可收起，固定高度、位置稳定） ──
        friends_header = QHBoxLayout()
        self.friendsHeaderLabel = StrongBodyLabel("好友")
        friends_header.addWidget(self.friendsHeaderLabel)
        friends_header.addStretch(1)
        self.friendsToggle = ToolButton(FIF.CARE_DOWN_SOLID)
        self.friendsToggle.setFixedSize(24, 24)
        self.friendsToggle.clicked.connect(
            lambda: self._toggle_section(self.session_stack, self.friendsToggle))
        friends_header.addWidget(self.friendsToggle)
        ll.addLayout(friends_header)

        self.session_list = ListWidget()
        self.session_list.itemDoubleClicked.connect(self._on_session_activated)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._show_session_menu)
        self.session_stack = QStackedWidget()
        self.session_stack.setFixedHeight(300)
        self.session_stack.addWidget(self.session_list)
        self.session_stack.addWidget(self._collapsed_page())
        ll.addWidget(self.session_stack)

        # 弹性占位：无论收起/展开，好友区保持在上方、群聊区贴在下方
        ll.addStretch(1)

        # ── 分界线 ──
        from qfluentwidgets import HorizontalSeparator
        ll.addWidget(HorizontalSeparator())

        # ── 群聊分区（可收起，贴底、位置稳定） ──
        groups_header = QHBoxLayout()
        self.groupsHeaderLabel = StrongBodyLabel("群聊")
        groups_header.addWidget(self.groupsHeaderLabel)
        groups_header.addStretch(1)
        btn_group_add = ToolButton(FIF.ADD)
        btn_group_add.setToolTip("建群（从好友中选择成员）")
        btn_group_add.clicked.connect(self._on_create_group)
        groups_header.addWidget(btn_group_add)
        self.groupsToggle = ToolButton(FIF.CARE_DOWN_SOLID)
        self.groupsToggle.setFixedSize(24, 24)
        self.groupsToggle.clicked.connect(
            lambda: self._toggle_section(self.groups_stack, self.groupsToggle))
        groups_header.addWidget(self.groupsToggle)
        ll.addLayout(groups_header)

        self.groups_list = ListWidget()
        self.groups_list.itemDoubleClicked.connect(self._on_group_activated)
        self.groups_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.groups_list.customContextMenuRequested.connect(self._show_group_menu)
        self.groups_stack = QStackedWidget()
        self.groups_stack.setFixedHeight(160)
        self.groups_stack.addWidget(self.groups_list)
        self.groups_stack.addWidget(self._collapsed_page())
        ll.addWidget(self.groups_stack)
        layout.addWidget(left)

        # ── 右：聊天区 ──
        self.right_stack = QStackedWidget()
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(2)
        ev.addWidget(TitleLabel("好友聊天"), 0, Qt.AlignmentFlag.AlignHCenter)
        ev.addWidget(BodyLabel("选择左侧好友开始聊天，或点击右上角 + 添加好友"),
                     0, Qt.AlignmentFlag.AlignHCenter)
        ev.addStretch(3)
        self.right_stack.addWidget(empty)          # 0 = 空态
        self.chat_view = self._make_chat_view()
        self.right_stack.addWidget(self.chat_view)  # 1 = 聊天
        layout.addWidget(self.right_stack, 1)

    def _make_chat_view(self) -> QWidget:
        view = QWidget()
        v = QVBoxLayout(view)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        header = QHBoxLayout()
        self.chatName = TitleLabel("")
        header.addWidget(self.chatName)
        header.addStretch(1)
        self.chatOnline = CaptionLabel("")
        header.addWidget(self.chatOnline)
        v.addLayout(header)

        self.chat_area = ChatTextEdit()
        self.chat_area.setReadOnly(True)
        self.chat_area.anchor_clicked.connect(self._on_chat_anchor_clicked)
        v.addWidget(self.chat_area, 1)

        input_row = QHBoxLayout()
        self.imgBtn = ToolButton(FIF.PHOTO)
        self.imgBtn.setToolTip("发送图片")
        self.imgBtn.clicked.connect(self._send_image)
        input_row.addWidget(self.imgBtn)
        self.input = ChatInput()
        self.input.setPlaceholderText("输入消息…（回车发送）")
        self.input.setFixedHeight(72)
        self.input.send_requested.connect(self._send_text)
        input_row.addWidget(self.input, 1)
        self.sendBtn = PrimaryPushButton("发送")
        self.sendBtn.setFixedWidth(96)
        self.sendBtn.clicked.connect(self._send_text)
        input_row.addWidget(self.sendBtn)
        v.addLayout(input_row)
        return view

    # ── 数据轮询 ────────────────────────────────────────
    def _poll_tick(self):
        token = _token()
        if not token or self._poll_in_flight:
            return
        self._poll_in_flight = True

        def _work():
            r1 = auth_client.chat_poll(UPDATE_SERVER, token, self.last_msg_id)
            r2 = auth_client.friends_list(UPDATE_SERVER, token)
            r3 = auth_client.groups_list(UPDATE_SERVER, token)
            return (r1, r2, r3)

        def _done(result):
            self._poll_in_flight = False
            self._on_polled(result)

        self._run(_work, _done)

    def _on_polled(self, result):
        try:
            if not result or len(result) != 3:
                return
            (ok1, d1), (ok2, d2), (ok3, d3) = result
            if ok1:
                for m in d1.get("messages", []):
                    self._absorb_message(m)
                reqs = d1.get("friend_requests", [])
                if reqs != self.requests:
                    self.requests = reqs
                    self._render_requests()
                    if reqs:
                        InfoBar.info("好友请求",
                                     f"{reqs[0]['username']} 请求添加你为好友",
                                     parent=self, position=InfoBarPosition.TOP,
                                     duration=4000)
            if ok2:
                self.friends = d2.get("friends", [])
                self._render_sessions()
            if ok3:
                self.groups = d3.get("groups", [])
                self._render_groups()
        except Exception as e:
            module_logger.error(f"poll 处理异常: {e}")

    def _absorb_message(self, m: dict):
        if m["id"] <= self.last_msg_id:
            return
        self.last_msg_id = m["id"]
        other = m["receiver_id"] if m["sender_id"] == _my_uid() else m["sender_id"]
        self.history.setdefault(other, []).append(m)
        if other == self.current_chat:
            self._append_message(m)
        else:
            self.unread[other] = self.unread.get(other, 0) + 1
            self._render_sessions()

    def _run(self, fn, cb):
        # 任务由 parent 持有，deleteLater 后不再保留 Python 引用，
        # 从根本上避免 "Internal C++ object already deleted"
        task = _Task(fn, self)
        task.done.connect(cb)
        task.finished.connect(task.deleteLater)
        task.start()

    # ── 会话列表 / 请求 ─────────────────────────────────
    def _on_session_activated(self, item):
        fid = item.data(Qt.ItemDataRole.UserRole)
        self.open_chat(fid)

    def _on_group_activated(self, item):
        gid = item.data(Qt.ItemDataRole.UserRole)
        self.open_chat(gid)

    def _render_sessions(self):
        self.friendsHeaderLabel.setText(f"好友 ({len(self.friends)})")
        current_row = self.session_list.currentRow()
        self.session_list.clear()
        for f in self.friends:
            unread = self.unread.get(f["id"], 0)
            dot = "●" if f.get("online") else "○"
            mark = f"（{unread} 条新消息）" if unread else ""
            last = (f.get("last") or {})
            preview = ""
            if last:
                body = last.get("content", "")
                if last.get("type") == "image":
                    body = "[图片]"
                who = "我: " if last.get("sender_id") == _my_uid() else ""
                preview = f"  {who}{body[:16]}"
            disp = f.get("remark") or f.get("display_name") or f["username"]
            item = QListWidgetItem(f"{dot} {disp}{mark}{preview}")
            avatar_icon = self._friend_avatar(f)
            if avatar_icon is not None:
                item.setIcon(avatar_icon)
            item.setData(Qt.ItemDataRole.UserRole, f["id"])
            if f["id"] == self.current_chat:
                from PySide6.QtGui import QFont
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.session_list.addItem(item)
        if 0 <= current_row < self.session_list.count():
            self.session_list.setCurrentRow(current_row)

    def _render_groups(self):
        self.groupsHeaderLabel.setText(f"群聊 ({len(self.groups)})")
        self.groups_list.clear()
        for g in self.groups:
            unread = self.unread.get("G" + g["gid"], 0)
            mark = f"（{unread} 条新消息）" if unread else ""
            item = QListWidgetItem(f"[群] {g['name']}（{len(g.get('members', []))}人）{mark}")
            item.setData(Qt.ItemDataRole.UserRole, "G" + g["gid"])
            self.groups_list.addItem(item)

    def _show_session_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        item = self.session_list.itemAt(pos)
        if not item:
            return
        fid = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        act_remark = menu.addAction("修改备注")
        act_del = menu.addAction("删除好友")
        chosen = menu.exec(self.session_list.mapToGlobal(pos))
        if chosen == act_remark:
            self._on_set_remark(fid)
            return
        if chosen != act_del:
            return
        token = _token()

        def _done(r):
            if r and r[0]:
                self.friends = [f for f in self.friends if f["id"] != fid]
                self.history.pop(fid, None)
                self.unread.pop(fid, None)
                if self.current_chat == fid:
                    self.current_chat = ""
                    self.right_stack.setCurrentIndex(0)
                self._render_sessions()
                InfoBar.info("已删除", "好友已移除", parent=self,
                             position=InfoBarPosition.TOP, duration=2500)

        self._run(lambda: auth_client.friends_remove(UPDATE_SERVER, token, fid), _done)

    # ── 建群 / 群操作 ───────────────────────────────────
    def _on_create_group(self):
        token = _token()
        if not token:
            return
        online_friends = self.friends
        dlg = GroupCreateDialog([f["username"] for f in online_friends], self)
        if not dlg.exec():
            return
        name = dlg.groupNameEdit.text().strip() or "未命名群聊"
        member_ids = [online_friends[i]["id"] for i in range(dlg.memberList.count())
                      if dlg.memberList.item(i).checkState() == Qt.CheckState.Checked]

        def _done(r):
            if r and r[0]:
                InfoBar.success("建群成功", f"群号 {r[1].get('gid')}", parent=self,
                                position=InfoBarPosition.TOP, duration=3000)
                self._poll_tick()
            else:
                err = r[1].get("error", "建群失败") if r and isinstance(r, tuple) else "网络异常"
                InfoBar.error("建群失败", err, parent=self,
                              position=InfoBarPosition.TOP, duration=4000)

        self._run(lambda: auth_client.groups_create(UPDATE_SERVER, token, name,
                                                    member_ids), _done)

    def _show_group_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        item = self.groups_list.itemAt(pos)
        if not item:
            return
        gid = item.data(Qt.ItemDataRole.UserRole)
        group = next((g for g in self.groups if "G" + g["gid"] == gid), None)
        if not group:
            return
        is_owner = group.get("owner_id") == _my_uid()
        menu = QMenu(self)
        if is_owner:
            act_invite = menu.addAction("邀请好友入群")
        act_quit = menu.addAction("退出群聊" if not is_owner else "解散群聊")
        chosen = menu.exec(self.groups_list.mapToGlobal(pos))
        token = _token()
        if not token:
            return
        if is_owner and chosen == act_invite:
            self._invite_to_group(gid)
        elif chosen == act_quit:
            def _done(r):
                res = r[1].get("result") if r and r[0] else None
                if res == "disbanded":
                    InfoBar.info("已解散", "群聊已解散", parent=self,
                                 position=InfoBarPosition.TOP, duration=3000)
                elif r and r[0]:
                    InfoBar.info("已退出", "你已退出该群聊", parent=self,
                                 position=InfoBarPosition.TOP, duration=3000)
                self._poll_tick()
            self._run(lambda: auth_client.groups_quit(UPDATE_SERVER, token, gid[1:]),
                      _done)

    def _invite_to_group(self, gid_key: str):
        token = _token()
        gid = gid_key[1:]
        friends = self.friends
        if not friends:
            InfoBar.warning("提示", "还没有好友可以邀请", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return
        dlg = GroupCreateDialog([f["username"] for f in friends], self,
                                title="邀请好友入群")
        if not dlg.exec():
            return
        member_ids = [friends[i]["id"] for i in range(dlg.memberList.count())
                      if dlg.memberList.item(i).checkState() == Qt.CheckState.Checked]
        if not member_ids:
            return
        self._run(lambda: auth_client.groups_invite(UPDATE_SERVER, token, gid,
                                                    member_ids),
                  lambda r: self._poll_tick())

    @staticmethod
    def _friend_display_name(f: dict) -> str:
        return f.get("remark") or f.get("display_name") or f.get("username", "")

    def _friend_avatar(self, f: dict):
        rel = f.get("avatar") or ""
        if not rel:
            return None
        if rel in self._avatar_cache:
            return self._avatar_cache[rel]
        token = _token()
        if not token:
            return None
        url = f"{UPDATE_SERVER.rstrip('/')}/api/files?p={rel}&token={token}"

        def _work():
            return auth_client.download_bytes(url)

        def _done(data):
            if not data:
                return
            img = _decode_image(data)
            if img is None:
                return
            from PySide6.QtGui import QIcon
            icon = QIcon(QPixmap.fromImage(img.scaled(
                36, 36, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)))
            self._avatar_cache[rel] = icon
            self._render_sessions()

        self._run(_work, _done)
        return None

    def _on_set_remark(self, fid: str):
        friend = next((f for f in self.friends if f["id"] == fid), None)
        if not friend:
            return
        token = _token()
        dlg = RemarkDialog(friend, self)
        if not dlg.exec():
            return
        remark = dlg.remarkEdit.text().strip()

        def _done(r):
            if r and r[0]:
                for f in self.friends:
                    if f["id"] == fid:
                        f["remark"] = remark
                self._render_sessions()
                InfoBar.success("备注已保存", "", parent=self,
                                position=InfoBarPosition.TOP, duration=2000)

        self._run(lambda: auth_client.friends_remark(UPDATE_SERVER, token, fid,
                                                     remark), _done)

    def _render_requests(self):
        while self.requestsLayout.count():
            item = self.requestsLayout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not self.requests:
            # 无请求时完全隐藏，保证列表紧贴「好友」标题行
            self.requestsBox.setVisible(False)
            return
        self.requestsBox.setVisible(True)
        self.requestsLayout.addWidget(StrongBodyLabel("好友请求"))
        for r in self.requests:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(2, 0, 2, 0)
            rl.addWidget(BodyLabel(f"{r.get('display_name') or r.get('username', '')} ({r['id']})"), 1)
            ok_btn = PushButton("同意")
            ok_btn.clicked.connect(lambda _=False, i=r["id"]: self._accept_request(i))
            rl.addWidget(ok_btn)
            no_btn = PushButton("拒绝")
            no_btn.clicked.connect(lambda _=False, i=r["id"]: self._reject_request(i))
            rl.addWidget(no_btn)
            self.requestsLayout.addWidget(row)

    @staticmethod
    def _collapsed_page():
        page = QLabel("已收起 · 点击右上角箭头展开")
        page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page.setStyleSheet("color: #999; background: transparent;")
        return page

    def _toggle_section(self, stack: QStackedWidget, btn: ToolButton):
        collapsed = stack.currentIndex() == 1
        stack.setCurrentIndex(0 if collapsed else 1)
        btn.setIcon(FIF.CARE_DOWN_SOLID if collapsed else FIF.CARE_RIGHT_SOLID)

    def open_chat(self, chat_key: str):
        """chat_key: 好友 ID 或 'G{gid}'。"""
        self._chat_gen += 1
        if chat_key.startswith("G"):
            group = next((g for g in self.groups if g["gid"] == chat_key[1:]), None)
            if not group:
                return
            self.current_chat = chat_key
            self.unread.pop(chat_key, None)
            self.chatName.setText(f"{group['name']}（{len(group.get('members', []))}人）")
            self.chatOnline.setText("群聊")
            self.chat_area.clear()
            for m in self.history.get(chat_key, []):
                self._append_message(m)
            self.right_stack.setCurrentIndex(1)
            self._render_sessions()
            return
        friend = next((f for f in self.friends if f["id"] == chat_key), None)
        if not friend:
            return
        self.current_chat = chat_key
        self.unread.pop(chat_key, None)
        self.chatName.setText(self._friend_display_name(friend))
        self.chatOnline.setText("在线" if friend.get("online") else "离线")
        self.chat_area.clear()
        for m in self.history.get(chat_key, []):
            self._append_message(m)
        self.right_stack.setCurrentIndex(1)
        self._render_sessions()

    # ── 消息渲染 ────────────────────────────────────────
    def _append_message(self, m: dict):
        mine = m["sender_id"] == _my_uid()
        is_group = str(m.get("receiver_id", "")).startswith("G")
        if is_group or not mine:
            who = m.get("sender_username") or m["sender_id"]
        else:
            who = "我"
        body = html.escape(m.get("content", ""))
        if m.get("type") == "image":
            cursor = self.chat_area.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cfmt = QTextCharFormat()
            cfmt.setForeground(QColor("#888888"))
            cursor.insertText(f"{who}  {_now_str(m.get('created_at', 0))}\n", cfmt)
            anchor_pos = cursor.position()
            end_cursor = self.chat_area.textCursor()
            end_cursor.movePosition(QTextCursor.MoveOperation.End)
            end_cursor.insertText("\n")
            self._insert_image_async(m["content"], int(m["id"]), anchor_pos,
                                     self._chat_gen, self.current_chat)
            return
        align = (Qt.AlignmentFlag.AlignRight if mine
                 else Qt.AlignmentFlag.AlignLeft)
        bg = THEME if mine else BUBBLE_OTHER
        fg = "#ffffff" if mine else "#222222"
        cursor = self.chat_area.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        block = QTextBlockFormat()
        block.setAlignment(align)
        cursor.insertBlock(block)
        cursor.insertHtml(
            f'<span style="background-color:{bg}; color:{fg};"> {body} </span>'
            f'<span style="color:#aaaaaa; font-size:small;">  {_now_str(m.get("created_at", 0))}</span>')
        cursor.insertBlock(QTextBlockFormat())
        self.chat_area.setTextCursor(cursor)
        self.chat_area.ensureCursorVisible()

    def _insert_image_async(self, rel_path: str, msg_id: int, position: int,
                            chat_gen: int, chat_key: str):
        """异步下载图片，按指定文档位置插入；失败显示可重试占位。

        position 是消息渲染时已固定的文档偏移，保证后续到达的 text
        消息不会把图片推到错误位置。chat_gen / chat_key 用于切到其他
        会话时丢弃过期的回调。
        """
        token = _token()
        url = f"{UPDATE_SERVER.rstrip('/')}/api/files?p={rel_path}&token={token}"

        def _load():
            return auth_client.download_bytes(url)

        def _done(data):
            if chat_gen != self._chat_gen or chat_key != self.current_chat:
                return
            img = _decode_image(data) if data else None
            if img is None:
                self._insert_image_placeholder(rel_path, msg_id, position,
                                               chat_gen, chat_key)
                return
            max_w = 240
            scale = min(1.0, max_w / max(img.width(), 1))
            fmt = QTextImageFormat()
            key = f"srvimg://{rel_path}"
            self.chat_area.document().addResource(
                QTextDocument.ResourceType.ImageResource, QUrl(key), img)
            fmt.setName(key)
            fmt.setWidth(int(img.width() * scale))
            fmt.setHeight(int(img.height() * scale))
            cursor = self.chat_area.textCursor()
            cursor.setPosition(position)
            cursor.insertImage(fmt)
            self.chat_area.setTextCursor(cursor)

        self._run(_load, _done)

    def _insert_image_placeholder(self, rel_path: str, msg_id: int, position: int,
                                  chat_gen: int, chat_key: str):
        """在指定位置插入'图片加载失败，点击重试'可点击占位。"""
        cfmt = QTextCharFormat()
        cfmt.setForeground(QColor("#cc4400"))
        cfmt.setFontUnderline(True)
        cfmt.setAnchor(True)
        cfmt.setAnchorHref(
            f"{IMG_RETRY_PREFIX}{rel_path}|{msg_id}|{position}|{chat_gen}|{chat_key}")
        cursor = self.chat_area.textCursor()
        cursor.setPosition(position)
        cursor.insertText(IMG_RETRY_TEXT, cfmt)

    def _on_chat_anchor_clicked(self, href: str):
        if not href.startswith(IMG_RETRY_PREFIX):
            return
        payload = href[len(IMG_RETRY_PREFIX):]
        parts = payload.split("|")
        if len(parts) != 5:
            return
        rel_path, msg_id_s, position_s, gen_s, chat_key = parts
        try:
            msg_id = int(msg_id_s)
            position = int(position_s)
            stored_gen = int(gen_s)
        except ValueError:
            return
        if stored_gen != self._chat_gen or chat_key != self.current_chat:
            return
        cursor = self.chat_area.textCursor()
        cursor.setPosition(position)
        cursor.setPosition(position + len(IMG_RETRY_TEXT),
                           QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        self._insert_image_async(rel_path, msg_id, position,
                                 self._chat_gen, self.current_chat)

    # ── 发送 ────────────────────────────────────────────
    def _send_text(self):
        token = _token()
        text = self.input.toPlainText().strip()
        if not token or not text or not self.current_chat:
            return
        self.input.clear()
        self._run(lambda: auth_client.chat_send(UPDATE_SERVER, token,
                                                self.current_chat, "text", text),
                  lambda r: None if r and r[0] else self._send_fail(r))

    def _send_fail(self, r):
        if r and isinstance(r, tuple) and r[1].get("error"):
            InfoBar.error("发送失败", r[1]["error"], parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _send_image(self):
        token = _token()
        if not token or not self.current_chat:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return

        def _work():
            try:
                img = PILImage.open(path)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                if max(img.size) > 1600:
                    img.thumbnail((1600, 1600))
                import io
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=85)
                return auth_client.chat_upload(UPDATE_SERVER, token,
                                               self.current_chat,
                                               buf.getvalue(), "jpg")
            except Exception as e:
                return False, {"error": str(e)}

        def _done(r):
            if r and r[0]:
                rel = r[1].get("path", "")
                auth_client.chat_send(UPDATE_SERVER, token, self.current_chat,
                                      "image", rel)
            else:
                self._send_fail(r)

        self._run(_work, _done)

    # ── 好友请求 ────────────────────────────────────────
    def _on_add_friend(self):
        token = _token()
        if not token:
            return
        dlg = AddFriendDialog(self)
        if not dlg.exec():
            return
        target = dlg.targetEdit.text().strip()
        if not target:
            return

        def _done(r):
            if r and r[0]:
                status = r[1].get("status")
                if status == "matched":
                    InfoBar.success("已成为好友", "对方也向你发起了请求，已自动添加",
                                    parent=self, position=InfoBarPosition.TOP, duration=4000)
                else:
                    InfoBar.success("请求已发送", "等待对方同意", parent=self,
                                    position=InfoBarPosition.TOP, duration=4000)
                self._refresh_friends()
            else:
                err = (r[1].get("error", "请求失败") if r and isinstance(r, tuple) else "网络异常")
                InfoBar.error("添加失败", err, parent=self,
                              position=InfoBarPosition.TOP, duration=4000)

        self._run(lambda: auth_client.friends_add(UPDATE_SERVER, token, target), _done)

    def _accept_request(self, fid: str):
        token = _token()

        def _done(r):
            self._poll_tick()
            if r and r[0]:
                InfoBar.success("已同意", "你们现在是好友了", parent=self,
                                position=InfoBarPosition.TOP, duration=3000)

        self._run(lambda: auth_client.friends_accept(UPDATE_SERVER, token, fid), _done)

    def _reject_request(self, fid: str):
        token = _token()
        self._run(lambda: auth_client.friends_reject(UPDATE_SERVER, token, fid),
                  lambda r: self._poll_tick())

    def _refresh_friends(self):
        token = _token()
        if not token:
            return
        self._run(lambda: auth_client.friends_list(UPDATE_SERVER, token),
                  self._on_polled_friends)

    def _on_polled_friends(self, r):
        if r and r[0]:
            self.friends = r[1].get("friends", [])
            self._render_sessions()


class FriendsWidget(QFrame):
    """导航容器。"""

    def __init__(self, name: str, parent=None):
        super().__init__(parent=parent)
        self.setObjectName(name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        self.friendsInterface = FriendsInterface(self)
        layout.addWidget(self.friendsInterface)
