# -*- coding: utf-8 -*-
"""视频播放器（QtMultimedia / Windows Media Foundation 后端）

支持 MP4/MOV/M4V/MKV/AVI/WMV/FLV 等系统可解码格式（HEVC/H.265 需系统
安装对应编解码器扩展）。功能：进度/音量/倍速/全屏/截图/播放列表（持久化）、
字幕（外部 .srt/.vtt 文本字幕 + MKV/MP4 内嵌字幕轨道识别）。

注意：Windows Media Foundation 后端不会渲染内嵌字幕位图，本播放器会读取
QMediaPlayer 报告的字幕轨道信息供用户在 UI 选择；同时把解析后的文本字幕
叠加显示在视频画面上。
"""
import json
import logging
import os
import re
import time
from dataclasses import dataclass

from PySide6.QtCore import (QEvent, QUrl, Qt, QRect,
                            QThread, QTimer, Signal)
from PySide6.QtGui import QFont, QKeyEvent, QMouseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QVBoxLayout,
                               QLabel, QHeaderView, QWidget)

from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF,
                            InfoBar, InfoBarPosition, PrimaryPushButton, PushButton,
                            Slider, SubtitleLabel, TableWidget, ToggleToolButton, ToolButton)

module_logger = logging.getLogger("flu_widget.video_player")

PLAYLIST_FILE = "./Config/VideoPlaylist.json"
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".wmv", ".flv", ".webm",
              ".ts", ".mpg", ".mpeg")
SUBTITLE_EXTS = (".srt", ".vtt", ".ass", ".ssa")
SPEEDS = ["0.5×", "0.75×", "1.0×", "1.25×", "1.5×", "2.0×"]


def _fmt_ms(ms: int) -> str:
    if ms <= 0:
        return "00:00"
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}" if s < 3600 else \
        f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ── 字幕解析 ────────────────────────────────────────────
@dataclass
class SubtitleCue:
    """单条字幕：起止时间（毫秒）+ 文本（可含换行）。"""
    start_ms: int
    end_ms: int
    text: str


_SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
_VTT_TIME = re.compile(r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{1,3})")


def _parse_ts_to_ms(h: str, m: str, s: str, ms: str) -> int:
    return ((int(h) * 3600) + (int(m) * 60) + int(s)) * 1000 + int(ms.ljust(3, "0")[:3])


def parse_srt(text: str) -> list[SubtitleCue]:
    """解析 SRT 字幕。容错：跳过空块、缺失序号、格式错误。"""
    cues: list[SubtitleCue] = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 用空行切分块
    blocks = re.split(r"\n\s*\n", text.strip())
    for blk in blocks:
        lines = [ln for ln in blk.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        # 第 1 行可能是序号，跳过；若不是序号（即时间码），则没有序号
        idx = 0
        if not _SRT_TIME.search(lines[0]):
            idx = 1
        if idx >= len(lines):
            continue
        m = _SRT_TIME.search(lines[idx])
        if not m:
            continue
        try:
            start_ms = _parse_ts_to_ms(*m.groups())
        except Exception:
            continue
        # 同行或下一行可能含结束时间
        rest = lines[idx][m.end():]
        m2 = _SRT_TIME.search(rest)
        if m2:
            end_ms = _parse_ts_to_ms(*m2.groups())
            body = "\n".join(lines[idx + 1:])
        elif idx + 1 < len(lines) and "-->" in lines[idx + 1]:
            m2 = _SRT_TIME.search(lines[idx + 1])
            if not m2:
                continue
            end_ms = _parse_ts_to_ms(*m2.groups())
            body = "\n".join(lines[idx + 2:])
        else:
            end_ms = start_ms + 3000
            body = "\n".join(lines[idx + 1:])
        body = body.strip()
        if body:
            cues.append(SubtitleCue(start_ms, end_ms, body))
    return cues


def parse_vtt(text: str) -> list[SubtitleCue]:
    """解析 WebVTT 字幕。与 SRT 几乎相同，区别：可选小时段、允许无序号。"""
    cues: list[SubtitleCue] = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 去掉首行 WEBVTT 头
    if text.lstrip().startswith("WEBVTT"):
        nl = text.find("\n")
        text = text[nl + 1:] if nl >= 0 else ""
    blocks = re.split(r"\n\s*\n", text.strip())
    for blk in blocks:
        lines = [ln for ln in blk.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        idx = 0
        if not _VTT_TIME.search(lines[0]):
            idx = 1
        if idx >= len(lines):
            continue
        m = _VTT_TIME.search(lines[idx])
        if not m:
            continue
        h = m.group(1) or "0"
        try:
            start_ms = _parse_ts_to_ms(h, m.group(2), m.group(3), m.group(4))
        except Exception:
            continue
        rest = lines[idx][m.end():]
        m2 = _VTT_TIME.search(rest)
        if m2:
            h2 = m2.group(1) or "0"
            end_ms = _parse_ts_to_ms(h2, m2.group(2), m2.group(3), m2.group(4))
            body = "\n".join(lines[idx + 1:])
        else:
            end_ms = start_ms + 3000
            body = "\n".join(lines[idx + 1:])
        body = body.strip()
        if body:
            cues.append(SubtitleCue(start_ms, end_ms, body))
    return cues


def parse_subtitle_file(path: str) -> tuple[list[SubtitleCue], str]:
    """读取字幕文件并解析。返回 (cue 列表, 错误信息)。ASS/SSA 返回提示。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            raw = f.read()
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="gb18030") as f:
                raw = f.read()
        except Exception as e:
            return [], f"读取失败：{e}"
    except Exception as e:
        return [], f"读取失败：{e}"
    ext = os.path.splitext(path)[1].lower()
    if ext == ".srt":
        return parse_srt(raw), ""
    if ext == ".vtt":
        return parse_vtt(raw), ""
    if ext in (".ass", ".ssa"):
        return [], "暂不支持 ASS/SSA 渲染，请转换为 SRT/VTT"
    # 未知后缀：尝试按内容嗅探
    if "WEBVTT" in raw[:64]:
        return parse_vtt(raw), ""
    return parse_srt(raw), ""


class ClickableSlider(Slider):
    """支持点击定位的进度条。"""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > 0:
            from PySide6.QtWidgets import QStyle
            pos = event.position().toPoint()
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), pos.x(), max(self.width() - 1, 1))
            self.setValue(value)
            self.sliderMoved.emit(value)
        super().mousePressEvent(event)


class SubtitleOverlay(QWidget):
    """自定义字幕叠加层。

    用 QPainter 直接画字幕（白字 + 8 向黑色描边），支持多行文本（如双语字幕）。
    相比 QLabel + 富文本多层 span 描边，本控件：
    - 不会因 position:relative 累加行高，sizeHint 完全由字体度量决定
    - 真正支持 \n 多行（双语字幕上下两行）
    - 跨主题自动取白/黑文本色（在视频上始终清晰）
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 鼠标穿透、不抢焦点
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

        self._text = ""
        self._font_px = 28
        self._outline_px = 3
        self._apply_font()

    def _apply_font(self):
        f = QFont("Microsoft YaHei")
        f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        f.setPixelSize(self._font_px)
        f.setBold(True)
        self.setFont(f)

    def set_style(self, font_px: int, outline_px: int):
        self._font_px = max(10, int(font_px))
        self._outline_px = max(1, int(outline_px))
        self._apply_font()
        self._relayout()  # 字号变 → boundingSize 变 → 重排

    def _parent_resized(self):
        """父容器尺寸变化时调用（resizeEvent 转发）。"""
        self._relayout()

    def set_text(self, text: str):
        new_text = text or ""
        if new_text == self._text:
            # 即使文本相同，位置也可能因父容器尺寸变了；不强制重排
            return
        self._text = new_text
        if not self._text:
            self.hide()
            return
        self.show()
        self._relayout()
        self.update()

    def text(self) -> str:
        return self._text

    def _bounding_size(self, max_w: int) -> tuple[int, int]:
        """根据字体度量算真实尺寸（已含多行）。"""
        if not self._text:
            return 0, 0
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font())
        # boundingRect 给 (x, y, w, h)；x/y 是基线相关偏移，宽度按 max_w 强制换行
        r = fm.boundingRect(QRect(0, 0, max_w, 10000),
                            int(Qt.TextFlag.TextWordWrap), self._text)
        # 行高 = fontHeight + leading；fm.height() 更稳定
        return max(r.width(), 1), max(r.height(), fm.height())

    def _relayout(self):
        if not self._text or self.parent() is None:
            return
        pw = self.parent().width()
        ph = self.parent().height()
        if pw <= 0 or ph <= 0:
            return
        # 字幕区宽度 = 父容器宽度的 80%
        max_w = max(int(pw * 0.8), 240)
        w, h = self._bounding_size(max_w)
        # 贴底部 6% 留白，再加 padding
        pad_x, pad_y = self._outline_px * 4, self._outline_px * 2
        x = (pw - w) // 2 - pad_x
        y = ph - h - int(ph * 0.06) - pad_y
        self.setGeometry(max(x, 0), max(y, 0), w + pad_x * 2, h + pad_y * 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def paintEvent(self, event):
        if not self._text:
            return
        from PySide6.QtGui import QPainter, QFontMetrics, QColor, QPen
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing
                               | QPainter.RenderHint.TextAntialiasing)
        fm = QFontMetrics(self.font())
        # 可绘制区域（去掉 padding）
        pad_x = self._outline_px * 4
        pad_y = self._outline_px * 2
        draw_rect = QRect(pad_x, pad_y,
                          self.width() - pad_x * 2,
                          self.height() - pad_y * 2)
        # 8 个方向的描边偏移
        outline = self._outline_px
        offsets = [(-outline, 0), (outline, 0), (0, -outline), (0, outline),
                   (-outline, -outline), (-outline, outline),
                   (outline, -outline), (outline, outline)]
        # 先描边（黑色）
        painter.setPen(QColor(0, 0, 0, 220))
        for dx, dy in offsets:
            shifted = draw_rect.translated(dx, dy)
            painter.drawText(shifted,
                             int(Qt.AlignmentFlag.AlignHCenter
                                 | Qt.AlignmentFlag.AlignBottom
                                 | Qt.TextFlag.TextWordWrap),
                             self._text)
        # 最后一遍白色覆盖
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(draw_rect,
                         int(Qt.AlignmentFlag.AlignHCenter
                             | Qt.AlignmentFlag.AlignBottom
                             | Qt.TextFlag.TextWordWrap),
                         self._text)


class ClickableVideoWidget(QVideoWidget):
    """双击全屏、Esc 退出、←/→ ±5s、↑/↓ ±30s、空格播放暂停。

    作为字幕叠加层 + 全屏浮动进度条的父容器：
    - SubtitleOverlay 自绘控件，跟随视频画面
    - FullScreenSeekBar 全屏浮动进度条（鼠标移动时显示）
    """

    toggleFullScreen = Signal()
    seekRequested = Signal(int)
    togglePlayRequested = Signal()
    fsSeekRequested = Signal(int)  # 全屏拖动条 seek（独立信号便于预览）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        # 必须打开鼠标跟踪，否则 mouseMoveEvent 仅在按下时触发
        self.setMouseTracking(True)

        # 自绘字幕叠加层
        self.subtitleLabel = SubtitleOverlay(self)
        self.subtitleLabel.set_style(font_px=28, outline_px=3)
        self.subtitleLabel._relayout()

        # 全屏浮动进度条：默认隐藏，仅在全屏模式下被激活
        self.fsSeekBar = FullScreenSeekBar(self)
        self.fsSeekBar.hide()
        self.fsSeekBar.seekTo.connect(self.fsSeekRequested.emit)
        self._relayout_fs_bar()

    def set_subtitle_text(self, text: str):
        self.subtitleLabel.set_text(text)

    def _relayout_fs_bar(self):
        """浮动条固定在底部居中，宽 70%，高 56px。"""
        w = int(self.width() * 0.7)
        h = 56
        x = (self.width() - w) // 2
        y = self.height() - h - 24
        self.fsSeekBar.setGeometry(max(x, 0), max(y, 0), max(w, 400), h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 按视频高度等比缩放字幕（参考高度 720）
        ref = 720
        ratio = max(0.4, min(self.height() / ref, 2.0))
        self.subtitleLabel.set_style(
            font_px=max(14, int(28 * ratio)),
            outline_px=max(1, int(3 * ratio)),
        )
        # 父尺寸变化也需重排（即使字号不变）
        self.subtitleLabel._parent_resized()
        self._relayout_fs_bar()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key.Key_Escape and self.isFullScreen():
            self.setFullScreen(False)
        elif key == Qt.Key.Key_Left:
            self.seekRequested.emit(-5000)
        elif key == Qt.Key.Key_Right:
            self.seekRequested.emit(5000)
        elif key == Qt.Key.Key_Up:
            self.seekRequested.emit(-30000)
        elif key == Qt.Key.Key_Down:
            self.seekRequested.emit(30000)
        elif key == Qt.Key.Key_Space:
            self.togglePlayRequested.emit()
        else:
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.setFullScreen(not self.isFullScreen())
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        super().mouseMoveEvent(event)
        # 全屏模式下：显示浮动进度条
        if self.isFullScreen():
            self.fsSeekBar.show_temporarily()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self.isFullScreen():
            self.fsSeekBar._hide_timer.start()


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


class FullScreenSeekBar(QWidget):
    """全屏浮动进度条。鼠标移动时淡入显示，静止 2s 后淡出。

    - 显示当前进度（已播放 / 总时长）
    - 鼠标悬停时在滑块上方显示时间预览气泡（指向位置对应的时长）
    - 点击/拖动滑块触发 seekTo(int ms) 信号
    - 内部用 QPropertyAnimation 实现淡入淡出
    """

    seekTo = Signal(int)  # 用户拖动或点击后发出目标位置（ms）

    def __init__(self, parent=None):
        super().__init__(parent)
        # 关键：接受鼠标事件、不透明
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self.setAutoFillBackground(False)

        # 内部控件
        from PySide6.QtWidgets import QLabel
        self.posLabel = CaptionLabel("00:00", self)
        self.durLabel = CaptionLabel("00:00", self)
        self.posLabel.setStyleSheet("color: white; background: transparent;")
        self.durLabel.setStyleSheet("color: white; background: transparent;")
        self.slider = Slider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.seekTo.emit)
        # 悬停预览气泡
        self.previewLabel = CaptionLabel("00:00", self)
        self.previewLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.previewLabel.setStyleSheet(
            "color: white; background: rgba(0,0,0,180); padding: 2px 6px;"
            " border-radius: 4px;")
        self.previewLabel.hide()
        self.previewLabel.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # 让 slider 自己也接受悬停事件
        self.slider.setMouseTracking(True)
        self.slider.installEventFilter(self)

        self.hide()
        self._opacity = 0.0

        # 透明度动画：用 QTimer 步进（避免 QPropertyAnimation 与重写方法冲突）
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)  # ~60fps
        self._anim_timer.timeout.connect(self._tick_anim)
        self._target_opacity = 0.0
        self._fade_step = 0.08  # 每帧步进

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(2000)
        self._hide_timer.timeout.connect(self._start_fade_out)

        self.setMouseTracking(True)

    # 暴露给外层调用的接口
    def set_duration(self, ms: int):
        self.slider.setRange(0, max(ms, 0))
        self.durLabel.setText(_fmt_ms(ms))

    def set_position(self, ms: int):
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(ms)
            self.slider.blockSignals(False)
        self.posLabel.setText(_fmt_ms(ms))

    def show_temporarily(self):
        """外部（主 widget）可主动显示并自动隐藏。"""
        self._hide_timer.start()
        self._start_fade_in()

    def _start_fade_in(self):
        self.show()
        self.raise_()
        self._target_opacity = 1.0
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def _start_fade_out(self):
        self._target_opacity = 0.0
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def _tick_anim(self):
        diff = self._target_opacity - self._opacity
        if abs(diff) < self._fade_step:
            self._opacity = self._target_opacity
            self._anim_timer.stop()
            if self._opacity <= 0.01:
                self.hide()
        else:
            self._opacity += self._fade_step if diff > 0 else -self._fade_step
        # 半透明场景下，让 Qt 不参与 alpha 通道合成（避免某些后端双重透明）
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,
                          self._opacity < 0.99)
        self.update()

    def eventFilter(self, obj, event):
        if obj is self.slider:
            et = event.type()
            if et == QEvent.Type.MouseMove:
                self._update_preview(event.position().toPoint())
            elif et == QEvent.Type.Leave:
                self.previewLabel.hide()
        return super().eventFilter(obj, event)

    def _update_preview(self, mouse_pos):
        if self.slider.maximum() <= 0:
            self.previewLabel.hide()
            return
        # 鼠标位置映射到 ms
        from PySide6.QtWidgets import QStyle
        v = QStyle.sliderValueFromPosition(
            self.slider.minimum(), self.slider.maximum(),
            mouse_pos.x(), max(self.slider.width() - 1, 1))
        self.previewLabel.setText(_fmt_ms(v))
        self.previewLabel.adjustSize()
        # 气泡放在鼠标上方（slider 之上）
        x = mouse_pos.x() - self.previewLabel.width() // 2
        y = -self.previewLabel.height() - 6
        self.previewLabel.move(max(x, 0), max(y, -self.previewLabel.height()))
        self.previewLabel.show()
        self.previewLabel.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 布局：posLabel  左  ── slider 中间 ──  durLabel 右
        m = 12
        self.posLabel.setFixedHeight(20)
        self.durLabel.setFixedHeight(20)
        lbl_w = 56
        self.posLabel.setGeometry(m, (self.height() - 20) // 2, lbl_w, 20)
        self.durLabel.setGeometry(self.width() - lbl_w - m,
                                  (self.height() - 20) // 2, lbl_w, 20)
        self.slider.setGeometry(m + lbl_w + 8,
                                (self.height() - 20) // 2,
                                self.width() - (lbl_w + 8) * 2 - m * 2,
                                20)

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor, QBrush
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 半透明黑色背板
        bg = QColor(0, 0, 0, int(180 * self._opacity))
        painter.fillRect(self.rect(), QBrush(bg))
        painter.setPen(QColor(255, 255, 255, int(220 * self._opacity)))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class ScreenshotWorker(QThread):
    """后台把视频帧转换为图片并保存，避免大图转换阻塞 UI。"""
    done = Signal(bool, str)  # (是否成功, 保存路径)

    def __init__(self, frame, path: str, parent=None):
        super().__init__(parent)
        self._frame = frame
        self._path = path

    def run(self):
        try:
            img = self._frame.toImage()
            if img.isNull() or not img.save(self._path):
                self.done.emit(False, self._path)
            else:
                self.done.emit(True, self._path)
        except Exception as e:
            module_logger.error(f"截图保存异常: {e}")
            self.done.emit(False, self._path)


class VideoPlayerWidget(QFrame):
    """视频播放器主界面。"""

    def __init__(self, name: str, parent=None):
        super().__init__(parent=parent)
        self.setObjectName(name)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.8)
        self.playlist: list[str] = []
        self.current_index = -1
        self._last_volume = 80  # 静音前记住的音量，取消静音时恢复
        self._shot_worker = None

        # 字幕状态
        # self._cues: 字幕条目（外部导入 + 内嵌轨道由 QMediaPlayer 报告）
        # self._sub_enabled: 是否启用字幕显示（叠加层）
        self._cues: list[SubtitleCue] = []
        self._sub_source_label = ""  # 当前字幕源名称（显示在提示中）
        self._sub_enabled = True
        # 内嵌字幕轨道：保存 QMediaPlayer 报告的轨道索引，避免 setSource 时丢失
        self._embedded_subtitle_tracks: list = []
        self._active_embedded_sub_idx: int = -1
        self._cue_cursor = 0  # 上一条已显示 cue 的下标，避免每次从头扫

        self._build_ui()
        self._bind()
        self._load_playlist()

    # ── UI ─────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 16)
        layout.setSpacing(10)

        # 画布
        self.videoWidget = ClickableVideoWidget()
        self.videoWidget.toggleFullScreen.connect(self._on_toggle_fullscreen)
        self.videoWidget.seekRequested.connect(self._on_keyboard_seek)
        self.videoWidget.togglePlayRequested.connect(self._on_toggle_play)
        self.player.setVideoOutput(self.videoWidget)
        layout.addWidget(self.videoWidget, 1)

        # 当前文件 + 进度
        self.titleLabel = SubtitleLabel("未在播放")
        layout.addWidget(self.titleLabel)
        self.hintLabel = CaptionLabel(
            "双击画面全屏；格式支持取决于系统解码器（MP4/MKV/AVI/WMV 等），"
            "HEVC/H.265 需安装系统编解码器扩展")
        layout.addWidget(self.hintLabel)

        progress_row = QHBoxLayout()
        self.posLabel = CaptionLabel("00:00")
        self.progressSlider = ClickableSlider(Qt.Orientation.Horizontal)
        self.progressSlider.setRange(0, 0)
        self.durLabel = CaptionLabel("00:00")
        progress_row.addWidget(self.posLabel)
        progress_row.addWidget(self.progressSlider, 1)
        progress_row.addWidget(self.durLabel)
        layout.addLayout(progress_row)

        # 控制条
        ctrl = QHBoxLayout()
        self.prevBtn = ToolButton(FIF.PAGE_LEFT)
        self.prevBtn.setToolTip("上一个")
        self.prevBtn.clicked.connect(self.play_previous)
        ctrl.addWidget(self.prevBtn)
        self.playBtn = PrimaryPushButton(FIF.PLAY, "播 放")
        self.playBtn.setFixedWidth(120)
        self.playBtn.clicked.connect(self._on_toggle_play)
        ctrl.addWidget(self.playBtn)
        self.nextBtn = ToolButton(FIF.PAGE_RIGHT)
        self.nextBtn.setToolTip("下一个")
        self.nextBtn.clicked.connect(self.play_next)
        ctrl.addWidget(self.nextBtn)

        self.volumeBtn = ToolButton(FIF.VOLUME)
        self.volumeBtn.clicked.connect(self._on_toggle_mute)
        ctrl.addWidget(self.volumeBtn)
        self.volumeSlider = Slider(Qt.Orientation.Horizontal)
        self.volumeSlider.setRange(0, 100)
        self.volumeSlider.setValue(80)
        self.volumeSlider.setFixedWidth(110)
        self.volumeSlider.valueChanged.connect(self._on_volume_changed)
        ctrl.addWidget(self.volumeSlider)

        ctrl.addWidget(BodyLabel("倍速"))
        self.speedBox = ComboBox()
        self.speedBox.addItems(SPEEDS)
        self.speedBox.setCurrentIndex(2)
        self.speedBox.currentIndexChanged.connect(self._on_speed_changed)
        ctrl.addWidget(self.speedBox)

        # 字幕：按钮（开关/导入）+ 轨道下拉
        # ToggleToolButton：内置 checkable + 选中态自动反色图标（qfw 官方）
        self.subBtn = ToggleToolButton(FIF.LABEL)
        self.subBtn.setToolTip("字幕（点击切换显隐）")
        self.subBtn.setChecked(True)
        self.subBtn.toggled.connect(self._on_subtitle_toggle)
        ctrl.addWidget(self.subBtn)
        self.subTrackBox = ComboBox()
        self.subTrackBox.setMinimumWidth(120)
        self.subTrackBox.setToolTip("选择字幕轨道")
        self.subTrackBox.currentIndexChanged.connect(self._on_subtitle_track_changed)
        ctrl.addWidget(self.subTrackBox)
        self.importSubBtn = ToolButton(FIF.ADD)
        self.importSubBtn.setToolTip("导入外部字幕（.srt / .vtt）")
        self.importSubBtn.clicked.connect(self._on_import_subtitle)
        ctrl.addWidget(self.importSubBtn)

        self.shotBtn = ToolButton(FIF.CAMERA)
        self.shotBtn.setToolTip("截图")
        self.shotBtn.clicked.connect(self._on_screenshot)
        ctrl.addWidget(self.shotBtn)
        self.fsBtn = ToolButton(FIF.FULL_SCREEN)
        self.fsBtn.setToolTip("全屏")
        self.fsBtn.clicked.connect(self._on_toggle_fullscreen)
        ctrl.addWidget(self.fsBtn)
        ctrl.addStretch(1)
        layout.addLayout(ctrl)

        # 播放列表
        list_header = QHBoxLayout()
        list_header.addWidget(SubtitleLabel("播放列表"))
        list_header.addStretch(1)
        btn_open = PushButton(FIF.VIDEO, "打开视频")
        btn_open.clicked.connect(self._on_open_files)
        list_header.addWidget(btn_open)
        btn_folder = PushButton(FIF.FOLDER_ADD, "打开文件夹")
        btn_folder.clicked.connect(self._on_open_folder)
        list_header.addWidget(btn_folder)
        btn_clear = ToolButton(FIF.BROOM)
        btn_clear.setToolTip("清空列表")
        btn_clear.clicked.connect(self._on_clear_playlist)
        list_header.addWidget(btn_clear)
        layout.addLayout(list_header)

        self.table = TableWidget()
        self.table.setColumnCount(1)
        self.table.setHorizontalHeaderLabels(["视频文件"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setFixedHeight(160)
        self.table.doubleClicked.connect(lambda i: self._play_at(i.row()))
        layout.addWidget(self.table)

    def _bind(self):
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.errorOccurred.connect(
            lambda err, msg: self.hintLabel.setText(f"播放出错：{msg}（缺少解码器？）"))
        self.player.tracksChanged.connect(self._on_tracks_changed)
        self.player.activeTracksChanged.connect(self._on_active_tracks_changed)
        self.progressSlider.sliderMoved.connect(self.player.setPosition)
        # 全屏浮动进度条：拖动 seek + 同步显示
        self.videoWidget.fsSeekRequested.connect(self.player.setPosition)

    # ── 列表 ────────────────────────────────────────────
    def _load_playlist(self):
        try:
            with open(PLAYLIST_FILE, "r", encoding="utf-8") as f:
                for p in json.load(f) or []:
                    if os.path.isfile(p):
                        self.playlist.append(p)
        except Exception:
            pass
        self._refresh_table()

    def _save_playlist(self):
        try:
            with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
                json.dump(self.playlist, f, ensure_ascii=False, indent=2)
        except Exception as e:
            module_logger.error(f"保存视频列表失败: {e}")

    def _refresh_table(self):
        self.table.setRowCount(len(self.playlist))
        for r, p in enumerate(self.playlist):
            mark = "▶ " if r == self.current_index else ""
            self.table.setItem(r, 0, _tw_item(mark + os.path.basename(p)))

    def _on_open_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件", "",
            f"视频文件 ({' '.join('*' + e for e in VIDEO_EXTS)});;所有文件 (*)")
        for p in files:
            self.playlist.append(p)
        if files:
            self._after_playlist_change()
            self._play_at(len(self.playlist) - len(files))

    def _on_open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if not folder:
            return
        added = 0
        for root, _dirs, files in os.walk(folder):
            for f in sorted(files):
                if f.lower().endswith(VIDEO_EXTS):
                    self.playlist.append(os.path.join(root, f))
                    added += 1
        if added:
            self._after_playlist_change()

    def _after_playlist_change(self):
        self._refresh_table()
        self._save_playlist()

    def _on_clear_playlist(self):
        self.player.stop()
        self.playlist.clear()
        self.current_index = -1
        self._refresh_table()
        self._save_playlist()
        self._update_now_playing()

    # ── 播放控制 ────────────────────────────────────────
    def _play_at(self, index: int):
        if not (0 <= index < len(self.playlist)):
            return
        self.current_index = index
        # 切源前清空字幕叠加，重置游标与提示
        self._reset_subtitle_state(keep_source=False)
        self.player.setSource(QUrl.fromLocalFile(self.playlist[index]))
        self.player.play()
        self._refresh_table()
        self._update_now_playing()
        # 探测同目录下的同名字幕（自动加载为最便捷的体验）
        self._auto_load_side_subtitle(self.playlist[index])

    def play_next(self):
        if self.playlist:
            self._play_at((self.current_index + 1) % len(self.playlist))

    def play_previous(self):
        if self.playlist:
            self._play_at((self.current_index - 1) % len(self.playlist))

    def _on_toggle_play(self):
        if not self.playlist:
            self.hintLabel.setText("请先打开视频文件")
            return
        if self.current_index < 0:
            self._play_at(0)
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_toggle_fullscreen(self):
        self.videoWidget.setFullScreen(not self.videoWidget.isFullScreen())

    def _on_keyboard_seek(self, delta_ms: int):
        self.player.setPosition(max(0, self.player.position() + delta_ms))

    def _on_volume_changed(self, v: int):
        self.audio.setVolume(v / 100)
        if v > 0:
            self._last_volume = v
        if v == 0:
            self.audio.setMuted(True)
        elif self.audio.isMuted():
            self.audio.setMuted(False)
        self._update_volume_icon(v)

    def _update_volume_icon(self, v: int):
        muted = self.audio.isMuted() or v == 0
        self.volumeBtn.setIcon(FIF.MUTE if muted else FIF.VOLUME)
        self.volumeBtn.setToolTip("取消静音" if muted else "静音")

    def _on_toggle_mute(self):
        if self.audio.isMuted():
            # 取消静音：恢复之前的音量（_on_volume_changed 内会解除静音并刷新图标）
            self.volumeSlider.setValue(max(self._last_volume, 1))
        else:
            # 静音：记住当前音量，滑条归 0（_on_volume_changed 内会置为静音）
            self.volumeSlider.setValue(0)

    def _on_speed_changed(self, index: int):
        try:
            self.player.setPlaybackRate(float(SPEEDS[index].rstrip("×")))
        except Exception:
            pass

    def _on_screenshot(self):
        frame = self.videoWidget.videoSink().videoFrame()
        if frame is None or not frame.isValid():
            InfoBar.warning("截图失败", "当前没有可截取的画面", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return
        out_dir = os.path.join(os.getcwd(), "Screenshots")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            module_logger.error(f"创建截图目录失败: {e}")
        default_path = os.path.join(
            out_dir, f"NoMY_video_{time.strftime('%Y%m%d_%H%M%S')}.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存截图", default_path,
            "PNG 图片 (*.png);;JPEG 图片 (*.jpg)")
        if not path:
            return
        # 帧转换与写盘放后台线程，避免大帧 toImage 阻塞 UI 造成卡顿
        self.shotBtn.setEnabled(False)
        self._shot_worker = ScreenshotWorker(frame, path, self)
        self._shot_worker.done.connect(self._on_screenshot_done)
        self._shot_worker.finished.connect(self._shot_worker.deleteLater)
        self._shot_worker.start()

    def _on_screenshot_done(self, ok: bool, path: str):
        self.shotBtn.setEnabled(True)
        if ok:
            InfoBar.success("截图成功", path, parent=self,
                            position=InfoBarPosition.TOP, duration=4000)
        else:
            InfoBar.error("截图失败", "保存文件失败", parent=self,
                          position=InfoBarPosition.TOP, duration=3000)

    # ── 播放器事件 ──────────────────────────────────────
    def _on_position_changed(self, pos: int):
        if not self.progressSlider.isSliderDown():
            self.progressSlider.blockSignals(True)
            self.progressSlider.setValue(pos)
            self.progressSlider.blockSignals(False)
        self.posLabel.setText(_fmt_ms(pos))
        # 同步全屏浮动条
        self.videoWidget.fsSeekBar.set_position(pos)
        self._sync_subtitle_at(pos)

    def _on_duration_changed(self, dur: int):
        self.progressSlider.blockSignals(True)
        self.progressSlider.setRange(0, max(dur, 0))
        self.progressSlider.blockSignals(False)
        self.durLabel.setText(_fmt_ms(dur))
        # 同步全屏浮动条
        self.videoWidget.fsSeekBar.set_duration(dur)

    def _on_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.playBtn.setText("暂 停" if playing else "播 放")
        self.playBtn.setIcon(FIF.PAUSE if playing else FIF.PLAY)

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_next()
        # LoadedMedia 时去探一次内嵌轨道（部分后端此时才填齐）
        elif status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._refresh_subtitle_track_box()

    def _on_tracks_changed(self):
        """内嵌字幕轨道变化时刷新下拉。"""
        self._refresh_subtitle_track_box()

    def _on_active_tracks_changed(self):
        """任意轨道（含字幕）激活状态变化时，记录当前内嵌字幕轨道。"""
        try:
            self._active_embedded_sub_idx = self.player.activeSubtitleTrack()
        except Exception:
            pass

    def _update_now_playing(self):
        if 0 <= self.current_index < len(self.playlist):
            self.titleLabel.setText(os.path.basename(self.playlist[self.current_index]))
        else:
            self.titleLabel.setText("未在播放")

    # ── 字幕：内嵌轨道识别 ──────────────────────────────
    def _refresh_subtitle_track_box(self):
        """读取 QMediaPlayer 报告的字幕轨道，刷新下拉框（保留外部导入项）。"""
        try:
            tracks = list(self.player.subtitleTracks())
        except Exception:
            tracks = []
        self._embedded_subtitle_tracks = tracks
        # 不打断用户当前选中的外部字幕项
        prev_data = self.subTrackBox.currentData()
        self.subTrackBox.blockSignals(True)
        self.subTrackBox.clear()
        # 第一项：关闭字幕
        self.subTrackBox.addItem("字幕：关闭", userData=None)
        # 外部导入的字幕
        if self._cues:
            tag = self._sub_source_label or "外部字幕"
            self.subTrackBox.addItem(f"字幕：{tag} (外部)", userData="external")
        # 内嵌字幕轨道
        for i, t in enumerate(tracks):
            label = self._format_track_label(t, i)
            self.subTrackBox.addItem(f"字幕：{label} (内嵌)", userData=("embedded", i))
        # 还原选中
        if prev_data is not None:
            for i in range(self.subTrackBox.count()):
                if self.subTrackBox.itemData(i) == prev_data:
                    self.subTrackBox.setCurrentIndex(i)
                    break
        else:
            # 默认优先外部
            if self._cues:
                for i in range(self.subTrackBox.count()):
                    if self.subTrackBox.itemData(i) == "external":
                        self.subTrackBox.setCurrentIndex(i)
                        break
            else:
                self.subTrackBox.setCurrentIndex(0)
        self.subTrackBox.blockSignals(False)

    @staticmethod
    def _format_track_label(track, idx: int) -> str:
        """从 QMediaMetaData 读取人类可读标签。

        优先级：Title / Description / Comment / Language / TrackNumber。
        注意 Qt 的 QMediaMetaData 在 MKV 场景下常常拿不到任何字符串字段
        （MKV 的 Track.Name 不被映射到 Qt Key），这时回退为"轨道 N"。
        """
        try:
            from PySide6.QtMultimedia import QMediaMetaData as _QMD
            for key in (_QMD.Key.Title, _QMD.Key.Description,
                        _QMD.Key.Comment):
                try:
                    val = track.stringValue(key)
                except Exception:
                    val = None
                if val:
                    val = val.strip()
                    if val and val.lower() != "default" and "<" not in val:
                        return f"轨道{idx + 1} · {val[:24]}"
            for key in (_QMD.Key.Language,):
                try:
                    raw = track.value(key)
                except Exception:
                    raw = None
                text = str(raw).strip() if raw is not None else ""
                if text and text.lower() != "default" and "<" not in text:
                    return f"轨道{idx + 1} [{text}]"
            # TrackNumber（int）作为最后兜底
            try:
                tn = track.value(_QMD.Key.TrackNumber)
                if tn is not None and int(tn) > 0:
                    return f"字幕轨道{int(tn)}"
            except Exception:
                pass
        except Exception:
            pass
        return f"字幕轨道{idx + 1}"

    def _on_subtitle_track_changed(self, index: int):
        data = self.subTrackBox.itemData(index)
        if data is None:
            # 关闭字幕
            self._sub_enabled = False
            self.videoWidget.set_subtitle_text("")
            return
        if data == "external":
            self._sub_enabled = True
            self._sub_source_label = self._sub_source_label or "外部字幕"
            # 让当前位置的 cue 立即显示
            self._cue_cursor = 0
            self._sync_subtitle_at(self.player.position())
            return
        if isinstance(data, tuple) and data[0] == "embedded":
            self._sub_enabled = True
            emb_idx = data[1]
            # 通知 QMediaPlayer 切换内嵌字幕轨道（WMF 后端通常不会渲染，仅记录）
            try:
                if 0 <= emb_idx < len(self._embedded_subtitle_tracks):
                    self.player.setActiveSubtitleTrack(emb_idx)
            except Exception:
                pass
            self.hintLabel.setText(
                "已切换内嵌字幕轨道；当前 Windows 后端不会渲染内嵌字幕位图，"
                "如未显示请用「+」导入同名 .srt/.vtt 文件")

    # ── 字幕：外部导入 / 自动加载 ────────────────────────
    def _on_subtitle_toggle(self, checked: bool):
        self._sub_enabled = checked
        if not checked:
            self.videoWidget.set_subtitle_text("")

    def _on_import_subtitle(self):
        if not (0 <= self.current_index < len(self.playlist)):
            InfoBar.warning("无法导入", "请先打开一个视频", parent=self,
                            position=InfoBarPosition.TOP, duration=2500)
            return
        video_path = self.playlist[self.current_index]
        ddir = os.path.dirname(video_path)
        fn = QFileDialog.getOpenFileName(
            self, "导入字幕文件", ddir,
            f"字幕文件 ({' '.join('*' + e for e in SUBTITLE_EXTS)});;"
            "SRT/VTT (*.srt *.vtt);;所有文件 (*)")
        path = fn[0]
        if not path:
            return
        self._load_external_subtitle(path)

    def _load_external_subtitle(self, path: str):
        cues, err = parse_subtitle_file(path)
        if err:
            InfoBar.error("字幕加载失败", err, parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        if not cues:
            InfoBar.warning("字幕为空", "未解析到任何字幕条目", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return
        self._cues = cues
        self._sub_source_label = os.path.basename(path)
        self._cue_cursor = 0
        self._refresh_subtitle_track_box()
        InfoBar.success("字幕已加载", f"{os.path.basename(path)}（{len(cues)} 条）",
                        parent=self, position=InfoBarPosition.TOP, duration=2500)
        # 立即把当前时间点的字幕显示出来
        self._sync_subtitle_at(self.player.position())

    def _load_multiple_external_subtitles(self, paths: list[str]):
        """加载多个字幕文件并叠加（用于多语言/双语自动加载）。"""
        if not paths:
            return
        all_cues: list[SubtitleCue] = []
        labels: list[str] = []
        skipped: list[str] = []
        for p in paths:
            cues, err = parse_subtitle_file(p)
            if err or not cues:
                skipped.append(os.path.basename(p))
                continue
            all_cues.extend(cues)
            labels.append(os.path.basename(p))
        if not all_cues:
            InfoBar.warning("字幕加载失败", "所有候选字幕均解析失败", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return
        # 按时戳排序（混合多源时按时间对齐）
        all_cues.sort(key=lambda c: c.start_ms)
        self._cues = all_cues
        self._sub_source_label = " + ".join(labels) if len(labels) <= 2 else (
            f"{labels[0]} 等 {len(labels)} 个")
        self._cue_cursor = 0
        self._refresh_subtitle_track_box()
        msg = f"已叠加 {len(labels)} 个字幕（{len(all_cues)} 条）"
        if skipped:
            msg += f"；跳过 {len(skipped)} 个无效文件"
        InfoBar.success("字幕已加载", msg, parent=self,
                        position=InfoBarPosition.TOP, duration=3000)
        self._sync_subtitle_at(self.player.position())

    def _auto_load_side_subtitle(self, video_path: str):
        """自动加载视频同目录下的同名/同前缀字幕。

        匹配规则（按优先级）：
        1) 完全同名：xxx.mkv → xxx.srt / xxx.vtt
        2) 多语言变体：xxx.mkv → xxx.zh.srt / xxx.eng.srt / xxx.chs.srt ...
        3) 同前缀同后缀：<prefix>.mkv → <prefix>.*.srt / <prefix>.*.vtt
        所有匹配文件会**叠加**进字幕源（解析后的 cue 按时间戳对齐渲染）。
        """
        base, _ = os.path.splitext(video_path)
        ddir = os.path.dirname(video_path)
        stem = os.path.basename(base)
        candidates: list[str] = []

        # 1) 完全同名
        for ext in (".srt", ".vtt"):
            cand = base + ext
            if os.path.isfile(cand):
                candidates.append(cand)

        # 2) 多语言变体：xxx.zh.srt / xxx.eng.srt ...
        if not candidates:
            try:
                for fn in os.listdir(ddir):
                    fp = os.path.join(ddir, fn)
                    if not os.path.isfile(fp):
                        continue
                    name, ext = os.path.splitext(fn)
                    if ext.lower() not in (".srt", ".vtt"):
                        continue
                    # 模式 A：<stem>.<lang>.<ext>  /  <stem>.<lang>
                    if name == stem:
                        continue  # 已在 1) 处理
                    if name.startswith(stem + "."):
                        candidates.append(fp)
            except OSError:
                pass

        if not candidates:
            return

        # 按文件名排序，多文件叠加
        candidates.sort()
        self._load_multiple_external_subtitles(candidates)

    def _reset_subtitle_state(self, keep_source: bool = False):
        """切源/清空时调用。默认连外部字幕一起清。"""
        self.videoWidget.set_subtitle_text("")
        if not keep_source:
            self._cues = []
            self._sub_source_label = ""
            self._cue_cursor = 0
            self._embedded_subtitle_tracks = []
            self._active_embedded_sub_idx = -1

    # ── 字幕：按播放位置同步 ─────────────────────────────
    def _sync_subtitle_at(self, pos_ms: int):
        """根据当前播放位置选择并显示当前 cue（支持多源叠加渲染）。"""
        if not self._sub_enabled or not self._cues:
            self.videoWidget.set_subtitle_text("")
            return
        cues = self._cues
        # 1) 快路径：从上次游标处向后扫
        i = min(max(self._cue_cursor, 0), len(cues) - 1)
        while i < len(cues) and cues[i].end_ms < pos_ms:
            i += 1
        if i >= len(cues) or cues[i].start_ms > pos_ms:
            # 二分兜底（处理大跨度向后 seek）
            lo, hi = 0, len(cues) - 1
            hit = -1
            while lo <= hi:
                mid = (lo + hi) // 2
                c = cues[mid]
                if c.end_ms < pos_ms:
                    lo = mid + 1
                elif c.start_ms > pos_ms:
                    hi = mid - 1
                else:
                    hit = mid
                    break
            if hit < 0:
                self.videoWidget.set_subtitle_text("")
                self._cue_cursor = max(0, lo - 1)
                return
            i = hit

        # 2) 收集所有覆盖 pos_ms 的 cue（最多 3 个，避免溢出屏幕）
        MAX_LINES = 3
        active = []
        # 优先命中 i 自身，然后向前向后各看一条（处理交错的双语）
        active.append(cues[i])
        j = i - 1
        while j >= 0 and len(active) < MAX_LINES:
            if cues[j].end_ms >= pos_ms and cues[j].start_ms <= pos_ms:
                active.insert(0, cues[j])
            j -= 1
        j = i + 1
        while j < len(cues) and len(active) < MAX_LINES:
            if cues[j].start_ms <= pos_ms and cues[j].end_ms >= pos_ms:
                active.append(cues[j])
            j += 1

        # 3) 拼接为多行（按 cue 顺序）
        merged = "\n".join(c.text for c in active).strip()
        self.videoWidget.set_subtitle_text(merged)
        # 更新游标到 active 中最早一条，便于下次快路径
        self._cue_cursor = max(0, i - (len(active) - 1))


def _tw_item(text: str):
    from PySide6.QtWidgets import QTableWidgetItem
    return QTableWidgetItem(text)
