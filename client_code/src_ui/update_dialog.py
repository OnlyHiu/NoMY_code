# -*- coding: utf-8 -*-
"""更新对话框。

- UpdateProgressDialog: 显示下载进度 + 自动安装勾选 + 取消
"""
import logging
import os

from PySide6.QtWidgets import (QCheckBox, QProgressBar)
from qfluentwidgets import MessageBoxBase, SubtitleLabel, BodyLabel

from src_ui.update_manager import (UpdateManager, fetch_in_thread,
                                   compare_version)

module_logger = logging.getLogger("flu_widget.update_dialog")


class UpdateProgressDialog(MessageBoxBase):
    """检查 + 下载 + 安装三合一对话框。

    用法：
        dlg = UpdateProgressDialog(parent, info=server_info)
        if dlg.exec():
            # 用户点了「立即安装」
            UpdateManager.install_silently(dlg.installer_path)
    """

    def __init__(self, parent, info: dict, current_version: str, save_dir: str):
        super().__init__(parent)
        self.info = info
        self.current_version = current_version
        self.save_dir = save_dir
        self.installer_path: str | None = None
        self.auto_install_requested: bool = False
        self._manager = UpdateManager(self)
        self._manager.progress.connect(self._on_progress)
        self._manager.status.connect(self._on_status)
        self._manager.finished.connect(self._on_finished)

        remote_v = info.get("version", "?")
        self.titleLabel = SubtitleLabel()
        if compare_version(remote_v, current_version) <= 0:
            self.titleLabel.setText(f"已是最新版本: {current_version}")
            self._mode = "uptodate"
        else:
            self.titleLabel.setText(f"发现新版本: {remote_v}（当前 {current_version}）")
            self._mode = "update"

        # 详情
        from src_ui.update_manager import HARDCODED_UPDATE_SERVER
        self.infoLabel = BodyLabel()
        size = info.get("package_size", 0)
        size_str = f"{size/1024/1024:.2f} MB" if size else "未知"
        info_text = (
            # f"<b>服务端:</b> <code>{HARDCODED_UPDATE_SERVER}</code><br>"
            # f"<b>返回服务端:</b> {info.get('server', '?')}<br>"
            f"<b>包大小:</b> {size_str}<br>"
            # f"<b>SHA256:</b> <code>{info.get('sha256', '?')[:16]}...</code><br>"
        )
        if info.get("info"):
            info_text += f"<br><b>更新说明:</b><br>{self._md_to_html(info['info'])}"
        self.infoLabel.setText(info_text)
        self.infoLabel.setWordWrap(True)
        self.infoLabel.setStyleSheet("QLabel { background:#f3f9fd; padding:10px; border-radius:4px; }")

        # 进度
        self.progressBar = QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        self.progressBar.setFormat("%p%   (%v / %m KB)")

        self.statusLabel = BodyLabel("准备开始...")
        self.statusLabel.setStyleSheet("color:#666;")

        # 自动安装勾选
        self.autoInstallCheck = QCheckBox("下载完成后自动安装（需要管理员权限）")
        self.autoInstallCheck.setChecked(False)

        # 布局
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.infoLabel)
        self.viewLayout.addWidget(self.progressBar)
        self.viewLayout.addWidget(self.statusLabel)
        self.viewLayout.addWidget(self.autoInstallCheck)

        # 按钮
        self.yesButton.setText("开始下载")
        self.cancelButton.setText("取消")

        self.widget.setMinimumWidth(520)

        if self._mode == "update":
            # ★ 关键：断开 yesButton 底层默认的 accept() 连接
            try:
                self.yesButton.clicked.disconnect()
            except Exception:
                pass
            self.yesButton.clicked.connect(self._on_yes_clicked)
            self.autoInstallCheck.toggled.connect(self._on_auto_toggle)
        else:
            self.yesButton.setText("关闭")
            self.yesButton.clicked.connect(self.accept)
            self.progressBar.hide()
            self.statusLabel.hide()
            self.autoInstallCheck.hide()

        self._user_canceled = False

        # 下载完成后保持置顶，避免被主窗口遮住
        self.setWindowFlags(self.windowFlags() | 0x00000008)  # Qt.WindowStaysOnTopHint

    def accept(self):
        import logging
        logging.getLogger("flu_widget.update_dialog").info(
            "[DIAG] accept 被调用 — 对话框即将关闭")
        super().accept()

    def _on_auto_toggle(self, checked: bool):
        self.auto_install_requested = checked

    def _md_to_html(self, text: str) -> str:
        return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    .replace("\n", "<br>"))

    def _on_start(self):
        self.yesButton.setEnabled(False)
        self.yesButton.setText("下载中...")
        self.cancelButton.setText("取消")
        self.cancelButton.setEnabled(True)
        self._user_canceled = False
        self._should_exit_app = False
        # server_url 不传，自动走硬编码 HARDCODED_UPDATE_SERVER
        fetch_in_thread(self._manager, self.save_dir)

    def _on_progress(self, received: int, total: int):
        if total > 0:
            pct = int(received * 100 / total)
            self.progressBar.setValue(pct)
            self.progressBar.setFormat(
                f"%p%   ({received/1024:.0f} / {total/1024:.0f} KB)"
            )
        else:
            self.progressBar.setRange(0, 0)  # 不确定大小：滚动

    def _on_status(self, text: str):
        self.statusLabel.setText(text)

    def _on_finished(self, ok: bool, msg: str):
        import logging
        module_logger = logging.getLogger("flu_widget.update_dialog")
        module_logger.info("[DIAG] _on_finished ok=%s msg=%r user_canceled=%s", ok, msg, self._user_canceled)
        if self._user_canceled:
            return
        if not ok:
            self.statusLabel.setText(f"✗ {msg}")
            self.statusLabel.setStyleSheet("color:#a4262c;")
            self.yesButton.setEnabled(True)
            self.yesButton.setText("重试")
            self.cancelButton.setText("关闭")
            return
        # 成功
        if msg.startswith("已是最新版本"):
            self.statusLabel.setText(f"✓ {msg}")
            self.statusLabel.setStyleSheet("color:#107c10;")
            self.yesButton.setText("关闭")
            self.yesButton.setEnabled(True)
            # 统一用 _on_yes_clicked 处理，不直接 accept
            return
        # 下载到本地文件
        self.installer_path = msg
        self.statusLabel.setText(f"✓ 下载完成：{os.path.basename(msg)}")
        self.statusLabel.setStyleSheet("color:#107c10;")
        if self.auto_install_requested:
            self._do_install()
        else:
            self.yesButton.setText("立即安装")
            self.yesButton.setEnabled(True)
            # 不动 cancelButton 信号，只改文字
            self.cancelButton.setText("关闭")

    def _do_install(self):
        if not self.installer_path:
            return
        # 启动 updater.bat：它会等主程序退出后再装
        ok, m = UpdateManager.install_silently(self.installer_path)
        self.statusLabel.setText(m)
        self.statusLabel.setStyleSheet("color:#107c10;" if ok else "color:#a4262c;")
        if ok:
            self.yesButton.setEnabled(False)
            self.yesButton.setText("已启动安装")
            self.cancelButton.setText("退出程序")
            # 通知主程序：主程序应立即退出，让 updater 开始工作
            self._should_exit_app = True
            # 自动关闭对话框（用户也可以点"退出程序"按钮）
            from PySide6.QtCore import QTimer
            QTimer.singleShot(800, self.accept)

    def _on_yes_clicked(self):
        """统一处理 yesButton 点击，根据当前状态决定行为"""
        if self._mode != "update":
            self.accept()
            return
        # 已是最新版本 → 点「关闭」
        if not self.installer_path and self.yesButton.text() == "关闭":
            self.accept()
            return
        # 未开始 → 点「开始下载」
        if not self.installer_path:
            self._on_start()
            return
        # 已下载 → 点「立即安装」
        self._do_install()

    def reject(self):
        import logging
        module_logger = logging.getLogger("flu_widget.update_dialog")
        module_logger.info("[DIAG] reject 被调用 (installer_path=%s)", self.installer_path)
        # 用户点 X 或「取消」：中止下载
        if self._mode == "update" and not self.installer_path:
            self._user_canceled = True
            self._manager.cancel()
        super().reject()

    def closeEvent(self, e):
        import logging
        module_logger = logging.getLogger("flu_widget.update_dialog")
        module_logger.info("[DIAG] closeEvent 被调用 (installer_path=%s)", self.installer_path)
        self._user_canceled = True
        self._manager.cancel()
        super().closeEvent(e)