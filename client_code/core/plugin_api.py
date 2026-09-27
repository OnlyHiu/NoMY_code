# -*- coding: utf-8 -*-
"""插件契约：别人写插件必须遵守的接口，以及主程序注入给插件的 HostApi。

用法（插件 main.py）::

    from core.plugin_api import PluginBase, PluginMeta, HostApi

    class MyPlugin(PluginBase):
        meta = PluginMeta(name="我的工具", category="工具",
                          description="xxx", version="1.0.0", icon="SYNC")

        def create_widget(self, host: HostApi, parent=None):
            # 返回你的功能界面（QWidget）
            return MyWidget(host, parent)
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import webbrowser

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget
from qfluentwidgets import InfoBarPosition


class PluginMeta:
    """插件元数据：主程序根据它自动生成导航条目和界面"""

    __slots__ = ("name", "category", "description", "version", "icon", "obj_name")

    def __init__(self, name: str, category: str = "工具", description: str = "",
                 version: str = "1.0.0", icon: str = "APPLICATION",
                 obj_name: str | None = None):
        self.name = name
        self.category = category
        self.description = description
        self.version = version
        self.icon = icon                    # FluentIcon 枚举成员名，如 "SYNC"
        self.obj_name = obj_name or f"plugin_{name}"


class PluginBase:
    """插件协议基类：实现 create_widget 返回功能界面即可被主程序挂载"""

    meta: PluginMeta = None

    def create_widget(self, host: "HostApi", parent: QWidget | None = None) -> QWidget:
        raise NotImplementedError


class _AsyncWorker(QThread):
    """后台线程执行函数，结果/异常通过信号回主线程"""

    done = Signal(object, object)  # (结果, 异常)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn(), None)
        except Exception as e:  # noqa: BLE001 - 异常需通过信号回传
            self.done.emit(None, e)


class HostApi:
    """主程序注入给插件的受限接口：通知、文件选择、对话框、配置、命令、日志、异步执行。

    插件不要 import fluwidget / 主窗口，一律通过这些方法访问宿主能力，
    这样插件与主程序完全解耦，主程序内部改动不影响插件。

    namespace 是插件的唯一标识（通常用 PluginMeta.obj_name），
    用于把插件自己的配置独立保存到 Config/Plugins/<namespace>.json。
    """

    def __init__(self, main_window, namespace: str = "default"):
        self._win = main_window
        self._workers = []
        self._namespace = re.sub(r"[^A-Za-z0-9_-]", "_", namespace or "default")

    # ── 通知 ────────────────────────────────────────────────

    def show_info(self, title: str, content: str = "", duration: int = 3000,position=InfoBarPosition.TOP):
        from src_ui.information_history import create_infomation_info
        create_infomation_info(self._win, title, content, duration=duration,position=position)

    def show_warning(self, title: str, content: str = "",duration: int = 3000,position=InfoBarPosition.TOP):
        from src_ui.information_history import create_Warning_info
        create_Warning_info(self._win, title, content,duration=duration,position=position)

    def show_success(self, title: str, content: str = "", duration: int = 2000,position=InfoBarPosition.TOP):
        from src_ui.information_history import create_success_info
        create_success_info(self._win, title, content, duration=duration,position=position)

    def show_error(self, title: str, content: str = "", duration: int = 2000,position=InfoBarPosition.TOP):
        from src_ui.information_history import create_error_info
        create_error_info(self._win, title, content, duration=duration,position=position)


    # ── 对话框（确认 / 输入 / 下拉选择）────────────────────

    def confirm(self, title: str, content: str = "",
                ok_text: str = "确定", cancel_text: str = "取消") -> bool:
        """弹出确认框，点「确定」返回 True，点「取消」或关闭返回 False"""
        from qfluentwidgets import MessageBox
        box = MessageBox(title, content, self._win)
        box.yesButton.setText(ok_text)
        box.cancelButton.setText(cancel_text)
        return bool(box.exec())

    def input_text(self, title: str, label: str = "请输入:",
                   default: str = "", placeholder: str = "") -> str | None:
        """弹出输入框，返回输入文本；用户取消返回 None"""
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        text, ok = QInputDialog.getText(
            self._win, title, label, QLineEdit.EchoMode.Normal, default)
        if not ok:
            return None
        if placeholder and text == "":
            text = placeholder
        return text

    def select_option(self, title: str, label: str = "请选择:",
                      items: list | None = None, default: int = 0) -> str | None:
        """弹出下拉选择框，返回选中的文本；用户取消返回 None"""
        from PySide6.QtWidgets import QInputDialog
        options = items or []
        if not options:
            return None
        text, ok = QInputDialog.getItem(
            self._win, title, label, options, max(0, min(default, len(options) - 1)),
            False)
        return text if ok else None

    # ── 配置持久化（每个插件独立的 JSON 配置文件）──────────

    @property
    def _config_path(self) -> str:
        return os.path.join("Config", "Plugins", f"{self._namespace}.json")

    def get_config(self, key: str, default=None):
        """读取插件自己的配置项（保存在 Config/Plugins/<插件名>.json）"""
        try:
            if not os.path.isfile(self._config_path):
                return default
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get(key, default) if isinstance(data, dict) else default
        except Exception:
            return default

    def set_config(self, key: str, value) -> bool:
        """保存插件自己的配置项，返回是否成功"""
        try:
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            data = {}
            if os.path.isfile(self._config_path):
                try:
                    with open(self._config_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        data = {}
                except Exception:
                    data = {}
            data[key] = value
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self.get_logger(self._namespace).error(f"保存配置失败 {self._config_path}: {e}")
            return False

    # ── 剪贴板 / 打开文件 / 打开网址 ──────────────────────

    def copy_to_clipboard(self, text: str) -> bool:
        """复制文本到剪贴板，返回是否成功"""
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            return True
        except Exception:
            return False

    # ── 文件选择（原生对话框，与主程序一致）──────────────────

    def choose_file(self, title: str = "选择文件", start_dir: str = "",
                    filter: str = "All Files (*)") -> str:
        from PySide6.QtWidgets import QFileDialog
        return QFileDialog.getOpenFileName(self._win, title, start_dir, filter)[0]

    def choose_files(self, title: str = "选择文件（可多选）", start_dir: str = "",
                     filter: str = "All Files (*)") -> list:
        from PySide6.QtWidgets import QFileDialog
        return QFileDialog.getOpenFileNames(self._win, title, start_dir, filter)[0]

    def choose_directory(self, title: str = "选择文件夹", start_dir: str = "") -> str:
        from PySide6.QtWidgets import QFileDialog
        return QFileDialog.getExistingDirectory(self._win, title, start_dir)

    def save_file(self, title: str = "保存文件", start_dir: str = "",
                  filter: str = "All Files (*)") -> str:
        from PySide6.QtWidgets import QFileDialog
        return QFileDialog.getSaveFileName(self._win, title, start_dir, filter)[0]

    def open_directory(self, path: str):
        if path and os.path.isdir(path):
            os.startfile(path)

    # ── 日志 ────────────────────────────────────────────────

    def get_logger(self, name: str):
        return logging.getLogger(f"flu_widget.plugin.{name}")

    # ── 异步执行（耗时操作放后台，避免卡死界面）─────────────

    def run_async(self, fn, on_done=None):
        """在后台线程执行 fn()，结束后在主线程回调 on_done(result, error)。

        fn 返回的结果和抛出的异常都会传给 on_done。
        """
        worker = _AsyncWorker(fn)
        self._workers.append(worker)

        def _finish(result, error):
            if worker in self._workers:
                self._workers.remove(worker)
            if on_done:
                on_done(result, error)

        worker.done.connect(_finish)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        return worker

    # ── 后台命令执行（不弹黑窗、不卡界面）──────────────────

    def run_command(self, cmd, on_done=None, timeout: float | None = None,
                    cwd: str = ""):
        """在后台线程执行命令，回调 on_done((returncode, stdout, stderr), error)。

        cmd 传字符串（走 shell）或参数列表（不走 shell，如 ["adb", "devices"]）。
        """
        def _work():
            try:
                if isinstance(cmd, (list, tuple)):
                    proc = subprocess.run(
                        [str(c) for c in cmd], capture_output=True, text=True,
                        timeout=timeout, cwd=cwd or None, shell=False)
                else:
                    proc = subprocess.run(
                        str(cmd), capture_output=True, text=True,
                        timeout=timeout, cwd=cwd or None, shell=True)
                return proc.returncode, proc.stdout or "", proc.stderr or ""
            except Exception as e:  # noqa: BLE001 - 异常需通过信号回传
                return None, "", str(e)

        self.run_async(_work, on_done)