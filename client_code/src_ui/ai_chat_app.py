import json
import logging
import os
import sys
from datetime import datetime

import markdown

module_logger = logging.getLogger("flu_widget.ai_chat_app")
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QTextCursor, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QFrame, QSizePolicy, QFormLayout, QListWidgetItem, QSplitter, QMenu
)
from openai import OpenAI
from qfluentwidgets import TextEdit, LineEdit, BodyLabel, PushButton, ListWidget, ScrollArea, MessageBoxBase, \
    SubtitleLabel, MessageBox, isDarkTheme, qconfig, Theme, EditableComboBox, StateToolTip

from src_ui.information_history import create_Warning_info, create_success_info

# 历史记录保存目录
HISTORY_DIR = "./chat_histories"
os.makedirs(HISTORY_DIR, exist_ok=True)


# ─────────────────────────────────────────────
#  后台线程
# ─────────────────────────────────────────────
class AIWorker(QThread):
    response_chunk = Signal(str)
    response_done  = Signal()
    error_occurred = Signal(str)

    def __init__(self, client, model, messages):
        super().__init__()
        self.client = client
        self.model = model
        self.messages = messages
        self._stop = False
        module_logger.info("🔧 AIWorker 初始化 - 使用模型：" + self.model)

    def stop(self):
        """停止生成（保留已生成部分）。"""
        self._stop = True

    def run(self):
        try:
            print(f"🚀 发送请求 - 模型：{self.model}")
            print(f"📡 Base URL: {str(self.client.base_url)}")
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                stream=True,
            )
            print(f"✅ 请求成功，开始接收流式响应...")
            for chunk in stream:
                if self._stop:
                    break
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        self.response_chunk.emit(delta)
            self.response_done.emit()
            print(f"✔️ 响应完成")
        except Exception as e:
            if self._stop:
                self.response_done.emit()
                return
            error_msg = f"❌ API 错误：{str(e)}"
            print(error_msg)
            self.error_occurred.emit(error_msg)



class MessageBubble(QFrame):
    # 折叠阈值（像素），超过此高度时显示折叠按钮
    COLLAPSE_THRESHOLD = 200

    def __init__(self, text: str, is_user: bool, parent=None, batch_mode: bool = False):
        super().__init__(parent)
        self.is_user = is_user
        self.raw_text = text
        self._is_collapsed = True   # 默认折叠
        self._full_height = 0       # 完整内容高度
        self._needs_collapse = False  # 是否需要折叠功能
        self._setup_ui(text, batch_mode)
        # 右键复制全文
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_copy_menu)

    def _show_copy_menu(self, pos):
        if not self.raw_text:
            return
        menu = QMenu(self)
        act_copy = menu.addAction("复制全文")
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen == act_copy:
            QApplication.clipboard().setText(self.raw_text)
            create_success_info(self, "已复制", "内容已复制到剪贴板")

    def _setup_ui(self, text: str, batch_mode: bool = False):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 4, 10, 4)

        # 内容容器（气泡 + 折叠按钮）
        content_widget = QWidget()
        content_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        content_v = QVBoxLayout(content_widget)
        content_v.setContentsMargins(0, 0, 0, 0)
        content_v.setSpacing(0)

        self.label = TextEdit()
        self.label.setReadOnly(True)

        if not self.is_user and text.strip():
            html_content = self._convert_to_html(text)
            self.label.setHtml(html_content)
        else:
            self.label.setPlainText(text)

        self.label.setFrameShape(QFrame.Shape.NoFrame)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        if not batch_mode:
            self.label.document().contentsChanged.connect(self._adjust_height)

        # 折叠按钮
        self.toggle_btn = PushButton()
        self.toggle_btn.setFixedHeight(26)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._toggle_collapse)
        self.toggle_btn.hide()  # 默认隐藏，内容超过阈值才显示

        content_v.addWidget(self.label)
        content_v.addWidget(self.toggle_btn)

        self._apply_theme_style()

        if self.is_user:
            outer.addStretch()
            outer.addWidget(content_widget, 0)
        else:
            outer.addWidget(content_widget, 0)
            outer.addStretch()

        if not batch_mode:
            QTimer.singleShot(10, self._adjust_height)

    def _get_inline_styles(self) -> dict:
        """根据当前主题返回内联样式字典"""
        dark = isDarkTheme()
        if dark:
            return {
                "body_color": "#e0e0e0",
                # 代码块
                "code_bg": "#2b2b2b",
                "code_color": "#ff9d00",
                "pre_bg": "#1e1e1e",
                "pre_border": "#404040",
                "pre_color": "#e84855",  # 代码块正文色
                "pre_header_bg": "#2d2d2d",  # 顶部语言标签背景
                "pre_header_border": "#404040",
                # 引用块
                "blockquote_border": "#4a90e2",
                "blockquote_bg": "#1e2a38",
                "blockquote_color": "#a0b4c8",
                # 表格
                "table_border": "#404040",
                "th_bg": "#2d2d2d",
                "th_color": "#e0e0e0",
                "tr_even_bg": "#252525",
                # 其他
                "link_color": "#4a9eff",
                "hr_color": "#404040",
                "h_color": "#ffffff",
                "h2_color": "#c9d1d9",
                "h3_color": "#8b949e",
                "h_border": "#404040",
                "strong_color": "#ffffff",
            }
        else:
            return {
                "body_color": "#24292f",
                # 代码块
                "code_bg": "#f0f0f0",
                "code_color": "#d63384",
                "pre_bg": "#f6f8fa",
                "pre_border": "#d0d7de",
                "pre_color": "#e84855",
                "pre_header_bg": "#eaeef2",
                "pre_header_border": "#d0d7de",
                # 引用块
                "blockquote_border": "#0969da",
                "blockquote_bg": "#ddf4ff",
                "blockquote_color": "#57606a",
                # 表格
                "table_border": "#d0d7de",
                "th_bg": "#f6f8fa",
                "th_color": "#24292f",
                "tr_even_bg": "#f6f8fa",
                # 其他
                "link_color": "#0969da",
                "hr_color": "#d0d7de",
                "h_color": "#1f2328",
                "h2_color": "#1f2328",
                "h3_color": "#57606a",
                "h_border": "#d0d7de",
                "strong_color": "#1f2328",
            }

    def _convert_to_html(self, text: str) -> str:
        """将 Markdown 转换为带内联样式的 HTML"""
        if not text:
            return ""

        try:
            html_body = markdown.markdown(
                text,
                extensions=['tables', 'fenced_code', 'nl2br', 'attr_list'],
                output_format='html5'
            )
        except Exception as e:
            module_logger.error(f"Markdown 转换失败: {e}")
            return f"<p>{text}</p>"

        s = self._get_inline_styles()
        import re

        def style_tags(html: str) -> str:

            # ─── 1. 代码块 <pre><code> 优先处理 ───
            # 提取语言类型（如 class="language-python"）
            def replace_pre_code(m):
                lang_match = re.search(r'class="language-(\w+)"', m.group(0))
                lang = lang_match.group(1).upper() if lang_match else "CODE"

                # 顶部语言标签 + 代码内容
                return (
                    f'<div style="border-radius:8px;overflow:hidden;margin:12px 0;'
                    f'border:1px solid {s["pre_border"]};box-shadow:0 2px 6px rgba(0,0,0,0.12);">'

                    # 顶部标签栏
                    f'<div style="background-color:{s["pre_header_bg"]};'
                    f'border-bottom:1px solid {s["pre_header_border"]};'
                    f'padding:6px 14px;font-size:12px;font-weight:bold;'
                    f'color:{s["h3_color"]};font-family:Consolas,monospace;'
                    f'letter-spacing:0.5px;">'
                    f'⌨ {lang}</div>'

                    # 代码区域
                    f'<pre style="background-color:{s["pre_bg"]};margin:0;padding:14px 16px;'
                    f'white-space:pre-wrap;word-wrap:break-word;overflow-x:auto;">'
                    f'<code style="background:none;color:{s["pre_color"]};padding:0;'
                    f'font-family:Consolas,\'Courier New\',monospace;'
                    f'font-size:13.5px;line-height:1.7;">'
                )

            html = re.sub(
                r'<pre>\s*<code[^>]*>',
                replace_pre_code,
                html
            )
            # 闭合 </code></pre> → 补上外层 </div>
            html = html.replace('</code></pre>', '</code></pre></div>')

            # ─── 2. 内联 code（未被步骤1处理的）───
            html = re.sub(
                r'<code>',
                f'<code style="background-color:{s["code_bg"]};color:{s["code_color"]};'
                f'padding:2px 6px;border-radius:4px;'
                f'font-family:Consolas,\'Courier New\',monospace;'
                f'font-size:13px;font-weight:500;">',
                html
            )

            # ─── 3. 标题 ───
            html = re.sub(
                r'<h1>',
                f'<h1 style="color:{s["h_color"]};font-size:22px;font-weight:700;'
                f'margin:20px 0 12px 0;padding-bottom:8px;'
                f'border-bottom:2px solid {s["h_border"]};">',
                html
            )
            html = re.sub(
                r'<h2>',
                f'<h2 style="color:{s["h2_color"]};font-size:18px;font-weight:600;'
                f'margin:16px 0 10px 0;padding-bottom:6px;'
                f'border-bottom:1px solid {s["h_border"]};">',
                html
            )
            html = re.sub(
                r'<h3>',
                f'<h3 style="color:{s["h3_color"]};font-size:15px;font-weight:600;'
                f'margin:12px 0 6px 0;">',
                html
            )

            # ─── 4. 段落 ───
            html = re.sub(
                r'<p>',
                f'<p style="margin:8px 0;line-height:1.8;color:{s["body_color"]};">',
                html
            )

            # ─── 5. 引用块 ───
            html = re.sub(
                r'<blockquote>',
                f'<blockquote style="border-left:4px solid {s["blockquote_border"]};'
                f'margin:12px 0;padding:8px 14px;'
                f'color:{s["blockquote_color"]};'
                f'background-color:{s["blockquote_bg"]};'
                f'border-radius:0 6px 6px 0;">',
                html
            )

            # ─── 6. 表格 ───
            html = re.sub(
                r'<table>',
                f'<table style="border-collapse:collapse;width:100%;'
                f'margin:12px 0;font-size:14px;">',
                html
            )
            html = re.sub(
                r'<th>',
                f'<th style="border:1px solid {s["table_border"]};padding:8px 12px;'
                f'background-color:{s["th_bg"]};color:{s["th_color"]};'
                f'font-weight:600;text-align:left;white-space:nowrap;">',
                html
            )
            html = re.sub(
                r'<td>',
                f'<td style="border:1px solid {s["table_border"]};'
                f'padding:7px 12px;color:{s["body_color"]};">',
                html
            )

            # ─── 7. 链接 ───
            html = re.sub(
                r'<a ',
                f'<a style="color:{s["link_color"]};text-decoration:underline;" ',
                html
            )

            # ─── 8. 分割线 ───
            html = re.sub(
                r'<hr\s*/?>',
                f'<hr style="border:none;border-top:1px solid {s["hr_color"]};'
                f'margin:16px 0;" />',
                html
            )

            # ─── 9. 列表 ───
            html = re.sub(r'<ul>',
                          f'<ul style="margin:8px 0;padding-left:24px;color:{s["body_color"]};">',
                          html)
            html = re.sub(r'<ol>',
                          f'<ol style="margin:8px 0;padding-left:24px;color:{s["body_color"]};">',
                          html)
            html = re.sub(r'<li>',
                          f'<li style="margin:4px 0;line-height:1.7;color:{s["body_color"]};">',
                          html)

            # ─── 10. 加粗 ───
            html = re.sub(r'<strong>',
                          f'<strong style="color:{s["strong_color"]};font-weight:700;">',
                          html)

            return html

        styled_body = style_tags(html_body)

        full_html = (
            f'<div style="font-family:\'Microsoft YaHei\',\'Segoe UI\',Arial,sans-serif;'
            f'line-height:1.8;font-size:14px;color:{s["body_color"]};">'
            f'{styled_body}'
            f'</div>'
        )
        return full_html


    def _apply_theme_style(self):
        """只设置气泡背景/边框"""
        dark = isDarkTheme()
        if dark:
            btn_style = """
                PushButton {
                    background-color: #3a3a3a;
                    color: #a0a0a0;
                }
                PushButton:hover { background-color: #444444; color: #c0c0c0; }
            """
            if self.is_user:
                self.label.setStyleSheet("""
                    QTextEdit {
                        background-color: #1a6fb8; color: #ffffff;
                        border-radius: 12px; padding: 10px 14px;
                        border: 1px solid #2a7fc8;
                    }
                """)
            else:
                self.label.setStyleSheet("""
                    QTextEdit {
                        background-color: #2d2d2d; color: #e0e0e0;
                        border-radius: 12px; padding: 12px 16px;
                        border: 1px solid #3d3d3d;
                    }
                """)
        else:
            btn_style = """
                PushButton {
                    background-color: #ebebeb;
                    color: #606060;
                }
                PushButton:hover { background-color: #e0e0e0; color: #404040; }
            """
            if self.is_user:
                self.label.setStyleSheet("""
                    QTextEdit {
                        background-color: #0078d4; color: white;
                        border-radius: 12px; padding: 10px 14px;
                        border: 1px solid #005a9e;
                    }
                """)
            else:
                self.label.setStyleSheet("""
                    QTextEdit {
                        background-color: #f0f0f0; color: #1a1a1a;
                        border-radius: 12px; padding: 12px 16px;
                        border: 1px solid #d0d0d0;
                    }
                """)

        # 折叠按钮折叠状态下圆角要跟气泡底部对齐
        if hasattr(self, 'toggle_btn'):
            self.toggle_btn.setStyleSheet(btn_style)

    def _adjust_height(self):
        """动态调整气泡高度，处理折叠逻辑"""
        self.label.document().adjustSize()
        doc_height = int(self.label.document().size().height())
        self._full_height = doc_height + 32  # 完整高度缓存

        if self._full_height > self.COLLAPSE_THRESHOLD:
            # 内容超过阈值，需要折叠功能
            self._needs_collapse = True
            self.toggle_btn.show()
            self._update_toggle_btn_text()

            if self._is_collapsed:
                # 折叠状态：限制显示高度
                self.label.setFixedHeight(self.COLLAPSE_THRESHOLD)
                # 折叠时气泡底部圆角去掉（按钮接上）
                self._set_label_bottom_radius(False)
            else:
                # 展开状态：完整高度
                self.label.setFixedHeight(self._full_height)
                self._set_label_bottom_radius(True)
        else:
            # 内容未超过阈值，无需折叠
            self._needs_collapse = False
            self.toggle_btn.hide()
            self.label.setFixedHeight(max(self._full_height, 44))
            self._set_label_bottom_radius(True)

    def _set_label_bottom_radius(self, has_radius: bool):
        """控制气泡底部圆角，折叠时去掉底部圆角与按钮衔接"""
        dark = isDarkTheme()
        if has_radius:
            radius = "border-radius: 12px;"
        else:
            radius = "border-radius: 12px 12px 0 0;"

        if dark:
            if self.is_user:
                self.label.setStyleSheet(f"""
                    QTextEdit {{ background-color: #1a6fb8; color: #ffffff;
                        {radius} padding: 10px 14px; border: 1px solid #2a7fc8; }}
                """)
            else:
                self.label.setStyleSheet(f"""
                    QTextEdit {{ background-color: #2d2d2d; color: #e0e0e0;
                        {radius} padding: 12px 16px; border: 1px solid #3d3d3d; }}
                """)
        else:
            if self.is_user:
                self.label.setStyleSheet(f"""
                    QTextEdit {{ background-color: #0078d4; color: white;
                        {radius} padding: 10px 14px; border: 1px solid #005a9e; }}
                """)
            else:
                self.label.setStyleSheet(f"""
                    QTextEdit {{ background-color: #f0f0f0; color: #1a1a1a;
                        {radius} padding: 12px 16px; border: 1px solid #d0d0d0; }}
                """)

    def _update_toggle_btn_text(self):
        """更新折叠按钮文字"""
        if self._is_collapsed:
            self.toggle_btn.setText("▼  展开全部内容")
        else:
            self.toggle_btn.setText("▲  收起")

    def _toggle_collapse(self):
        """切换折叠/展开状态"""
        self._is_collapsed = not self._is_collapsed
        self._adjust_height()
        # 展开时滚动到按钮可见位置
        if not self._is_collapsed:
            QTimer.singleShot(50, lambda: self.toggle_btn.ensurePolished())

    def append_text(self, text: str):
        """流式追加：实时渲染 Markdown"""
        self.raw_text += text
        if self.is_user:
            cursor = self.label.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(text)
            self._adjust_height()
        else:
            should_render = (
                len(self.raw_text) % 8 == 0
                or any(c in text for c in '\n`#*-|>')
            )
            if should_render:
                html_content = self._convert_to_html(self.raw_text)
                self.label.document().contentsChanged.disconnect(self._adjust_height)
                self.label.setHtml(html_content)
                self.label.document().contentsChanged.connect(self._adjust_height)
                # 流式输出时保持展开，让用户看到实时内容
                self._is_collapsed = False
                self._adjust_height()

    def finalize(self):
        """流式结束后做最终一次完整渲染，并重置为折叠状态"""
        if not self.is_user and self.raw_text.strip():
            html_content = self._convert_to_html(self.raw_text)
            self.label.document().contentsChanged.disconnect(self._adjust_height)
            self.label.setHtml(html_content)
            self.label.document().contentsChanged.connect(self._adjust_height)
            # 渲染完成后恢复折叠
            self._is_collapsed = True
            QTimer.singleShot(30, self._adjust_height)

    def update_theme(self):
        """更新主题：重新应用样式和渲染内容"""
        self._apply_theme_style()
        if not self.is_user and self.raw_text.strip():
            html_content = self._convert_to_html(self.raw_text)
            try:
                self.label.document().contentsChanged.disconnect(self._adjust_height)
            except Exception:
                pass
            self.label.setHtml(html_content)
            self.label.document().contentsChanged.connect(self._adjust_height)
        QTimer.singleShot(10, self._adjust_height)



class RenameDialog(MessageBoxBase):
    """ Custom message box """

    def __init__(self, old_name, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel()
        self.titleLabel.setText("重命名")

        layout = QFormLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)
        self.oldname_edit = LineEdit()
        self.oldname_edit.setText(old_name)
        self.oldname_edit.setReadOnly(True)
        layout.addRow("旧标题：", self.oldname_edit)

        self.newname_edit = LineEdit()
        self.newname_edit.setText("")
        self.newname_edit.setPlaceholderText("请输入新标题")
        layout.addRow("新标题：", self.newname_edit)


        # 增加组件到布局
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addLayout(layout)

        self.yesButton.setText("重命名")
        self.cancelButton.setText("取消")
        # 设置对话框的最小宽度
        self.widget.setMinimumWidth(350)

    def get_new_name(self):
        """获取新标题"""
        return self.newname_edit.text().strip()

# ─────────────────────────────────────────────
#  设置对话框
# ─────────────────────────────────────────────

class SettingsDialog(MessageBoxBase):
    """ Custom message box """

    def __init__(self, api_key, base_url, model, system_prompt, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel()
        self.titleLabel.setText("模型设置")

        layout = QFormLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)
        self.key_edit = LineEdit()
        self.key_edit.setText(api_key)
        self.key_edit.setPlaceholderText("sk-xxxxxxxxxxxxxxxx")
        self.key_edit.setEchoMode(LineEdit.EchoMode.Password)
        layout.addRow("API Key：", self.key_edit)

        self.url_edit = LineEdit()
        self.url_edit.setText(base_url)
        self.url_edit.setPlaceholderText("https://api.openai.com/v1")
        layout.addRow("Base URL：", self.url_edit)

        self.model_combo = EditableComboBox()
        preset_models = ["claude-sonnet-4.6", "deepseek-chat", "gpt-5.4", "qwen-plus", "gemini-3.1", "gemini-nanobanana-3"]
        self.model_combo.addItems(preset_models)
        self.model_combo.setEnabled(True)
        self.model_combo.setText(model)
        layout.addRow("模型：", self.model_combo)

        self.prompt_edit = TextEdit()
        self.prompt_edit.setText(system_prompt)
        self.prompt_edit.setPlaceholderText("你是一个有帮助的助手。")
        self.prompt_edit.setFixedHeight(100)
        layout.addRow("系统提示词：", self.prompt_edit)

        # 增加组件到布局
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addLayout(layout)

        self.yesButton.setText("设置")
        self.yesButton.clicked.connect(self.get_values)
        self.cancelButton.setText("取消")
        self.cancelButton.clicked.connect(self.reject)
        # 设置对话框的最小宽度
        self.widget.setMinimumWidth(350)



    def get_values(self):
        return (
            self.key_edit.text().strip(),
            self.url_edit.text().strip(),
            self.model_combo.currentText().strip(),
            self.prompt_edit.toPlainText().strip(),
        )

# ─────────────────────────────────────────────
#  历史记录侧边栏
# ─────────────────────────────────────────────
class HistorySidebar(QWidget):
    # 信号：请求加载某条历史
    load_requested = Signal(str)   # 传文件路径
    # 信号：请求删除某条历史
    delete_requested = Signal(str)  # 传文件路径
    # 信号：重命名标题
    rename_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setMinimumWidth(200)
        self.setMaximumWidth(260)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 侧边栏标题栏
        header = QWidget()
        header.setObjectName("sidebarHeader")
        header.setFixedHeight(52)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 8, 0)

        title = BodyLabel()
        title.setText("历史对话")
        title.setFont(QFont("微软雅黑", 12, QFont.Weight.Bold))

        self.new_btn = PushButton()
        self.new_btn.setText("＋")
        self.new_btn.setFixedSize(38, 28)
        self.new_btn.setToolTip("新建对话")
        self.new_btn.setCursor(Qt.CursorShape.PointingHandCursor)


        h_layout.addWidget(title)
        h_layout.addStretch()
        h_layout.addWidget(self.new_btn)
        layout.addWidget(header)

        # 历史列表
        self.list_widget = ListWidget()
        self.list_widget.setObjectName("historyList")
        self.list_widget.setSpacing(2)
        self.list_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget, 1)

        # # 底部提示
        # tip = BodyLabel()
        # tip.setText("双击加载 · 右键删除")
        # tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # layout.addWidget(tip)

    def refresh(self):
        """重新扫描目录，刷新列表"""
        self.list_widget.clear()
        files = sorted(
            [f for f in os.listdir(HISTORY_DIR) if f.endswith(".json")],
            reverse=True
        )
        for fname in files:
            path = os.path.join(HISTORY_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                title = data.get("title", fname)
                preview = data.get("preview", "")
                time_str= data.get("time", "")
            except Exception:
                title, preview, time_str = fname, "", ""

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, path)

            # 自定义显示 widget
            cell = self._make_cell(title, preview, time_str)
            item.setSizeHint(cell.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, cell)

    def _make_cell(self, title: str, preview: str, time_str: str) -> QWidget:
        w = QWidget()
        w.setObjectName("historyCell")
        vl = QVBoxLayout(w)
        vl.setContentsMargins(10, 6, 10, 6)
        vl.setSpacing(2)

        title_lbl = BodyLabel()
        title_lbl.setText(title[:20] + ("…" if len(title) > 20 else ""))
        title_lbl.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.Bold))

        time_lbl = BodyLabel()
        time_lbl.setText(time_str)

        vl.addWidget(title_lbl)
        vl.addWidget(time_lbl)
        return w

    def _on_item_double_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        self.load_requested.emit(path)

    def _show_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        load_action = QAction("📂 加载对话", self)
        rename_action = QAction("✏️ 重命名", self)
        delete_action = QAction("🗑 删除记录", self)
        load_action.triggered.connect(lambda: self.load_requested.emit(path))
        rename_action.triggered.connect(lambda: self.rename_requested.emit(path))
        delete_action.triggered.connect(lambda: self.delete_requested.emit(path))
        menu.addAction(load_action)
        menu.addSeparator()
        menu.addAction(rename_action)
        menu.addSeparator()
        menu.addAction(delete_action)
        menu.exec(self.list_widget.mapToGlobal(pos))


# ─────────────────────────────────────────────
#  主窗口
# ─────────────────────────────────────────────
def load_settings(SETTINGS_FILE=None):
    if SETTINGS_FILE is None:
        # 判断是否为打包环境
        if getattr(sys, 'frozen', False):
            # 打包环境：使用exe所在目录
            base_dir = os.path.dirname(sys.executable)
        else:
            # 开发环境：使用项目根目录（src_ui的父目录）
            base_dir = os.path.dirname(os.path.dirname(__file__))

        SETTINGS_FILE = os.path.join(base_dir, "Config", "Aisetting.json")

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        api_key = data.get("api_key", "")
        base_url = data.get("base_url", "")
        model = data.get("model", "")
        system_prompt = data.get("system_prompt", "")
        return api_key, base_url, model, system_prompt
    except Exception as e:
        module_logger.error(f"加载设置失败：{e},点击设置会生成新的文件")
        # create_Warning_info(self, "警告", f"加载设置失败：{e}，点击设置会生成新的文件")

def save_settings(ma, api_key, base_url, model, system_prompt, SETTINGS_FILE=None):
    if SETTINGS_FILE is None:
        # 判断是否为打包环境
        if getattr(sys, 'frozen', False):
            # 打包环境：使用exe所在目录
            base_dir = os.path.dirname(sys.executable)
        else:
            # 开发环境：使用项目根目录（src_ui的父目录）
            base_dir = os.path.dirname(os.path.dirname(__file__))

    SETTINGS_FILE = os.path.join(base_dir, "Config", "Aisetting.json")

    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "system_prompt": system_prompt
        }, f, ensure_ascii=False, indent=2)
    create_success_info(ma, "成功", "设置已保存！")


class LoadHistoryWorker(QThread):
    """后台加载历史记录线程"""
    finished = Signal(dict)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.finished.emit(data)


class ChatWindow(QFrame):
    def __init__(self, name: str, parent=None):               # ← 加 parent 参数
        super().__init__(parent)
        self.api_key = ""
        self.base_url = ""
        self.model = ""
        self.system_prompt = "你是一个有帮助的中文助手，回答简洁清晰。"

        try:
            self.api_key, self.base_url, self.model, self.system_prompt = load_settings()
            print("加载成功")
            module_logger.info("加载成功")
        except Exception as e:
            print(e)
            module_logger.error(f"加载失败: {e}")
            pass

        self.history: list[dict] = []
        self.client = None
        self.worker = None
        self.ai_bubble: MessageBubble | None = None
        self.current_session_file: str | None = None
        self.sidebar_visible = True
        self.bubbles: list[MessageBubble] = []
        self.stateTooltip = None
        self._pending_load_path = None
        self.setObjectName(name)
        self._build_ui()
        self.__connectSignalToSlot()
        self.__set_qss()
        self.sidebar.refresh()

        # ── 界面构建 ──────────────────────────────
    def _build_ui(self):
        central = self

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── 顶部工具栏 ──
        toolbar = QWidget()
        toolbar.setFixedHeight(52)
        toolbar.setObjectName("toolbar")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(16, 0, 16, 0)

        # 侧边栏切换按钮
        self.toggle_sidebar_btn = PushButton()
        self.toggle_sidebar_btn.setText("☰")
        self.toggle_sidebar_btn.setFixedSize(66, 36)
        self.toggle_sidebar_btn.setToolTip("显示/隐藏历史记录")
        self.toggle_sidebar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_sidebar_btn.clicked.connect(self._toggle_sidebar)

        title = BodyLabel()
        title.setText("🤖 AI 对话助手")

        self.model_label = BodyLabel()
        self.model_label.setText(f"模型：{self.model}")

        self.clear_btn = PushButton()
        self.clear_btn.setText("🗑 清空")
        self.setting_btn = PushButton()
        self.setting_btn.setText("⚙ 设置")
        for btn in (self.clear_btn, self.setting_btn):
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self.clear_btn.clicked.connect(self._clear_chat)
        self.setting_btn.clicked.connect(self._open_settings)

        tb_layout.addWidget(self.toggle_sidebar_btn)
        tb_layout.addSpacing(8)
        tb_layout.addWidget(title)
        tb_layout.addSpacing(10)
        tb_layout.addWidget(self.model_label)
        tb_layout.addStretch()
        tb_layout.addWidget(self.clear_btn)
        tb_layout.addSpacing(8)
        tb_layout.addWidget(self.setting_btn)
        root_layout.addWidget(toolbar)

        # ── 主体区域（侧边栏 + 聊天区）──
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(1)

        # 侧边栏
        self.sidebar = HistorySidebar()
        self.sidebar.new_btn.clicked.connect(self._new_chat)
        self.sidebar.load_requested.connect(self._load_history)
        self.sidebar.rename_requested.connect(self._rename_history)
        self.sidebar.delete_requested.connect(self._delete_history)

        # 聊天主区域
        chat_area = QWidget()
        chat_area.setObjectName("chatArea")
        chat_v = QVBoxLayout(chat_area)
        chat_v.setContentsMargins(0, 0, 0, 0)
        chat_v.setSpacing(0)

        self.scroll_area = ScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_layout.setSpacing(6)
        self.chat_layout.setContentsMargins(0, 12, 0, 12)

        self.scroll_area.setWidget(self.chat_container)
        chat_v.addWidget(self.scroll_area, 1)

        # 底部输入区
        input_bar = QWidget()
        input_bar.setObjectName("inputBar")
        input_bar.setFixedHeight(200)
        ib_layout = QHBoxLayout(input_bar)
        ib_layout.setContentsMargins(16, 12, 16, 12)
        ib_layout.setSpacing(10)

        self.input_edit = TextEdit()
        self.input_edit.setPlaceholderText("输入消息，按 Enter 发送，Shift + Enter 换行，点击按钮发送…")
        self.input_edit.setFixedHeight(180)

        # 按键绑定Shift + Enter 换行
        self.input_edit.installEventFilter(self)

        self.send_btn = PushButton()
        self.send_btn.setText("发送")
        self.send_btn.setFixedSize(80, 44)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self._send_message)

        ib_layout.addWidget(self.input_edit)
        ib_layout.addWidget(self.send_btn)
        chat_v.addWidget(input_bar)

        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(chat_area)
        self.splitter.setSizes([220, 840])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        root_layout.addWidget(self.splitter, 1)

        self._add_system_tip("👋 请先点击右上角【⚙ 设置】填写 API Key，然后开始对话。")

    # ── 主题相关方法 ──────────────────────────
    def __connectSignalToSlot(self):
        """连接主题变化信号"""
        qconfig.themeChanged.connect(self.__onThemeChanged)

    def __set_qss(self):
        """应用当前主题的样式"""
        theme = 'dark' if isDarkTheme() else 'light'

        # 应用侧边栏样式
        self.sidebar.setStyleSheet(self._get_sidebar_qss(theme))

        # 应用聊天区域样式
        self.setStyleSheet(self._get_chat_qss(theme))

        # 应用滚动区域样式
        self.scroll_area.setStyleSheet(self._get_scroll_area_qss(theme))

        # 应用容器样式
        self.chat_container.setStyleSheet(self._get_chat_container_qss(theme))

        # 更新所有消息气泡的主题
        for bubble in self.bubbles:
            bubble.update_theme()

    def __onThemeChanged(self, theme: Theme):
        """主题变化槽函数"""
        self.__set_qss()

    def eventFilter(self, obj, event):
        """拦截键盘事件，实现 Enter 发送、Shift+Enter 换行"""
        if obj == self.input_edit and event.type() == event.Type.KeyPress:

            key_event = event
            if key_event.key() == Qt.Key.Key_Return:
                # Shift + Enter 换行
                if key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    cursor = self.input_edit.textCursor()
                    cursor.insertText("\n")
                    return True
                # 单独 Enter 发送（需检查是否有修饰键）
                elif not (key_event.modifiers() & (
                        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
                    self._send_message()
                    return True
        return super().eventFilter(obj, event)

    # ── 主题颜色表 ─────────────────────────────
    _THEME_COLORS = {
        'dark': {
            'sidebar_bg':       '#1e1e1e',
            'sidebar_border':   '#3d3d3d',
            'header_bg':        '#252525',
            'cell_bg':          '#2d2d2d',
            'cell_hover':       '#3d3d3d',
            'item_selected':    '#3d3d3d',
            'chat_bg':          '#1a1a1a',
            'input_bar_bg':     '#252525',
            'toolbar_bg':       '#252525',
            'toolbar_border':   '#3d3d3d',
            'scrollbar_bg':     '#1a1a1a',
            'scrollbar_handle': '#3d3d3d',
            'scrollbar_hover':  '#4d4d4d',
        },
        'light': {
            'sidebar_bg':       '#f5f5f5',
            'sidebar_border':   '#e0e0e0',
            'header_bg':        '#fafafa',
            'cell_bg':          '#ffffff',
            'cell_hover':       '#f0f0f0',
            'item_selected':    '#e8e8e8',
            'chat_bg':          '#ffffff',
            'input_bar_bg':     '#fafafa',
            'toolbar_bg':       '#fafafa',
            'toolbar_border':   '#e0e0e0',
            'scrollbar_bg':     '#ffffff',
            'scrollbar_handle': '#d0d0d0',
            'scrollbar_hover':  '#b0b0b0',
        },
    }

    def _get_sidebar_qss(self, theme: str) -> str:
        c = self._THEME_COLORS[theme]
        return f"""
            QWidget#sidebar       {{ background-color: {c['sidebar_bg']}; border-right: 1px solid {c['sidebar_border']}; }}
            QWidget#sidebarHeader {{ background-color: {c['header_bg']};  border-bottom: 1px solid {c['sidebar_border']}; }}
            QWidget#historyCell   {{ background-color: {c['cell_bg']};    border-radius: 4px; }}
            QWidget#historyCell:hover                   {{ background-color: {c['cell_hover']}; }}
            QListWidget#historyList                     {{ background-color: {c['sidebar_bg']}; border: none; outline: none; }}
            QListWidget#historyList::item               {{ border-radius: 4px; margin: 2px 4px; }}
            QListWidget#historyList::item:selected      {{ background-color: {c['item_selected']}; }}
        """

    def _get_chat_qss(self, theme: str) -> str:
        c = self._THEME_COLORS[theme]
        return f"""
            QWidget#chatArea  {{ background-color: {c['chat_bg']}; }}
            QWidget#inputBar  {{ background-color: {c['input_bar_bg']}; border-top:    1px solid {c['toolbar_border']}; }}
            QWidget#toolbar   {{ background-color: {c['toolbar_bg']};   border-bottom: 1px solid {c['toolbar_border']}; }}
        """

    def _get_scroll_area_qss(self, theme: str) -> str:
        c = self._THEME_COLORS[theme]
        scrollbar_common = f"""
            background-color: {c['scrollbar_bg']};
            border-radius: 5px;
        """
        handle_common = f"""
            background-color: {c['scrollbar_handle']};
            border-radius: 5px;
        """
        return f"""
            ScrollArea          {{ background-color: {c['chat_bg']}; border: none; }}
            QScrollBar:vertical         {{ {scrollbar_common} width: 10px; }}
            QScrollBar:horizontal       {{ {scrollbar_common} height: 10px; }}
            QScrollBar::handle:vertical         {{ {handle_common} min-height: 20px; }}
            QScrollBar::handle:horizontal       {{ {handle_common} min-width:  20px; }}
            QScrollBar::handle:vertical:hover,
            QScrollBar::handle:horizontal:hover {{ background-color: {c['scrollbar_hover']}; border-radius: 5px; }}
            QScrollBar::add-line:vertical,  QScrollBar::sub-line:vertical   {{ height: 0px; }}
            QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {{ width:  0px; }}
            QScrollBar::add-page:vertical,  QScrollBar::sub-page:vertical,
            QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal {{ background-color: {c['scrollbar_bg']}; }}
        """

    def _get_chat_container_qss(self, theme: str) -> str:
        c = self._THEME_COLORS[theme]
        return f"QWidget {{ background-color: {c['chat_bg']}; }}"

    # ── 工具方法 ──────────────────────────────
    def _add_system_tip(self, text: str):
        tip = BodyLabel()
        tip.setText(text)
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chat_layout.addWidget(tip)

    def _scroll_to_bottom(self):
        QTimer.singleShot(50, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()
        ))

    def _set_input_enabled(self, enabled: bool):
        self.input_edit.setEnabled(enabled)
        self.send_btn.setEnabled(enabled)

    def _build_client(self):
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def _clear_bubbles(self):
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── 侧边栏切换 ────────────────────────────
    def _toggle_sidebar(self):
        self.sidebar_visible = not self.sidebar_visible
        self.sidebar.setVisible(self.sidebar_visible)

    # ── 新建对话 ──────────────────────────────
    def _new_chat(self):
        if self.history:
            # reply = QMessageBox.question(
            #     self, "新建对话", "当前对话将被保存，确定新建？",
            #     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            # )
            # if reply != QMessageBox.StandardButton.Yes:
            #     return
            self._save_current_session()

        self.history.clear()
        self.current_session_file = None
        self._clear_bubbles()
        self._add_system_tip("💬 新对话已开始，有什么可以帮你的？")
        self.sidebar.refresh()

        # ── 保存当前会话 ──────────────────────────
    def _save_current_session(self):
        if not self.history:
            return

        # 用第一条用户消息作为标题
        first_user_msg = next((m["content"] for m in self.history if m["role"] == "user"), "未命名对话")
        title = first_user_msg.split('\n')[0].strip()[:40]
        # title   = next((m["content"] for m in self.history if m["role"] == "user"), "未命名对话")
        preview = next((m["content"] for m in self.history if m["role"] == "assistant"), "")
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        if not self.current_session_file:
            fname = datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
            self.current_session_file = os.path.join(HISTORY_DIR, fname)

        # 先读取现有数据，保留可能已被重命名的标题
        existing_title = title
        if self.current_session_file and os.path.exists(self.current_session_file):
            try:
                with open(self.current_session_file, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                # 如果文件中已有标题且与自动生成的不同，保留文件中的标题
                if "title" in existing_data and existing_data["title"] != title:
                    existing_title = existing_data["title"]
            except Exception:
                pass

        data = {
            "title": existing_title,
            "preview": preview[:60],
            "time": time_str,
            "model": self.model,
            "history": self.history,
        }
        with open(self.current_session_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── 重命名历史会话 ────────────────────────
    def _rename_history(self, path: str):
        """重命名指定的历史会话文件"""
        if not path or not os.path.exists(path):
            create_Warning_info(self, "错误", "文件不存在！")
            return

        try:
            # 如果当前正在编辑的是其他会话，先保存
            if self.current_session_file and self.current_session_file != path and self.history:
                self._save_current_session()

            # 读取当前标题
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            old_title = data.get("title", "未命名对话")

            # 显示重命名对话框
            rename_dialog = RenameDialog(old_name=old_title, parent=self)
            if rename_dialog.exec():
                new_title = rename_dialog.get_new_name()
                if not new_title:
                    create_Warning_info(self, "提示", "标题不能为空！")
                    return

                # 更新 JSON 文件中的标题
                data["title"] = new_title[:40]
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                create_success_info(self, "成功", "重命名成功！")
                self.sidebar.refresh()
        except Exception as e:
            create_Warning_info(self, "错误", f"重命名失败：{e}")


    # ── 加载历史会话 ──────────────────────────
    def _load_history(self, path: str):
        import time
        # 显示加载状态提示（点击时立即显示）
        self.stateTooltip = StateToolTip('正在切换会话', '请耐心等待哦~~', self)
        self.stateTooltip.move(self.width()/2 - 70, 80)
        self.stateTooltip.show()

        # 保存当前会话
        if self.history:
            self._save_current_session()

        # 记录开始时间
        self._load_start_time = time.time()
        self._pending_load_path = path

        # 后台线程加载
        self._load_worker = LoadHistoryWorker(path, self)
        self._load_worker.finished.connect(self._on_history_loaded)
        self._load_worker.start()

    def _on_history_loaded(self, data: dict):
        """后台加载完成后，在主线程更新UI"""
        self.history = data.get("history", [])
        self.current_session_file = self._pending_load_path

        # 重绘气泡
        self._clear_bubbles()
        self.bubbles.clear()
        self._add_system_tip(f"💬 已加载：{data.get('title', '')}  ({data.get('time', '')})")

        # 批量加载优化：禁用重绘，避免逐条布局计算
        scroll_widget = self.scroll_area.widget()
        scroll_widget.setUpdatesEnabled(False)

        try:
            for msg in self.history:
                is_user_msg = (msg["role"] == "user")
                bubble = MessageBubble(msg["content"], is_user=is_user_msg, batch_mode=True)
                self.chat_layout.addWidget(bubble)
                self.bubbles.append(bubble)
        finally:
            scroll_widget.setUpdatesEnabled(True)

        # 批量加载完成后统一调整高度
        for bubble in self.bubbles:
            if hasattr(bubble, '_adjust_height'):
                bubble._adjust_height()

        # 滚动到底部
        QTimer.singleShot(100, self._scroll_to_bottom)

        # 确保tooltip至少显示500ms，让用户看到转圈动画
        import time
        elapsed = time.time() - self._load_start_time
        remaining = max(0, int((0.5 - elapsed) * 1000))
        QTimer.singleShot(remaining, self._finish_state_tooltip)

    def _finish_state_tooltip(self):
        """关闭加载状态提示"""
        if self.stateTooltip:
            self.stateTooltip.setContent('完成')
            self.stateTooltip.setState(True)
            self.stateTooltip = None



    # ── 删除历史 ──────────────────────────────
    def _delete_history(self, path: str):
        reply = MessageBox("删除", "确定删除该条历史记录吗？",self)
        reply.setClosableOnMaskClicked(True)
        if reply.exec():
            try:
                module_logger.info(f"🗑️ 删除历史记录：{path}")
                os.remove(path)
                # 如果删除的是当前正在查看的会话，清空内存数据
                if self.current_session_file == path:
                    self.history.clear()
                    self.current_session_file = None
                    self._clear_bubbles()
                    self.bubbles.clear()
                    self._add_system_tip("💬 对话已清空，开始新的对话吧！")
                self.sidebar.refresh()
            except Exception:
                pass
            if self.current_session_file == path:
                self.current_session_file = None
            self.sidebar.refresh()

    # ── 发送消息 ──────────────────────────────
    def _send_message(self):
        # 生成中点击 = 停止
        worker = getattr(self, "worker", None)
        if worker is not None:
            try:
                if worker.isRunning():
                    worker.stop()
                    return
            except RuntimeError:
                pass

        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        if not self.api_key:
            create_Warning_info(self, "提示", "请先在【设置】中填写 API Key！")
            return

        if not self.model:
            create_Warning_info(self, "提示", "请先在【设置】中选择模型！")
            return


        module_logger.info(f"{'=' * 50}")
        module_logger.info(f"📤 发送消息")
        module_logger.info(f"🎯 当前模型：：{self.model}")
        module_logger.info(f"🌐 Base URL：{self.base_url}")
        module_logger.info(f"{'=' * 50}\n")

        self.input_edit.clear()
        self._set_input_enabled(False)

        user_bubble = MessageBubble(text, is_user=True)
        self.chat_layout.addWidget(user_bubble)
        self.bubbles.append(user_bubble)

        self.history.append({"role": "user", "content": text})
        # 多轮上下文截断：仅携带最近 30 条，超长会话不拖垮模型
        recent = self.history[-30:]
        messages = [{"role": "system", "content": self.system_prompt}] + recent

        self.ai_bubble = MessageBubble("", is_user=False)
        self.chat_layout.addWidget(self.ai_bubble)
        self.bubbles.append(self.ai_bubble)
        self._scroll_to_bottom()

        self._build_client()
        self.worker = AIWorker(self.client, self.model, messages)
        self.worker.response_chunk.connect(self._on_chunk)
        self.worker.response_done.connect(self._on_done)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.start()
        self.send_btn.setText("停止")

    # ── AI 回调 ───────────────────────────────
    def _on_chunk(self, text: str):
        if self.ai_bubble:
            self.ai_bubble.append_text(text)
            # 降低滚动频率,提升性能
            if len(self.ai_bubble.raw_text) % 20 == 0:
                self._scroll_to_bottom()

    def _on_done(self):
        if self.ai_bubble:
            self.ai_bubble.finalize()          # ← 流式结束后渲染 Markdown
            ai_text = self.ai_bubble.raw_text
            self.history.append({"role": "assistant", "content": ai_text})
        self.ai_bubble = None
        self._set_input_enabled(True)
        self.send_btn.setText("发送")
        self.input_edit.setFocus()
        self._save_current_session()
        self.sidebar.refresh()
        QTimer.singleShot(200, self._scroll_to_bottom)  # 渲染后再滚动


    def _on_error(self, err: str):
        if self.ai_bubble:
            self.ai_bubble.append_text(f"❌ 错误：{err}")
        self.ai_bubble = None
        self._set_input_enabled(True)
        self.send_btn.setText("发送")

    # ── 清空对话 ──────────────────────────────
    def _clear_chat(self):
        reply = MessageBox("确认", "确定要清空当前对话记录吗？",self)

        reply.setClosableOnMaskClicked(True)

        if reply.exec():
            print('Yes button is pressed')
            self.history.clear()
            self.current_session_file = None
            self._clear_bubbles()
            self.bubbles.clear()
            self._add_system_tip("💬 对话已清空，开始新的对话吧！")
        else:
            print('Cancel button is pressed')



    # ── 设置 ──────────────────────────────────
    def _open_settings(self):

        try:
            saved_api_key, saved_base_url, saved_model, saved_prompt = load_settings()
            # 只有当配置文件中有值时才使用，否则保留当前值
            if saved_api_key:
                self.api_key = saved_api_key
            if saved_base_url:
                self.base_url = saved_base_url
            if saved_model:
                self.model = saved_model
            if saved_prompt:
                self.system_prompt = saved_prompt
            print(f"📂 已加载配置 - 模型：{self.model}")
            module_logger.info(f"已加载配置 - 模型：{self.model}")
        except Exception as e:
            print(f"⚠️ 加载配置失败：{e}，使用当前配置")
            module_logger.error(f"加载配置失败：{e}，使用当前配置")
            pass

        dlg = SettingsDialog(
            self.api_key, self.base_url, self.model,
            self.system_prompt, parent=self
        )
        if dlg.exec():
            self.api_key, self.base_url, self.model, self.system_prompt = dlg.get_values()
            self.model_label.setText(f"模型：{self.model}")
            module_logger.info(f"⚙️ 设置已更新")
            module_logger.info(f"🎯 模型：{self.model}")
            module_logger.info(f"🌐 Base URL：{self.base_url}")
            module_logger.info(f"{'='*50}")
            # 保存到本地
            save_settings(self,self.api_key, self.base_url, self.model, self.system_prompt)



    # ── 关闭时自动保存（改为公开方法，供外部调用）────
    def save_and_close(self):                     # ← 替代 closeEvent
        self._save_current_session()

    # 如果仍需支持独立窗口运行时的关闭事件，保留此方法
    def closeEvent(self, event):
        self._save_current_session()
        event.accept()
# ─────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    # app.setFont(QFont("Microsoft YaHei", 10))

    win = QMainWindow()                           # ← 外部套一个窗口
    win.setWindowTitle("AI 对话助手")
    win.resize(1060, 680)
    chat = ChatWindow('AiChat',parent=win)
    win.setCentralWidget(chat)                    # ← 嵌入主窗口
    win.show()
    sys.exit(app.exec())