# -*- coding: utf-8 -*-
"""NoMY — 服务管理后台（GUI 版）

PySide6 + qfluentwidgets 实现，调用同进程的 distribute_server 后台线程。

用法：
    python distribute_server_gui.py

功能：
    - 服务控制：启动/停止 HTTP 服务（账号认证 + 更新分发）
    - 用户管理：新建账号 / 重置密码 / 禁用 / 删除
    - 在线会话：查看令牌会话、踢下线
    - 运行日志：实时请求日志
"""
import logging
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import distribute_server as srv   # noqa: E402
from auth_store import USER_STORE, SESSION_STORE, validate_password   # noqa: E402

from PySide6.QtCore import QTimer, Signal   # noqa: E402
from PySide6.QtGui import QIcon   # noqa: E402
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout,   # noqa: E402
                               QHeaderView, QTableWidgetItem, QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel,  # noqa: E402
                            FluentIcon as FIF, FluentWindow, InfoBar, InfoBarPosition,
                            LineEdit, MessageBox, MessageBoxBase, PasswordLineEdit,
                            PrimaryPushButton, PushButton, StrongBodyLabel,
                            SubtitleLabel, SwitchButton, TableWidget, TextBrowser,
                            TitleLabel, ToolButton)

DEFAULT_PORT = 18888


def _exe_dir() -> str:
    """打包 exe（Nuitka onefile）时配置/图标应从 exe 所在目录查找。"""
    if getattr(sys, "frozen", False) or globals().get("__compiled__"):
        for p in (sys.argv[0], sys.executable):
            if p:
                return os.path.dirname(os.path.abspath(p))
    return str(Path(__file__).resolve().parent.parent)


_SCRIPT_ROOT = str(Path(__file__).resolve().parent.parent)

DEFAULT_VERSION_INI = os.path.join(_exe_dir(), "Config", "Version.ini")
if not os.path.isfile(DEFAULT_VERSION_INI):
    DEFAULT_VERSION_INI = os.path.join(_SCRIPT_ROOT, "Config", "Version.ini")

APP_ICON = os.path.join(_exe_dir(), "Config", "image", "menu.ico")
if not os.path.isfile(APP_ICON):
    APP_ICON = os.path.join(_SCRIPT_ROOT, "Config", "image", "menu.ico")


class QtLogHandler(logging.Handler):
    """把日志从服务线程投递到 UI 线程（跨线程信号自动排队）。"""

    def __init__(self, signal):
        super().__init__()
        self._signal = signal
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                            datefmt="%H:%M:%S"))

    def emit(self, record):
        try:
            self._signal.emit(self.format(record))
        except Exception:
            pass


class NewUserDialog(MessageBoxBase):
    """新建账号对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("新建账号")
        self.usernameEdit = LineEdit()
        self.usernameEdit.setPlaceholderText("2-32 位字母/数字/下划线/中文")
        self.usernameEdit.setClearButtonEnabled(True)
        self.passwordEdit = PasswordLineEdit()
        self.passwordEdit.setPlaceholderText("至少 8 位，含两类字符")
        self.emailEdit = LineEdit()
        self.emailEdit.setPlaceholderText("user@example.com（可留空，用于密码找回）")

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel("用户名"))
        self.viewLayout.addWidget(self.usernameEdit)
        self.viewLayout.addWidget(CaptionLabel("密码"))
        self.viewLayout.addWidget(self.passwordEdit)
        self.viewLayout.addWidget(CaptionLabel("邮箱"))
        self.viewLayout.addWidget(self.emailEdit)

        self.yesButton.setText("创建")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(420)

    def validate(self) -> bool:
        username = self.usernameEdit.text().strip()
        password = self.passwordEdit.text()
        from auth_store import validate_username
        err = validate_username(username) or validate_password(password)
        if err:
            InfoBar.warning("输入有误", err, parent=self, position=InfoBarPosition.TOP,
                            duration=3000)
            return False
        return True


class ResetPasswordDialog(MessageBoxBase):
    """重置密码对话框。"""

    def __init__(self, username: str, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(f"重置密码 — {username}")
        self.passwordEdit = PasswordLineEdit()
        self.passwordEdit.setPlaceholderText("输入新密码（至少 8 位，含两类字符）")
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel("新密码"))
        self.viewLayout.addWidget(self.passwordEdit)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(420)

    def validate(self) -> bool:
        err = validate_password(self.passwordEdit.text())
        if err:
            InfoBar.warning("输入有误", err, parent=self, position=InfoBarPosition.TOP,
                            duration=3000)
            return False
        return True


class ServiceControlPage(QWidget):
    """服务控制页。"""

    def __init__(self, window: "ServerMainWindow", parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("serviceControlPage")
        self._build_ui()


    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(12)

        layout.addWidget(TitleLabel("服务控制"))

        # ── 状态卡片 ──
        self.statusLabel = StrongBodyLabel("○ 未启动")
        layout.addWidget(self.statusLabel)
        self.urlLabel = BodyLabel("客户端访问地址：启动后显示")
        self.urlLabel.setWordWrap(True)
        layout.addWidget(self.urlLabel)

        # ── 配置表单 ──
        def add_row(label_text, widget):
            row = QHBoxLayout()
            label = BodyLabel(label_text)
            label.setFixedWidth(120)
            row.addWidget(label)
            row.addWidget(widget, 1)
            layout.addLayout(row)
            return widget

        self.portEdit = LineEdit()
        self.portEdit.setText(str(DEFAULT_PORT))
        add_row("监听端口", self.portEdit)

        self.subnetEdit = LineEdit()
        self.subnetEdit.setPlaceholderText("CIDR，逗号分隔，留空不限制。例: 192.168.1.0/24")
        add_row("允许子网", self.subnetEdit)

        # TLS（可选）：提供证书/私钥后以 HTTPS 服务，公网部署强烈建议
        tls_row = QHBoxLayout()
        self.tlsCertEdit = LineEdit()
        self.tlsCertEdit.setPlaceholderText("证书 PEM 路径（可选，启用 HTTPS）")
        btn_cert = ToolButton(FIF.FOLDER)
        btn_cert.clicked.connect(self._pick_cert)
        tls_row.addWidget(self.tlsCertEdit, 1)
        tls_row.addWidget(btn_cert)
        layout.addLayout(self._labeled_row("TLS 证书", tls_row))

        tlskey_row = QHBoxLayout()
        self.tlsKeyEdit = LineEdit()
        self.tlsKeyEdit.setPlaceholderText("私钥 PEM 路径（与证书配套）")
        btn_key = ToolButton(FIF.FOLDER)
        btn_key.clicked.connect(self._pick_key)
        tlskey_row.addWidget(self.tlsKeyEdit, 1)
        tlskey_row.addWidget(btn_key)
        layout.addLayout(self._labeled_row("TLS 私钥", tlskey_row))

        ini_row = QHBoxLayout()
        self.iniEdit = LineEdit()
        self.iniEdit.setText(DEFAULT_VERSION_INI)
        btn_ini = ToolButton(FIF.FOLDER)
        btn_ini.clicked.connect(self._pick_ini)
        ini_row.addWidget(self.iniEdit, 1)
        ini_row.addWidget(btn_ini)
        layout.addLayout(self._labeled_row("Version.ini", ini_row))

        pkg_row = QHBoxLayout()
        self.pkgEdit = LineEdit()
        self.pkgEdit.setPlaceholderText("更新安装包路径（可选，仅更新分发用）")
        btn_pkg = ToolButton(FIF.FOLDER_ADD)
        btn_pkg.clicked.connect(self._pick_package)
        pkg_row.addWidget(self.pkgEdit, 1)
        pkg_row.addWidget(btn_pkg)
        layout.addLayout(self._labeled_row("安装包", pkg_row))

        ver_row = QHBoxLayout()
        self.versionEdit = LineEdit()
        self.versionEdit.setPlaceholderText("留空则使用 Version.ini 中的版本号")
        btn_read = PushButton("从 INI 读取")
        btn_read.clicked.connect(self._read_version_from_ini)
        ver_row.addWidget(self.versionEdit, 1)
        ver_row.addWidget(btn_read)
        layout.addLayout(self._labeled_row("版本号覆盖", ver_row))

        reg_row = QHBoxLayout()
        reg_label = BodyLabel("开放注册")
        reg_label.setFixedWidth(120)
        self.regSwitch = SwitchButton()
        self.regSwitch.setChecked(USER_STORE.allow_registration)
        self.regSwitch.checkedChanged.connect(self._on_reg_toggle)
        reg_row.addWidget(self.regSwitch)
        reg_row.addStretch(1)
        layout.addLayout(reg_row)

        # ── 操作按钮 ──
        btn_bar = QHBoxLayout()
        self.startBtn = PrimaryPushButton(FIF.PLAY, "启动服务")
        self.startBtn.clicked.connect(self._on_start)
        self.stopBtn = PushButton(FIF.PAUSE, "停止服务")
        self.stopBtn.setEnabled(False)
        self.stopBtn.clicked.connect(self._on_stop)
        btn_bar.addWidget(self.startBtn)
        btn_bar.addWidget(self.stopBtn)
        btn_bar.addStretch(1)
        layout.addLayout(btn_bar)

        layout.addStretch(1)

    @staticmethod
    def _labeled_row(label_text, inner_layout) -> QHBoxLayout:
        row = QHBoxLayout()
        label = BodyLabel(label_text)
        label.setFixedWidth(120)
        row.addWidget(label)
        row.addLayout(inner_layout, 1)
        return row

    def _pick_ini(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 Version.ini",
                                              self.iniEdit.text(), "INI (*.ini)")
        if path:
            self.iniEdit.setText(path)
            srv.STATE.set_version_ini_path(path)
            self._read_version_from_ini()

    def _pick_cert(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 TLS 证书 (PEM)",
                                              self.tlsCertEdit.text(),
                                              "证书 (*.pem *.crt *.cer);;所有文件 (*)")
        if path:
            self.tlsCertEdit.setText(path)

    def _pick_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 TLS 私钥 (PEM)",
                                              self.tlsKeyEdit.text(),
                                              "私钥 (*.pem *.key);;所有文件 (*)")
        if path:
            self.tlsKeyEdit.setText(path)

    def _pick_package(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择安装包", "",
                                              "安装包 (*.exe *.msi);;所有文件 (*)")
        if path:
            self.pkgEdit.setText(path)

    def _read_version_from_ini(self):
        meta = srv.load_version_info(self.iniEdit.text())
        if meta["version"]:
            self.versionEdit.setText(meta["version"])

    def _on_reg_toggle(self, checked: bool):
        USER_STORE.set_allow_registration(checked)
        self.window.show_info("注册开关", "已" + ("开启" if checked else "关闭") + "开放注册")

    def _on_start(self):
        try:
            port = int(self.portEdit.text().strip() or DEFAULT_PORT)
        except ValueError:
            self.window.show_error("启动失败", "端口必须是数字")
            return
        host = "0.0.0.0"

        pkg = self.pkgEdit.text().strip()
        if pkg:
            try:
                srv.STATE.set_package(pkg)
            except Exception as e:
                self.window.show_error("启动失败", f"加载安装包失败：{e}")
                return
        srv.STATE.set_version_ini_path(self.iniEdit.text().strip() or DEFAULT_VERSION_INI)
        override = self.versionEdit.text().strip()
        srv.STATE.set_version_override(override or None)

        allowed = srv._parse_subnets(self.subnetEdit.text())
        tls_cert = self.tlsCertEdit.text().strip()
        tls_key = self.tlsKeyEdit.text().strip()
        if bool(tls_cert) != bool(tls_key):
            self.window.show_error("启动失败", "TLS 证书与私钥必须同时提供（或都留空）")
            return
        ok, err = srv.start_server_in_thread(host, port, allowed,
                                             tls_cert=tls_cert or None,
                                             tls_key=tls_key or None)
        if not ok:
            self.window.show_error("启动失败", err)
            return
        self.window.log_signal_emit(
            f"服务已启动 {host}:{port}" + ("（HTTPS）" if tls_cert else ""))
        self._refresh_status()
        self.startBtn.setEnabled(False)
        self.stopBtn.setEnabled(True)

    def _on_stop(self):
        srv.stop_server_in_thread()
        self._refresh_status()
        self.startBtn.setEnabled(True)
        self.stopBtn.setEnabled(False)

    def _refresh_status(self):
        if srv.is_server_running():
            try:
                port = int(self.portEdit.text().strip() or DEFAULT_PORT)
            except ValueError:
                port = DEFAULT_PORT
            self.statusLabel.setText(f"● 运行中  0.0.0.0:{port}  "
                                     f"认证:{'开' if srv.AUTH_ENABLED else '关'}  "
                                     f"注册:{'开' if USER_STORE.allow_registration else '关'}")
            urls = "\n".join(srv.get_access_urls(port))
            self.urlLabel.setText(f"客户端访问地址：\n{urls}")
        else:
            self.statusLabel.setText("○ 未启动")
            self.urlLabel.setText("客户端访问地址：启动后显示")


class UsersPage(QWidget):
    """用户管理页。"""

    HEADERS = ["ID", "用户名", "邮箱", "创建时间", "最后登录时间", "最后登录 IP", "状态"]

    def __init__(self, window: "ServerMainWindow", parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("usersPage")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(12)

        layout.addWidget(TitleLabel("用户管理"))

        btn_bar = QHBoxLayout()
        btn_refresh = PushButton(FIF.SYNC, "刷新")
        btn_refresh.clicked.connect(self.refresh)
        btn_new = PrimaryPushButton(FIF.ADD, "新建账号")
        btn_new.clicked.connect(self._on_new_user)
        btn_reset = PushButton(FIF.EDIT, "重置密码")
        btn_reset.clicked.connect(self._on_reset_password)
        btn_email = PushButton(FIF.MAIL, "绑定邮箱")
        btn_email.clicked.connect(self._on_bind_email)
        self.btn_disable = PushButton(FIF.HIDE, "禁用")
        self.btn_disable.clicked.connect(self._on_toggle_disable)
        btn_delete = PushButton(FIF.DELETE, "删除")
        btn_delete.clicked.connect(self._on_delete)
        for b in (btn_refresh, btn_new, btn_reset, btn_email, self.btn_disable, btn_delete):
            btn_bar.addWidget(b)
        btn_bar.addStretch(1)
        layout.addLayout(btn_bar)

        self.table = TableWidget()
        self.table.setColumnCount(len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(self.table.SelectionMode.SingleSelection)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        layout.addWidget(self.table, 1)

    def selected_username(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 1)  # 用户名在第 2 列
        return item.text() if item else None

    def refresh(self):
        users = USER_STORE.list_users()
        self.table.setRowCount(len(users))
        for r, u in enumerate(users):
            values = [u.get("id", ""), u["username"], u.get("email", "-"),
                      u["created_at"], u["last_login_at"],
                      u["last_login_ip"], "已禁用" if u["disabled"] else "正常"]
            for c, v in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))

    def _require_selection(self) -> str | None:
        username = self.selected_username()
        if not username:
            self.window.show_warning("未选择", "请先在表格中选择一个用户")
        return username

    def _on_new_user(self):
        dlg = NewUserDialog(self.window)
        if not dlg.exec():
            return
        ok, err = USER_STORE.create_user(dlg.usernameEdit.text().strip(),
                                         dlg.passwordEdit.text(),
                                         email=dlg.emailEdit.text().strip())
        if ok:
            self.window.show_info("创建成功", "账号已创建")
        else:
            self.window.show_error("创建失败", err)
        self.refresh()

    def _on_bind_email(self):
        username = self._require_selection()
        if not username:
            return
        dlg = BindEmailDialog(username, self.window)
        if not dlg.exec():
            return
        ok, err = USER_STORE.bind_email(username, dlg.emailEdit.text().strip())
        if ok:
            self.window.show_info("绑定成功", f"{username} 已绑定邮箱")
        else:
            self.window.show_error("绑定失败", err)
        self.refresh()

    def _on_reset_password(self):
        username = self._require_selection()
        if not username:
            return
        dlg = ResetPasswordDialog(username, self.window)
        if not dlg.exec():
            return
        ok, err = USER_STORE.reset_password(username, dlg.passwordEdit.text())
        if ok:
            SESSION_STORE.revoke_user_tokens(username)  # 密码重置后强制重新登录
            self.window.show_info("重置成功", f"{username} 的密码已重置，其会话已全部注销")
        else:
            self.window.show_error("重置失败", err)

    def _on_toggle_disable(self):
        username = self._require_selection()
        if not username:
            return
        user = next((u for u in USER_STORE.list_users() if u["username"] == username), None)
        if not user:
            return
        new_state = not user["disabled"]
        w = MessageBox("确认操作",
                       f"{'禁用' if new_state else '启用'}账号 {username}？"
                       + ("\n禁用后其所有会话将被注销。" if new_state else ""),
                       self.window)
        if not w.exec():
            return
        ok, err = USER_STORE.set_disabled(username, new_state)
        if ok and new_state:
            SESSION_STORE.revoke_user_tokens(username)
        self.refresh()

    def _on_delete(self):
        username = self._require_selection()
        if not username:
            return
        w = MessageBox("确认删除", f"删除账号 {username}？该操作不可恢复。", self.window)
        if not w.exec():
            return
        ok, err = USER_STORE.delete_user(username)
        if ok:
            SESSION_STORE.revoke_user_tokens(username)
        else:
            self.window.show_error("删除失败", err)
        self.refresh()


class SessionsPage(QWidget):
    """在线会话页。"""

    HEADERS = ["用户名", "来源 IP", "登录时间", "到期时间", "状态"]

    def __init__(self, window: "ServerMainWindow", parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("sessionsPage")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(12)

        layout.addWidget(TitleLabel("在线会话"))

        btn_bar = QHBoxLayout()
        btn_refresh = PushButton(FIF.SYNC, "刷新")
        btn_refresh.clicked.connect(self.refresh)
        btn_kick = PushButton(FIF.CLOSE, "踢下线")
        btn_kick.clicked.connect(self._on_kick)
        btn_bar.addWidget(btn_refresh)
        btn_bar.addWidget(btn_kick)
        btn_bar.addStretch(1)
        layout.addLayout(btn_bar)

        self.table = TableWidget()
        self.table.setColumnCount(len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(self.table.SelectionMode.SingleSelection)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        layout.addWidget(self.table, 1)

    def refresh(self):
        sessions = SESSION_STORE.list_sessions()
        self.table.setRowCount(len(sessions))
        for r, s in enumerate(sessions):
            values = [s["username"], s["ip"], s["created_at"], s["expires_at"],
                      "在线" if s["valid"] else "已过期"]
            for c, v in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))

    def _on_kick(self):
        row = self.table.currentRow()
        if row < 0:
            self.window.show_warning("未选择", "请先在表格中选择一个会话")
            return
        item = self.table.item(row, 0)
        if not item:
            return
        username = item.text()
        count = SESSION_STORE.revoke_user_tokens(username)
        self.window.show_info("已踢下线", f"{username} 的 {count} 个会话已注销")
        self.refresh()


class LogPage(QWidget):
    """运行日志页。"""

    def __init__(self, window: "ServerMainWindow", parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("logPage")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(12)

        bar = QHBoxLayout()
        bar.addWidget(TitleLabel("运行日志"))
        bar.addStretch(1)
        btn_clear = PushButton(FIF.DELETE, "清空")
        btn_clear.clicked.connect(lambda: self.browser.clear())
        bar.addWidget(btn_clear)
        layout.addLayout(bar)

        self.browser = TextBrowser()
        self.browser.setReadOnly(True)
        layout.addWidget(self.browser, 1)

    def append_log(self, line: str):
        self.browser.append(line)
        self.browser.verticalScrollBar().setValue(
            self.browser.verticalScrollBar().maximum())


class BindEmailDialog(MessageBoxBase):
    """为已有账号绑定邮箱。"""

    def __init__(self, username: str, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel(f"绑定邮箱 — {username}")
        self.emailEdit = LineEdit()
        self.emailEdit.setPlaceholderText("user@example.com")
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel("邮箱（用于验证码找回密码，一个邮箱仅绑定一个账号）"))
        self.viewLayout.addWidget(self.emailEdit)
        self.yesButton.setText("绑定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(440)


class MailSettingsPage(QWidget):
    """邮箱设置页：SMTP 配置与测试发送。"""

    def __init__(self, window: "ServerMainWindow", parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("mailSettingsPage")
        self._build_ui()
        self._load_config()

    def _build_ui(self):
        from mail_service import send_mail

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(10)
        layout.addWidget(TitleLabel("邮箱设置"))
        layout.addWidget(CaptionLabel("配置 SMTP 后，用户注册/找回密码的验证码将通过此邮箱发送"))

        def add_row(label_text, widget):
            row = QHBoxLayout()
            label = BodyLabel(label_text)
            label.setFixedWidth(120)
            row.addWidget(label)
            row.addWidget(widget, 1)
            layout.addLayout(row)
            return widget

        self.hostEdit = LineEdit()
        self.hostEdit.setPlaceholderText("SMTP 服务器，如 smtp.example.com")
        add_row("SMTP 服务器", self.hostEdit)

        self.portEdit = LineEdit()
        self.portEdit.setPlaceholderText("465（SSL）/ 587 / 25")
        add_row("端口", self.portEdit)

        self.sslSwitch = SwitchButton()
        self.sslSwitch.setChecked(True)
        ssl_row = QHBoxLayout()
        ssl_label = BodyLabel("使用 SSL")
        ssl_label.setFixedWidth(120)
        ssl_row.addWidget(ssl_label)
        ssl_row.addWidget(self.sslSwitch)
        ssl_row.addStretch(1)
        layout.addLayout(ssl_row)

        self.userEdit = LineEdit()
        self.userEdit.setPlaceholderText("发件邮箱账号")
        add_row("发件账号", self.userEdit)

        self.passwordEdit = PasswordLineEdit()
        self.passwordEdit.setPlaceholderText("授权码/密码")
        add_row("授权码", self.passwordEdit)

        self.senderEdit = LineEdit()
        self.senderEdit.setPlaceholderText("发件人显示名（默认 NoMY 服务管理后台）")
        add_row("发件人显示名", self.senderEdit)

        btn_row = QHBoxLayout()
        save_btn = PrimaryPushButton(FIF.SAVE, "保存配置")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        self.testEdit = LineEdit()
        self.testEdit.setPlaceholderText("接收测试邮件的邮箱")
        btn_row.addWidget(self.testEdit, 1)
        test_btn = PushButton(FIF.SEND, "发送测试邮件")
        test_btn.clicked.connect(lambda: self._on_test(send_mail))
        btn_row.addWidget(test_btn)
        layout.addLayout(btn_row)
        layout.addStretch(1)

    def _load_config(self):
        cfg = USER_STORE.get_smtp_config()
        self.hostEdit.setText(cfg.get("host", ""))
        self.portEdit.setText(str(cfg.get("port", 465)))
        self.sslSwitch.setChecked(bool(cfg.get("ssl", True)))
        self.userEdit.setText(cfg.get("user", ""))
        self.passwordEdit.setText(cfg.get("password", ""))
        self.senderEdit.setText(cfg.get("sender_name", ""))

    def _collect(self) -> dict:
        try:
            port = int(self.portEdit.text().strip() or 465)
        except ValueError:
            port = 465
        return {
            "host": self.hostEdit.text().strip(),
            "port": port,
            "ssl": self.sslSwitch.isChecked(),
            "user": self.userEdit.text().strip(),
            "password": self.passwordEdit.text(),
            "sender_name": self.senderEdit.text().strip(),
        }

    def _on_save(self):
        USER_STORE.set_smtp_config(self._collect())
        self.window.show_info("已保存", "SMTP 配置已保存")

    def _on_test(self, send_mail):
        to_addr = self.testEdit.text().strip()
        if not to_addr:
            self.window.show_warning("未填写", "请填写接收测试邮件的邮箱")
            return
        USER_STORE.set_smtp_config(self._collect())
        ok, err = send_mail(USER_STORE.get_smtp_config(), to_addr,
                            "NoMY 测试邮件", "这是一封来自服务管理后台的测试邮件，配置成功。")
        if ok:
            self.window.show_info("发送成功", f"测试邮件已发送至 {to_addr}")
        else:
            self.window.show_error("发送失败", err)


class ServerMainWindow(FluentWindow):
    """服务管理后台主窗口。"""

    log_received = Signal(str)   # 日志线程 → UI

    def __init__(self):
        super().__init__()
        self._init_pages()
        self._init_logging()
        self._init_window()

    def _init_pages(self):
        self.controlPage = ServiceControlPage(self)
        self.usersPage = UsersPage(self)
        self.mailPage = MailSettingsPage(self)
        self.sessionsPage = SessionsPage(self)
        self.logPage = LogPage(self)

        self.addSubInterface(self.controlPage, FIF.HOME, "服务控制")
        self.addSubInterface(self.usersPage, FIF.PEOPLE, "用户管理")
        self.addSubInterface(self.mailPage, FIF.MAIL, "邮箱设置")
        self.addSubInterface(self.sessionsPage, FIF.WIFI, "在线会话")
        self.addSubInterface(self.logPage, FIF.DOCUMENT, "运行日志")

        # 每 5 秒自动刷新表格
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self._refresh_timer.start(5000)

    def _init_logging(self):
        self.log_received.connect(self.logPage.append_log)
        self._log_handler = QtLogHandler(self.log_received)
        srv.logger.addHandler(self._log_handler)
        for name in ("auth_store",):
            logging.getLogger(name).addHandler(self._log_handler)

    def _init_window(self):
        self.setWindowTitle("NoMY — 服务管理后台")
        if os.path.isfile(APP_ICON):
            self.setWindowIcon(QIcon(APP_ICON))
        self.resize(1000, 700)
        self.controlPage._refresh_status()

    # ── 供各页面调用的提示 ──────────────────────────────
    def show_info(self, title, content):
        InfoBar.success(title, content, parent=self, position=InfoBarPosition.TOP,
                        duration=3000)

    def show_warning(self, title, content):
        InfoBar.warning(title, content, parent=self, position=InfoBarPosition.TOP,
                        duration=3000)

    def show_error(self, title, content):
        InfoBar.error(title, content, parent=self, position=InfoBarPosition.TOP,
                      duration=4000)

    def log_signal_emit(self, msg: str):
        self.log_received.emit(msg)

    def _auto_refresh(self):
        if self.usersPage.isVisible():
            self.usersPage.refresh()
        if self.sessionsPage.isVisible():
            self.sessionsPage.refresh()

    def closeEvent(self, event):
        if srv.is_server_running():
            w = MessageBox("服务还在运行", "退出将停止 HTTP 服务，确定关闭？", self)
            if not w.exec():
                event.ignore()
                return
            srv.stop_server_in_thread()
        try:
            srv.logger.removeHandler(self._log_handler)
        except Exception:
            pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = ServerMainWindow()
    window.show()
    # 打开界面后自动启动服务（等效点击「启动服务」按钮）
    QTimer.singleShot(300, window.controlPage._on_start)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
