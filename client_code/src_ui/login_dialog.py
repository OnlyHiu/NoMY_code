# -*- coding: utf-8 -*-
"""启动登录对话框（邮箱验证码注册 / 忘记密码 / 离线登录）

三页流程：启动图 → 登录表单（左品牌 / 右输入） → 主界面
- 本地有令牌 → 后台静默验证（7 天滑动有效）→ 成功直接进入
- 令牌失效   → 清除令牌，展示登录表单
- 网络失败   → 提供「重试 / 离线进入（需本地令牌）/ 退出」
- 注册       → 邮箱 + 验证码注册（服务端 SMTP 发码），成功后自动登录
- 忘记密码   → 邮箱验证码重置
"""
import logging
from time import sleep

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QPixmap, QColor, QIcon
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QWidget,
                               QStackedWidget, QVBoxLayout)

from qfluentwidgets import (BodyLabel, CaptionLabel, CheckBox, FluentIcon as FIF,
                            HyperlinkButton, InfoBar, InfoBarPosition, LineEdit,
                            PasswordLineEdit, PrimaryPushButton, PushButton,
                            SubtitleLabel, TitleLabel)

from src_ui import cfg, RUNTIME
from src import UPDATE_SERVER
from src import auth_client

module_logger = logging.getLogger("flu_widget.login")


class _AuthWorker(QThread):
    """后台执行一次认证请求，避免阻塞 UI。"""
    finished = Signal(bool, dict)

    def __init__(self, action, kwargs, parent=None):
        super().__init__(parent)
        self._action = action
        self._kwargs = kwargs

    def run(self):
        try:
            ok, data = self._action(**self._kwargs)
        except Exception as e:
            module_logger.error(f"认证请求异常: {e}")
            ok, data = False, {"error": auth_client.ERR_NETWORK, "network": True}
        self.finished.emit(ok, data)


class LoginDialog(QDialog):
    """登录 / 注册 / 找回密码对话框。exec() 返回 Accepted 表示已登录成功。"""

    THEME = "#009faa"
    THEME_DARK = "#006a70"

    def __init__(self, parent=None, auto_login: bool = True):
        super().__init__(parent)
        self.setWindowTitle("NoMY")
        self.setWindowIcon(QIcon('.\\Config\\image\\menu.ico'))
        self.setFixedSize(900, 700)
        self._worker = None
        self._auto_login = auto_login  # False = 应用内手动登录（跳过令牌静默验证）
        self._mode = "login"  # login / register / reset / error
        self._countdown = 0
        self._build_ui()
        if auto_login:
            self._stack.setCurrentIndex(0)   # 先显示启动图
            QTimer.singleShot(300, self._try_auto_login)
        else:
            self._stack.setCurrentIndex(1)   # 直接显示登录表单
            self._enter_login_mode()

    # ══ 页面 0：启动图 ══════════════════════════════════
    def _make_splash_page(self):
        page = QFrame()
        page.setStyleSheet(f"QFrame {{ background-color: {self.THEME}; }}")
        lay = QVBoxLayout(page)
        lay.setSpacing(14)
        lay.addStretch(2)

        logo = QLabel()
        pix = QPixmap("./Config/image/controls/gougou.png")
        if not pix.isNull():
            logo.setPixmap(pix.scaled(140, 140, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(logo)

        title = TitleLabel("NoMY")
        title.setStyleSheet("color: white; background: transparent;")
        lay.addWidget(title, 0, Qt.AlignmentFlag.AlignHCenter)

        tagline = SubtitleLabel("一个简单的应用")
        tagline.setStyleSheet("color: rgba(255,255,255,0.85); background: transparent;")
        lay.addWidget(tagline, 0, Qt.AlignmentFlag.AlignHCenter)

        lay.addStretch(3)
        return page

    # ══ 页面 1：登录表单（左品牌 / 右输入） ══════════════
    def _make_form_page(self):
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── 左侧品牌区 ──
        brand = QFrame()
        brand.setFixedWidth(340)
        brand.setStyleSheet(f"QFrame {{ background-color: {self.THEME}; }}")
        bl = QVBoxLayout(brand)
        bl.setContentsMargins(32, 40, 32, 32)
        bl.setSpacing(10)
        bl.addStretch(2)

        brand_logo = QLabel()
        pix = QPixmap("./Config/image/controls/gougou.png")
        if not pix.isNull():
            brand_logo.setPixmap(pix.scaled(110, 110,
                                            Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
        brand_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(brand_logo)

        brand_title = TitleLabel("NoMY")
        brand_title.setStyleSheet("color: white; background: transparent;")
        bl.addWidget(brand_title, 0, Qt.AlignmentFlag.AlignHCenter)

        brand_sub = BodyLabel("一个简单的应用")
        brand_sub.setStyleSheet("color: rgba(255,255,255,0.80); background: transparent;")
        bl.addWidget(brand_sub, 0, Qt.AlignmentFlag.AlignHCenter)
        bl.addStretch(3)
        lay.addWidget(brand)

        # ── 右侧表单区 ──
        form = QWidget()
        self._form_lay = QVBoxLayout(form)
        self._form_lay.setContentsMargins(40, 36, 40, 24)
        self._form_lay.setSpacing(8)
        lay.addWidget(form, 1)

        self.formTitle = SubtitleLabel("请登录以继续")
        self._form_lay.addWidget(self.formTitle)
        self._form_lay.addSpacing(6)

        self.usernameEdit = LineEdit()
        self.usernameEdit.setPlaceholderText("用户名")
        self.usernameEdit.setClearButtonEnabled(True)
        self.usernameEdit.setText(cfg.auth_username.value or "")
        self._form_lay.addWidget(self.usernameEdit)

        self.emailEdit = LineEdit()
        self.emailEdit.setPlaceholderText("邮箱（用于注册与找回密码）")
        self.emailEdit.setClearButtonEnabled(True)
        self.emailEdit.hide()
        self._form_lay.addWidget(self.emailEdit)

        code_row = QHBoxLayout()
        self.codeEdit = LineEdit()
        self.codeEdit.setPlaceholderText("邮箱验证码")
        code_row.addWidget(self.codeEdit, 1)
        self.sendCodeBtn = PushButton("发送验证码")
        self.sendCodeBtn.clicked.connect(self._on_send_code)
        code_row.addWidget(self.sendCodeBtn)
        self._form_lay.addLayout(code_row)
        self._hide_code_row()

        self.passwordEdit = PasswordLineEdit()
        self.passwordEdit.setPlaceholderText("密码")
        self._form_lay.addWidget(self.passwordEdit)

        self.confirmEdit = PasswordLineEdit()
        self.confirmEdit.setPlaceholderText("确认密码")
        self.confirmEdit.hide()
        self._form_lay.addWidget(self.confirmEdit)

        self.rememberCheck = CheckBox("记住登录（7 天内免密码自动登录）")
        self.rememberCheck.setChecked(bool(cfg.auth_remember.value))
        self._form_lay.addWidget(self.rememberCheck)

        self.statusLabel = BodyLabel("")
        self.statusLabel.setWordWrap(True)
        self.statusLabel.hide()
        self._form_lay.addWidget(self.statusLabel)

        self.primaryBtn = PrimaryPushButton(FIF.PEOPLE, "登 录")
        self.primaryBtn.setFixedHeight(40)
        self.primaryBtn.clicked.connect(self._on_primary)
        self._form_lay.addWidget(self.primaryBtn)

        self.forgotBtn = HyperlinkButton("", "忘记密码？")
        self.forgotBtn.clicked.connect(self._on_forgot)
        self._form_lay.addWidget(self.forgotBtn, 0, Qt.AlignmentFlag.AlignHCenter)

        self.switchBtn = PushButton(FIF.ADD, "没有账号？去注册")
        self.switchBtn.setFlat(True)
        self.switchBtn.clicked.connect(self._on_switch_mode)
        self._form_lay.addWidget(self.switchBtn)

        self.offlineBtn = PushButton(FIF.CLOUD, "离线进入")
        self.offlineBtn.hide()
        self._form_lay.addWidget(self.offlineBtn)
        self.offlineBtn.clicked.connect(self._on_offline_enter)

        self.guestBtn = PushButton(FIF.PEOPLE, "游客进入（无需登录）")
        self.guestBtn.hide()
        self._form_lay.addWidget(self.guestBtn)
        self.guestBtn.clicked.connect(self._on_guest_enter)

        self._form_lay.addStretch(1)
        self.serverLabel = CaptionLabel(f"认证服务器：{UPDATE_SERVER}")
        self._form_lay.addWidget(self.serverLabel)

        self.usernameEdit.returnPressed.connect(self._on_primary)
        self.passwordEdit.returnPressed.connect(self._on_primary)
        self.confirmEdit.returnPressed.connect(self._on_primary)
        self.codeEdit.returnPressed.connect(self._on_primary)

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)

        return page

    # ══ UI 构建 ══════════════════════════════════════════
    def _build_ui(self):
        self._stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._stack.addWidget(self._make_splash_page())   # 0 = 启动图
        self._stack.addWidget(self._make_form_page())     # 1 = 登录表单

    # ── 模式切换 ────────────────────────────────────────
    def _show_form(self):
        self._stack.setCurrentIndex(1)

    def _hide_code_row(self):
        self.emailEdit.hide()
        self.codeEdit.hide()
        self.sendCodeBtn.hide()

    def _show_code_row(self):
        self.emailEdit.show()
        self.codeEdit.show()
        self.sendCodeBtn.show()

    def _enter_login_mode(self, message: str = ""):
        self._mode = "login"
        self._show_form()
        self.formTitle.setText("请登录以继续")
        self.primaryBtn.setText("登 录")
        self.primaryBtn.setIcon(FIF.PEOPLE)
        self.switchBtn.setText("没有账号？去注册")
        self.switchBtn.show()
        self.forgotBtn.show()
        self.usernameEdit.show()
        self.passwordEdit.show()
        self.confirmEdit.hide()
        self.rememberCheck.show()
        self.offlineBtn.hide()
        self.guestBtn.show()
        self._hide_code_row()
        self._set_busy(False)
        if message:
            InfoBar.warning("提示", message, parent=self,
                            position=InfoBarPosition.TOP, duration=4000)

    def _enter_register_mode(self):
        self._mode = "register"
        self.formTitle.setText("注册新账号（邮箱验证码）")
        self.primaryBtn.setText("注 册")
        self.primaryBtn.setIcon(FIF.ADD)
        self.switchBtn.setText("已有账号？返回登录")
        self.forgotBtn.hide()
        self.rememberCheck.hide()
        self.guestBtn.hide()
        self._show_code_row()
        self.confirmEdit.show()

    def _enter_reset_mode(self):
        self._mode = "reset"
        self.formTitle.setText("通过邮箱找回密码")
        self.primaryBtn.setText("重置密码")
        self.primaryBtn.setIcon(FIF.SYNC)
        self.switchBtn.setText("返回登录")
        self.forgotBtn.hide()
        self.rememberCheck.hide()
        self.guestBtn.hide()
        self._show_code_row()
        self.confirmEdit.show()

    def _enter_error_mode(self, err: str):
        """网络失败：重试 / 离线进入（有令牌时）/ 退出。"""
        self._show_form()
        self._mode = "error"
        self.formTitle.setText("无法连接服务器")
        self.statusLabel.setText(err)
        self.statusLabel.show()
        self.usernameEdit.hide()
        self.passwordEdit.hide()
        self.confirmEdit.hide()
        self.rememberCheck.hide()
        self.forgotBtn.hide()
        self.switchBtn.setText("退出程序")
        self.primaryBtn.setText("重 试")
        self.primaryBtn.setIcon(FIF.SYNC)
        self.primaryBtn.setEnabled(True)
        self.switchBtn.setEnabled(True)
        self._hide_code_row()
        self.guestBtn.hide()
        has_token = bool((cfg.auth_token.value or "").strip())
        self.offlineBtn.setVisible(has_token)

    def _set_busy(self, busy: bool, status: str = ""):
        self.primaryBtn.setEnabled(not busy)
        self.switchBtn.setEnabled(not busy)
        self.sendCodeBtn.setEnabled(not busy)
        for w in (self.usernameEdit, self.passwordEdit, self.confirmEdit,
                  self.emailEdit, self.codeEdit, self.rememberCheck):
            w.setEnabled(not busy)
        if status:
            self.statusLabel.setText(status)
            self.statusLabel.show()
        else:
            self.statusLabel.hide()

    # ── 启动时令牌自动验证 ──────────────────────────────
    def _try_auto_login(self):
        token = (cfg.auth_token.value or "").strip()
        if not token:
            # 无令牌 → 延迟切到登录表单（给启动图最短展示时间）
            QTimer.singleShot(800, lambda: self._enter_login_mode())
            return
        self._set_busy(True, "正在验证登录状态…")
        self._start_worker(auth_client.verify, {"base_url": UPDATE_SERVER, "token": token},
                           self._on_verify_result)

    def _on_verify_result(self, ok: bool, data: dict):
        if ok:
            RUNTIME["guest"] = False
            RUNTIME["token"] = (cfg.auth_token.value or "").strip()
            RUNTIME["username"] = data.get("username", "")
            RUNTIME["user_id"] = str(data.get("user_id", "") or "")
            cfg.set(cfg.auth_user_id, data.get("user_id", cfg.auth_user_id.value))
            cfg.set(cfg.auth_email, data.get("email", cfg.auth_email.value))
            cfg.save()
            module_logger.info(f"令牌自动登录成功: {RUNTIME['username']}")
            # 启动图多停留 3 秒再进入主界面
            self.statusLabel.setText(f"欢迎回来，{RUNTIME['username']}")
            self.statusLabel.show()
            QTimer.singleShot(3000, self.accept)
            return
        if data.get("network"):
            module_logger.warning("令牌验证网络失败（可离线进入）")
            self._set_busy(False)
            return self._enter_error_mode(data.get("error", auth_client.ERR_NETWORK))
        cfg.set(cfg.auth_token, "")
        cfg.save()
        module_logger.info("本地令牌已失效，请重新登录")
        self._set_busy(False)
        self._enter_login_mode("登录状态已过期，请重新登录")

    # ── 主按钮：登录 / 注册 / 重置 / 重试 ────────────────
    def _on_primary(self):
        if self._mode == "error":
            self._enter_login_mode()
            self._try_auto_login()
            return
        username = self.usernameEdit.text().strip()
        password = self.passwordEdit.text()
        if self._mode == "login":
            if not username:
                return self._warn("请输入用户名")
            if not password:
                return self._warn("请输入密码")
            self._set_busy(True, "正在登录…")
            self._start_worker(auth_client.login,
                               {"base_url": UPDATE_SERVER, "username": username,
                                "password": password},
                               self._on_login_result)
        elif self._mode == "register":
            self._do_register()
        else:
            self._do_reset()

    def _do_register(self):
        username = self.usernameEdit.text().strip()
        email = self.emailEdit.text().strip()
        password = self.passwordEdit.text()
        code = self.codeEdit.text().strip()
        if not username:
            return self._warn("请输入用户名")
        if not email:
            return self._warn("请输入邮箱")
        if len(password) < 8:
            return self._warn("密码长度至少 8 位，且包含字母、数字、符号中的两类")
        if password != self.confirmEdit.text():
            return self._warn("两次输入的密码不一致")
        if not code:
            return self._warn("请输入邮箱验证码")
        self._set_busy(True, "正在注册…")
        self._start_worker(auth_client.register,
                           {"base_url": UPDATE_SERVER, "username": username,
                            "password": password, "email": email, "code": code},
                           self._on_register_result)

    def _do_reset(self):
        email = self.emailEdit.text().strip()
        code = self.codeEdit.text().strip()
        new_password = self.passwordEdit.text()
        if not email:
            return self._warn("请输入邮箱")
        if not code:
            return self._warn("请输入邮箱验证码")
        if len(new_password) < 8:
            return self._warn("新密码长度至少 8 位，且包含字母、数字、符号中的两类")
        if new_password != self.confirmEdit.text():
            return self._warn("两次输入的新密码不一致")
        self._set_busy(True, "正在重置密码…")
        self._start_worker(auth_client.reset_password,
                           {"base_url": UPDATE_SERVER, "email": email,
                            "code": code, "new_password": new_password},
                           self._on_reset_result)

    # ── 验证码发送与倒计时 ──────────────────────────────
    def _on_send_code(self):
        email = self.emailEdit.text().strip()
        if not email:
            return self._warn("请先输入邮箱")
        purpose = "register" if self._mode == "register" else "reset"
        self.sendCodeBtn.setEnabled(False)
        self._start_worker(auth_client.send_code,
                           {"base_url": UPDATE_SERVER, "email": email,
                            "purpose": purpose},
                           self._on_send_code_result)

    def _on_send_code_result(self, ok: bool, data: dict):
        if not ok:
            self.sendCodeBtn.setEnabled(True)
            if data.get("network"):
                return self._enter_error_mode(data.get("error", auth_client.ERR_NETWORK))
            return self._warn(data.get("error", "验证码发送失败"))
        InfoBar.success("已发送", "验证码已发送到邮箱，10 分钟内有效", parent=self,
                        position=InfoBarPosition.TOP, duration=3000)
        self._countdown = 60
        self.sendCodeBtn.setEnabled(False)
        self.sendCodeBtn.setText("重新发送(60s)")
        self._countdown_timer.start()

    def _on_countdown_tick(self):
        self._countdown -= 1
        if self._countdown <= 0:
            self._countdown_timer.stop()
            self.sendCodeBtn.setText("发送验证码")
            self.sendCodeBtn.setEnabled(True)
        else:
            self.sendCodeBtn.setText(f"重新发送({self._countdown}s)")

    # ── 结果回调 ────────────────────────────────────────
    def _on_register_result(self, ok: bool, data: dict):
        if not ok:
            self._set_busy(False)
            if data.get("network"):
                return self._enter_error_mode(data.get("error", auth_client.ERR_NETWORK))
            return self._warn(data.get("error", "注册失败"))
        module_logger.info(f"注册成功: {data.get('username')} (id={data.get('user_id')})")
        InfoBar.success("注册成功", f"用户ID {data.get('user_id', '')}，正在自动登录…",
                        parent=self, position=InfoBarPosition.TOP, duration=2500)
        self._do_login(self.usernameEdit.text().strip(), self.passwordEdit.text())

    def _on_reset_result(self, ok: bool, data: dict):
        if not ok:
            self._set_busy(False)
            if data.get("network"):
                return self._enter_error_mode(data.get("error", auth_client.ERR_NETWORK))
            return self._warn(data.get("error", "重置失败"))
        self._set_busy(False)
        InfoBar.success("重置成功", "请使用新密码登录", parent=self,
                        position=InfoBarPosition.TOP, duration=3000)
        self._enter_login_mode()

    def _on_login_result(self, ok: bool, data: dict):
        if not ok:
            self._set_busy(False)
            if data.get("network"):
                return self._enter_error_mode(data.get("error", auth_client.ERR_NETWORK))
            return self._warn(data.get("error", "用户名或密码错误"))
        self._finish_login(data)

    def _do_login(self, username: str, password: str):
        self._set_busy(True, "正在登录…")
        self._start_worker(auth_client.login,
                           {"base_url": UPDATE_SERVER, "username": username,
                            "password": password},
                           self._on_login_result)

    def _on_forgot(self):
        if self._mode == "login":
            self._enter_reset_mode()

    def _on_switch_mode(self):
        if self._mode == "error":
            self.reject()
            return
        if self._mode == "login":
            self._enter_register_mode()
        else:
            self._enter_login_mode()

    def _on_offline_enter(self):
        token = (cfg.auth_token.value or "").strip()
        RUNTIME["token"] = token
        RUNTIME["username"] = cfg.auth_username.value or "离线用户"
        # 离线模式下重置登录表单，避免下次打开时残留忙碌状态
        self._set_busy(False)
        module_logger.warning("离线模式进入（服务器不可达）")
        self.accept()

    def _on_guest_enter(self):
        """游客进入：无需账号密码，用户设置页此时显示登录界面。"""
        RUNTIME["guest"] = True
        module_logger.info("游客模式进入（未登录）")
        self.accept()

    # ── 登录成功 ────────────────────────────────────────
    def _finish_login(self, data: dict):
        token = data.get("token", "")
        username = data.get("username", "")
        RUNTIME["guest"] = False
        RUNTIME["token"] = token
        RUNTIME["username"] = username
        RUNTIME["user_id"] = str(data.get("user_id", "") or "")
        cfg.set(cfg.auth_username, username)
        cfg.set(cfg.auth_remember, bool(self.rememberCheck.isChecked()))
        cfg.set(cfg.auth_user_id, data.get("user_id", ""))
        cfg.set(cfg.auth_email, data.get("email", ""))
        # 新登录会话：清空上个账号遗留的显示名与头像，
        # 避免用户设置页显示错误身份（显示名属于账号资料，换号即失效）
        cfg.set(cfg.auth_display_name, "")
        cfg.set(cfg.auth_avatar, "")
        if self.rememberCheck.isChecked():
            cfg.set(cfg.auth_token, token)
        else:
            cfg.set(cfg.auth_token, "")
        cfg.save()
        module_logger.info(f"登录成功: {username}（记住登录={self.rememberCheck.isChecked()}）")
        self.accept()

    # ── 工具 ────────────────────────────────────────────
    def _start_worker(self, action, kwargs, callback):
        self._worker = _AuthWorker(action, kwargs, self)
        self._worker.finished.connect(callback)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _warn(self, message: str):
        InfoBar.warning("提示", message, parent=self,
                        position=InfoBarPosition.TOP, duration=4000)
