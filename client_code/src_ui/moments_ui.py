# -*- coding: utf-8 -*-
"""朋友圈（与好友联动，基于服务器）

- 发布：文字 + 多图（≤9，自动压缩 ≤2MB，按用户 ID 存服务器）
- 信息流：好友与自己的动态（圆形文字头像、九宫格图片、时间），可删除自己的
- 图片：服务端返回相对路径，客户端拼成完整 URL；带 PIL 解码兜底与 LRU 缓存
"""
import io
import logging
import os
import time
from collections import OrderedDict

from PIL import Image as PILImage
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from qfluentwidgets import (AvatarWidget, BodyLabel, CaptionLabel, CardWidget,
                            FluentIcon as FIF, InfoBar, InfoBarPosition,
                            PrimaryPushButton, PushButton, ScrollArea,
                            SubtitleLabel, TextEdit, TitleLabel, ToolButton)

from src_ui import cfg
from src_ui.login_dialog import RUNTIME
from src_ui.friends_ui import _Task, _decode_image
from src import UPDATE_SERVER
from src import auth_client

module_logger = logging.getLogger("flu_widget.moments")

MAX_IMAGES = 9
THUMB_SIZE = 110
_IMG_CACHE_MAX = 256
_IMG_CACHE: "OrderedDict[str, QPixmap]" = OrderedDict()


def _token() -> str:
    return (cfg.auth_token.value or RUNTIME.get("token") or "").strip()


def _my_uid() -> str:
    return (cfg.auth_user_id.value or RUNTIME.get("user_id") or "").strip()


def _now_str(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def _abs_url(url: str) -> str:
    """把 /api/files?... 这种相对路径补成完整地址；已是完整 URL 则原样返回。"""
    if not url:
        return url
    if url.startswith(("http://", "https://")):
        return url
    return f"{UPDATE_SERVER.rstrip('/')}{url}"


def _cache_get(key: str) -> QPixmap | None:
    pix = _IMG_CACHE.get(key)
    if pix is not None:
        _IMG_CACHE.move_to_end(key)
    return pix


def _cache_put(key: str, pix: QPixmap) -> None:
    _IMG_CACHE[key] = pix
    _IMG_CACHE.move_to_end(key)
    while len(_IMG_CACHE) > _IMG_CACHE_MAX:
        _IMG_CACHE.popitem(last=False)


class ImageViewerDialog(QDialog):
    """大图查看，支持多图上下张切换。"""

    def __init__(self, items: list[tuple[QPixmap, str]], index: int, parent=None):
        """items: [(pixmap, caption), ...]；caption 显示在窗口标题。"""
        super().__init__(parent)
        self._items = items
        self._index = max(0, min(index, len(items) - 1))
        self.setWindowTitle(self._caption())

        self.imageLabel = QLabel()
        self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = CaptionLabel("")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        nav = QHBoxLayout()
        self.prevBtn = PushButton("上一张")
        self.prevBtn.clicked.connect(lambda: self._step(-1))
        self.nextBtn = PushButton("下一张")
        self.nextBtn.clicked.connect(lambda: self._step(1))
        nav.addStretch(1)
        nav.addWidget(self.prevBtn)
        nav.addWidget(self.nextBtn)
        nav.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.imageLabel, 1)
        layout.addWidget(self.hint)
        layout.addLayout(nav)

        self._render()
        self.resize(720, 600)

    def _caption(self) -> str:
        if not self._items:
            return "查看图片"
        cap = self._items[self._index][1] if self._index < len(self._items) else ""
        return f"查看图片  {self._index + 1}/{len(self._items)}  {cap}".strip()

    def _step(self, delta: int):
        if not self._items:
            return
        self._index = (self._index + delta) % len(self._items)
        self._render()

    def _render(self):
        pix = self._items[self._index][0] if self._items else QPixmap()
        screen = self.screen()
        max_h = int(screen.availableGeometry().height() * 0.78) if screen else 700
        max_w = int(screen.availableGeometry().width() * 0.78) if screen else 900
        scaled = pix
        if not scaled.isNull():
            if scaled.height() > max_h or scaled.width() > max_w:
                scaled = pix.scaled(max_w, max_h,
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
        self.imageLabel.setPixmap(scaled)
        self.hint.setText("← / → 切换，Esc 关闭" if len(self._items) > 1 else "Esc 关闭")
        self.setWindowTitle(self._caption())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        if event.key() == Qt.Key.Key_Left:
            self._step(-1)
            return
        if event.key() == Qt.Key.Key_Right:
            self._step(1)
            return
        super().keyPressEvent(event)


class MomentImageCell(QLabel):
    """九宫格中的单张图片格，支持点击放大或重试。"""

    clicked = Signal(int)

    _LOADING = "加载中…"
    _FAIL = "加载失败\n点击重试"
    _CORRUPT = "图片损坏\n点击重试"

    def __init__(self, index: int, parent=None):
        super().__init__(self._LOADING, parent)
        self._index = index
        self.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QLabel{background:#f5f5f5; border:1px solid #e0e0e0; border-radius:6px;"
            "color:#888;}")
        self._failed = False

    def set_loading(self):
        self._failed = False
        self.setText(self._LOADING)
        self.setPixmap(QPixmap())

    def set_pixmap(self, pix: QPixmap):
        self._failed = False
        scaled = pix.scaled(THUMB_SIZE, THUMB_SIZE,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)

    def set_failed(self, reason: str = "fail"):
        self._failed = True
        self.setText(self._FAIL if reason == "fail" else self._CORRUPT)
        self.setPixmap(QPixmap())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._index)
            event.accept()
            return
        super().mousePressEvent(event)


class MomentCard(CardWidget):
    """单条朋友圈卡片。"""

    def __init__(self, moment: dict, parent=None):
        super().__init__(parent)
        self.moment = moment
        self._pixmaps: list[QPixmap] = []
        self._cells: list[MomentImageCell] = []
        self._urls: list[str] = []

        my_uid = _my_uid()
        self.mine = str(moment.get("user_id", "")) == my_uid

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        avatar = AvatarWidget()
        author = moment.get("author") or moment.get("username") or "?"
        avatar.setText(author[0])
        avatar.setRadius(24)
        avatar.setBackgroundColor(QColor("#009faa"), QColor("#006a70"))
        layout.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(4)
        head_row = QHBoxLayout()
        head_row.addWidget(BodyLabel(
            f"{author} · {moment.get('user_id', '')}"))
        head_row.addStretch(1)
        head_row.addWidget(CaptionLabel(_now_str(moment.get("created_at", 0))))
        if self.mine:
            del_btn = ToolButton(FIF.DELETE)
            del_btn.setToolTip("删除")
            del_btn.clicked.connect(self._on_delete)
            head_row.addWidget(del_btn)
        body.addLayout(head_row)

        text = moment.get("text", "")
        if text:
            text_label = BodyLabel(text)
            text_label.setTextFormat(Qt.PlainText)
            text_label.setWordWrap(True)
            body.addWidget(text_label)

        self._urls = list(moment.get("images", []))[:MAX_IMAGES]
        if self._urls:
            grid = QGridLayout()
            grid.setSpacing(6)
            for i, rel in enumerate(self._urls):
                cell = MomentImageCell(i)
                cell.clicked.connect(self._on_cell_clicked)
                self._cells.append(cell)
                grid.addWidget(cell, i // 3, i % 3)
                self._load_image_async(cell, rel)
            body.addLayout(grid)
        layout.addLayout(body, 1)

    def _load_image_async(self, cell: MomentImageCell, rel: str):
        full = _abs_url(rel)
        cached = _cache_get(rel)
        if cached is not None and not cached.isNull():
            self._pixmaps.append(cached)
            cell.set_pixmap(cached)
            return
        self._pixmaps.append(QPixmap())

        def _work():
            return auth_client.download_bytes(full)

        def _done(data):
            idx = self._cells.index(cell) if cell in self._cells else -1
            if idx < 0 or idx >= len(self._urls) or self._urls[idx] != rel:
                return
            img = _decode_image(data) if data else None
            if img is None:
                cell.set_failed("fail" if not data else "corrupt")
                return
            pix = QPixmap.fromImage(img)
            if pix.isNull():
                cell.set_failed("corrupt")
                return
            _cache_put(rel, pix)
            if idx < len(self._pixmaps):
                self._pixmaps[idx] = pix
            cell.set_pixmap(pix)

        task = _Task(_work, self)
        task.done.connect(_done)
        task.finished.connect(task.deleteLater)
        task.start()

    def _on_cell_clicked(self, index: int):
        if not (0 <= index < len(self._urls)):
            return
        rel = self._urls[index]
        cached = _cache_get(rel)
        if cached is not None and not cached.isNull():
            self._show_viewer([(cached, f"图片 {index + 1}")], 0)
            return

        def _work():
            return auth_client.download_bytes(_abs_url(rel))

        def _done(data):
            img = _decode_image(data) if data else None
            if img is None:
                InfoBar.warning("查看失败", "图片加载失败，请稍后再试",
                                parent=self.window(),
                                position=InfoBarPosition.TOP, duration=3000)
                return
            pix = QPixmap.fromImage(img)
            _cache_put(rel, pix)
            self._show_viewer([(pix, f"图片 {index + 1}")], 0)

        task = _Task(_work, self)
        task.done.connect(_done)
        task.finished.connect(task.deleteLater)
        task.start()

    def _show_viewer(self, items: list[tuple[QPixmap, str]], index: int):
        if not items:
            return
        dlg = ImageViewerDialog(items, index, self.window())
        dlg.exec()

    def _on_delete(self):
        token = _token()

        def _done(r):
            parent = self.parent()
            while parent and not isinstance(parent, MomentsInterface):
                parent = parent.parent()
            if r and r[0] and isinstance(parent, MomentsInterface):
                parent.refresh()

        task = _Task(lambda: auth_client.moments_delete(UPDATE_SERVER, token,
                                                        self.moment.get("id", 0)),
                     self)
        task.done.connect(_done)
        task.finished.connect(task.deleteLater)
        task.start()


class MomentsInterface(ScrollArea):
    """朋友圈主界面。"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = QVBoxLayout(self.scrollWidget)
        self._selected_images: list[str] = []
        self._build_ui()
        self.resize(900, 760)
        self.setViewportMargins(0, 20, 12, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.refresh()

    def _build_ui(self):
        self.expandLayout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("朋友圈"))
        header.addStretch(1)
        refresh_btn = PushButton(FIF.SYNC, "刷新")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        self.expandLayout.addLayout(header)

        publish_card = CardWidget()
        pl = QVBoxLayout(publish_card)
        pl.setContentsMargins(16, 14, 16, 14)
        pl.setSpacing(8)
        pl.addWidget(SubtitleLabel("分享新鲜事"))
        self.inputBox = TextEdit()
        self.inputBox.setPlaceholderText("这一刻的想法…")
        self.inputBox.setFixedHeight(72)
        pl.addWidget(self.inputBox)
        pub_row = QHBoxLayout()
        self.imgBtn = PushButton(FIF.PHOTO, "添加图片")
        self.imgBtn.clicked.connect(self._on_pick_images)
        pub_row.addWidget(self.imgBtn)
        self.imgHint = CaptionLabel("未选择图片（最多 9 张，自动压缩）")
        pub_row.addWidget(self.imgHint)
        pub_row.addStretch(1)
        self.publishBtn = PrimaryPushButton(FIF.SEND, "发 布")
        self.publishBtn.setFixedWidth(120)
        self.publishBtn.clicked.connect(self._on_publish)
        pub_row.addWidget(self.publishBtn)
        pl.addLayout(pub_row)
        self.expandLayout.addWidget(publish_card)

        self.feedTitle = SubtitleLabel("最新动态")
        self.expandLayout.addWidget(self.feedTitle)
        self.feedLayout = QVBoxLayout()
        self.feedLayout.setSpacing(10)
        self.expandLayout.addLayout(self.feedLayout)
        self.expandLayout.addStretch(1)

    def _on_pick_images(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片（最多 9 张）", "",
            "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not files:
            return
        self._selected_images = files[:MAX_IMAGES]
        self.imgHint.setText(f"已选择 {len(self._selected_images)} 张图片")

    def _on_publish(self):
        token = _token()
        if not token:
            return
        text = self.inputBox.toPlainText().strip()
        if not text and not self._selected_images:
            InfoBar.warning("提示", "说点什么或者添加图片吧", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return
        files = self._selected_images[:]
        self.publishBtn.setEnabled(False)
        self.publishBtn.setText("发布中…")

        def _work():
            rel_paths = []
            for p in files:
                try:
                    img = PILImage.open(p)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    if max(img.size) > 1600:
                        img.thumbnail((1600, 1600))
                    buf = io.BytesIO()
                    img.save(buf, "JPEG", quality=85)
                    ok, d = auth_client.moments_upload(UPDATE_SERVER, token,
                                                       buf.getvalue(), "jpg")
                    if ok:
                        rel_paths.append(d.get("path", ""))
                except Exception as e:
                    module_logger.error(f"朋友圈图片上传失败: {e}")
            return auth_client.moments_publish(UPDATE_SERVER, token, text, rel_paths)

        task = _Task(_work, self)
        task.done.connect(self._on_published)
        task.finished.connect(task.deleteLater)
        task.start()

    def _on_published(self, r):
        self.publishBtn.setEnabled(True)
        self.publishBtn.setText("发 布")
        if r and r[0]:
            self.inputBox.clear()
            self._selected_images = []
            self.imgHint.setText("未选择图片（最多 9 张，自动压缩）")
            InfoBar.success("发布成功", "你的好友可以看到这条动态了", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            self.refresh()
        else:
            err = r[1].get("error", "发布失败") if r and isinstance(r, tuple) else "网络异常"
            InfoBar.error("发布失败", err, parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def refresh(self):
        token = _token()
        if not token:
            return

        def _work():
            return auth_client.moments_feed(UPDATE_SERVER, token)

        task = _Task(_work, self)
        task.done.connect(self._render_feed)
        task.finished.connect(task.deleteLater)
        task.start()

    def _render_feed(self, r):
        while self.feedLayout.count():
            item = self.feedLayout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not (r and r[0]):
            self.feedLayout.addWidget(
                BodyLabel("暂时没有动态——添加好友后即可看到彼此的分享"))
            return
        feed = r[1].get("feed", [])
        if not feed:
            self.feedLayout.addWidget(BodyLabel("还没有动态，发第一条朋友圈吧"))
            return
        for m in feed:
            self.feedLayout.addWidget(MomentCard(m))


class MomentsWidget(QFrame):
    """导航容器。"""

    def __init__(self, name: str, parent=None):
        super().__init__(parent=parent)
        self.setObjectName(name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.momentsInterface = MomentsInterface(self)
        layout.addWidget(self.momentsInterface)