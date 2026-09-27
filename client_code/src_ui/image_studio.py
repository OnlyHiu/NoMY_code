# -*- coding: utf-8 -*-
"""图片工坊（PS/Lightroom 式调色修图）

- 分区面板：光（曝光/对比度/高光/阴影/白色/黑色）、颜色（色温/色调/自然饱和度/饱和度）、
  效果（纹理/清晰度/去除薄雾/晕影/颗粒）、曲线（可拖动 RGB 色调曲线）
- 网红滤镜：原图/黑白/复古/冷调/暖调/鲜艳/胶片/哈苏/徕卡/奶油/日系/赛博朋克/港风/落日/森林/黑金
- 流畅性：预览 ≤1600px + 独立线程渲染 + 120ms 防抖 + 新旧帧 6 步插值过渡
- 导出：全分辨率应用同参数流水线
"""
import logging
import os

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (QFileDialog, QFrame, QGraphicsPixmapItem,
                               QGraphicsScene, QGraphicsView, QHBoxLayout,
                               QLabel, QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF,
                            InfoBar, InfoBarPosition, PushButton, Slider,
                            SubtitleLabel, TitleLabel, ToolButton)

module_logger = logging.getLogger("flu_widget.image_studio")

PREVIEW_MAX = 1600
DEBOUNCE_MS = 120
FADE_STEPS = 6
FADE_INTERVAL_MS = 24

DEFAULT_PARAMS = {
    "brightness": 0, "contrast": 0, "highlights": 0, "shadows": 0,
    "whites": 0, "blacks": 0,
    "temperature": 0, "tint": 0, "vibrance": 0, "saturation": 0,
    "texture": 0, "sharpness": 0, "dehaze": 0, "vignette": 0, "grain": 0,
}
DEFAULT_CURVE = [[0, 0], [64, 64], [128, 128], [192, 192], [255, 255]]

FILTERS = ["原图", "黑白", "复古", "冷调", "暖调", "鲜艳", "胶片", "哈苏", "徕卡",
           "奶油", "日系", "赛博朋克", "港风", "落日", "森林", "黑金"]
FILTER_PRESETS = {
    "黑白": {"saturation": -100},
    "复古": {"saturation": -25, "temperature": 25, "contrast": -10,
             "shadows": 20, "highlights": -15},
    "冷调": {"temperature": -40, "tint": -8, "saturation": -5},
    "暖调": {"temperature": 35, "saturation": 8},
    "鲜艳": {"saturation": 45, "contrast": 12, "sharpness": 15},
    "胶片": {"contrast": 14, "saturation": -12, "highlights": -22,
             "shadows": 26, "temperature": 8, "grain": 14},
    # 哈苏 Natural Colour Solution：自然平衡、中性微暖、中间调丰厚
    "哈苏": {"contrast": -5, "saturation": 6, "temperature": 6, "tint": 2,
             "highlights": -12, "shadows": 14, "sharpness": 8},
    # 徕卡 look：高对比、暗部深沉、暖高光、微暗角与颗粒
    "徕卡": {"contrast": 22, "saturation": -6, "temperature": 14,
             "highlights": -6, "shadows": -10, "sharpness": 18,
             "vignette": 22, "grain": 10},
    # ── 网红滤镜 ──
    "奶油": {"brightness": 8, "contrast": -8, "temperature": 6, "saturation": -10,
             "highlights": -10, "shadows": 15, "blacks": 18},
    "日系": {"brightness": 10, "contrast": -15, "saturation": -18,
             "temperature": -5, "shadows": 20, "whites": 10, "blacks": 15},
    "赛博朋克": {"contrast": 18, "saturation": 30, "tint": -25, "temperature": -15,
                 "dehaze": 15, "vignette": 30},
    "港风": {"contrast": 15, "saturation": -20, "temperature": -8, "tint": 6,
             "grain": 20, "vignette": 18},
    "落日": {"temperature": 35, "tint": 10, "saturation": 12, "highlights": -18,
             "vignette": 12},
    "森林": {"temperature": -20, "tint": -10, "saturation": 8, "shadows": 10,
             "dehaze": 15},
    "黑金": {"saturation": -55, "contrast": 25, "temperature": 18,
             "vignette": 35, "grain": 8, "dehaze": 10},
}


# ── 像素流水线（numpy 实现，与 UI 解耦） ─────────────────────
def _tone_lut(curve_points) -> np.ndarray:
    """控制点 → 256 级 LUT。curve_points: [[x,y], ...] 按 x 升序。"""
    xs = [p[0] for p in curve_points]
    ys = [p[1] for p in curve_points]
    return np.interp(np.arange(256), xs, ys).astype(np.float32)


def apply_pipeline(pil_img: Image.Image, params: dict, preview: bool = True) -> np.ndarray:
    """对 PIL 图执行完整调色流水线，返回 HxWx3 uint8 数组。"""
    img = pil_img

    # ── PIL 阶段：纹理（大半径低强度反差）与清晰度（USM） ──
    if params.get("texture", 0):
        amount = params["texture"] / 100.0 * (0.6 if preview else 1.0)
        blurred = np.asarray(img.filter(ImageFilter.GaussianBlur(radius=8))).astype(np.float32)
        base = np.asarray(img).astype(np.float32)
        img = Image.fromarray(np.clip(base + (base - blurred) * amount, 0, 255).astype(np.uint8))
    if params.get("sharpness", 0) > 0:
        percent = params["sharpness"] * (1.0 if preview else 1.5)
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=int(percent), threshold=3))

    # 基础三件套
    b, c, s = params.get("brightness", 0), params.get("contrast", 0), params.get("saturation", 0)
    if b:
        img = ImageEnhance.Brightness(img).enhance(1.0 + b / 100.0)
    if c:
        img = ImageEnhance.Contrast(img).enhance(1.0 + c / 100.0)
    if s:
        img = ImageEnhance.Color(img).enhance(1.0 + s / 100.0)

    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    lum = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32) / 255.0

    # 色温 / 色调
    t = params.get("temperature", 0) / 100.0
    if t:
        arr[..., 0] *= (1.0 + 0.30 * t)
        arr[..., 2] *= (1.0 - 0.30 * t)
    tint = params.get("tint", 0) / 100.0
    if tint:
        arr[..., 1] *= (1.0 - 0.25 * tint)
        arr[..., 0] *= (1.0 + 0.12 * tint)
        arr[..., 2] *= (1.0 + 0.12 * tint)

    # 曲线（RGB 联动 LUT）
    curve = params.get("curve") or DEFAULT_CURVE
    if any(p[0] != p[1] for p in curve):
        lut = _tone_lut(curve)
        idx = np.clip(arr, 0, 255).astype(np.int32)
        arr = lut[idx]

    # 高光 / 阴影
    hi = params.get("highlights", 0) / 100.0
    sh = params.get("shadows", 0) / 100.0
    if hi or sh:
        lum = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32) / 255.0
        if hi:
            hm = np.clip((lum - 0.5) * 2.0, 0.0, 1.0) ** 1.5
            arr += (hi * 70.0) * hm[..., None]
        if sh:
            sm = np.clip((0.5 - lum) * 2.0, 0.0, 1.0) ** 1.5
            arr += (sh * 70.0) * sm[..., None]

    # 白色 / 黑色
    wh = params.get("whites", 0) / 100.0
    bl = params.get("blacks", 0) / 100.0
    if wh or bl:
        lum = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32) / 255.0
        if wh:
            wm = np.clip((lum - 0.55) * 2.5, 0.0, 1.0)
            arr += (wh * 45.0) * wm[..., None]
        if bl:
            bm = np.clip((0.45 - lum) * 2.5, 0.0, 1.0)
            arr += (bl * 45.0) * bm[..., None]

    # 自然饱和度（低饱和像素增益更多）
    vib = params.get("vibrance", 0) / 100.0
    if vib:
        mx = arr.max(axis=-1)
        mn = arr.min(axis=-1)
        sat = np.clip((mx - mn) / 255.0, 0.0, 1.0)
        factor = (1.0 + vib * (1.0 - sat))[..., None]
        avg = arr.mean(axis=-1, keepdims=True)
        arr = avg + (arr - avg) * factor

    # 去除薄雾：黑位拉伸 + 轻微提饱和
    dz = params.get("dehaze", 0) / 100.0
    if dz:
        m = dz * 45.0
        arr = (arr - m) * (255.0 / max(255.0 - m, 1.0))
        avg = arr.mean(axis=-1, keepdims=True)
        arr = avg + (arr - avg) * (1.0 + dz * 0.35)

    # 暗角
    vig = params.get("vignette", 0) / 100.0
    if vig > 0:
        h, w = arr.shape[:2]
        y, x = np.ogrid[:h, :w]
        cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
        d = np.sqrt(((x - cx) / max(cx, 1)) ** 2 + ((y - cy) / max(cy, 1)) ** 2) / np.sqrt(2)
        mask = 1.0 - vig * np.clip(d - 0.55, 0.0, 1.0) ** 1.6
        arr *= mask[..., None]

    # 颗粒（固定种子，预览与导出一致）
    grain = params.get("grain", 0) / 100.0
    if grain > 0:
        rng = np.random.default_rng(42)
        noise = rng.normal(0.0, grain * 16.0, arr.shape[:2])[..., None]
        arr = arr + noise

    return np.clip(arr, 0, 255).astype(np.uint8)


class _RenderWorker(QThread):
    """后台渲染一帧预览。"""
    rendered = Signal(object)   # np.ndarray (H,W,3)

    def __init__(self, image: Image.Image, params: dict, parent=None):
        super().__init__(parent)
        self._image = image
        self._params = dict(params)

    def run(self):
        try:
            self.rendered.emit(apply_pipeline(self._image, self._params, preview=True))
        except Exception as e:
            module_logger.error(f"预览渲染失败: {e}")


class _Canvas(QGraphicsView):
    """支持滚轮缩放（以光标为中心）与拖拽平移的画布。"""

    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.pixmap_item = self._scene.addPixmap(QPixmap())
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def setPixmap(self, pixmap: QPixmap):
        self._scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        self.pixmap_item.setPixmap(pixmap)

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)
        self.zoomChanged.emit(self.transform().m11())

    def fit_view(self):
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


class CurveEditor(QWidget):
    """RGB 色调曲线编辑器：首尾端点固定，3 个内部控制点可拖动；
    在曲线任意位置按下即抓取最近的内部控制点（x 移动到按下处），上下拖动调整。"""
    curveChanged = Signal(list)

    MARGIN = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = [list(p) for p in DEFAULT_CURVE]
        self._drag_index = -1
        self.setFixedSize(200, 200)

    # ── 坐标换算 ──
    def _to_widget(self, x, y):
        w = self.width() - self.MARGIN * 2
        h = self.height() - self.MARGIN * 2
        return (self.MARGIN + x / 255 * w,
                self.MARGIN + (255 - y) / 255 * h)

    def _to_value(self, wx, wy):
        w = self.width() - self.MARGIN * 2
        h = self.height() - self.MARGIN * 2
        x = round(max(0.0, min(1.0, (wx - self.MARGIN) / max(w, 1))) * 255)
        y = round(max(0.0, min(1.0, 1.0 - (wy - self.MARGIN) / max(h, 1))) * 255)
        return x, y

    def reset(self):
        self.points = [list(p) for p in DEFAULT_CURVE]
        self.update()
        self.curveChanged.emit([list(p) for p in self.points])

    def get_curve(self) -> list:
        return [list(p) for p in self.points]

    def set_curve(self, points):
        self.points = [list(p) for p in points]
        self.update()

    # ── 交互：首尾固定，仅内部点可拖 ──
    def mousePressEvent(self, event):
        event.accept()
        pos = event.position().toPoint()
        vx, vy = self._to_value(pos.x(), pos.y())
        interior = list(range(1, len(self.points) - 1))
        if not interior:
            return
        # 抓取 x 最近（其次 y 最近）的内部控制点
        self._drag_index = min(
            interior,
            key=lambda i: abs(self.points[i][0] - vx) * 2 + abs(self.points[i][1] - vy))
        # 将该点的 x 移到按下位置（夹在相邻点之间），y 跟随按下位置
        lo = self.points[self._drag_index - 1][0] + 1
        hi = self.points[self._drag_index + 1][0] - 1
        self.points[self._drag_index][0] = max(lo, min(hi, vx))
        self.points[self._drag_index][1] = vy
        self.update()
        self.curveChanged.emit(self.get_curve())

    def mouseMoveEvent(self, event):
        if self._drag_index < 0:
            return
        event.accept()
        pos = event.position().toPoint()
        vx, vy = self._to_value(pos.x(), pos.y())
        lo = self.points[self._drag_index - 1][0] + 1
        hi = self.points[self._drag_index + 1][0] - 1
        self.points[self._drag_index][0] = max(lo, min(hi, vx))
        self.points[self._drag_index][1] = max(0, min(255, vy))
        self.update()
        self.curveChanged.emit(self.get_curve())

    def mouseReleaseEvent(self, event):
        event.accept()
        self._drag_index = -1

    # ── 绘制 ──
    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(28, 28, 30))
        painter.setPen(QPen(QColor(66, 66, 70), 1))
        for i in range(1, 4):
            f = i / 4
            x = int(self.MARGIN + f * (self.width() - self.MARGIN * 2))
            yy = int(self.MARGIN + f * (self.height() - self.MARGIN * 2))
            painter.drawLine(x, self.MARGIN, x, self.height() - self.MARGIN)
            painter.drawLine(self.MARGIN, yy, self.width() - self.MARGIN, yy)
        # 对角参考线
        painter.setPen(QPen(QColor(92, 92, 96), 1, Qt.PenStyle.DashLine))
        a = self._to_widget(0, 0)
        b = self._to_widget(255, 255)
        painter.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
        # 曲线（经 LUT 插值，平滑）
        lut = _tone_lut(self.points)
        painter.setPen(QPen(QColor("#00b7ad"), 2))
        for x in range(0, 255, 2):
            p1 = self._to_widget(x, lut[x])
            p2 = self._to_widget(x + 1, lut[x + 1])
            painter.drawLine(int(p1[0]), int(p1[1]), int(p2[0]), int(p2[1]))
        # 控制点：首尾方块（固定），内部圆点（可拖）
        for i, (px, py) in enumerate(self.points):
            cx, cy = self._to_widget(px, py)
            cx, cy = int(cx), int(cy)
            if i == 0 or i == len(self.points) - 1:
                painter.setBrush(QColor("#888888"))
                painter.drawRect(cx - 5, cy - 5, 10, 10)
            else:
                painter.setBrush(QColor("#00b7ad"))
                painter.drawEllipse(cx - 6, cy - 6, 12, 12)
        painter.end()


class HistogramWidget(QWidget):
    """RGB 颜色直方图（随调参实时刷新）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channels = None   # (r, g, b) 每通道 64 bin
        self.setMinimumHeight(180)

    def set_image(self, frame: np.ndarray):
        """frame: HxWx3 uint8（预览帧）。"""
        try:
            small = frame[::4, ::4].reshape(-1, 3)
            bins = np.linspace(0, 256, 65)
            self._channels = tuple(
                np.histogram(small[:, c], bins=bins)[0] for c in range(3))
            self.update()
        except Exception as e:
            module_logger.debug(f"直方图计算失败: {e}")

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(28, 28, 30))
        painter.setPen(QPen(QColor(66, 66, 70), 1))
        w, h = self.width(), self.height()
        for i in range(1, 4):
            f = i / 4
            painter.drawLine(int(self.MARGIN_G + f * (w - 20)), 8,
                             int(self.MARGIN_G + f * (w - 20)), h - 8)
        if not self._channels:
            painter.setPen(QPen(QColor(120, 120, 120)))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "打开图片后显示直方图")
            painter.end()
            return
        peak = max(int(c.max()) for c in self._channels) or 1
        for hist, color in zip(self._channels, ("#ff6666", "#66ff88", "#6699ff")):
            painter.setPen(QPen(QColor(color), 1))
            n = len(hist)
            for i in range(n - 1):
                y1 = h - 8 - int(hist[i] / peak * (h - 20))
                y2 = h - 8 - int(hist[i + 1] / peak * (h - 20))
                x1 = int(10 + i / (n - 1) * (w - 20))
                x2 = int(10 + (i + 1) / (n - 1) * (w - 20))
                painter.drawLine(x1, y1, x2, y2)
        painter.end()


HistogramWidget.MARGIN_G = 10


class _RenderTask(QThread):
    rendered = Signal(object)

    def __init__(self, image: Image.Image, params: dict, parent=None):
        super().__init__(parent)
        self._image = image
        self._params = dict(params)

    def run(self):
        try:
            self.rendered.emit(apply_pipeline(self._image, self._params, preview=True))
        except Exception as e:
            module_logger.error(f"预览渲染失败: {e}")


class CollapsibleSection(QWidget):
    """可折叠分区（标题行 + 内容）。"""

    toggled = Signal(bool)  # 展开/收起状态变化

    def __init__(self, title: str, parent=None, open_default: bool = True):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        header_row = QHBoxLayout()
        self.header_btn = PushButton(title)
        self.header_btn.setFixedHeight(30)
        self.arrow = CaptionLabel("▾")
        header_row.addWidget(self.header_btn)
        header_row.addStretch(1)
        header_row.addWidget(self.arrow)
        self._layout.addLayout(header_row)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(2, 0, 2, 0)
        self.content_layout.setSpacing(4)
        self._layout.addWidget(self.content)
        self.header_btn.clicked.connect(self._toggle)
        self._open = open_default
        if not self._open:
            self.content.setVisible(False)
            self.arrow.setText("▸")

    def _toggle(self):
        self._open = not self._open
        self.content.setVisible(self._open)
        self.arrow.setText("▾" if self._open else "▸")
        self.toggled.emit(self._open)

    @property
    def is_open(self) -> bool:
        return self._open

    def add(self, thing):
        if isinstance(thing, QWidget):
            self.content_layout.addWidget(thing)
        else:
            self.content_layout.addLayout(thing)

    def addStretch(self, n=0):
        self.content_layout.addStretch(n)


class ImageStudioWidget(QFrame):
    """图片工坊主界面。"""

    def __init__(self, name: str, parent=None):
        super().__init__(parent=parent)
        self.setObjectName(name)

        self.source_image: Image.Image | None = None
        self.preview_image: Image.Image | None = None
        self.params = dict(DEFAULT_PARAMS)
        self.params["curve"] = [list(p) for p in DEFAULT_CURVE]
        self._undo_stack: list = []
        self._worker = None
        self._rerun_pending = False
        self._prev_frame = None
        self._target_frame = None
        self._fade_step = 0
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(FADE_INTERVAL_MS)
        self._fade_timer.timeout.connect(self._on_fade_tick)
        # 调色分区 + 窗口高度还原（展开会撑高主窗口，收起后需还原）
        self._sections: list = []
        self._win_base_size = None

        self._build_ui()
        self._bind()

    # ── UI ─────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # ── 中间列（画布 + 缩放行 + 直方图；宽度一致） ──
        # 中间列单独先建，最后与「画布上方按钮行 + 右侧调色面板」一起塞进顶栏。
        # 这样直方图宽度自然对齐画布宽度（都在中间列内），不依赖父 layout 计算。
        center = QVBoxLayout()
        center.setSpacing(6)

        # 画布上方按钮行（打开 / 另存 / 导出 / 撤销 / 还原 / 旋转 / 镜像）
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.openBtn = PushButton(FIF.FOLDER_ADD, "打开图片")
        self.openBtn.clicked.connect(self._on_open)
        action_row.addWidget(self.openBtn)
        self.saveBtn = PushButton(FIF.SAVE, "保存")
        self.saveBtn.clicked.connect(self._on_save)
        action_row.addWidget(self.saveBtn)
        self.exportBtn = PushButton(FIF.SAVE, "导出全分辨率图片")
        self.exportBtn.clicked.connect(self._on_save)
        action_row.addWidget(self.exportBtn)
        self.undoBtn = ToolButton(FIF.RETURN)
        self.undoBtn.setToolTip("撤销")
        self.undoBtn.clicked.connect(self._on_undo)
        action_row.addWidget(self.undoBtn)
        self.resetBtn = ToolButton(FIF.SYNC)
        self.resetBtn.setToolTip("还原全部")
        self.resetBtn.clicked.connect(self._on_reset)
        action_row.addWidget(self.resetBtn)
        self.rotateBtn = ToolButton(FIF.ROTATE)
        self.rotateBtn.setToolTip("旋转 90°")
        self.rotateBtn.clicked.connect(self._on_rotate)
        action_row.addWidget(self.rotateBtn)
        self.flipBtn = ToolButton(FIF.SEARCH_MIRROR)
        self.flipBtn.setToolTip("水平镜像")
        self.flipBtn.clicked.connect(self._on_flip_h)
        action_row.addWidget(self.flipBtn)
        action_row.addStretch(1)
        center.addLayout(action_row)

        # 画布
        self.canvas = _Canvas()
        center.addWidget(self.canvas, 1)

        # 缩放行
        zoom_row = QHBoxLayout()
        zoom_row.addStretch(1)
        btn_fit = PushButton("适应窗口")
        btn_fit.setFixedWidth(100)
        btn_fit.clicked.connect(self.canvas.fit_view)
        zoom_row.addWidget(btn_fit)
        btn_100 = PushButton("1:1")
        btn_100.setFixedWidth(80)
        btn_100.clicked.connect(self.canvas.resetTransform)
        zoom_row.addWidget(btn_100)
        self.zoomLabel = CaptionLabel("100%")
        zoom_row.addWidget(self.zoomLabel)
        zoom_row.addStretch(1)
        center.addLayout(zoom_row)

        # 直方图（画布下方，与画布同宽）
        self.histogramWidget = HistogramWidget()
        center.addWidget(self.histogramWidget)

        # ── 顶栏：中间列 + 右侧调色面板 ──
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addLayout(center, 1)

        # 右侧调色面板（滤镜 + 光/颜色/效果/曲线，均默认收起）
        panel = QFrame()
        panel.setFixedWidth(310)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(4, 0, 4, 0)
        pl.setSpacing(6)

        pl.addWidget(TitleLabel("调色"))

        pl.addWidget(BodyLabel("滤镜"))
        self.filterBox = ComboBox()
        self.filterBox.addItems(FILTERS)
        self.filterBox.currentTextChanged.connect(self._on_filter_changed)
        pl.addWidget(self.filterBox)

        self._sliders = {}

        def make_slider(section, key, title, rng=(-100, 100)):
            row = QHBoxLayout()
            row.addWidget(CaptionLabel(title))
            row.addStretch(1)
            value_label = CaptionLabel(str(self.params.get(key, 0)))
            row.addWidget(value_label)
            section.add(row)
            slider = Slider(Qt.Orientation.Horizontal)
            slider.setRange(*rng)
            slider.setValue(int(self.params.get(key, 0)))
            slider.valueChanged.connect(
                lambda v, k=key, lb=value_label: self._on_param_changed(k, v, lb))
            section.add(slider)
            self._sliders[key] = (slider, value_label)

        sec_light = CollapsibleSection("光", open_default=False)
        make_slider(sec_light, "brightness", "曝光 / 亮度")
        make_slider(sec_light, "contrast", "对比度")
        make_slider(sec_light, "highlights", "高光")
        make_slider(sec_light, "shadows", "阴影")
        make_slider(sec_light, "whites", "白色")
        make_slider(sec_light, "blacks", "黑色")
        pl.addWidget(sec_light)

        sec_color = CollapsibleSection("颜色", open_default=False)
        make_slider(sec_color, "temperature", "色温（冷暖）")
        make_slider(sec_color, "tint", "色调（绿-品红）")
        make_slider(sec_color, "vibrance", "自然饱和度")
        make_slider(sec_color, "saturation", "饱和度")
        pl.addWidget(sec_color)

        sec_effect = CollapsibleSection("效果", open_default=False)
        make_slider(sec_effect, "texture", "纹理")
        make_slider(sec_effect, "sharpness", "清晰度", (0, 100))
        make_slider(sec_effect, "dehaze", "去除薄雾", (0, 100))
        make_slider(sec_effect, "vignette", "晕影", (0, 100))
        make_slider(sec_effect, "grain", "颗粒", (0, 100))
        self.sec_effect = sec_effect
        pl.addWidget(sec_effect)

        sec_curve = CollapsibleSection("曲线", open_default=False)
        curve_row = QHBoxLayout()
        self.curveEditor = CurveEditor()
        curve_row.addWidget(self.curveEditor)
        curve_side = QVBoxLayout()
        curve_side.addStretch(1)
        curve_reset = PushButton("重置曲线")
        curve_reset.clicked.connect(self._on_curve_reset)
        curve_side.addWidget(curve_reset)
        curve_side.addWidget(CaptionLabel("拖动控制点\n调整色调响应"))
        curve_side.addStretch(1)
        curve_row.addLayout(curve_side, 1)
        sec_curve.add(curve_row)
        self.sec_curve = sec_curve
        pl.addWidget(sec_curve)

        pl.addStretch(1)
        top.addWidget(panel)
        layout.addLayout(top, 1)

        # 调色分区展开/收起联动窗口高度
        for _sec in (sec_light, sec_color, sec_effect, sec_curve):
            self._sections.append(_sec)
            _sec.toggled.connect(self._on_section_toggled)

        self._set_adjust_enabled(False)

    def _bind(self):
        self.canvas.zoomChanged.connect(self._update_zoom_label)
        self.curveEditor.curveChanged.connect(self._on_curve_changed)

    # ── 调色分区展开/收起：窗口高度还原 ─────────────────
    def _on_section_toggled(self, opened: bool):
        win = self.window()
        if win.isMaximized():
            return
        if opened and self._win_base_size is None:
            # 首次展开前记录窗口大小，作为收起后的还原基准
            self._win_base_size = win.size()
        if self._win_base_size is not None:
            # 延迟一拍等布局完成后再计算所需高度
            QTimer.singleShot(0, self._fit_window_height)

    def _fit_window_height(self):
        win = self.window()
        if win.isMaximized() or self._win_base_size is None:
            return
        # 只在全部收起时收缩窗口；展开过程交给 Qt 自动撑高。
        if any(sec.is_open for sec in self._sections):
            return
        base_h = self._win_base_size.height()
        self._win_base_size = None   # 清空基准，允许下次展开重新记录
        if win.height() > base_h:
            # 关键：收起后窗口最小尺寸仍停留在展开时的大值（布局缓存未刷新），
            # 直接 resize 会被钳制回原高。先解除最小尺寸约束并强制布局
            # 立即重算，再把窗口还原到展开前的基准高度。
            # （不要紧接着用 minimumSizeHint 恢复下限——此刻它仍是旧值，会把窗口顶回去）
            win.setMinimumSize(0, 0)
            if self.layout() is not None:
                self.layout().activate()
            win.resize(win.width(), base_h)

    # ── 打开 / 导出 ─────────────────────────────────────
    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开图片", "", "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        try:
            img = Image.open(path)
            if img.mode != "RGB":
                img = img.convert("RGB")
            self.source_image = img
        except Exception as e:
            InfoBar.error("打开失败", str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        self.params = dict(DEFAULT_PARAMS)
        self.params["curve"] = [list(p) for p in DEFAULT_CURVE]
        self._undo_stack.clear()
        self._reset_sliders()
        self.curveEditor.set_curve(DEFAULT_CURVE)
        self._set_adjust_enabled(True)
        w, h = img.size
        scale = min(1.0, PREVIEW_MAX / max(w, h))
        self.preview_image = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        self._prev_frame = None
        self._request_render()
        self.canvas.fit_view()
        InfoBar.success("已打开", f"{os.path.basename(path)}（{w}×{h}）", parent=self,
                        position=InfoBarPosition.TOP, duration=3000)

    def _on_save(self):
        if self.source_image is None:
            InfoBar.warning("提示", "请先打开图片", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出图片", "", "PNG 图片 (*.png);;JPEG 图片 (*.jpg)")
        if not path:
            return
        self.exportBtn.setEnabled(False)
        self._export_path = path

        def _work():
            try:
                out = Image.fromarray(apply_pipeline(
                    self.source_image, self.params, preview=False))
                if path.lower().endswith((".jpg", ".jpeg")):
                    out.save(path, quality=95)
                else:
                    out.save(path)
                QTimer.singleShot(0, lambda: self._on_export_done(path, ""))
            except Exception as e:
                QTimer.singleShot(0, lambda: self._on_export_done("", str(e)))

        import threading
        threading.Thread(target=_work, daemon=True, name="img-export").start()

    def _on_export_done(self, path: str, err: str):
        self.exportBtn.setEnabled(True)
        if err:
            InfoBar.error("导出失败", err, parent=self,
                          position=InfoBarPosition.TOP, duration=5000)
        else:
            InfoBar.success("导出成功", path, parent=self,
                            position=InfoBarPosition.TOP, duration=4000)

    # ── 参数 / 撤销 / 几何 ──────────────────────────────
    def _on_param_changed(self, key: str, value: int, label: QLabel):
        if self.source_image is None:
            return
        self.params[key] = value
        label.setText(str(value))
        self._push_undo()
        self._request_render()

    def _on_curve_changed(self, points):
        if self.source_image is None:
            return
        self._push_undo()
        self.params["curve"] = [list(p) for p in points]
        self._request_render()

    def _on_curve_reset(self):
        if self.source_image is None:
            return
        self._push_undo()
        self.params["curve"] = [list(p) for p in DEFAULT_CURVE]
        self.curveEditor.set_curve(DEFAULT_CURVE)
        self._request_render()

    def _on_filter_changed(self, name: str):
        if self.source_image is None:
            return
        self._push_undo()
        if name == "原图":
            self.params = dict(DEFAULT_PARAMS)
            self.params["curve"] = [list(p) for p in DEFAULT_CURVE]
            self.curveEditor.set_curve(DEFAULT_CURVE)
        else:
            self.params.update(FILTER_PRESETS.get(name, {}))
        self._reset_sliders()
        self._request_render()

    def _push_undo(self):
        self._undo_stack.append(dict(self.params))
        if len(self._undo_stack) > 20:
            self._undo_stack.pop(0)

    def _on_undo(self):
        if self._undo_stack:
            self.params = self._undo_stack.pop()
            self._reset_sliders()
            self.curveEditor.set_curve(self.params.get("curve", DEFAULT_CURVE))
            self._request_render()

    def _on_reset(self):
        self._push_undo()
        self.params = dict(DEFAULT_PARAMS)
        self.params["curve"] = [list(p) for p in DEFAULT_CURVE]
        self._reset_sliders()
        self.curveEditor.set_curve(DEFAULT_CURVE)
        self._request_render()

    def _on_rotate(self):
        if self.source_image is None:
            return
        self._push_undo()
        self.source_image = self.source_image.transpose(Image.ROTATE_90)
        self._rebuild_preview()

    def _on_flip_h(self):
        if self.source_image is None:
            return
        self._push_undo()
        self.source_image = self.source_image.transpose(Image.FLIP_LEFT_RIGHT)
        self._rebuild_preview()

    def _rebuild_preview(self):
        w, h = self.source_image.size
        scale = min(1.0, PREVIEW_MAX / max(w, h))
        self.preview_image = self.source_image.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        self._prev_frame = None
        self._request_render()

    def _reset_sliders(self):
        for key, (slider, label) in self._sliders.items():
            slider.blockSignals(True)
            slider.setValue(int(self.params.get(key, 0)))
            label.setText(str(int(self.params.get(key, 0))))
            slider.blockSignals(False)

    def _set_adjust_enabled(self, enabled: bool):
        self.filterBox.setEnabled(enabled)
        for slider, _label in self._sliders.values():
            slider.setEnabled(enabled)
        self.curveEditor.setEnabled(enabled)
        self.exportBtn.setEnabled(enabled)
        # 顶部按钮行里需要图片的操作在无图时禁用；打开图片始终可用
        self.saveBtn.setEnabled(enabled)
        self.undoBtn.setEnabled(enabled)
        self.resetBtn.setEnabled(enabled)
        self.rotateBtn.setEnabled(enabled)
        self.flipBtn.setEnabled(enabled)

    # ── 渲染与过渡 ──────────────────────────────────────
    def _request_render(self):
        if self.preview_image is None:
            return
        worker = self._worker
        if worker is not None:
            try:
                running = worker.isRunning()
            except RuntimeError:
                self._worker = None
                running = False
            if running:
                self._rerun_pending = True
                return
            self._worker = None
        self._worker = _RenderTask(self.preview_image, self.params, self)
        self._worker.rendered.connect(self._on_rendered)
        self._worker.finished.connect(lambda: setattr(self, "_worker", None))
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_rendered(self, frame: np.ndarray):
        self._prev_frame = self._target_frame if self._target_frame is not None else frame
        self._target_frame = frame
        self.histogramWidget.set_image(frame)   # 直方图实时刷新
        self._fade_step = 0
        self._fade_timer.start()

    def _on_fade_tick(self):
        if self._target_frame is None:
            return
        self._fade_step += 1
        alpha = self._fade_step / FADE_STEPS
        if self._fade_step >= FADE_STEPS or self._prev_frame is None or \
                self._prev_frame.shape != self._target_frame.shape:
            shown = self._target_frame
        else:
            shown = (self._prev_frame.astype(np.float32) * (1 - alpha)
                     + self._target_frame.astype(np.float32) * alpha).astype(np.uint8)
        h, w, _ = shown.shape
        qimg = QImage(shown.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        self.canvas.setPixmap(QPixmap.fromImage(qimg.copy()))
        self._update_zoom_label()
        if self._fade_step >= FADE_STEPS:
            self._fade_timer.stop()
            if self._rerun_pending:
                self._rerun_pending = False
                self._request_render()

    def _update_zoom_label(self, factor=None):
        try:
            if factor is None:
                factor = self.canvas.transform().m11()
            self.zoomLabel.setText(f"{factor * 100:.0f}%")
        except Exception:
            pass


if __name__ == '__main__':
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    w = ImageStudioWidget('TEST')
    w.show()
    sys.exit(app.exec())
