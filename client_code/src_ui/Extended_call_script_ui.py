# -*- coding: utf-8 -*-
"""启动器（Starward 风格）

- 每页 2×2 四个启动项卡片，PipsPager 左右翻页（可鼠标滚轮）
- 卡片显示对应 EXE 的真实图标（ctypes 提取，失败回退 Fluent 图标）
- 数据沿用 Config/ExtendedCallScript.json：[[名称, 路径], ...]
"""
import ctypes
import json
import logging
import os
import sys
from ctypes import wintypes

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout,
                               QWidget)

from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, FluentIcon as FIF,
                            FluentIconBase, HorizontalPipsPager, IconWidget, InfoBar,
                            InfoBarPosition, LineEdit, MessageBox, MessageBoxBase,
                            PrimaryPushButton, PushButton, PipsScrollButtonDisplayMode,
                            SubtitleLabel, TitleLabel, ToolButton)

from .information_history import (create_Warning_info, load_last_directory,
                                      save_last_directory)
from src.Extended_Call_Script import ExtendedCallScript

module_logger = logging.getLogger("flu_widget.Extended_call_script_ui")

HISTORY_FILE_PATH = '../Config/HistoryPath/.LastDirectoryExtend'
TOOL_LIST_PATH = './Config/ExtendedCallScript.json'
PER_PAGE = 4


def get_file_icon(path: str) -> FluentIconBase:
    ext = os.path.splitext(path)[-1].lower()
    return {
        '.exe': FIF.APPLICATION,
        '.bat': FIF.COMMAND_PROMPT,
        '.sh': FIF.COMMAND_PROMPT,
        '.py': FIF.CODE,
    }.get(ext, FIF.DOCUMENT)


# ── EXE 图标提取（ctypes，带缓存） ──────────────────────────
_ICON_CACHE: dict = {}


def extract_exe_icon(path: str) -> QIcon | None:
    """提取 exe 第一图标为 QIcon；失败返回 None。"""
    try:
        key = (path.lower(), os.path.getmtime(path))
    except OSError:
        return None
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    icon = None
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    gdi32 = ctypes.windll.gdi32
    large = wintypes.HICON()
    small = wintypes.HICON()
    try:
        if shell32.ExtractIconExW(str(path), 0, ctypes.byref(large),
                                  ctypes.byref(small), 1) > 0 and large:
            class ICONINFO(ctypes.Structure):
                _fields_ = [("fIcon", wintypes.BOOL),
                            ("xHotspot", wintypes.DWORD),
                            ("yHotspot", wintypes.DWORD),
                            ("hbmMask", wintypes.HBITMAP),
                            ("hbmColor", wintypes.HBITMAP)]

            info = ICONINFO()
            if user32.GetIconInfo(large, ctypes.byref(info)):
                class BITMAP(ctypes.Structure):
                    _fields_ = [("bmType", wintypes.LONG),
                                ("bmWidth", wintypes.LONG),
                                ("bmHeight", wintypes.LONG),
                                ("bmWidthBytes", wintypes.LONG),
                                ("bmPlanes", wintypes.WORD),
                                ("bmBitsPixel", wintypes.WORD),
                                ("bmBits", ctypes.c_void_p)]

                bmp = BITMAP()
                if gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(BITMAP),
                                    ctypes.byref(bmp)):
                    w, h = bmp.bmWidth, abs(bmp.bmHeight)

                    class BMIH(ctypes.Structure):
                        _fields_ = [("biSize", wintypes.DWORD),
                                    ("biWidth", wintypes.LONG),
                                    ("biHeight", wintypes.LONG),
                                    ("biPlanes", wintypes.WORD),
                                    ("biBitCount", wintypes.WORD),
                                    ("biCompression", wintypes.DWORD),
                                    ("biSizeImage", wintypes.DWORD),
                                    ("biXPelsPerMeter", wintypes.LONG),
                                    ("biYPelsPerMeter", wintypes.LONG),
                                    ("biClrUsed", wintypes.DWORD),
                                    ("biClrImportant", wintypes.DWORD)]

                    class BMI(ctypes.Structure):
                        _fields_ = [("bmiHeader", BMIH)]

                    bmi = BMI()
                    bmi.bmiHeader.biSize = ctypes.sizeof(BMIH)
                    bmi.bmiHeader.biWidth = w
                    bmi.bmiHeader.biHeight = -h   # 自上而下
                    bmi.bmiHeader.biPlanes = 1
                    bmi.bmiHeader.biBitCount = 32
                    bmi.bmiHeader.biCompression = 0  # BI_RGB
                    buf = ctypes.create_string_buffer(w * h * 4)
                    hdc = user32.GetDC(0)
                    gdi32.GetDIBits(hdc, info.hbmColor, 0, h, buf,
                                    ctypes.byref(bmi), 0)  # DIB_RGB_COLORS
                    user32.ReleaseDC(0, hdc)
                    img = QImage(buf, w, h, w * 4,
                                 QImage.Format.Format_ARGB32).copy()
                    # 无 alpha 通道的图标整体置不透明
                    if img.allGray() and img.pixelColor(0, 0).alpha() == 255:
                        pass
                    icon = QIcon(QPixmap.fromImage(img))
            if info.hbmMask:
                gdi32.DeleteObject(info.hbmMask)
            if info.hbmColor:
                gdi32.DeleteObject(info.hbmColor)
    except Exception as e:
        module_logger.debug(f"提取图标失败 {path}: {e}")
    finally:
        if large:
            user32.DestroyIcon(large)
        if small:
            user32.DestroyIcon(small)
    _ICON_CACHE[key] = icon
    return icon


class ResMessageBox(MessageBoxBase):
    """启动项命名对话框。"""

    def __init__(self, file_name: str, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("命名启动项")
        self.res_lineedit = LineEdit()
        self.res_lineedit.setPlaceholderText("启动项名称")
        self.res_lineedit.setClearButtonEnabled(True)
        self.res_lineedit.setText(os.path.splitext(file_name)[0])

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.res_lineedit)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(400)


class LauncherCard(CardWidget):
    """单个启动项卡片（Starward 风格）。"""

    removeRequested = Signal(str, str)  # name, path

    def __init__(self, tool_name: str, tool_path: str, parent=None):
        super().__init__(parent)
        self.tool_name = tool_name
        self.tool_path = tool_path

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)

        layout.setContentsMargins(22, 18, 22, 16)

        icon_label = QLabel()
        exe_icon = extract_exe_icon(tool_path) if tool_path.lower().endswith(".exe") else None
        if exe_icon is not None:
            icon_label.setPixmap(exe_icon.pixmap(96, 96))
        else:
            iw = IconWidget(get_file_icon(tool_path), icon_label)
            iw.setFixedSize(88, 88)
        icon_label.setFixedHeight(100)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        self.nameLabel = TitleLabel(tool_name)
        self.nameLabel.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.nameLabel)

        path_label = CaptionLabel(tool_path)
        path_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        path_label.setWordWrap(False)
        layout.addWidget(path_label)
        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.launchBtn = PrimaryPushButton(FIF.PLAY, "启 动")
        self.launchBtn.setFixedHeight(40)
        self.launchBtn.setMinimumWidth(150)
        self.launchBtn.clicked.connect(self._on_launch)
        btn_row.addWidget(self.launchBtn)
        del_btn = ToolButton(FIF.DELETE)
        del_btn.setFixedSize(40, 40)
        del_btn.setToolTip("移除")
        del_btn.clicked.connect(lambda: self.removeRequested.emit(self.tool_name,
                                                                  self.tool_path))
        btn_row.addWidget(del_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

    def _on_launch(self):
        if not os.path.isfile(self.tool_path):
            create_Warning_info(self, "启动失败", f"文件不存在：{self.tool_path}")
            return
        try:
            ExtendedCallScript(self.tool_path)
            module_logger.info(f"启动器执行: {self.tool_name} <- {self.tool_path}")
        except Exception as e:
            create_Warning_info(self, "启动失败", str(e))


class LauncherEmptyPage(QWidget):
    """空态引导页。"""

    addRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 12, 24, 12)
        layout.setSpacing(14)
        layout.addStretch(2)

        icon = IconWidget(FIF.APPLICATION, self)
        icon.setFixedSize(88, 88)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)

        layout.addWidget(TitleLabel("还没有启动项"), 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(BodyLabel("把常用的程序或脚本添加进来，一键快速启动"),
                         0, Qt.AlignmentFlag.AlignHCenter)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.addBtn = PrimaryPushButton(FIF.ADD, "添加启动项")
        self.addBtn.setFixedWidth(180)
        self.addBtn.clicked.connect(self.addRequested.emit)
        btn_row.addWidget(self.addBtn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        layout.addStretch(3)


class WheelPager(HorizontalPipsPager):
    """支持鼠标滚轮翻页、放大显示的 PipsPager。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setVisibleNumber(15)
        self.setPreviousButtonDisplayMode(PipsScrollButtonDisplayMode.ALWAYS)
        self.setNextButtonDisplayMode(PipsScrollButtonDisplayMode.ALWAYS)

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            self.scrollPrevious()
        else:
            self.scrollNext()
        event.accept()


class LauncherPage(QWidget):
    """一页最多 4 个启动项（2×2 网格），滚轮翻页。"""

    wheelMoved = Signal(int)  # angleDelta().y()

    def __init__(self, cards: list, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(16, 8, 16, 8)
        grid.setSpacing(12)
        for i, card in enumerate(cards):
            grid.addWidget(card, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

    def wheelEvent(self, event):
        self.wheelMoved.emit(event.angleDelta().y())
        event.accept()


class ExtendedCallScriptWidget(QFrame):
    """启动器主界面：每页 4 个启动项 + PipsPager 翻页。"""

    def __init__(self, name: str, parent=None):
        super().__init__(parent=parent)
        self._cards: list[LauncherCard] = []
        self._pages: list[LauncherPage] = []
        self._syncing = False
        self._build_ui()
        self._bind()
        try:
            self.load_tool_list()
        except Exception as e:
            module_logger.warning(f"加载启动项失败: {e}")
        self.setObjectName(name)

    # ── UI ─────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 12)
        layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.addWidget(TitleLabel("启动器"))
        top_row.addStretch(1)
        self.addButton = PrimaryPushButton(FIF.ADD, "添加启动项")
        self.addButton.clicked.connect(self.add_card)
        top_row.addWidget(self.addButton)
        layout.addLayout(top_row)

        self.stack = QStackedWidget()
        self.emptyPage = LauncherEmptyPage()
        self.emptyPage.addRequested.connect(self.add_card)
        self.stack.addWidget(self.emptyPage)  # index 0 = 空态页
        layout.addWidget(self.stack, 1)

        self.pager = WheelPager()
        self.pager.setVisible(False)
        pager_row = QHBoxLayout()
        prev_btn = ToolButton(FIF.CARE_LEFT_SOLID)
        prev_btn.setFixedSize(36, 36)
        prev_btn.clicked.connect(self.pager.scrollPrevious)
        pager_row.addWidget(prev_btn)
        pager_row.addStretch(1)
        pager_row.addWidget(self.pager)
        pager_row.addStretch(1)
        next_btn = ToolButton(FIF.CARE_RIGHT_SOLID)
        next_btn.setFixedSize(36, 36)
        next_btn.clicked.connect(self.pager.scrollNext)
        pager_row.addWidget(next_btn)
        layout.addLayout(pager_row)

    def _bind(self):
        self.pager.currentIndexChanged.connect(self._on_pager_changed)

    # ── 页面构建 ────────────────────────────────────────
    def _current_page_index(self) -> int:
        w = self.stack.currentWidget()
        try:
            return self._pages.index(w)
        except ValueError:
            return 0

    def _rebuild_pages(self):
        # 清空旧页
        for p in self._pages:
            self.stack.removeWidget(p)
            p.deleteLater()
        self._pages = []
        # 按 4 个一页分块
        for i in range(0, len(self._cards), PER_PAGE):
            page = LauncherPage(self._cards[i:i + PER_PAGE])
            page.wheelMoved.connect(self._on_wheel)
            self._pages.append(page)
            self.stack.addWidget(page)
        n_pages = len(self._pages)
        self.pager.setVisible(n_pages >= 2)
        if n_pages >= 2:
            self.pager.setPageNumber(n_pages)
            self.pager.setCurrentIndex(min(self._current_page_index(), n_pages - 1))
        # 空态 / 页面切换
        if not self._cards:
            self.stack.setCurrentWidget(self.emptyPage)
        else:
            self.stack.setCurrentWidget(self._pages[min(self._current_page_index(),
                                                        len(self._pages) - 1)])

    def _on_pager_changed(self, index: int):
        if self._syncing or not (0 <= index < len(self._pages)):
            return
        self._syncing = True
        self.stack.setCurrentWidget(self._pages[index])
        self._syncing = False

    def _on_wheel(self, delta: int):
        if delta > 0:
            self.pager.scrollPrevious()
        else:
            self.pager.scrollNext()

    def _create_card(self, tool_name: str, tool_path: str):
        card = LauncherCard(tool_name, tool_path)
        card.removeRequested.connect(self.remove_card)
        self._cards.append(card)
        return card

    # ── 数据（沿用 ExtendedCallScript.json 的 [name, path] 格式） ──
    def load_tool_list(self):
        try:
            with open(TOOL_LIST_PATH, 'r', encoding='utf-8') as f:
                tools = json.load(f)
        except FileNotFoundError:
            os.makedirs('./Config', exist_ok=True)
            tools = []
        except Exception as e:
            module_logger.error(f"读取启动项配置失败: {e}")
            tools = []
        for tool in tools or []:
            if isinstance(tool, (list, tuple)) and len(tool) >= 2:
                self._create_card(str(tool[0]), str(tool[1]))
        self._rebuild_pages()

    def save_tool_list(self):
        try:
            with open(TOOL_LIST_PATH, 'w', encoding='utf-8') as f:
                json.dump([[c.tool_name, c.tool_path] for c in self._cards],
                          f, ensure_ascii=False, indent=4)
        except Exception as e:
            create_Warning_info(self, "保存失败", str(e))

    # ── 新增 / 移除 ─────────────────────────────────────
    def add_card(self):
        last_dir = load_last_directory(HISTORY_FILE_PATH)
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择程序或者脚本", last_dir, "EXE&bat (*.exe *.bat);;所有文件 (*)")
        if not file_path:
            return
        save_last_directory(HISTORY_FILE_PATH, os.path.dirname(file_path))

        name_text = ""
        res = ResMessageBox(os.path.basename(file_path), self)
        if res.exec():
            name_text = res.res_lineedit.text().strip()
        if not name_text:
            name_text = os.path.splitext(os.path.basename(file_path))[0]

        self._create_card(name_text, file_path)
        self.save_tool_list()
        self._rebuild_pages()
        # 跳到最后一页
        last_page = (len(self._cards) - 1) // PER_PAGE
        self.stack.setCurrentWidget(self._pages[last_page])
        self.pager.setCurrentIndex(last_page)
        module_logger.info(f"添加启动项: {name_text} <- {file_path}")

    def remove_card(self, tool_name: str, tool_path: str):
        w = MessageBox("移除启动项", f"确定移除「{tool_name}」？", self)
        if not w.exec():
            return
        self._cards = [c for c in self._cards
                       if not (c.tool_name == tool_name and c.tool_path == tool_path)]
        self.save_tool_list()
        self._rebuild_pages()
        module_logger.info("移除启动项完成")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = ExtendedCallScriptWidget('TEST')
    w.show()
    sys.exit(app.exec())
