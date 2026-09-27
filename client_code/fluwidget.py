# -*- coding: utf-8 -*-
# NoMY - Desktop client
# Copyright (c) 2024 NoMY Contributors
# SPDX-License-Identifier: MIT

import importlib.metadata as _meta

from src_ui.Extended_call_script_ui import ExtendedCallScriptWidget
from src_ui.update_dialog import UpdateProgressDialog
from src_ui.update_manager import (UpdateManager, fetch_update_info_threaded,
                                   show_update_dialog)

_orig_read_text = _meta.PathDistribution.read_text


def _patched_read_text(self, filename):
    try:
        return _orig_read_text(self, filename)
    except (UnicodeDecodeError, Exception):
        try:
            # 使用 latin-1 兜底，latin-1 可以解码任意字节
            path = self._path / filename
            return path.read_text(encoding='latin-1')
        except Exception:
            return None


_meta.PathDistribution.read_text = _patched_read_text
# ===== 补丁结束 =====

import logging
import os
import sys
import datetime
import faulthandler
import threading
import traceback

# 打包运行（Nuitka/PyInstaller）时切换到 exe 所在目录，
# 保证模块加载初期的 ./Config、./LOG 等相对路径可用
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(os.path.abspath(sys.executable)))



import psutil
from PySide6.QtCore import QSize, QEventLoop, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QIcon, QAction, Qt, QFont
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QMessageBox, QVBoxLayout, QTextBrowser, QWidget, \
    QHBoxLayout, QFrame

from core.plugin_api import HostApi
from core.plugin_manager import discover_plugins, purge_loaded_modules
from src_ui import cfg, RUNTIME
from src_ui import (get_active_interfaces, get_active_order,
                    get_parent_map, get_interface_registry)
from src_ui.friends_ui import FriendsWidget
from src_ui.home_ui import HomeInterface, signalBus
from src_ui.image_studio import ImageStudioWidget
from src_ui.moments_ui import MomentsWidget
from src_ui.music_player import MusicPlayerWidget
from src_ui.sysinfo_ui import SysInfoWidget
from src_ui.user_setting_ui import UserSettingPage
from src_ui.video_player import VideoPlayerWidget
from src_ui.wallpaper_ui import WallpaperWidget
from src_ui.setting_ui import SettingWidget
from qfluentwidgets import FluentIcon as FIF, InfoBarPosition, InfoBar, BodyLabel, IconWidget
from qfluentwidgets import (NavigationItemPosition, FluentWindow, SplashScreen,
                            MessageBoxBase, SubtitleLabel, RadioButton, CheckBox, PushButton)
from qfluentwidgets.components.navigation import NavigationTreeWidget
from src import VERSION
from src.Config_ini import module_logger



def _install_crash_logging():
    """崩溃日志：主程序模块导入时生效，记录闪退原因及崩溃时刻的进程/系统状态到 LOG/crash.log"""
    import platform

    log_dir = './LOG'
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass
    crash_log = os.path.join(log_dir, "crash.log")
    startup_log = os.path.join(log_dir, "startup.log")
    crash_lock = threading.Lock()

    def _collect_snapshot():
        """收集当前进程占用和电脑运行参数"""
        lines = []
        try:
            proc = psutil.Process()
            with proc.oneshot():
                mem = proc.memory_info()
                lines.append(
                    f"进程占用: PID={proc.pid}，内存={mem.rss / 1024 / 1024:.1f}MB"
                    f"（虚拟={mem.vms / 1024 / 1024:.1f}MB），线程数={proc.num_threads()}，"
                    f"句柄数={proc.num_handles()}，CPU={proc.cpu_percent(None):.1f}%")
                try:
                    create_time = datetime.datetime.fromtimestamp(proc.create_time())
                    run_min = (datetime.datetime.now() - create_time).total_seconds() / 60
                    lines.append(f"进程创建: {create_time}，已运行 {run_min:.1f} 分钟")
                except Exception:
                    pass
        except Exception as e:
            lines.append(f"进程状态获取失败: {e}")
        try:
            vm = psutil.virtual_memory()
            lines.append(f"系统内存: 总={vm.total / 1024 / 1024 / 1024:.1f}GB，"
                         f"可用={vm.available / 1024 / 1024 / 1024:.1f}GB，使用率={vm.percent}%")
        except Exception as e:
            lines.append(f"内存状态获取失败: {e}")
        try:
            lines.append(f"CPU: 总使用率={psutil.cpu_percent(interval=None):.1f}%，"
                         f"逻辑核心数={psutil.cpu_count(logical=True)}")
        except Exception as e:
            lines.append(f"CPU状态获取失败: {e}")
        try:
            lines.append(f"系统: {platform.platform()}，架构={platform.machine()}，"
                         f"开机时间={datetime.datetime.fromtimestamp(psutil.boot_time())}，"
                         f"用户={os.getenv('USERNAME', '')}")
        except Exception as e:
            lines.append(f"系统信息获取失败: {e}")
        return "\n".join(lines)

    def _write_snapshot(f):
        f.write(f"[{datetime.datetime.now()}] 崩溃时刻进程/系统状态:\n")
        try:
            f.write(_collect_snapshot() + "\n")
        except Exception:
            f.write("[崩溃时刻状态获取失败]\n")

    try:
        with open(startup_log, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] 程序启动，版本 {VERSION}，"
                    f"Python {sys.version.split()[0]}\n")
            try:
                f.write("启动时状态:\n" + _collect_snapshot() + "\n")
            except Exception:
                pass
    except Exception:
        pass

    if getattr(_install_crash_logging, "_installed", False):
        return
    _install_crash_logging._installed = True

    previous_hook = sys.excepthook

    def global_excepthook(exc_type, exc_value, exc_tb):
        try:
            # 加锁防止多线程同时崩溃时日志交错
            with crash_lock:
                with open(crash_log, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.datetime.now()}] 未捕获异常:\n")
                    _write_snapshot(f)
                    traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
                    f.write("\n")
        except Exception:
            pass
        try:
            previous_hook(exc_type, exc_value, exc_tb)
        except Exception:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = global_excepthook
    try:
        # C 层崩溃（访问违规等）堆栈写入 crash.log
        faulthandler.enable(open(crash_log, "a", encoding="utf-8"))

        # 在 faulthandler 前面再链一层 Windows 异常过滤器：崩溃瞬间先写状态快照，再交给 faulthandler 转储线程
        try:
            import ctypes
            from ctypes import wintypes

            _set_unhandled = ctypes.windll.kernel32.SetUnhandledExceptionFilter
            _set_unhandled.restype = ctypes.c_void_p
            _set_unhandled.argtypes = [ctypes.c_void_p]

            holder = {"prev": None}

            @ctypes.WINFUNCTYPE(wintypes.LONG, ctypes.c_void_p)
            def _snapshot_filter(exception_info):
                try:
                    with open(crash_log, "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.datetime.now()}] C层崩溃（Windows fatal exception）:\n")
                        _write_snapshot(f)
                except Exception:
                    pass
                if holder["prev"]:
                    return holder["prev"](exception_info)
                return 0  # EXCEPTION_CONTINUE_SEARCH

            _install_crash_logging._win_filter = _snapshot_filter  # 防止回调对象被垃圾回收
            holder["prev"] = _set_unhandled(None)   # 卸载并取回 faulthandler 安装的过滤器
            _set_unhandled(_snapshot_filter)        # 安装我们自己的过滤器
        except Exception:
            pass
    except Exception:
        pass


_install_crash_logging()

LOG_LEVEL = logging.INFO
try:
    if not os.path.exists('LOG') or not os.path.exists('Config/HistoryPath'):
        try:
            os.mkdir('LOG')
        except:
            pass
        try:
            os.mkdir('Config/HistoryPath')
        except:
            pass
    else:
        pass
except FileNotFoundError:
    print("Failed to create LOG directory")
    sys.exit(1)


def configure_logger(log_file=f"./LOG/app_log.log"):
    # 创建 logger
    log_ger = logging.getLogger("flu_widget")
    log_ger.setLevel(LOG_LEVEL)

    # 移除已有的 handler
    for hdlr in log_ger.handlers[:]:
        log_ger.removeHandler(hdlr)

    try:
        # 创建 FileHandler
        handler = logging.FileHandler(log_file, 'a', encoding='utf-8')
        handler.setLevel(LOG_LEVEL)

        # 创建 StreamHandler
        console = logging.StreamHandler()
        console.setLevel(LOG_LEVEL)

        # 创建 Formatter
        formatter = logging.Formatter('%(asctime)s - %(name)s:[%(levelname)s] %(message)s')

        # 设置 Formatter
        handler.setFormatter(formatter)
        console.setFormatter(formatter)

        # 添加 handler 到 logger
        log_ger.addHandler(handler)
        log_ger.addHandler(console)

        return log_ger
    except IOError as error_config:
        log_ger.error(f"Failed to open log file: {error_config}")


logger = configure_logger()
try:
    if not os.path.isdir('./Config'):
        os.mkdir('./Config')
        logger.info('创建目录成功')
    else:
        logger.info('Config目录已存在')
        pass
except FileNotFoundError:
    logger.error("Config directory not found")
    sys.exit(1)


# 游客模式下需要登录才能使用的功能（好友 / 朋友圈）
GUEST_LOCKED_KEYS = ("P2PNWT", "MOMENTS")
GUEST_LOCKED_ROUTE_KEYS = ("p2pnwt", "moments")


class _GuestLockedPage(QFrame):
    """游客模式下好友/朋友圈的占位页：提示请登录，并提供去登录入口。"""

    def __init__(self, name: str, feature: str, parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self._feature = feature
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 20, 28, 16)
        lay.addStretch(2)

        icon = IconWidget(FIF.PEOPLE, self)
        icon.setFixedSize(96, 96)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)

        lay.addWidget(SubtitleLabel("请登录后使用"), 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(BodyLabel(f"「{feature}」需要登录账号，登录后即可使用。"),
                      0, Qt.AlignmentFlag.AlignHCenter)

        btn = PushButton(FIF.PEOPLE, "去登录")
        btn.setFixedWidth(140)
        btn.clicked.connect(self._go_login)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(3)

    def _go_login(self):
        # 关闭主页面，从头走启动登录流程
        relogin = getattr(self.window(), "requestRelogin", None)
        if callable(relogin):
            relogin()

    def showEvent(self, event):
        super().showEvent(event)
        window = self.window()
        if window is not None:
            InfoBar.warning("请登录", f"「{self._feature}」需要登录账号，请在「用户设置」中登录",
                            parent=window, position=InfoBarPosition.TOP, duration=2500)


class MainWindow(FluentWindow):
    """ Main Interface """

    def __init__(self, check_update: bool = True):
        super().__init__()
        self.AiChatInterface = None  # AI对话界面
        self.P2Pwidget = None  # 好友
        self.momentsInterface = None  # 朋友圈
        self.list_interfaces = []  # 界面列表
        self.homeInterface = None  # 首页
        self.extendedCallscriptInterface = None  # 扩展调用脚本界面
        self.showDigInfoInterface = None  # 显示信息界面
        self.P2PChatInterface = None # 好友聊天界面
        self.imageStudioInterface = None  # 图片工坊
        self.musicPlayerInterface = None  # 音乐播放器
        self.videoPlayerInterface = None  # 视频播放器
        self.sysInfoInterface = None  # 电脑配置
        self.wallpaperInterface = None  # 壁纸设置
        self.settingInterface = None  # 配置界面
        self.new_flag = False  # 是否有新版本
        self._is_closing = False  # 关闭标志
        self._logout_requested = False  # 退出登录标志（回登录界面）
        self._is_guest = not ((cfg.auth_token.value or "").strip()
                              or (RUNTIME.get("token") or "").strip())  # 游客模式
        self._guest_locked = {}  # 游客锁定的功能：key -> 占位页
        self.plugin_host = None  # 插件宿主接口
        self._plugin_widgets = []  # 已加载的插件界面
        self._update_task = None  # 后台检查更新的 QThread（启动时只跑一次）
        self.initInterface()  # 初始化界面
        self.initWindow()  # 初始化窗口
        if check_update:
            self.check_version()  # 检查版本（重新登录时跳过，避免重复弹窗）
        self.initTrayIcon()  # 初始化托盘图标
        signalBus.switchToSampleCard.connect(self.switchToSample)  # 切换到样本卡


    def initInterface(self):


        from src_ui.ai_chat_app import ChatWindow

        from src_ui.memo_ui import ShowDigInfoWidget

        self.homeInterface = HomeInterface()
        self.list_interfaces.append(self.homeInterface)
        self.addSubInterface(self.homeInterface, FIF.HOME, '首页', NavigationItemPosition.TOP)

        active = get_active_interfaces()
        order = get_active_order()
        active_keys = {item['key'] for item in active}
        module_logger.info(f"active_keys: {active_keys}")
        sorted_active = [item for item in order if item in active_keys]
        module_logger.info(f"sorted_active: {sorted_active}")
        widget_cls_map = {
            'ChatWindow': ChatWindow,
            'ShowDigInfoWidget': ShowDigInfoWidget,
            'ExtendedCallScriptWidget': ExtendedCallScriptWidget,
            'FriendsWidget': FriendsWidget,
            'MomentsWidget': MomentsWidget,
            'ImageStudioWidget': ImageStudioWidget,
            'MusicPlayerWidget': MusicPlayerWidget,
            'VideoPlayerWidget': VideoPlayerWidget,
            'SysInfoWidget': SysInfoWidget,
            'WallpaperWidget': WallpaperWidget,
        }

        # 侧边栏图标：优先使用 controls 图标库（ICON_MAP），缺失时回退 FluentIcon
        from src_ui import ICON_MAP
        icon_map = {}
        for _k, _path in ICON_MAP.items():
            try:
                icon_map[_k] = QIcon(_path)
            except Exception:
                pass
        icon_map.update({
            'TILES': FIF.TILES,
            'ASTERISK': FIF.ASTERISK,
            'BASKETBALL': FIF.BASKETBALL,
            'CHAT': FIF.CHAT,
            'CERTIFICATE': FIF.CERTIFICATE,
            'HIDE': FIF.HIDE,
            'CAR': FIF.CAR,
            'SYNC': FIF.SYNC,
            'DOCUMENT': FIF.DOCUMENT,
            'CAFE': FIF.CAFE,
            'CHECKBOX': FIF.CHECKBOX,
            'BUS': FIF.BUS,
            'PENCIL_INK': FIF.PENCIL_INK,
            'ROBOT': FIF.ROBOT,
            'DATE_TIME': FIF.DATE_TIME,
            'BOOK_SHELF': FIF.BOOK_SHELF,
            'APPLICATION': FIF.APPLICATION,
            'SEARCH_MIRROR': FIF.SEARCH_MIRROR,
            'LANGUAGE': FIF.LANGUAGE,
            'SCROLL': FIF.SCROLL,
            'DICTIONARY': FIF.DICTIONARY,
            'DOWNLOAD': FIF.DOWNLOAD,
            'TAG': FIF.TAG,
            'CONNECT': FIF.CONNECT,
        })

        attr_map = {
            'AICHAT': 'AiChatInterface',
            'DIAINFO': 'showDigInfoInterface',
            'EXTEND': 'extendedCallscriptInterface',
            'P2PNWT':  'P2PChatInterface',
            'MOMENTS': 'momentsInterface',
            'IMGSTUDIO': 'imageStudioInterface',
            'MUSIC': 'musicPlayerInterface',
            'VIDEO': 'videoPlayerInterface',
            'SYSINFO': 'sysInfoInterface',
            'WALLPAPER': 'wallpaperInterface',
        }

        created = {}
        parent_map = get_parent_map()

        # 拓扑排序：确保父菜单排在子菜单前面
        def _topo_sort(keys, p_map):
            result = []
            visited = set()
            visiting = set()

            def dfs(key):
                if key in visited:
                    return
                if key in visiting:
                    return
                visiting.add(key)
                parent = p_map.get(key)
                if parent and parent in keys:
                    dfs(parent)
                visiting.remove(key)
                visited.add(key)
                result.append(key)

            for key in keys:
                dfs(key)
            return result

        sorted_active = _topo_sort(sorted_active, parent_map)
        # 第一步：创建所有widget（游客模式下好友/朋友圈用占位页顶替）
        for key in sorted_active:
            item = next(i for i in active if i['key'] == key)
            if self._is_guest and key in GUEST_LOCKED_KEYS:
                widget = _GuestLockedPage(item['obj_name'], item['display_name'], self)
                self._guest_locked[key] = widget
                module_logger.info(f"[游客] {item['display_name']}使用占位页（需登录）")
            else:
                widget_cls = widget_cls_map[item['widget_cls']]
                widget = widget_cls(item['obj_name'], self)

            module_logger.info(f"[initInterface] 创建界面: %s" % key)
            created[key] = widget
            setattr(self, attr_map[key], widget)
            self.list_interfaces.append(widget)

        # 第二步：按顺序添加到导航（此时所有parent都已创建）
        for key in sorted_active:
            item = next(i for i in active if i['key'] == key)
            parent_key = parent_map.get(key)
            parent_widget = created.get(parent_key) if parent_key else None
            widget = created[key]
            tooltip = item['tooltip']
            nav_item = self.addSubInterface(widget, icon_map.get(item['icon'], FIF.APPLICATION), item['display_name'],
                                            parent=parent_widget)
            nav_item.setToolTip(tooltip)

        # 插件系统
        self.plugin_host = HostApi(self)
        self._load_plugins()
        # 登录用户设置（排在设置上方）
        self.userInterface = UserSettingPage('userSettings', self)
        self.addSubInterface(self.userInterface, FIF.PEOPLE, '用户设置',
                             NavigationItemPosition.BOTTOM)
        # 设置
        self.settingInterface = SettingWidget('setting', self)
        self.addSubInterface(self.settingInterface, FIF.SETTING, '设置', NavigationItemPosition.BOTTOM)
        self.list_interfaces.append(self.settingInterface)

    def _get_plugins_dir(self):
        """插件目录：exe 旁边优先（打包后别人可直接往这里丢插件），
        其次 exe 包内的 plugins（用 --add-data 内置的插件）。"""
        candidate = None
        if getattr(sys, "frozen", False):  # PyInstaller 打包后
            candidate = os.path.join(os.path.dirname(sys.executable), "plugins")
        else:
            candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
        if os.path.isdir(candidate):
            return candidate
        bundled = getattr(sys, "_MEIPASS", None)
        if bundled:
            inner = os.path.join(bundled, "plugins")
            if os.path.isdir(inner):
                return inner
        return candidate

    @staticmethod
    def _plugin_doc_text():
        """插件分组页顶部的使用说明（纯文本）"""
        from src_ui import TEXT_DICT
        
        return TEXT_DICT[261]

    def _load_plugins(self):
        """首次扫描插件目录并挂载界面，挂载「重新加载插件」按钮。

        - 插件放 plugins/<插件名>/main.py，导出 PluginBase 子类
        - 修改插件后点击「重新加载插件」按钮即热重载，无需重启程序
        - 单个插件加载失败只记录日志，不影响主程序
        """
        plugin_group = QWidget()
        plugin_group.setObjectName("pluginGroup")
        group_layout = QVBoxLayout(plugin_group)
        group_layout.setContentsMargins(16, 16, 16, 16)
        group_layout.setSpacing(8)
        self._plugin_group_widget = plugin_group

        btn_row = QHBoxLayout()
        self._plugin_reload_btn = PushButton(FIF.SYNC, "重新加载插件")
        self._plugin_reload_btn.clicked.connect(self._reload_plugins)
        btn_row.addWidget(self._plugin_reload_btn)

        self._open_reload_btn = PushButton(FIF.FILTER, "打开插件目录")
        self._open_reload_btn.clicked.connect(self._open_plugins)
        btn_row.addWidget(self._open_reload_btn)

        group_layout.addLayout(btn_row)


        # 上半：插件使用说明（纯文本，占大部分空间）
        self._plugin_doc_browser = QTextBrowser()
        self._plugin_doc_browser.setFont(QFont("Microsoft YaHei", 10))
        self._plugin_doc_browser.setPlainText(MainWindow._plugin_doc_text())
        group_layout.addWidget(self._plugin_doc_browser, 1)

        # 下半：重新加载插件的信息展示（固定小区域，每次重载清空重写）
        self._plugin_info_browser = QTextBrowser()
        self._plugin_info_browser.setFont(QFont("Microsoft YaHei", 10))
        self._plugin_info_browser.setMaximumHeight(160)
        group_layout.addWidget(self._plugin_info_browser)



        self.addSubInterface(plugin_group, FIF.CODE, "插件", NavigationItemPosition.BOTTOM)

        self._reload_plugins(first=True)

    def _open_plugins(self):
        # 获取绝对路径
        if getattr(sys, 'frozen', False):
            # 打包成exe后，sys.executable 是exe文件的完整路径
            current_path = os.path.dirname(sys.executable)
        else:
            # 开发环境，__file__ 是当前脚本文件的路径
            current_path = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(current_path, "plugins")
        try:
            os.startfile(path)
            module_logger.info(f"打开路径：{path}")
        except Exception as e:
            module_logger.info(f"打开路径失败：{str(e)}")

    # ── 热重载 ──────────────────────────────────────────────
    def _reload_plugins(self, first=False):
        """卸载旧插件 → 清除模块缓存 → 重新扫描 → 挂载新界面。

        first=True 为首次加载（只记日志不弹通知）。
        """
        plugins_dir = self._get_plugins_dir()
        failures = []
        self._unmount_plugins()
        purge_loaded_modules()
        try:
            plugin_list = discover_plugins(plugins_dir, logger=module_logger,
                                           failures=failures)
        except Exception as e:
            module_logger.error(f"[插件] 扫描失败: {e}")
            plugin_list = []

        for plugin, main_py in plugin_list:
            try:
                self._add_plugin_interface(plugin, main_py)
            except Exception as e:
                module_logger.error(f"[插件] 初始化失败 ({main_py}): {e}")
                failures.append((os.path.basename(main_py), str(e)))

        self._refresh_plugin_info(plugin_list, failures, is_first=first)
        if first:
            module_logger.info(f"[插件] 首次加载完成: {len(plugin_list)} 个插件")
        else:
            self.plugin_host.show_success("插件热重载完成", f"当前加载 {len(plugin_list)} 个插件")

    def _unmount_plugins(self):
        """卸载当前所有插件界面与导航条目（热重载前调用）"""
        for widget in self._plugin_widgets:
            route_key = widget.objectName()
            try:
                self.navigationInterface.removeWidget(route_key)
            except Exception as e:
                module_logger.error(f"[插件] 移除导航失败 {route_key}: {e}")
            self.stackedWidget.removeWidget(widget)
            widget.deleteLater()
        self._plugin_widgets.clear()

    def _add_plugin_interface(self, plugin, main_py):
        """把单个插件挂载到导航与堆叠界面"""
        meta = plugin.meta
        host = HostApi(self, namespace=meta.obj_name or meta.name)
        widget = plugin.create_widget(host, self)
        widget.setObjectName(meta.obj_name or f"plugin_{meta.name}")
        icon = getattr(FIF, meta.icon, FIF.APPLICATION)
        nav_item = self.addSubInterface(widget, icon, meta.name, parent=self._plugin_group_widget)
        if meta.description:
            nav_item.setToolTip(meta.description)
        self._plugin_widgets.append(widget)
        module_logger.info(f"[插件] 加载成功: {meta.name} v{meta.version} <- {main_py}")

    def _refresh_plugin_info(self, plugin_list, failures=None, is_first=False):
        """把本次重载结果写入信息展示控件（每次先清空，不保留历史）"""
        from datetime import datetime
        browser = self._plugin_info_browser
        browser.clear()
        now = datetime.now().strftime("%H:%M:%S")
        if is_first:
            browser.append(f"[{now}] 首次加载完成，共 {len(plugin_list)} 个插件")
        else:
            browser.append(f"[{now}] 热重载完成，共 {len(plugin_list)} 个插件")
        if plugin_list:
            for plugin, main_py in plugin_list:
                meta = plugin.meta
                browser.append(f"  [✓] {meta.name} v{meta.version} —— {meta.description}")
        else:
            browser.append("  当前未发现任何插件")
        for entry, err in failures or []:
            browser.append(f"  [✗] {entry} 加载失败: {err}")

    def switchToSample(self, routeKey, index):
        """ switch to sample """
        if self._is_guest and routeKey in GUEST_LOCKED_ROUTE_KEYS:
            InfoBar.warning("请登录", "该功能需要登录账号，请在「用户设置」中登录",
                            parent=self, position=InfoBarPosition.TOP, duration=3000)
        for w in self.list_interfaces:
            if w.objectName() == routeKey:
                self.stackedWidget.setCurrentWidget(w, False)


    def check_version(self):
        """启动后台线程检查更新。启动检查完全独立于设置页的「检查更新」，
        两者都走 src_ui.update_manager 的统一入口。"""
        fetch_update_info_threaded(self, self._on_update_check_result)

    def _on_update_check_result(self, info):
        self.new_flag = bool(info and "version" in info and "error" not in info)
        should_exit = show_update_dialog(self, info)
        if should_exit:
            module_logger.info("用户触发了自动安装，退出主程序让 updater.bat 接管")
            self.quitApp()

    def initWindow(self):
        if cfg.appWindowSize.value:
            size = cfg.appWindowSize.value
            self.resize(*map(int, size.split('x')))
        else:
            self.resize(1000, 780)
        self.setWindowTitle(f'NoMY ' + VERSION)
        self.setWindowIcon(QIcon('.\\Config\\image\\menu.ico'))
        # 导航面板展开时使用浮层覆盖模式（与服务器一致），不推开内容
        self.navigationInterface.setMinimumExpandWidth(2000)
        self.titleBar.setDoubleClickEnabled(False)
        # 设置导航栏下功能层级下拉自动收起 - 通过事件过滤器实现
        self._installAutoCollapseFilter()
        useani_flag = cfg.nativebar.value
        # 设置导航栏是否打开
        if useani_flag:
            self.navigationInterface.expand(useAni=False)
        else:
            pass
        self.center()
        self.splashScreen = SplashScreen(QIcon("./Config/image/controls/gougou.png"), self)
        self.splashScreen.setIconSize(QSize(302, 302))
        # 标题栏隐藏按钮
        self.splashScreen.titleBar.setDoubleClickEnabled(False)
        self.splashScreen.titleBar.maxBtn.hide()
        self.splashScreen.titleBar.minBtn.hide()
        self.splashScreen.titleBar.closeBtn.hide()
        self.show()
        self.createSubInterface()
        self.splashScreen.finish()

    def center(self):
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()

        window_size = self.geometry()

        new_x = (screen_geometry.width() - window_size.width()) // 2
        new_y = (screen_geometry.height() - window_size.height()) // 2

        self.move(new_x, new_y)

    def _installAutoCollapseFilter(self):
        """安装自动收起的事件过滤器"""
        QTimer.singleShot(100, self._connectExpandedSignals)

    def _connectExpandedSignals(self):
        """连接所有非叶子树节点的展开信号（包含所有层级）"""
        for widget in self.navigationInterface.panel.findChildren(NavigationTreeWidget):
            if not widget.isLeaf():
                widget.expandAni.valueChanged.connect(lambda _, w=widget: self._onTreeExpanding(w))

    def _onTreeExpanding(self, expanding_widget):
        """当某个树节点开始展开动画时，收起其他已展开菜单"""
        if not expanding_widget.isExpanded:
            return

        ancestors = set()
        parent = expanding_widget.treeParent
        while parent:
            ancestors.add(parent)
            parent = parent.treeParent

        for widget in self.navigationInterface.panel.findChildren(NavigationTreeWidget):
            if widget == expanding_widget or widget in ancestors:
                continue
            if widget.isExpanded:
                widget.setExpanded(False, ani=True)

    def createSubInterface(self):
        loop = QEventLoop(self)
        QTimer.singleShot(500, loop.quit)
        loop.exec()

    def initTrayIcon(self):
        # 创建托盘图标
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setToolTip('NoMY')
        self.tray_icon.setIcon(self.windowIcon())

        # 创建托盘菜单
        self.tray_menu = QMenu(self)

        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.show)
        self.tray_menu.addAction(show_action)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quitApp)
        self.tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.show()

        # 连接双击事件
        self.tray_icon.activated.connect(self.onTrayIconActivated)

    def onTrayIconActivated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()

    def quitApp(self):
        self._is_closing = True
        self.close()
        sys.exit(0)

    def requestRelogin(self):
        """退出登录：关闭主界面，由 __main__ 循环重新拉起登录窗口。"""
        self._logout_requested = True
        try:
            signalBus.switchToSample.disconnect(self.switchToSample)
        except Exception:
            pass
        try:
            self.tray_icon.hide()  # 避免重新登录后出现重复托盘图标
        except Exception:
            pass
        self._is_closing = True
        self.close()

    def closeEvent(self, event):
        if self._is_closing:
            event.accept()
            return

        try:
            window_exit_flag = cfg.windowexit.value
            module_logger.info(f"程序操作:{window_exit_flag}")

            # 直接退出的情况
            if window_exit_flag == "退出程序":
                self._is_closing = True
                event.accept()
                return

            # 直接最小化的情况
            if window_exit_flag == "最小化到托盘":
                event.ignore()  # 忽略关闭事件
                self.hide()
                return

                # 弹出选择框情况
            Res_quit = ResMessageBox(self)
            if not Res_quit.exec():
                event.ignore()  # 用户取消关闭
                return

            # 处理用户选择
            remember_choice = Res_quit.res_checkbox.isChecked()
            minimize_selected = Res_quit.res_radio_1.isChecked()
            exit_selected = Res_quit.res_radio_2.isChecked()

            if remember_choice:
                cfg.windowexit.value = "最小化到托盘" if minimize_selected else "退出程序"
                cfg.save()

            if minimize_selected:
                event.ignore()
                self.hide()
            elif exit_selected:
                self._is_closing = True
                event.accept()

        except Exception as e:
            module_logger.error(f"关闭事件处理出错: {e}")
            self._is_closing = True
            event.accept()


class ResMessageBox(MessageBoxBase):
    """ Custom message box """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel()
        self.titleLabel.setText("选择退出操作")
        self.res_radio_1 = RadioButton()
        self.res_radio_1.setText("最小化到托盘")
        self.res_radio_2 = RadioButton()
        self.res_radio_2.setText("退出程序")
        self.res_checkbox = CheckBox()
        self.res_checkbox.setText("记住我的选择,下次不再提示")

        # 增加组件到布局
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.res_radio_1)
        self.viewLayout.addWidget(self.res_radio_2)
        self.viewLayout.addWidget(self.res_checkbox)

        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.res_radio_1.setChecked(True)
        # 设置对话框的最小宽度
        self.widget.setMinimumWidth(350)


def is_program_running(EXE_NAME):
    pid_list = []
    for process in psutil.process_iter(attrs=['pid', 'name']):
        if process.info['name'] == EXE_NAME:
            pid_list.append(process.info['pid'])
    if pid_list:
        return pid_list
    else:
        return pid_list


if __name__ == '__main__':
    exe_pid = is_program_running("NoMY.exe")
    if len(exe_pid) > 1:
        module_logger.info("程序已启动")
        # 弹窗提示
        app = QApplication([])
        msg_box = QMessageBox()
        msg_box.warning(msg_box, "提示", "程序已启动,请勿重复启动")
    else:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication(sys.argv)

        # 登录 → 主界面 循环：退出登录后重新回到登录界面
        from src_ui.login_dialog import LoginDialog
        first_login = True
        while True:
            login_dialog = LoginDialog()
            if not login_dialog.exec():
                module_logger.info("用户取消了登录，退出程序")
                sys.exit(0)

            w = MainWindow(check_update=first_login)
            first_login = False
            w.show()
            app.exec()

            if getattr(w, "_logout_requested", False):
                module_logger.info("已退出登录，返回登录界面")
                continue
            break
        sys.exit(0)

# pyinstaller --noconfirm --onedir --windowed --icon "Config\image\menu.ico" --name "NoMY" fluwidget.py
