# -*- coding: utf-8 -*-
"""桌面壁纸设置

- 左侧 QTableWidget 缩略图列表（点击 → 右侧预览）
- 下方按钮：选择文件夹、上一张/下一张（含直接套用）、设为壁纸
- 上次加载的文件夹路径写入 Config/config.json（Wallpaper.Folder），下次启动自动加载
- 支持：单图设壁纸、双屏独立设图、一图跨屏拼接、定时轮播
"""
import ctypes
import io
import logging
import os

from PIL import Image as PILImage
from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame,
                               QHBoxLayout, QHeaderView, QSizePolicy,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF,
                            HorizontalFlipView, InfoBar, InfoBarPosition, LineEdit,
                            PrimaryPushButton, PushButton, ScrollArea, SpinBox,
                            SubtitleLabel, TitleLabel, ToolButton)

from src_ui import cfg

module_logger = logging.getLogger("flu_widget.wallpaper")

SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02
DWPOS = {"居中": 0, "平铺": 1, "拉伸": 2, "适应": 3, "填充": 4, "跨屏拼接": 5}

CLSID_DESKTOP_WALLPAPER = "C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD"
IID_IDESKTOP_WALLPAPER = "B92B56A9-8B55-4E14-9A89-0199BBB6F93B"

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
THUMB_PX = 64
ROW_HEIGHT = 80
PREVIEW_MIN_W = 480
PREVIEW_MIN_H = 270
PREVIEW_RATIO = 16 / 9


def _scan_images(folder: str) -> list:
    if not folder or not os.path.isdir(folder):
        return []
    return [os.path.join(folder, f) for f in sorted(os.listdir(folder))
            if f.lower().endswith(IMAGE_EXTS)]


def _spi_set_wallpaper(path: str) -> bool:
    try:
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER, 0, path, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
        return True
    except Exception as e:
        module_logger.error(f"SPI 设置壁纸失败: {e}")
        return False


# ── IDesktopWallpaper COM（双屏/跨屏用，不可用时降级 SPI） ──
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _make_guid(s: str) -> _GUID:
    import re as _re
    hexs = _re.sub(r"[{}\-]", "", s)
    g = _GUID()
    g.Data1 = int(hexs[0:8], 16)
    g.Data2 = int(hexs[8:12], 16)
    g.Data3 = int(hexs[12:16], 16)
    for i in range(8):
        g.Data4[i] = int(hexs[16 + i * 2:18 + i * 2], 16)
    return g


class DesktopWallpaper:
    """IDesktopWallpaper COM 封装。"""

    def __init__(self):
        ctypes.oledll.ole32.CoInitializeEx(None, 2)
        self.ptr = ctypes.c_void_p()
        clsid = _make_guid(CLSID_DESKTOP_WALLPAPER)
        iid = _make_guid(IID_IDESKTOP_WALLPAPER)
        ctypes.oledll.ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(self.ptr))
        if not self.ptr.value:
            raise OSError("CoCreateInstance(IDesktopWallpaper) 失败")
        vtbl = ctypes.cast(ctypes.cast(self.ptr, ctypes.POINTER(ctypes.c_void_p)).contents,
                           ctypes.POINTER(ctypes.c_void_p))

        def fn(index, restype, *argtypes):
            proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
            return proto(vtbl[index])

        self._set_wallpaper = fn(3, ctypes.HRESULT, ctypes.c_wchar_p, ctypes.c_wchar_p)
        self._get_monitor_at = fn(5, ctypes.HRESULT, ctypes.c_uint,
                                  ctypes.POINTER(ctypes.c_wchar_p))
        self._get_monitor_count = fn(6, ctypes.HRESULT, ctypes.POINTER(ctypes.c_uint))
        self._set_position = fn(9, ctypes.HRESULT, ctypes.c_uint)

    def monitors(self) -> list:
        count = ctypes.c_uint(0)
        self._get_monitor_count(ctypes.byref(count))
        out = []
        for i in range(count.value):
            path_ptr = ctypes.c_wchar_p()
            self._get_monitor_at(i, ctypes.byref(path_ptr))
            if path_ptr.value:
                out.append(path_ptr.value)
        return out

    def set_wallpaper(self, path: str, monitor: str = None):
        self._set_wallpaper(monitor, path)

    def set_position(self, pos: int):
        self._set_position(pos)


def _get_wp() -> DesktopWallpaper | None:
    try:
        return DesktopWallpaper()
    except Exception as e:
        module_logger.warning(f"IDesktopWallpaper 不可用: {e}")
        return None


class _ThumbnailLoader(QThread):
    """后台生成缩略图。QImageReader.setScaledSize 只解码目标尺寸像素，
    大图也几乎瞬时；Qt 不识别的格式回落 PIL→PNG 再交给 Qt。"""

    thumb_ready = Signal(int, QPixmap)
    progress = Signal(int, int)
    failed = Signal(int)

    def __init__(self, paths: list[str], thumb_px: int, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._thumb_px = thumb_px
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        total = len(self._paths)
        target = self._thumb_px
        for i, path in enumerate(self._paths):
            if self._cancel:
                return
            pix = self._make_thumb(path, target)
            if self._cancel:
                return
            if pix is None:
                self.failed.emit(i)
            else:
                self.thumb_ready.emit(i, pix)
            self.progress.emit(i + 1, total)

    def _make_thumb(self, path: str, target: int) -> QPixmap | None:
        try:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and not size.isEmpty():
                longest = max(size.width(), size.height())
                if longest > target:
                    ratio = target / longest
                    reader.setScaledSize(
                        QSize(max(1, int(size.width() * ratio)),
                              max(1, int(size.height() * ratio))))
            img = reader.read()
            if not img.isNull():
                return QPixmap.fromImage(img)
        except Exception:
            pass
        try:
            with PILImage.open(path) as pil:
                pil.thumbnail((target, target))
                if pil.mode not in ("RGB", "RGBA"):
                    pil = pil.convert("RGB")
                buf = io.BytesIO()
                pil.save(buf, "PNG")
                img2 = QImageReader.fromData(buf.getvalue()).read()
                if not img2.isNull():
                    return QPixmap.fromImage(img2)
        except Exception:
            pass
        return None


class _WallpaperFlipView(HorizontalFlipView):
    """HorizontalFlipView 子类：按父容器实际宽度重算 itemSize。

    原生 HorizontalFlipView 不会自动跟随容器尺寸变化。这里用单一 single-shot
    QTimer 合并多个触发源（自身 resize / show / 外层 ScrollArea / preview_panel），
    避免触发器级联导致 widget 反复 setMinimumSize 撑大父容器。
    只设 itemSize，不动 minSize：layout 用 Expanding policy + sizeHint 把
    widget 钳到父容器可用空间，item 按 itemSize 渲染（被裁剪/留白都是可接受的）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._refit_timer = QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.setInterval(0)
        self._refit_timer.timeout.connect(self._refit_items)
        # 高度策略设为 Preferred：widget 高度 = sizeHint.height = max(item.height)
        # = itemSize.height。这样 widget 宽度由 Expanding 拉满父容器，
        # 高度跟随 itemSize（= w*9/16），比例自动维持。
        # 任何 setMinimumSize / setMaximumSize 都不能用 —— setMinimumSize
        # 会让父容器 (preview_panel) 跟着 flipView 的 minSize 撑大，
        # 还原时 preview_panel 的 minSize 残留，flipView 读到的 contentsRect
        # 永远是大值，widget 卡在最大化尺寸 → "自动放大"。
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)

    def _schedule_refit(self):
        # 合并触发：多次调用只会启动/重置同一个 timer，最终 _refit_items 只跑一次
        self._refit_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_refit()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_refit()

    def _refit_items(self):
        # 读父容器 (preview_panel QFrame) 的内容区宽度。父容器受 ScrollArea
        # 布局正确收缩，contentsRect 是真实可用空间。
        parent = self.parentWidget()
        if parent is not None:
            avail_w = max(parent.contentsRect().width(), 0)
        else:
            avail_w = 0
        w = max(avail_w, PREVIEW_MIN_W)
        target_h = int(w / PREVIEW_RATIO)
        target = QSize(w, target_h)
        if self.itemSize == target:
            return
        # 关键约束：
        # - 水平方向：minWidth 设下界，maxWidth 不设；让 Expanding policy
        #   把 widget 拉到父容器可用宽度（layout 自己处理）
        # - 垂直方向：minHeight = maxHeight = target_h，强制 widget 高度
        #   = target_h，从而维持 16:9 比例
        # 为什么不设 minWidth = w：会触发"preview_panel 跟着 flipView 的 minSize
        # 撑大 → 还原时 preview_panel 收不回 → 自动放大"循环。
        self.setMinimumWidth(PREVIEW_MIN_W)
        self.setMinimumHeight(target_h)
        self.setMaximumHeight(target_h)
        self.setItemSize(target)
        # itemSize 变化后必须按当前索引重新对齐 viewport。setCurrentIndexInstant
        # 内部用 self.width() 估算居中位置（同步可靠），并在 _refit_items 的同步
        # 路径上立即调用，无需延后到事件循环。
        # 注：直接父容器是 _PreviewPanel（QFrame），不持有 _images/_index；这里
        # 沿父链向上找到 WallpaperInterface（持有图片状态）。
        owner = self.parentWidget()
        while owner is not None and not hasattr(owner, "_images"):
            owner = owner.parentWidget()
        if owner is not None and 0 <= owner._index < self.count():
            self.setCurrentIndexInstant(owner._index)
        self.updateGeometry()
        self.viewport().update()

    def setCurrentIndexInstant(self, index: int):
        """立即跳转到指定索引，跳过基类 setCurrentIndex 的 500 ms 滚动动画。

        关键：基类 FlipView.setCurrentIndex → scrollToIndex → self.scrollBar.scrollTo
        写到一个独立的 SmoothScrollBar 子 widget，但 qfluentwidgets 的 FlipView
        并没有把这个 SmoothScrollBar 与 QListWidget.horizontalScrollBar() 同步，
        写过去对实际"显示哪一张"完全无效。所以必须直接驱动 QListWidget 自己的
        滚动条。

        居中算法（横向布局，spacing 视为 0 时实测 Qt 内部用的）：
            value = item_w * (index + 0.5) - vp_w * 0.5
        让 item[index] 的中心正好对齐 viewport 的水平中心，clamp 到合法范围。

        vp_w 取 widget.width() 而不是 viewport().width()：在 _refit_items 这种
        "setItemSize 刚刚改完 sizeHint 但 widget resize 还没传到 viewport"的
        时序里，viewport().width() 仍是旧值，会算出错位的 target；widget.width()
        由 resizeEvent 同步刷新，是真实可用尺寸（差异仅 frameWidth，1~2 像素，
        视觉上可接受）。
        """
        if not 0 <= index < self.count():
            return
        self._currentIndex = index
        if not self.count():
            return
        size_hint = self.item(index).sizeHint()
        if self.isHorizontal():
            item_primary = size_hint.width()
            vp_primary = self.width()
            sb = self.horizontalScrollBar()
        else:
            item_primary = size_hint.height()
            vp_primary = self.height()
            sb = self.verticalScrollBar()
        target = int(item_primary * (index + 0.5) - vp_primary * 0.5)
        target = max(sb.minimum(), min(target, sb.maximum()))
        sb.setValue(target)
        # 同步内部 SmoothScrollBar 的内部 __value，避免后续基类滚动动画以错误
        # 起点启动时出现"先跳一下再动画"的闪烁
        self.scrollBar.resetValue(target)
        if index == 0:
            self.preButton.fadeOut()
        elif self.preButton.isTransparent() and self.isHover:
            self.preButton.fadeIn()
        if index == self.count() - 1:
            self.nextButton.fadeOut()
        elif self.nextButton.isTransparent() and self.isHover:
            self.nextButton.fadeIn()
        self.currentIndexChanged.emit(index)


class _PreviewPanel(QFrame):
    """preview_panel 自定义 QFrame：自身 resize 时通知内部 flipView 重算。

    解决"flipView 被自身旧 minSize 卡死 → 自己的 resizeEvent 不触发 → 父级
    的 ScrollArea resize 触发的 _refit_items 又在几何未刷新时被调用"
    这条死循环。在 flipView 的 resizeEvent 之外多给一个触发源。
    """

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner

    def resizeEvent(self, event):
        super().resizeEvent(event)
        flip = getattr(self._owner, "flipView", None)
        if flip is not None:
            flip._schedule_refit()


class WallpaperInterface(ScrollArea):
    """壁纸设置界面：左侧缩略图列表 + 右侧大预览 + 底部快捷按钮。"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = QVBoxLayout(self.scrollWidget)
        self._images: list[str] = []
        self._index = 0
        self._rotation_images: list[str] = []
        self._rotation_pos = 0
        self._thumb_loader: _ThumbnailLoader | None = None
        self._build_ui()

        self._rotate_timer = QTimer(self)
        self._rotate_timer.timeout.connect(self._rotate_once)

        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setViewportMargins(0, 20, 12, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self._refresh_monitor_combo()
        QTimer.singleShot(0, self._restore_last_folder)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 主动推一把 flipView：触发单一 single-shot timer，会合并同一波事件
        if hasattr(self, "flipView") and self.flipView is not None:
            self.flipView._schedule_refit()
            # _schedule_refit 内部 0ms 后才执行 _refit_items；这里在 resize
            # 当下立刻同步一次索引，避免 _refit_items 触发前动画/动画残留导致
            # 显示位置与 _index 不一致（最大化 ↔ 标准窗口切换时尤为明显）。
            if self._images and 0 <= self._index < len(self._images):
                self.flipView.setCurrentIndexInstant(self._index)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "flipView") and self.flipView is not None:
            self.flipView._schedule_refit()
            if self._images and 0 <= self._index < len(self._images):
                self.flipView.setCurrentIndexInstant(self._index)

    # ── UI ─────────────────────────────────────────────
    def _build_ui(self):
        self.expandLayout.setSpacing(10)

        self.expandLayout.addWidget(TitleLabel("桌面壁纸"))

        # ── 上：左侧缩略图列表 + 右侧大预览 ──
        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        top_row.addWidget(self._build_list_panel(), 0)
        top_row.addWidget(self._build_preview_panel(), 1)
        self.expandLayout.addLayout(top_row)

        self.srcHint = CaptionLabel("选择单张图片或加载文件夹")
        self.srcHint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.expandLayout.addWidget(self.srcHint)

        # ── 中：底部快捷按钮 ──
        action_row = QHBoxLayout()
        self.folderBtn = PrimaryPushButton(FIF.FOLDER_ADD, "加载文件夹")
        self.folderBtn.clicked.connect(self._on_load_folder)
        action_row.addWidget(self.folderBtn)

        self.prevBtn = PushButton(FIF.CARE_LEFT_SOLID, "上一张")
        self.prevBtn.clicked.connect(lambda: self._step(-1, apply=False))
        action_row.addWidget(self.prevBtn)
        self.nextBtn = PushButton(FIF.CARE_RIGHT_SOLID, "下一张")
        self.nextBtn.clicked.connect(lambda: self._step(1, apply=False))
        action_row.addWidget(self.nextBtn)

        self.prevApplyBtn = ToolButton(FIF.CARE_LEFT_SOLID)
        self.prevApplyBtn.setToolTip("上一张并直接设为壁纸")
        self.prevApplyBtn.clicked.connect(lambda: self._step(-1, apply=True))
        action_row.addWidget(self.prevApplyBtn)
        self.nextApplyBtn = ToolButton(FIF.CARE_RIGHT_SOLID)
        self.nextApplyBtn.setToolTip("下一张并直接设为壁纸")
        self.nextApplyBtn.clicked.connect(lambda: self._step(1, apply=True))
        action_row.addWidget(self.nextApplyBtn)

        action_row.addStretch(1)

        self.setBtn = PrimaryPushButton(FIF.CHECKBOX, "设为壁纸")
        self.setBtn.clicked.connect(self._on_set_static)
        action_row.addWidget(self.setBtn)

        self.spanBtn = PushButton("一图跨屏拼接")
        self.spanBtn.clicked.connect(self._on_set_span)
        action_row.addWidget(self.spanBtn)
        self.expandLayout.addLayout(action_row)

        # ── 显示设置 ──
        self.expandLayout.addWidget(SubtitleLabel("应用设置"))
        self._pos_combo = ComboBox()
        for name in DWPOS:
            self._pos_combo.addItem(name)
        pos_row = QHBoxLayout()
        pos_row.addWidget(BodyLabel("显示方式"))
        pos_row.addWidget(self._pos_combo, 1)
        apply_pos = PushButton("应用显示方式")
        apply_pos.clicked.connect(self._on_apply_position)
        pos_row.addWidget(apply_pos)
        self.expandLayout.addLayout(pos_row)

        mon_row = QHBoxLayout()
        mon_row.addWidget(BodyLabel("目标显示器"))
        self.monitorCombo = ComboBox()
        self._refresh_monitor_combo()
        mon_row.addWidget(self.monitorCombo, 1)
        self.expandLayout.addLayout(mon_row)

        # ── 动态轮播 ──
        self.expandLayout.addWidget(SubtitleLabel("动态壁纸（定时轮播，程序运行时生效）"))
        folder2_row = self._folder_row()
        self.expandLayout.addLayout(folder2_row)
        interval_row = self._interval_row()
        self.expandLayout.addLayout(interval_row)
        ctrl_row = QHBoxLayout()
        self.startRotateBtn = PrimaryPushButton(FIF.PLAY, "开始轮播")
        self.startRotateBtn.clicked.connect(self._on_toggle_rotation)
        ctrl_row.addWidget(self.startRotateBtn)
        next_btn2 = PushButton(FIF.SYNC, "立即换一张")
        next_btn2.clicked.connect(self._rotate_once)
        ctrl_row.addWidget(next_btn2)
        ctrl_row.addStretch(1)
        self.expandLayout.addLayout(ctrl_row)
        self.expandLayout.addStretch(1)

    def _build_list_panel(self) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(SubtitleLabel("壁纸列表"))
        self.listTable = QTableWidget(0, 1)
        self.listTable.setHorizontalHeaderLabels(["文件名"])
        self.listTable.verticalHeader().setVisible(False)
        self.listTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.listTable.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.listTable.setIconSize(QSize(THUMB_PX, THUMB_PX))
        self.listTable.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.listTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.listTable.setShowGrid(False)
        self.listTable.setAlternatingRowColors(True)
        self.listTable.setFixedWidth(360)
        self.listTable.itemSelectionChanged.connect(self._on_table_selected)
        v.addWidget(self.listTable, 1)
        return wrap

    def _build_preview_panel(self) -> QWidget:
        wrap = _PreviewPanel(self)
        wrap.setStyleSheet("QFrame{background:#222; border-radius:8px;}")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(0)
        self.flipView = _WallpaperFlipView()
        self.flipView.setBorderRadius(8)
        self.flipView.currentIndexChanged.connect(self._on_flip_changed)
        v.addWidget(self.flipView, 1)
        return wrap

    def _folder_row(self):
        box = QHBoxLayout()
        box.addWidget(BodyLabel("轮播文件夹"))
        self.folderEdit = LineEdit()
        self.folderEdit.setPlaceholderText("包含壁纸图片的文件夹（轮播源）")
        box.addWidget(self.folderEdit, 1)
        pick = ToolButton(FIF.FOLDER)
        pick.clicked.connect(self._pick_folder)
        box.addWidget(pick)
        return box

    def _interval_row(self):
        box = QHBoxLayout()
        box.addWidget(BodyLabel("轮播间隔（分钟）"))
        self.intervalSpin = SpinBox()
        self.intervalSpin.setRange(1, 720)
        self.intervalSpin.setValue(10)
        box.addWidget(self.intervalSpin)
        box.addStretch(1)
        return box

    def _refresh_monitor_combo(self):
        self.monitorCombo.clear()
        self.monitorCombo.addItem("主屏（默认）")
        wp = _get_wp()
        if wp:
            for i, m in enumerate(wp.monitors()):
                self.monitorCombo.addItem(f"显示器 {i + 1}", userData=m)
        else:
            self.monitorCombo.setToolTip("多屏接口不可用")

    # ── 列表数据 ────────────────────────────────────────
    def _set_images(self, images: list[str], keep_index: bool = False):
        self._cancel_thumb_load()
        self._images = images
        self.listTable.setRowCount(len(images))
        self.listTable.clearContents()
        for row, p in enumerate(images):
            item = QTableWidgetItem(os.path.basename(p))
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p)
            self.listTable.setItem(row, 0, item)
            self.listTable.setRowHeight(row, ROW_HEIGHT)
        self.listTable.setHorizontalHeaderLabels(
            [f"文件名（{len(images)} 张）" if images else "文件名"])
        target = self._index if keep_index else 0
        if images:
            target = max(0, min(target, len(images) - 1))
            self._index = target
            self.listTable.selectRow(target)
        self._sync_flip_view()
        self._update_hint()

    def _start_thumb_load(self, paths: list[str]):
        if not paths:
            return
        loader = _ThumbnailLoader(paths, THUMB_PX, self)
        loader.thumb_ready.connect(self._on_thumb_ready)
        loader.failed.connect(self._on_thumb_failed)
        loader.finished.connect(loader.deleteLater)
        self._thumb_loader = loader
        loader.start()

    def _cancel_thumb_load(self):
        if self._thumb_loader is not None and self._thumb_loader.isRunning():
            self._thumb_loader.cancel()
            self._thumb_loader.wait(50)
        self._thumb_loader = None

    def _on_thumb_ready(self, row: int, pix: QPixmap):
        if row < 0 or row >= self.listTable.rowCount():
            return
        item = self.listTable.item(row, 0)
        if item is not None:
            item.setIcon(pix)

    def _on_thumb_failed(self, row: int):
        if row < 0 or row >= self.listTable.rowCount():
            return
        item = self.listTable.item(row, 0)
        if item is not None:
            item.setText(
                f"{os.path.basename(item.data(Qt.ItemDataRole.UserRole) or '')} (缩略图失败)")

    def _selected_path(self) -> str:
        if self._images and 0 <= self._index < len(self._images):
            return self._images[self._index]
        return ""

    def _update_hint(self):
        n = len(self._images)
        if n:
            path = self._images[min(self._index, n - 1)]
            self.srcHint.setText(
                f"{self._index + 1}/{n} · {os.path.basename(path)}")
        else:
            self.srcHint.setText("选择单张图片或加载文件夹")

    def _on_table_selected(self):
        items = self.listTable.selectedItems()
        if not items:
            return
        row = items[0].row()
        if 0 <= row < len(self._images):
            self._index = row
            if self.flipView.count() and self.flipView.currentIndex() != row:
                self.flipView.setCurrentIndexInstant(row)
            self._update_hint()

    def _on_flip_changed(self, index: int):
        if index < 0 or index >= len(self._images):
            return
        if self._index == index:
            return
        self._index = index
        if self.listTable.currentRow() != index:
            self.listTable.blockSignals(True)
            self.listTable.selectRow(index)
            self.listTable.scrollToItem(
                self.listTable.item(index, 0),
                QAbstractItemView.ScrollHint.PositionAtCenter)
            self.listTable.blockSignals(False)
        self._update_hint()

    def _sync_flip_view(self):
        self.flipView.blockSignals(True)
        self.flipView.clear()
        if self._images:
            self.flipView.addImages(self._images)
            if 0 <= self._index < self.flipView.count():
                self.flipView.setCurrentIndexInstant(self._index)
        self.flipView.blockSignals(False)

    def _step(self, delta: int, apply: bool = False):
        if not self._images:
            return
        self._index = (self._index + delta) % len(self._images)
        self.listTable.selectRow(self._index)
        self.listTable.scrollToItem(self.listTable.item(self._index, 0),
                                    QAbstractItemView.ScrollHint.PositionAtCenter)
        # 用即时跳转，避免 500ms 动画期间被窗口尺寸变化打断导致最终定位错位
        self.flipView.setCurrentIndexInstant(self._index)
        if apply:
            self._on_set_static()

    def _on_load_folder(self):
        start_dir = cfg.wallpaper_folder.value or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "选择壁纸文件夹", start_dir)
        if not folder:
            return
        self._load_folder_async(folder, user_picked=True)

    def _load_folder_async(self, folder: str, user_picked: bool):
        imgs = _scan_images(folder)
        if not imgs:
            self._set_images([])
            if user_picked:
                InfoBar.warning("未找到图片", "该文件夹中没有可用的壁纸图片",
                               parent=self,
                               position=InfoBarPosition.TOP, duration=3000)
            return
        cfg.set(cfg.wallpaper_folder, folder)
        try:
            cfg.save()
        except Exception as e:
            module_logger.warning(f"保存壁纸文件夹路径失败: {e}")
        self._set_images(imgs)
        self.folderEdit.setText(folder)
        self._start_thumb_load(imgs)
        if user_picked:
            InfoBar.success("已加载", f"扫描到 {len(imgs)} 张壁纸",
                            parent=self,
                            position=InfoBarPosition.TOP, duration=2500)

    def _restore_last_folder(self):
        saved = (cfg.wallpaper_folder.value or "").strip()
        if saved and os.path.isdir(saved):
            self._load_folder_async(saved, user_picked=False)

    # ── 动作 ────────────────────────────────────────────
    def _on_apply_position(self):
        name = self._pos_combo.currentText()
        wp = _get_wp()
        if not wp:
            return InfoBar.warning("提示", "当前环境不支持显示方式接口", parent=self,
                                   position=InfoBarPosition.TOP, duration=3000)
        wp.set_position(DWPOS[name])
        InfoBar.success("已应用", f"显示方式：{name}", parent=self,
                        position=InfoBarPosition.TOP, duration=2000)

    def _on_set_static(self):
        path = self._selected_path()
        if not path:
            return InfoBar.warning("提示", "请先选择壁纸图片", parent=self,
                                   position=InfoBarPosition.TOP, duration=3000)
        idx = self.monitorCombo.currentIndex()
        wp = _get_wp()
        if idx <= 0:
            if not _spi_set_wallpaper(path):
                return InfoBar.error("失败", "设置失败", parent=self,
                                     position=InfoBarPosition.TOP, duration=3000)
            InfoBar.success("设置成功", f"主屏壁纸：{os.path.basename(path)}",
                            parent=self, position=InfoBarPosition.TOP,
                            duration=2500)
            return
        if not wp:
            return InfoBar.warning("提示", "多屏设置需要系统 IDesktopWallpaper 支持",
                                   parent=self,
                                   position=InfoBarPosition.TOP, duration=3000)
        monitors = wp.monitors()
        if idx - 1 >= len(monitors):
            return InfoBar.error("失败", "显示器编号无效", parent=self,
                                 position=InfoBarPosition.TOP, duration=3000)
        wp.set_wallpaper(path, monitors[idx - 1])
        InfoBar.success("设置成功", f"显示器 {idx} 壁纸已更新", parent=self,
                        position=InfoBarPosition.TOP, duration=2500)

    def _on_set_span(self):
        path = self._selected_path()
        if not path:
            return InfoBar.warning("提示", "请先选择壁纸图片", parent=self,
                                   position=InfoBarPosition.TOP, duration=3000)
        wp = _get_wp()
        if not wp:
            return InfoBar.warning("提示", "跨屏拼接需要系统 IDesktopWallpaper 支持",
                                   parent=self,
                                   position=InfoBarPosition.TOP, duration=3000)
        wp.set_position(DWPOS["跨屏拼接"])
        for m in wp.monitors():
            wp.set_wallpaper(path, m)
        InfoBar.success("已跨屏拼接", "一张壁纸已铺满所有显示器", parent=self,
                        position=InfoBarPosition.TOP, duration=2500)

    # ── 轮播 ────────────────────────────────────────────
    def _pick_folder(self):
        start_dir = self.folderEdit.text().strip() or cfg.wallpaper_folder.value \
            or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "选择轮播文件夹", start_dir)
        if folder:
            self.folderEdit.setText(folder)
            cfg.set(cfg.wallpaper_folder, folder)
            try:
                cfg.save()
            except Exception:
                pass

    def closeEvent(self, event):
        self._cancel_thumb_load()
        super().closeEvent(event)

    def _on_toggle_rotation(self):
        if self._rotate_timer.isActive():
            self._rotate_timer.stop()
            self.startRotateBtn.setText("开始轮播")
            InfoBar.info("轮播已停止", "", parent=self,
                         position=InfoBarPosition.TOP, duration=2000)
            return
        folder = self.folderEdit.text().strip()
        self._rotation_images = _scan_images(folder)
        if not self._rotation_images:
            return InfoBar.warning("提示", "轮播文件夹中没有图片", parent=self,
                                   position=InfoBarPosition.TOP, duration=3000)
        self._rotation_pos = 0
        self._rotate_timer.start(self.intervalSpin.value() * 60 * 1000)
        self.startRotateBtn.setText("停止轮播")
        self._rotate_once()
        InfoBar.success("轮播已启动",
                        f"共 {len(self._rotation_images)} 张，"
                        f"每 {self.intervalSpin.value()} 分钟切换",
                        parent=self, position=InfoBarPosition.TOP, duration=3000)

    def _rotate_once(self):
        if not self._rotation_images:
            return
        path = self._rotation_images[self._rotation_pos % len(self._rotation_images)]
        self._rotation_pos += 1
        if _spi_set_wallpaper(path):
            module_logger.info(f"动态壁纸切换: {path}")


class WallpaperWidget(QFrame):
    """导航容器。"""

    def __init__(self, name: str, parent=None):
        super().__init__(parent=parent)
        self.setObjectName(name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.wallpaperInterface = WallpaperInterface(self)
        layout.addWidget(self.wallpaperInterface)