# -*- coding: utf-8 -*-
import base64
import json
import logging
import os
import shutil
import sys
import time

from PySide6.QtCore import Qt, QPoint, QUrl, QTimer, QThread, Signal
from PySide6.QtGui import (QAction, QImage, QKeySequence,QShortcut, QTextCursor,
                           QDesktopServices)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QApplication,
    QFileDialog, QStackedWidget
)

from qfluentwidgets import (
    PushButton, ListWidget, SubtitleLabel, LineEdit,
    BodyLabel, TextEdit, CaptionLabel, InfoBar, InfoBarPosition, MessageBoxBase,
    FluentIcon as FIF, IconWidget, PrimaryPushButton, RoundMenu, TitleLabel,
    TransparentPushButton
)

module_logger = logging.getLogger("flu_widget.show_dig_info_ui")

DATA_DIR = "./Config/MemoInfo"
NOTES_DIR = os.path.join(DATA_DIR, "notes")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
INDEX_FILE = os.path.join(DATA_DIR, "index.json")
ORDER_FILE = os.path.join(DATA_DIR, "order.json")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
AUTO_SAVE_MS = 2000
MAX_IMAGE_WIDTH = 900

DEFAULT_INFO_PATH = "./Config/Default_info.json"

_img_seq = 0

WELCOME_HTML = (
    "备忘录使用说明\n"
    "1. 左侧列表切换备忘录，点「新增备忘录」新建，「删除当前」删除选中条目；\n"
    "2. 在编辑区直接 <Ctrl+V 粘贴截图>，或把图片文件拖进来，也可以点「选择图片…」；\n"
    "3. 点「插入超链接」可在光标处插入可点击跳转的链接；\n"
    "4. 图片自动保存在 /Config/MemoInfo/images/ 下，内容保存在 notes 下，停止编辑 2 秒后自动保存；\n"
    "5. 拖拽左侧列表项可调整顺序。\n"
)


def _new_id():
    global _img_seq
    _img_seq += 1
    return time.strftime("%Y%m%d%H%M%S") + f"{_img_seq:06d}"


def _image_filename(ext):
    global _img_seq
    _img_seq += 1
    return f"img_{time.strftime('%Y%m%d_%H%M%S')}_{_img_seq:04d}{ext}"


def _resize_image_if_needed(src_path, dst_path):
    """如果图片宽度超过MAX_IMAGE_WIDTH，等比例缩小后保存到目标路径"""
    image = QImage(src_path)
    if image.isNull():
        shutil.copy2(src_path, dst_path)
        return dst_path

    if image.width() > MAX_IMAGE_WIDTH:
        ratio = MAX_IMAGE_WIDTH / image.width()
        new_height = int(image.height() * ratio)
        resized = image.scaled(MAX_IMAGE_WIDTH, new_height,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        ext = os.path.splitext(dst_path)[1].lower()
        fmt = "PNG" if ext == ".png" else "JPEG" if ext in (".jpg", ".jpeg") else ext.lstrip(".")
        resized.save(dst_path, fmt)
        module_logger.info(f"图片已缩放: {src_path} ({image.width()}x{image.height()}) -> {dst_path} ({MAX_IMAGE_WIDTH}x{new_height})")
    else:
        shutil.copy2(src_path, dst_path)
    return dst_path


# ── 图片处理线程 ──────────────────────────────────────────
class ImageResizeWorker(QThread):
    """后台批量缩放图片"""
    finished = Signal(list)

    def __init__(self, image_list, parent=None):
        super().__init__(parent)
        self.image_list = image_list

    def run(self):
        results = []
        for src, dst in self.image_list:
            _resize_image_if_needed(src, dst)
            results.append(dst)
        self.finished.emit(results)



# ── 富文本编辑器 ──────────────────────────────────────────
class RichInfoTextEdit(TextEdit):
    """支持 Ctrl+V 粘贴截图、拖入图片、插入超链接的富文本编辑器"""

    def __init__(self, parent=None):
        super().__init__(parent)

    def mouseReleaseEvent(self, event):
        cursor = self.cursorForPosition(event.pos())
        char_fmt = cursor.charFormat()
        if char_fmt.isAnchor():
            url = cursor.selectedText() or char_fmt.anchorHref()
            if url:
                if not url.startswith(('http://', 'https://')):
                    url = 'https://' + url
                QDesktopServices.openUrl(QUrl(url))
                return
        super().mouseReleaseEvent(event)

    def canInsertFromMimeData(self, source):
        if source.hasImage() or source.hasUrls():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasUrls():
            paths = [u.toLocalFile() for u in source.urls() if u.isLocalFile()]
            images = [p for p in paths if p.lower().endswith(IMAGE_EXTS)]
            if images:
                for p in images:
                    target = self._copy_image_file(p)
                    if target:
                        self._insert_image(target)
                return
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                path = self._save_clipboard_image(image)
                if path:
                    self._insert_image(path)
                    return
        super().insertFromMimeData(source)

    def insert_image_file(self, path):
        target = self._copy_image_file(path)
        if target:
            self._insert_image(target)

    def insert_hyperlink(self, url, text=None):
        display = text or url
        self.textCursor().insertHtml(f'<a href="{url}">{display}</a>&nbsp;')

    def _save_clipboard_image(self, image):
        try:
            os.makedirs(IMAGES_DIR, exist_ok=True)
            path = os.path.join(IMAGES_DIR, _image_filename(".png"))
            if image.save(path, "PNG"):
                module_logger.info(f"保存粘贴图片: {path}")
                return path
        except Exception as e:
            module_logger.error(f"保存粘贴图片失败: {e}")
        return None

    def _copy_image_file(self, src):
        try:
            os.makedirs(IMAGES_DIR, exist_ok=True)
            ext = os.path.splitext(src)[1].lower() or ".png"
            path = os.path.join(IMAGES_DIR, _image_filename(ext))
            _resize_image_if_needed(src, path)
            module_logger.info(f"导入图片文件: {src} -> {path}")
            return path
        except Exception as e:
            module_logger.error(f"导入图片文件失败: {e}")
            return None

    def _insert_image(self, path):
        url = QUrl.fromLocalFile(path).toString()
        width = ""
        image = QImage(path)
        max_w = max(200, self.viewport().width() - 24)
        if not image.isNull() and image.width() > max_w:
            width = f' width="{max_w}"'
        self.textCursor().insertHtml(f'<img src="{url}"{width} />')

    def createMimeDataFromSelection(self):
        paths = self._selected_image_paths()
        if len(paths) == 1 and not self._selection_has_text():
            img = QImage(paths[0])
            if not img.isNull():
                from PySide6.QtCore import QMimeData
                mime = QMimeData()
                mime.setImageData(img)
                return mime

        mime = super().createMimeDataFromSelection()
        if mime is None or not mime.hasHtml() or not paths:
            return mime

        html = mime.html()
        for p in paths:
            data_uri = self._image_data_uri(p)
            if data_uri:
                html = html.replace(QUrl.fromLocalFile(p).toString(), data_uri)
        mime.setHtml(html)
        return mime

    def _selection_has_text(self):
        raw = self.textCursor().selectedText()
        return bool(raw.replace("\u2029", "").replace("\ufffc", "").strip())

    def _selected_image_paths(self):
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return []
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        paths = []
        block = self.document().findBlock(start)
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                pos = frag.position()
                if pos + frag.length() > start and pos < end \
                        and frag.charFormat().isImageFormat():
                    name = frag.charFormat().toImageFormat().name()
                    path = QUrl(name).toLocalFile() or name
                    if os.path.isfile(path) and path not in paths:
                        paths.append(path)
                it += 1
            if block.position() + block.length() >= end:
                break
            block = block.next()
        return paths

    @staticmethod
    def _image_data_uri(path):
        try:
            ext = os.path.splitext(path)[1].lower().lstrip(".")
            mtype = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "bmp": "image/bmp", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/png")
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f"data:{mtype};base64,{b64}"
        except Exception:
            return None


# ── 可拖拽列表 ────────────────────────────────────────────
class DraggableListWidget(ListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(ListWidget.DragDropMode.InternalMove)
        self.setSelectionMode(ListWidget.SelectionMode.SingleSelection)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setMaximumWidth(200)
        self.setMinimumWidth(200)
        self.drag_start_position = QPoint()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_position = event.pos()
            item = self.itemAt(self.drag_start_position)
            if item:
                self.setCurrentItem(item)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.pos() - self.drag_start_position).manhattanLength() < QApplication.startDragDistance():
            return
        current_item = self.currentItem()
        if not current_item:
            return
        from PySide6.QtGui import QDrag
        drag = QDrag(self)
        mime_data = self.model().mimeData([self.currentIndex()])
        drag.setMimeData(mime_data)
        drop_action = drag.exec(Qt.DropAction.MoveAction)
        if drop_action == Qt.DropAction.MoveAction:
            self._save_order()

    def dropEvent(self, event):
        super().dropEvent(event)
        self._save_order()

    def _save_order(self):
        parent_widget = self.parent()
        while parent_widget and not isinstance(parent_widget, ShowDigInfoWidget):
            parent_widget = parent_widget.parent()
        if parent_widget:
            new_order = [self.item(i).text() for i in range(self.count())]
            parent_widget._save_order(new_order)


# ── 主界面 ────────────────────────────────────────────────
class ShowDigInfoWidget(QFrame):

    def __init__(self, name: str, parent=None):
        super().__init__(parent=parent)
        self.notes = []
        self.current_id = None
        self._loading = False

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(AUTO_SAVE_MS)
        self._save_timer.timeout.connect(lambda: self._save_current(manual=False))

        self.setObjectName(name)
        self._build_ui()
        self._load_index()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 4, 8, 8)
        main_layout.setSpacing(8)

        # ── 菜单栏（文件 / 编辑 / 插入 / 工具，扁平按钮 + Fluent 弹出菜单）──
        menus_def = (
            ("文件", [("新建备忘录", self._on_new, "Ctrl+N"),
                      ("立即保存", lambda: self._save_current(manual=True), "Ctrl+S"),
                      (None, None, None),
                      ("恢复默认", self._on_reset, None)]),
            ("编辑", [("删除当前备忘录", self._on_delete, None),
                      ("清空当前内容", self._on_clear_current, None)]),
            ("插入", [("选择图片…", self._on_choose_image, None),
                      ("插入超链接", self._on_insert_link, None),
                      ("插入时间戳", self._on_insert_timestamp, None)]),
            ("工具", [("打开图片目录", self._on_open_images, None)]),
        )
        menubar_row = QHBoxLayout()
        menubar_row.setSpacing(2)
        for title, items in menus_def:
            btn = TransparentPushButton(title)
            btn.setFixedHeight(30)

            def _open(_=False, t=title, its=items, b=btn):
                menu = RoundMenu(t, self)
                for text, slot, shortcut in its:
                    if text is None:
                        menu.addSeparator()
                        continue
                    action = QAction(text, self)
                    if slot is not None:
                        action.triggered.connect(slot)
                    if shortcut:
                        action.setShortcut(QKeySequence(shortcut))
                    menu.addAction(action)
                menu.exec(b.mapToGlobal(b.rect().bottomLeft()))

            btn.clicked.connect(_open)
            menubar_row.addWidget(btn)
        menubar_row.addStretch(1)
        main_layout.addLayout(menubar_row)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(lambda: self._save_current(manual=True))
        QShortcut(QKeySequence.StandardKey.New, self).activated.connect(self._on_new)
        # ── 内容区（左侧列表 + 右侧编辑器）──
        content_layout = QHBoxLayout()
        content_layout.setSpacing(8)

        self.list_widget = DraggableListWidget(self)
        self.list_widget.setMinimumWidth(180)
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        content_layout.addWidget(self.list_widget, 0)

        # ── 右侧：QStackedWidget（过渡页 / 编辑页）──
        self.right_stack = QStackedWidget()

        # 过渡页：未选择条目时显示
        placeholder = QFrame()
        ph_layout = QVBoxLayout(placeholder)
        ph_layout.setContentsMargins(12, 12, 12, 12)
        ph_layout.setSpacing(14)
        ph_layout.addStretch(2)
        ph_icon = IconWidget(FIF.BOOK_SHELF, placeholder)
        ph_icon.setFixedSize(88, 88)
        ph_layout.addWidget(ph_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        ph_layout.addWidget(TitleLabel("备忘录"),
                            0, Qt.AlignmentFlag.AlignHCenter)
        ph_layout.addWidget(BodyLabel("请选择查看或添加备忘录"),
                            0, Qt.AlignmentFlag.AlignHCenter)
        self.ph_new_btn = PrimaryPushButton(FIF.ADD, "新建备忘录")
        self.ph_new_btn.setFixedWidth(180)
        self.ph_new_btn.clicked.connect(self._on_new)
        ph_layout.addWidget(self.ph_new_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        ph_layout.addStretch(3)
        self.right_stack.addWidget(placeholder)  # index 0 = 过渡页

        # 编辑页：原有编辑区
        editor_page = QFrame()
        right_panel = QVBoxLayout(editor_page)
        right_panel.setContentsMargins(0, 0, 0, 0)
        right_panel.setSpacing(4)

        self.title_edit = LineEdit()
        self.title_edit.setPlaceholderText("备忘录标题")
        self.title_edit.textEdited.connect(self._on_title_edited)
        right_panel.addWidget(self.title_edit)

        self.editor = RichInfoTextEdit()
        self.editor.setPlaceholderText(
            "在这里编辑备忘录，支持 Ctrl+V 粘贴截图、拖入图片、插入超链接…")
        self.editor.textChanged.connect(self._on_content_changed)
        right_panel.addWidget(self.editor, 1)

        self.status_label = CaptionLabel("自动保存已开启（停止编辑 2 秒后保存）")
        right_panel.addWidget(self.status_label)
        self.right_stack.addWidget(editor_page)  # index 1 = 编辑页

        content_layout.addWidget(self.right_stack, 1)
        main_layout.addLayout(content_layout, 1)

    # ── 数据读写 ──────────────────────────────────────────
    def _load_index(self):
        os.makedirs(NOTES_DIR, exist_ok=True)
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                self.notes = json.load(f)
        except FileNotFoundError:
            self.notes = []
        except Exception as e:
            module_logger.error(f"读取索引失败: {e}")
            self.notes = []

        # 应用排序配置
        self._apply_order()

        self._loading = True
        self.list_widget.clear()
        for n in self.notes:
            self.list_widget.addItem(n.get("title", "(无标题)"))
        self._loading = False

        if not self.notes:
            # 首次启动且索引为空：显示过渡页，由用户新建
            self._show_placeholder()
        else:
            self.list_widget.setCurrentRow(0)
            self._load_note_to_editor(0)

    def _apply_order(self):
        """根据排序配置重新排列notes列表"""
        try:
            with open(ORDER_FILE, "r", encoding="utf-8") as f:
                order_list = json.load(f)
        except (FileNotFoundError, Exception):
            order_list = []

        if not order_list:
            self.notes.sort(key=lambda n: n.get("updated", ""), reverse=True)
            return

        ordered = []
        remaining = self.notes.copy()
        for title in order_list:
            for i, note in enumerate(remaining):
                if note.get("title") == title:
                    ordered.append(note)
                    remaining.pop(i)
                    break
        ordered.extend(remaining)
        self.notes = ordered

    def _write_index(self):
        try:
            with open(INDEX_FILE, "w", encoding="utf-8") as f:
                json.dump(self.notes, f, ensure_ascii=False, indent=2)
        except Exception as e:
            module_logger.error(f"写入索引失败: {e}")

    def _read_note_html(self, note_id):
        path = os.path.join(NOTES_DIR, f"{note_id}.html")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            module_logger.error(f"读取备忘内容失败: {e}")
            return ""

    def _save_current(self, manual=False):
        note = self._current_note()
        if note is None or self._loading:
            return
        try:
            os.makedirs(NOTES_DIR, exist_ok=True)
            path = os.path.join(NOTES_DIR, f"{note['id']}.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.editor.toHtml())
            note["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._write_index()
            self.status_label.setText(f"已保存 {time.strftime('%H:%M:%S')}")
            if manual:
                self._show_info(f"「{note['title']}」已保存")
        except Exception as e:
            module_logger.error(f"保存失败: {e}")
            self._show_error(f"保存失败: {e}")

    def _create_note(self, title, content_html=""):
        self._save_current(manual=False)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        note = {"id": _new_id(), "title": title, "is_default": False, "created": now, "updated": now}
        self.notes.insert(0, note)
        self._loading = True
        self.list_widget.insertItem(0, title)
        self.list_widget.setCurrentRow(0)
        self._loading = False
        self._load_note_to_editor(0)
        if content_html:
            self._loading = True
            self.editor.setHtml(content_html)
            self._loading = False
        self._save_current(manual=False)
        return note

    def _load_note_to_editor(self, row):
        if row < 0 or row >= len(self.notes):
            self._show_placeholder()
            return
        note = self.notes[row]
        self._loading = True
        self.current_id = note["id"]
        self.title_edit.setText(note.get("title", "(无标题)"))
        self.editor.setHtml(self._read_note_html(note["id"]))
        self.editor.moveCursor(QTextCursor.MoveOperation.Start)
        self._loading = False
        self._show_editor()

    def _show_placeholder(self):
        """未选择条目/列表为空时显示过渡页，并清掉残留编辑状态。"""
        self.current_id = None
        self._save_timer.stop()
        self._loading = True
        self.title_edit.clear()
        self.editor.clear()
        self._loading = False
        self.right_stack.setCurrentIndex(0)

    def _show_editor(self):
        self.right_stack.setCurrentIndex(1)

    def _current_note(self):
        return next((n for n in self.notes if n["id"] == self.current_id), None)

    def _row_of_current(self):
        for i, n in enumerate(self.notes):
            if n["id"] == self.current_id:
                return i
        return -1

    def _save_order(self, order_list):
        # 根据新的顺序重新排列notes
        ordered = []
        remaining = self.notes.copy()
        for title in order_list:
            for i, note in enumerate(remaining):
                if note["title"] == title:
                    ordered.append(note)
                    remaining.pop(i)
                    break
        ordered.extend(remaining)
        self.notes = ordered

        # 保存排序配置
        try:
            with open(ORDER_FILE, "w", encoding="utf-8") as f:
                json.dump(order_list, f, ensure_ascii=False, indent=2)
        except Exception as e:
            module_logger.error(f"保存排序配置失败: {e}")

        self._write_index()
        module_logger.info(f"顺序已更新: {order_list}")

    # ── 交互事件 ──────────────────────────────────────────
    def _on_row_changed(self, row):
        if self._loading:
            return
        if row < 0:
            # 列表清空/无选中：显示过渡页
            self._show_placeholder()
            return
        self._save_current(manual=False)
        self._load_note_to_editor(row)

    def _on_new(self):
        self._create_note(f"备忘录 {time.strftime('%m-%d %H:%M')}")
        self.editor.setFocus()

    def _on_clear_current(self):
        if self._current_note() is None:
            return
        self._loading = True
        self.editor.clear()
        self._loading = False
        self._save_current(manual=False)

    def _on_delete(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.notes):
            self._show_warning("请先选择一条记录")
            return
        note = self.notes[row]
        if note.get("is_default"):
            self._show_warning("默认资料不可删除")
            return

        self._save_timer.stop()

        # 删除关联的图片文件
        note_path = os.path.join(NOTES_DIR, f"{note['id']}.html")
        if os.path.exists(note_path):
            try:
                with open(note_path, "r", encoding="utf-8") as f:
                    content = f.read()
                # module_logger.info(f"笔记HTML内容: {content[:1000]}")

                # 查找所有src属性
                import re
                from urllib.parse import unquote

                # 匹配所有src属性
                src_pattern = r'src="([^"]+)"'
                src_matches = re.findall(src_pattern, content)
                module_logger.info(f"找到的src: {src_matches}")

                for src in src_matches:
                    # 先解码URL
                    src_decoded = unquote(src)
                    # module_logger.info(f"原始src: {src}, 解码后: {src_decoded}")

                    # 提取本地路径
                    if src_decoded.startswith("file:///"):
                        # file:///C:/path -> C:/path
                        local_path = src_decoded[8:]
                    elif src_decoded.startswith("file://"):
                        # file://host/path -> /path 或 \\host\path
                        local_path = src_decoded[7:]
                    elif src_decoded.startswith("file:"):
                        # file:./path -> ./path (相对路径)
                        local_path = src_decoded[5:]
                    else:
                        local_path = src_decoded

                    module_logger.info(f"提取的本地路径: {local_path}")

                    # 检查路径是否存在且在images目录下
                    if os.path.exists(local_path):
                        local_norm = os.path.normpath(local_path).lower()
                        images_norm = os.path.normpath(IMAGES_DIR).lower()
                        module_logger.info(f"路径比较: {local_norm} vs {images_norm}")
                        if images_norm in local_norm:
                            try:
                                os.remove(local_path)
                                module_logger.info(f"成功删除图片: {local_path}")
                            except OSError as e:
                                module_logger.error(f"删除图片失败: {e}")
                    else:
                        module_logger.info(f"文件不存在: {local_path}")
            except Exception as e:
                module_logger.error(f"解析笔记内容失败: {e}")
                import traceback
                module_logger.error(traceback.format_exc())

        self.notes.pop(row)
        try:
            os.remove(note_path)
        except OSError as e:
            module_logger.error(f"删除文件失败: {e}")
        self._write_index()
        self.current_id = None

        self._loading = True
        self.list_widget.takeItem(row)
        self._loading = False

        if not self.notes:
            # 不再自动重建条目：显示过渡页，由用户决定是否新建
            self._show_placeholder()
        else:
            row = min(row, len(self.notes) - 1)
            self.list_widget.setCurrentRow(row)
            self._load_note_to_editor(row)
        self._show_info(f"已删除「{note['title']}」")

    def _on_title_edited(self, text):
        note = self._current_note()
        if note is None:
            return
        note["title"] = text.strip() or "(无标题)"
        row = self._row_of_current()
        if row >= 0:
            self.list_widget.item(row).setText(note["title"])
        self._save_timer.start()

    def _on_content_changed(self):
        if not self._loading:
            self._save_timer.start()

    def _on_insert_timestamp(self):
        if self._current_note() is None:
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        self.editor.textCursor().insertHtml(
            f'<span style="color:#2a6fdb;"><b>[{ts}]</b></span> ')
        self.editor.setFocus()

    def _on_choose_image(self):
        if self._current_note() is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if path:
            self.editor.insert_image_file(path)

    def _on_insert_link(self):
        if self._current_note() is None:
            return
        dlg = _LinkDialog(self)
        if dlg.exec():
            url = dlg.url_edit.text().strip()
            text = dlg.text_edit.text().strip()
            if url:
                if not url.startswith(('http://', 'https://')):
                    url = 'https://' + url
                self.editor.insert_hyperlink(url, text or url)
                self.editor.setFocus()

    def _on_open_images(self):
        try:
            os.makedirs(IMAGES_DIR, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(IMAGES_DIR)
            else:
                self._show_info(f"图片目录: {IMAGES_DIR}")
        except Exception as e:
            module_logger.error(f"打开图片目录失败: {e}")
            self._show_warning(f"打开失败: {e}")

    def _on_reset(self):
        # 清除所有现有数据
        self._loading = True
        self.list_widget.clear()
        self._loading = False
        self.notes = []
        self.current_id = None

        # 删除所有notes文件
        if os.path.exists(NOTES_DIR):
            shutil.rmtree(NOTES_DIR, ignore_errors=True)
            os.makedirs(NOTES_DIR, exist_ok=True)

        # 删除所有图片文件
        if os.path.exists(IMAGES_DIR):
            shutil.rmtree(IMAGES_DIR, ignore_errors=True)
            os.makedirs(IMAGES_DIR, exist_ok=True)

        # 删除索引和排序配置
        if os.path.exists(INDEX_FILE):
            os.remove(INDEX_FILE)
        if os.path.exists(ORDER_FILE):
            os.remove(ORDER_FILE)

        # 从Default_info.json重新加载默认资料
        if os.path.exists(DEFAULT_INFO_PATH):
            try:
                with open(DEFAULT_INFO_PATH, 'r', encoding='utf-8') as f:
                    default_items = json.load(f)

                # 直接从默认配置创建笔记
                for name, value in default_items:
                    note_id = _new_id()
                    now = time.strftime("%Y-%m-%d %H:%M:%S")

                    if isinstance(value, dict) and value.get('type') == 'url':
                        url = value['url']
                        html = f'<p><a href="{url}">{url}</a></p>'
                    elif isinstance(value, str) and value.lower().endswith(IMAGE_EXTS):
                        if os.path.exists(value):
                            target = os.path.join(IMAGES_DIR, f"{note_id}{os.path.splitext(value)[1]}")
                            try:
                                _resize_image_if_needed(value, target)
                                file_url = QUrl.fromLocalFile(target).toString()
                                html = f'<p><img src="{file_url}" /></p>'
                            except Exception as e:
                                module_logger.error(f"复制图片失败: {e}")
                                html = f'<p>图片路径: {value}</p>'
                        else:
                            html = f'<p>图片路径: {value}</p>'
                    else:
                        text = value if isinstance(value, str) else str(value)
                        html = '<p>' + text.replace('\n', '<br>') + '</p>'

                    note_path = os.path.join(NOTES_DIR, f"{note_id}.html")
                    with open(note_path, "w", encoding="utf-8") as f:
                        f.write(html)

                    self.notes.append({
                        "id": note_id,
                        "title": name,
                        "is_default": True,
                        "created": now,
                        "updated": now
                    })

                # 保存索引
                self._write_index()

                # 保存排序配置
                order_names = [note["title"] for note in self.notes]
                with open(ORDER_FILE, "w", encoding="utf-8") as f:
                    json.dump(order_names, f, ensure_ascii=False, indent=2)

                module_logger.info(f"恢复默认完成，共{len(self.notes)}项")

            except Exception as e:
                module_logger.error(f"恢复默认失败: {e}")

        # 刷新界面
        self._loading = True
        self.list_widget.clear()
        for n in self.notes:
            self.list_widget.addItem(n.get("title", "(无标题)"))
        self._loading = False

        if not self.notes:
            # 恢复默认后为空（Default_info.json 为空列表）：显示过渡页
            self._show_placeholder()
        else:
            self.list_widget.setCurrentRow(0)
            self._load_note_to_editor(0)

        self._show_info("已恢复默认")

    # ── 提示 ──────────────────────────────────────────────
    def _show_info(self, msg):
        InfoBar.success(
            title=msg, content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True, position=InfoBarPosition.TOP,
            duration=3000, parent=self
        )

    def _show_warning(self, msg):
        InfoBar.warning(
            title=msg, content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True, position=InfoBarPosition.TOP,
            duration=5000, parent=self
        )

    def _show_error(self, msg):
        InfoBar.error(
            title=msg, content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True, position=InfoBarPosition.TOP,
            duration=8000, parent=self
        )


class _LinkDialog(MessageBoxBase):
    """插入超链接对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("插入超链接")
        self.url_edit = LineEdit()
        self.url_edit.setPlaceholderText("输入网址，如: www.example.com")
        self.text_edit = LineEdit()
        self.text_edit.setPlaceholderText("显示文字（可选）")

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(BodyLabel("链接地址:"))
        self.viewLayout.addWidget(self.url_edit)
        self.viewLayout.addWidget(BodyLabel("显示文字:"))
        self.viewLayout.addWidget(self.text_edit)

        self.widget.setMinimumWidth(500)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")

    def accept(self):
        url = self.url_edit.text().strip()
        if not url:
            self._show_warning("请输入网址")
            return
        super().accept()

    def _show_warning(self, msg):
        InfoBar.warning(
            title=msg, content="",
            orient=Qt.Orientation.Horizontal,
            isClosable=True, position=InfoBarPosition.TOP,
            duration=3000, parent=self
        )


if __name__ == "__main__":
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    w = ShowDigInfoWidget('show_dig_info')
    w.show()
    app.exec()
