# -*- coding: utf-8 -*-
"""登录用户设置页（主导航「设置」上方）

- 游客态：显示登录界面（「登录账号」卡片，弹出应用内登录窗），登录成功后自动刷新
- 登录态：圆形头像（qfluentwidgets AvatarWidget）+ 用户名 + 认证服务器
- 修改显示名 / 自助修改密码（改密后其他设备会话自动注销，当前会话保留）
- 记住登录开关、退出登录（退出后回到账号登录界面）
"""
import logging
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from PySide6.QtWidgets import QFileDialog
from qfluentwidgets import (AvatarWidget, BodyLabel, CaptionLabel,
                            FluentIcon as FIF, InfoBar, InfoBarPosition,
                            LineEdit, MessageBox, MessageBoxBase, PasswordLineEdit,
                            PushSettingCard, ScrollArea, SettingCardGroup,
                            SubtitleLabel, SwitchSettingCard, TitleLabel, isDarkTheme)

from src_ui import cfg
from src_ui import RUNTIME
from src import UPDATE_SERVER
from src import auth_client

module_logger = logging.getLogger("flu_widget.user_setting_ui")

THEME_COLOR_LIGHT = QColor("#009faa")
THEME_COLOR_DARK = QColor("#006a70")


def _current_token() -> str:
    return (cfg.auth_token.value or RUNTIME.get("token") or "").strip()


def _current_username() -> str:
    return cfg.auth_username.value or RUNTIME.get("username") or "未登录"


def _is_guest() -> bool:
    """游客模式：没有任何登录会话（本地令牌与内存令牌均为空）。"""
    return not _current_token()


class _ChangePwdWorker(QThread):
    """后台执行修改密码请求。"""
    finished = Signal(bool, dict)

    def __init__(self, token, old_password, new_password, parent=None):
        super().__init__(parent)
        self._args = (token, old_password, new_password)

    def run(self):
        try:
            ok, data = auth_client.change_password(UPDATE_SERVER, *self._args)
        except Exception as e:
            module_logger.error(f"修改密码请求异常: {e}")
            ok, data = False, {"error": "网络异常，请稍后重试"}
        self.finished.emit(ok, data)


class ChangePasswordDialog(MessageBoxBase):
    """修改密码对话框（旧密码 + 新密码 + 确认）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("修改密码")

        self.oldEdit = PasswordLineEdit()
        self.oldEdit.setPlaceholderText("旧密码")
        self.newEdit = PasswordLineEdit()
        self.newEdit.setPlaceholderText("新密码（至少 8 位，含两类字符）")
        self.confirmEdit = PasswordLineEdit()
        self.confirmEdit.setPlaceholderText("确认新密码")

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel("旧密码"))
        self.viewLayout.addWidget(self.oldEdit)
        self.viewLayout.addWidget(CaptionLabel("新密码"))
        self.viewLayout.addWidget(self.newEdit)
        self.viewLayout.addWidget(CaptionLabel("确认新密码"))
        self.viewLayout.addWidget(self.confirmEdit)

        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(420)

    def validate(self) -> bool:
        if not self.oldEdit.text():
            return self._warn("请输入旧密码")
        if len(self.newEdit.text()) < 8:
            return self._warn("新密码长度至少 8 位，且包含字母、数字、符号中的两类")
        if self.newEdit.text() != self.confirmEdit.text():
            return self._warn("两次输入的新密码不一致")
        return True

    def _warn(self, msg: str) -> bool:
        InfoBar.warning("提示", msg, parent=self, position=InfoBarPosition.TOP,
                        duration=3000)
        return False


class DisplayNameDialog(MessageBoxBase):
    """修改显示名对话框。"""

    def __init__(self, current_name: str, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("修改显示名")
        self.nameEdit = LineEdit()
        self.nameEdit.setPlaceholderText("显示名（好友列表中展示的名字）")
        self.nameEdit.setText(current_name)
        self.nameEdit.setClearButtonEnabled(True)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(CaptionLabel("显示名"))
        self.viewLayout.addWidget(self.nameEdit)

        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(420)

    def validate(self) -> bool:
        if not self.nameEdit.text().strip():
            InfoBar.warning("提示", "显示名不能为空", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return False
        return True


class UserSettingInterface(ScrollArea):
    """登录用户设置界面。"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = QVBoxLayout(self.scrollWidget)

        self._build_profile()
        self._build_cards()
        self._init_layout()

        self.resize(1000, 700)
        self.setViewportMargins(0, 24, 0, 24)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)

    # ── UI 构建 ─────────────────────────────────────────
    def _build_profile(self):
        profile_row = QHBoxLayout()
        profile_row.setSpacing(20)

        guest = _is_guest()
        username = "游客" if guest else (
            cfg.auth_display_name.value or _current_username())
        self.avatar = AvatarWidget()
        if not guest and cfg.auth_avatar.value:
            from PySide6.QtGui import QPixmap
            pix = QPixmap(cfg.auth_avatar.value.replace("/", os.sep))
            if not pix.isNull():
                self.avatar.setImage(pix)
            else:
                self.avatar.setText(username)
        else:
            self.avatar.setText("客" if guest else username)
        self.avatar.setBackgroundColor(THEME_COLOR_LIGHT, THEME_COLOR_DARK)
        if not guest:
            self.avatar.setCursor(Qt.CursorShape.PointingHandCursor)
            self.avatar.clicked.connect(self._on_pick_avatar)
            self.avatar.setToolTip("点击更换头像")
        profile_row.addWidget(self.avatar)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        self.nameLabel = TitleLabel(username)
        if guest:
            self.idLabel = CaptionLabel("未登录　登录后可使用好友、朋友圈等账号功能")
            self.serverLabel = BodyLabel(f"认证服务器：{UPDATE_SERVER}")
            self.statusLabel = CaptionLabel("当前以游客身份进入，仅本地功能可用")
        else:
            self.idLabel = CaptionLabel(
                f"用户ID：{cfg.auth_user_id.value or '-'}    邮箱：{cfg.auth_email.value or '未绑定'}")
            self.serverLabel = BodyLabel(f"认证服务器：{UPDATE_SERVER}")
            self.statusLabel = CaptionLabel("已登录（令牌 7 天滑动有效期，每次启动自动续期）")
        info_col.addWidget(self.nameLabel)
        info_col.addWidget(self.idLabel)
        info_col.addWidget(self.serverLabel)
        info_col.addWidget(self.statusLabel)
        profile_row.addLayout(info_col, 1)

        self.expandLayout.addLayout(profile_row)

    def _build_cards(self):
        guest = _is_guest()
        self.accountGroup = SettingCardGroup(
            "账号登录" if guest else "账号设置", self.scrollWidget)

        if guest:
            # 游客态：只放一张登录卡片，登录成功后整页刷新为个人设置
            self.loginCard = PushSettingCard(
                "登录账号",
                FIF.PEOPLE,
                "游客模式",
                "登录或注册账号后即可使用好友、朋友圈等账号功能",
                self.accountGroup
            )
            self.loginCard.clicked.connect(self._on_login)
            self.accountGroup.addSettingCard(self.loginCard)
            return

        self.displayNameCard = PushSettingCard(
            "修改显示名",
            FIF.EDIT,
            "修改显示名",
            cfg.auth_display_name.value or _current_username(),
            self.accountGroup
        )
        self.displayNameCard.clicked.connect(self._on_change_display_name)

        self.changePwdCard = PushSettingCard(
            "修改密码",
            FIF.FINGERPRINT,
            "修改密码",
            "验证旧密码后设置新密码，其他设备将被自动注销",
            self.accountGroup
        )
        self.changePwdCard.clicked.connect(self._on_change_password)

        self.rememberCard = SwitchSettingCard(
            FIF.CERTIFICATE,
            "记住登录",
            "重启后使用本地令牌自动登录（7 天内免密码）",
            configItem=cfg.auth_remember,
            parent=self.accountGroup
        )

        self.logoutCard = PushSettingCard(
            "退出登录",
            FIF.POWER_BUTTON,
            "当前登录用户",
            _current_username(),
            self.accountGroup
        )
        self.logoutCard.clicked.connect(self._on_logout)

        self.accountGroup.addSettingCard(self.displayNameCard)
        self.accountGroup.addSettingCard(self.changePwdCard)
        self.accountGroup.addSettingCard(self.rememberCard)
        self.accountGroup.addSettingCard(self.logoutCard)

    def _init_layout(self):
        self.expandLayout.setSpacing(24)
        self.expandLayout.addWidget(self.accountGroup)
        self.expandLayout.addStretch(1)

    # ── 头像 / 显示名 ───────────────────────────────────
    def _on_pick_avatar(self):
        token = _current_token()
        if not token:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择头像", "", "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return

        def _work():
            import io
            from PIL import Image as PILImage
            img = PILImage.open(path)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((256, 256))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            import base64
            return auth_client.profile_update(
                UPDATE_SERVER, token, "",
                base64.b64encode(buf.getvalue()).decode())

        def _done(r):
            if r and r[0]:
                rel = r[1].get("avatar", "")
                cfg.set(cfg.auth_avatar, rel)
                cfg.save()
                from PySide6.QtGui import QPixmap
                pix = QPixmap(rel.replace("/", os.sep))
                if not pix.isNull():
                    self.avatar.setImage(pix)
                InfoBar.success("头像已更新", "", parent=self,
                                position=InfoBarPosition.TOP, duration=2500)
            else:
                err = r[1].get("error", "上传失败") if r and isinstance(r, tuple) else "网络异常"
                InfoBar.error("头像更新失败", err, parent=self,
                              position=InfoBarPosition.TOP, duration=4000)

        self._run_async(_work, _done)

    def _on_change_display_name(self):
        token = _current_token()
        if not token:
            return
        dlg = DisplayNameDialog(cfg.auth_display_name.value or _current_username(),
                                self.window())
        if not dlg.exec():
            return
        name = dlg.nameEdit.text().strip()

        def _done(r):
            self._on_name_saved(r, name)

        self._run_async(
            lambda: auth_client.profile_update(UPDATE_SERVER, token, name, ""),
            _done)

    def _on_name_saved(self, r, name: str):
        if r and r[0]:
            cfg.set(cfg.auth_display_name, name)
            cfg.save()
            self.nameLabel.setText(name)
            self.displayNameCard.setContent(name)
            InfoBar.success("已保存", "显示名已更新，好友列表将展示新名字",
                            parent=self, position=InfoBarPosition.TOP, duration=3000)
        else:
            err = r[1].get("error", "保存失败") if r and isinstance(r, tuple) else "网络异常"
            InfoBar.error("保存失败", err, parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _run_async(self, fn, cb):
        from PySide6.QtCore import QThread as _QThread
        outer = self

        class _W(_QThread):
            done = Signal(object)

            def run(self):
                try:
                    self.done.emit(fn())
                except Exception as e:
                    module_logger.error(f"后台任务异常: {e}")
                    self.done.emit(None)

        self._profile_task = _W(outer)
        self._profile_task.done.connect(cb)
        self._profile_task.finished.connect(self._profile_task.deleteLater)
        self._profile_task.start()

    # ── 修改密码 ────────────────────────────────────────
    def _on_change_password(self):
        token = _current_token()
        if not token:
            InfoBar.warning("未登录", "当前没有登录会话，请重启程序重新登录",
                            parent=self, position=InfoBarPosition.TOP, duration=4000)
            return
        dlg = ChangePasswordDialog(self.window())
        if not dlg.exec():
            return
        self.changePwdCard.setEnabled(False)
        self._worker = _ChangePwdWorker(token, dlg.oldEdit.text(), dlg.newEdit.text(),
                                        self)
        self._worker.finished.connect(self._on_change_pwd_result)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_change_pwd_result(self, ok: bool, data: dict):
        self.changePwdCard.setEnabled(True)
        if ok:
            # 服务端改密后轮换令牌（旧令牌全部注销），更新本地令牌保持在线
            new_token = (data or {}).get("token", "")
            if new_token:
                RUNTIME["token"] = new_token
                if cfg.auth_token.value:   # 仅「记住登录」时持久化
                    cfg.set(cfg.auth_token, new_token)
                    cfg.save()
            InfoBar.success("修改成功", "密码已更新，其他设备已被注销",
                            parent=self, position=InfoBarPosition.TOP, duration=4000)
            module_logger.info("用户自助修改密码成功")
            return
        err = data.get("error", "修改失败")
        if data.get("network"):
            err = "无法连接认证服务器，请检查网络"
        InfoBar.error("修改失败", err, parent=self, position=InfoBarPosition.TOP,
                      duration=5000)
        module_logger.warning(f"修改密码失败: {err}")

    # ── 游客登录 ────────────────────────────────────────
    def _on_login(self):
        """游客态点「登录账号」：关闭主页面，从头走启动登录流程。"""
        main = self.window()
        relogin = getattr(main, "requestRelogin", None)
        if callable(relogin):
            module_logger.info("游客模式请求登录，返回启动登录界面")
            relogin()
            return
        # 兜底（无法回到登录流程时）：应用内登录窗，仅在真正登录后刷新
        from src_ui.login_dialog import LoginDialog
        dlg = LoginDialog(main, auto_login=False)
        if not dlg.exec() or not _current_token():
            return  # 取消或游客进入，不刷新
        page = self.parent()
        if callable(getattr(page, "reload_user", None)):
            page.reload_user()
        module_logger.info(f"应用内登录成功，用户设置页已刷新: {_current_username()}")

    # ── 退出登录 ────────────────────────────────────────
    def _on_logout(self):
        w = MessageBox("退出登录", "退出登录并返回账号登录界面？", self.window())
        if not w.exec():
            return
        try:
            token = _current_token()
            if token:
                auth_client.logout(UPDATE_SERVER, token)
        except Exception as e:
            module_logger.error(f"注销服务端会话失败: {e}")
        cfg.set(cfg.auth_token, "")
        cfg.save()
        RUNTIME["token"] = ""
        RUNTIME["username"] = ""
        RUNTIME["user_id"] = ""
        module_logger.info("用户已退出登录，返回登录界面")
        # 通知主窗口返回登录界面（主窗口未找到时兜底退出程序）
        main = self.window()
        relogin = getattr(main, "requestRelogin", None)
        if callable(relogin):
            relogin()
        else:
            from PySide6.QtWidgets import QApplication
            QApplication.quit()


class UserSettingPage(QFrame):
    """导航容器。"""

    def __init__(self, name: str, parent=None):
        super().__init__(parent=parent)
        self.setObjectName(name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.userSettingInterface = UserSettingInterface(self)
        layout.addWidget(self.userSettingInterface)

    def reload_user(self):
        """登录状态变化后重建设置界面（游客态 ↔ 个人设置）。"""
        old = self.userSettingInterface
        self.userSettingInterface = UserSettingInterface(self)
        self.layout().addWidget(self.userSettingInterface)
        old.deleteLater()
