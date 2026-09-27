# -*- coding: utf-8 -*-
"""常用控件示例模板：演示插件里常用的 Qt 控件和 HostApi 用法。

开发新插件 = 复制本目录为 plugins/你的插件名/main.py，
照这个模板写界面：输入框/下拉框/多选框/列表/文本浏览器/异步执行。
"""
import time

from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QTextBrowser, QVBoxLayout, QWidget)
from qfluentwidgets import (CaptionLabel, CheckBox, ComboBox, LineEdit,
                            PushButton, SubtitleLabel, InfoBarPosition, ListWidget)

from core.plugin_api import HostApi, PluginBase, PluginMeta


class ControlsDemoWidget(QWidget):
    """常用控件演示界面"""

    def __init__(self, host: HostApi, parent=None):
        super().__init__(parent)
        # 日志记录到主日志内
        self.module_logger = HostApi.get_logger(host, name='DEMO')
        self.host = host
        self.setObjectName("controlsDemoWidget")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        layout.addWidget(SubtitleLabel("常用控件示例"))

        # ── 输入框 LineEdit ─────────────────────────────
        layout.addWidget(CaptionLabel("▶ 输入框 (LineEdit)："))
        self.input_edit = LineEdit()
        self.input_edit.setPlaceholderText("请输入文本...")
        layout.addWidget(self.input_edit)

        # ── 下拉框 ComboBox ─────────────────────────────
        layout.addWidget(CaptionLabel("▶ 下拉框 (ComboBox)："))
        self.combo = ComboBox()
        self.combo.addItems(["选项一", "选项二", "选项三"])
        layout.addWidget(self.combo)

        # ── 多选框 CheckBox ─────────────────────────────
        layout.addWidget(CaptionLabel("▶ 多选框 (CheckBox)："))
        self.check_a = CheckBox("启用功能 A")
        self.check_b = CheckBox("启用功能 B")
        self.check_a.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(self.check_a)
        row.addWidget(self.check_b)
        layout.addLayout(row)

        # ── 列表 QListWidget（Ctrl/Shift 可多选）────────
        layout.addWidget(CaptionLabel("▶ 列表 (QListWidget，Ctrl/Shift 多选)："))
        self.list_widget = ListWidget()
        for item in ["列表项目 1", "列表项目 2", "列表项目 3", "列表项目 4"]:
            self.list_widget.addItem(item)
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setMaximumHeight(120)
        layout.addWidget(self.list_widget)

        # ── 操作按钮 ────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_collect = PushButton("收集控件数据")
        self.btn_collect.clicked.connect(self._on_collect)
        btn_row.addWidget(self.btn_collect)

        self.btn_async = PushButton("异步任务示例")
        self.btn_async.clicked.connect(self._on_async)
        btn_row.addWidget(self.btn_async)

        self.btn_dialog = PushButton("选择文件并显示")
        self.btn_dialog.clicked.connect(self._on_choose_file)
        btn_row.addWidget(self.btn_dialog)

        self.clear_dialog_btn = PushButton("清空输出面板")
        self.clear_dialog_btn.clicked.connect(self.clear_output)
        btn_row.addWidget(self.clear_dialog_btn)


        layout.addLayout(btn_row)

        # HostApi 新接口演示
        demo_row = QHBoxLayout()
        demo_row.setSpacing(8)
        self.btn_demo_dialog = PushButton("对话框演示")
        self.btn_demo_dialog.clicked.connect(self._on_demo_dialog)
        demo_row.addWidget(self.btn_demo_dialog)

        self.btn_demo_config = PushButton("保存/读取配置")
        self.btn_demo_config.clicked.connect(self._on_demo_config)
        demo_row.addWidget(self.btn_demo_config)

        self.btn_demo_clip = PushButton("复制到剪贴板")
        self.btn_demo_clip.clicked.connect(self._on_demo_clipboard)
        demo_row.addWidget(self.btn_demo_clip)

        self.btn_demo_cmd = PushButton("执行命令")
        self.btn_demo_cmd.clicked.connect(self._on_demo_command)
        demo_row.addWidget(self.btn_demo_cmd)


        self.show_label = PushButton("显示提醒（上方）")
        self.show_label.clicked.connect(self._on_show_label_top)
        demo_row.addWidget(self.show_label)

        self.show_label2 = PushButton("显示提醒（下方）")
        self.show_label2.clicked.connect(self._on_show_label_BOTTOM)
        demo_row.addWidget(self.show_label2)



        self.show_label3 = PushButton("显示提醒（左下方）")
        self.show_label3.clicked.connect(self._on_show_label_Bottomleft)
        demo_row.addWidget(self.show_label3)


        self.show_label4 = PushButton("显示提醒（右上方）")
        self.show_label4.clicked.connect(self._on_show_label_Topright)
        demo_row.addWidget(self.show_label4)

        layout.addLayout(demo_row)

        #  文本浏览器 QTextBrowser（输出区）
        layout.addWidget(CaptionLabel("▶ 文本浏览器 (QTextBrowser，输出区)："))
        self.output = QTextBrowser()
        layout.addWidget(self.output, 1)

        self._append("模板就绪，点击「收集控件数据」查看各控件的取值方法。")

        self.module_logger.info('加载模板')

    def _append(self, text):
        self.output.append(text)

    def clear_output(self):
        self.output.clear()

    # 消息提醒：InfoBarPosition.TOP 上方
    #  InfoBarPosition.BOTTOM  下方
    #  InfoBarPosition.BOTTOM_LEFT  左下
    #  InfoBarPosition.BOTTOM_RIGHT  右下
    #  InfoBarPosition.TOP_LEFT  左上
    #  InfoBarPosition.TOP_RIGHT  右上

    def _on_show_label_top(self):
        self.host.show_success('标题','文本',duration=10000,position=InfoBarPosition.TOP)

    def _on_show_label_BOTTOM(self):
        self.host.show_error('标题','文本',duration=10000,position=InfoBarPosition.BOTTOM)

    def _on_show_label_Bottomleft(self):
        self.host.show_info('标题','文本',duration=10000,position=InfoBarPosition.BOTTOM_LEFT)

    def _on_show_label_Topright(self):
        self.host.show_warning('标题','文本',duration=10000,position=InfoBarPosition.TOP_RIGHT)


    def _on_collect(self):
        """演示各控件如何取值"""
        self._append("─" * 40)
        self._append(f"输入框: {self.input_edit.text().strip() or '(空)'}")
        self._append(
            f"下拉框: {self.combo.currentText()} (index={self.combo.currentIndex()})")
        self._append("多选框: A="
                     f"{'开' if self.check_a.isChecked() else '关'}, B="
                     f"{'开' if self.check_b.isChecked() else '关'}")
        selected = [i.text() for i in self.list_widget.selectedItems()]
        self._append(f"列表选中: {selected if selected else '(未选择，Ctrl/Shift 可多选)'}")
        self.module_logger.info('取值')

    def _on_async(self):
        """异步任务：耗时操作放后台线程，绝不卡界面。

        注意：线程里不能直接碰控件（跨线程访问控件正是之前崩溃的那类问题），
        只能读取主线程提前捕获的“快照值”。
        """
        self._append("开始异步任务（不卡界面）...")
        self.module_logger.info('开始异步任务（不卡界面）...')
        combo_text = self.combo.currentText()
        input_text = self.input_edit.text().strip() or "无输入"

        def _work():
            for i in range(5):
                time.sleep(1)
                self._append(f"执行中{i+1}s")# 模拟耗时
            return combo_text, input_text

        def _done(result, error):
            if error:
                self._append(f"异步出错: {error}")
            else:
                self._append(f"异步完成: 下拉框={result[0]}，输入框={result[1]}")

        self.host.run_async(_work, _done)

    def _on_choose_file(self):
        path = self.host.choose_file("选择文件", filter="All Files (*)")
        if path:
            self._append(f"已选择: {path}")
            self.module_logger.info(f'已选择: {path}')
    # ── HostApi 新接口演示 ──────────────────────────────

    def _on_demo_dialog(self):
        """confirm / input_text / select_option 用法"""
        if not self.host.confirm("确认框", "接下来演示输入框，继续？"):
            self._append("你点了「取消」")
            return
        name = self.host.input_text("输入框", "请输入一个名称:", default="默认名")
        if name is None:
            self._append("输入框被取消")
            return
        choice = self.host.select_option(
            "下拉选择", "选择分类:", ["工具", "示例", "测试"])
        self._append(f"输入: {name}，选择: {choice}")

    def _on_demo_config(self):
        """get_config / set_config 用法：插件自己的配置持久化"""
        self._append("─" * 40)
        saved = self.host.get_config("last_input", "(无)")
        self._append(f"上次保存的配置: {saved}")
        current = self.input_edit.text().strip() or "hello"
        if self.host.set_config("last_input", current):
            self._append(f"已保存配置: {current} （Config/Plugins/ 下，重启后仍可读取）")
        else:
            self._append("保存配置失败")

    def _on_demo_clipboard(self):
        """copy_to_clipboard 用法"""
        text = self.output.toPlainText()
        if self.host.copy_to_clipboard(text[-500:]):
            self._append("已复制输出区末尾 500 字到剪贴板，可 Ctrl+V 粘贴")
        else:
            self._append("复制失败")

    def _on_demo_command(self):
        """run_command 用法：后台执行命令，不卡界面、不弹黑窗"""
        self._append("执行命令[adb devices]...")

        def _done(result, error):
            if error:
                self._append(f"命令出错: {error}")
                return
            code, stdout, stderr = result
            self._append(f"返回码: {code}")
            if stdout.strip():
                self._append(f"stdout: {stdout.strip()[:300]}")
            if stderr.strip():
                self._append(f"stderr: {stderr.strip()[:300]}")

        self.host.run_command(["adb", "devices"], _done)

class ExamplePlugin(PluginBase):
    meta = PluginMeta(
        name="DEMO",
        category="示例",
        description="插件模板：输入框/下拉框/多选/列表/文本浏览器 + HostApi 用法",
        version="1.0.0",
        icon="ROBOT",
        obj_name="plugin_controls_demo",
    )

    def create_widget(self, host: HostApi, parent=None) -> QWidget:
        return ControlsDemoWidget(host, parent)