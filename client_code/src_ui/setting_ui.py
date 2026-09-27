# -*- coding: utf-8 -*-
import json
import logging
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import Qt, QDesktopServices
from PySide6.QtWidgets import (QWidget, QFrame, QHBoxLayout, QGridLayout, QListWidget, QListWidgetItem)

from src_ui import get_file_size, cfg, get_config, get_interface_registry, save_menu_order, get_parent_map, \
    save_menu_parent, filter_circular_parents
from src_ui.information_history import create_success_info, create_error_info
from src_ui.update_manager import (UpdateManager, fetch_update_info_threaded,
                                 show_update_dialog)
from qfluentwidgets import FluentIcon as FIF, TextBrowser, MessageBoxBase, \
    SubtitleLabel, PushButton, ListWidget, ComboBox, BodyLabel, LineEdit
from qfluentwidgets import (SettingCardGroup, SwitchSettingCard, OptionsSettingCard, PushSettingCard,
                            ScrollArea,
                            ExpandLayout, Theme, setTheme, isDarkTheme)
from src import VERSION, VERSION_INFO, VERSION_LOG, VERSION_NEW, UPDATE_SERVER

module_logger = logging.getLogger("flu_widget.setting_ui")


class SettingWidget(QFrame):

    def __init__(self, name: str, parent=None):
        super().__init__(parent=parent)
        self.hBoxLayout = QHBoxLayout()
        self.setObjectName(name)
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.SettingInterface = SettingInterface(self)
        self.gridLayout = QGridLayout(self)
        self.gridLayout.addWidget(self.SettingInterface)
        self.hBoxLayout.addLayout(self.gridLayout)


class SettingInterface(ScrollArea):
    """ Setting interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.menuShowGroup = SettingCardGroup(self.tr('菜单显示'), self.scrollWidget)
        self.nativebarCard = SwitchSettingCard(
            FIF.CHEVRON_RIGHT,
            self.tr("设置软件启动时导航栏是否打开"),
            self.tr("调整软件启动时导航栏是否打开，打开，则软件启动时展开导航栏,重启软件生效"),
            configItem=cfg.nativebar,
            parent=self.menuShowGroup
        )
        self.showMenuCard = PushSettingCard(
            self.tr('功能菜单显示'),
            FIF.MENU,
            self.tr("设置菜单显示"),
            '自定义显示的功能',
            self.menuShowGroup
        )
        self.showMenuCard.clicked.connect(self.show_menu)

        self.setParentMenuCard = PushSettingCard(
            self.tr('设置层级关系'),
            FIF.ALBUM,
            self.tr("设置菜单层级关系"),
            '自定义菜单父子层级',
            self.menuShowGroup
        )
        self.setParentMenuCard.clicked.connect(self.show_parent_setting)

        # 个性化
        self.personalGroup = SettingCardGroup(self.tr('系统设置'), self.scrollWidget)
        self.themeCard = OptionsSettingCard(
            cfg.themeMode,
            FIF.BRUSH,
            self.tr('应用主题'),
            self.tr("调整你的应用外观"),
            texts=[
                self.tr('浅色'), self.tr('深色'), self.tr('跟随系统')
            ],
            parent=self.personalGroup
        )
        self.appWindowSizeCard = OptionsSettingCard(
            cfg.appWindowSize,
            FIF.ZOOM,
            self.tr("设置启动软件窗口大小"),
            self.tr("改变启动软件窗口大小"),
            texts=["1000x780", "1250x853", "1467x924", "1920x1035"],
            parent=self.personalGroup
        )
        # 退出按钮
        self.exitCard = OptionsSettingCard(
            cfg.windowexit,
            FIF.CLOSE,
            self.tr("退出按钮"),
            self.tr("每次退出时执行的操作"),
            texts=["每次询问", "最小化到托盘", "退出程序"],
            parent=self.personalGroup
        )
        # 清除缓存
        self.clearlogCard = PushSettingCard(
            self.tr('清除缓存'),
            FIF.CLEAR_SELECTION,
            self.tr("清除缓存文件"),
            '一些日志文件',
            self.personalGroup
        )
        self.clearlogCard.clicked.connect(lambda: self.clear_log(1))

        # 关于
        self.aboutGroup = SettingCardGroup(self.tr('关于'), self.scrollWidget)
        
                # 检查更新
        from src_ui.update_manager import HARDCODED_UPDATE_SERVER
        self.updateCheckCard = PushSettingCard(
            self.tr('检查更新'),
            FIF.UPDATE,
            self.tr(f"当前版本 {VERSION}"),
            f'点击检查是否有新版本',
            self.aboutGroup
        )
        self.updateCheckCard.clicked.connect(self.check_update)

        # 自动安装
        self.autoInstallCard = SwitchSettingCard(
            FIF.RIGHT_ARROW,
            self.tr("下载完成后自动启动安装程序"),
            self.tr("开启后，下载完成后会立即以静默方式启动安装程序；关闭则下载完成后手动确认"),
            configItem=cfg.update_auto_install,
            parent=self.aboutGroup
        )
        
        self.aboutappCard = PushSettingCard(
            self.tr('关于'),
            FIF.INFO,
            self.tr("关于"),
            f'打开查看版本说明,当前版本{VERSION}',
            self.aboutGroup
        )
        self.aboutappCard.clicked.connect(self.showVersionInfo)

        # 打开主页（软件网页主页 = 认证/更新服务器的信息展示页）
        self.homepageCard = PushSettingCard(
            self.tr('打开主页'),
            FIF.HOME,
            self.tr("软件主页"),
            UPDATE_SERVER,
            self.aboutGroup
        )
        self.homepageCard.clicked.connect(self.open_homepage)
        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        self.setViewportMargins(0, 50, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)

        # initialize style sheet
        self.__setQss()

        # initialize layout
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initLayout(self):
        # 菜单显示
        self.menuShowGroup.addSettingCard(self.nativebarCard)
        self.nativebarCard.switchButton.checkedChanged.connect(
            lambda checked: create_success_info(self, self.tr('导航栏设置'), self.tr('重启软件生效'), 1000))
        self.menuShowGroup.addSettingCard(self.showMenuCard)
        self.menuShowGroup.addSettingCard(self.setParentMenuCard)

        # 设置
        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.appWindowSizeCard)
        self.appWindowSizeCard.optionChanged.connect(
            lambda index: create_success_info(self, self.tr('软件默认窗口大小设置'), self.tr('重启软件生效'), 1000))
        self.personalGroup.addSettingCard(self.exitCard)
        self.personalGroup.addSettingCard(self.clearlogCard)

        # 更新（仅客户端：检查更新 + 自动安装；服务器地址写死）
        self.aboutGroup.addSettingCard(self.updateCheckCard)
        self.aboutGroup.addSettingCard(self.autoInstallCard)

        # 关于
        self.aboutGroup.addSettingCard(self.aboutappCard)
        self.aboutGroup.addSettingCard(self.homepageCard)
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(60, 10, 60, 0)
        self.expandLayout.addWidget(self.menuShowGroup)
        self.expandLayout.addWidget(self.personalGroup)
        self.expandLayout.addWidget(self.aboutGroup)

    def __setQss(self):
        """ set style sheet """
        self.scrollWidget.setObjectName('scrollWidget')

        theme = 'dark' if isDarkTheme() else 'light'
        with open(f'Config/resource/qss/{theme}/setting_interface.qss', encoding='utf-8') as f:
            self.setStyleSheet(f.read())

    def __onThemeChanged(self, theme: Theme):
        """ theme changed slot """
        # 改变主题
        setTheme(theme)
        # 改变qss
        self.__setQss()

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        cfg.themeChanged.connect(self.__onThemeChanged)

    def clear_log(self, flag=1):
        """清空运行日志（日志文件被日志句柄持有，直接截断而非删除）"""
        try:
            log_file = "./LOG/app_log.log"
            if os.path.isfile(log_file):
                size_log = get_file_size(log_file)
                with open(log_file, 'w', encoding='utf-8'):
                    pass
                if flag == 1:
                    create_success_info(self, '清理缓存',
                                        f'清理缓存数据成功，大小为{size_log:.2f}MB')
                    self.clearlogCard.setContent('清理缓存文件')
            else:
                if flag == 1:
                    create_success_info(self, '清理缓存', '无缓存')
        except Exception as log:
            module_logger.error(log)

    def showVersionInfo(self):
        info = SubWindow(self)
        if info.exec():
            pass
        else:
            pass

    def open_homepage(self):
        """用系统浏览器打开软件主页（服务器的信息展示页）。"""
        url = (UPDATE_SERVER or "").strip().rstrip("/")
        if not url:
            create_error_info(self, self.tr("打开主页失败"), "未配置服务器地址")
            return
        if QDesktopServices.openUrl(QUrl(url)):
            module_logger.info(f"打开软件主页: {url}")
        else:
            create_error_info(self, self.tr("打开主页失败"), url)

    # ── 更新相关方法 ─────────────────────────────────────────
    def check_update(self):
        """点「检查更新」：与启动时的检查更新共享同一入口。"""
        fetch_update_info_threaded(self, self._on_update_result)

    def _on_update_result(self, info):
        if not info or "error" in info:
            err = (info or {}).get("error", "无法连接更新服务器")
            create_error_info(self, self.tr("检查更新失败"), err)
            return
        should_exit = show_update_dialog(self, info)
        if should_exit:
            from PySide6.QtWidgets import QApplication
            module_logger.info("用户触发了自动安装，退出主程序让 updater.bat 接管")
            QApplication.quit()

    def show_menu(self):
        menu = MenuShowMessageBox(self)
        if menu.exec():
            create_success_info(self, self.tr('菜单显示设置'), self.tr('重启软件生效'), 1000)
            selected_items = []
            order_keys = []
            for i in range(menu.list_widget.count()):
                item = menu.list_widget.item(i)
                order_keys.append(item.data(Qt.UserRole))
                if item.checkState() == Qt.CheckState.Checked:
                    selected_items.append(item.text())
            module_logger.info(f"选中的菜单项：{selected_items}")
            module_logger.info(f"菜单顺序：{order_keys}")
            config_item = get_config('MenuShow')
            try:
                for item_name, item in config_item.items():
                    if item_name == 'menu_order':
                        continue
                    if item.name in selected_items:
                        cfg.set(item, True)
                        module_logger.info(f"{item.name}已勾选，将设置为True")
                    else:
                        cfg.set(item, False)
                        module_logger.info(f"{item.name}未勾选，将设置为False")
                save_menu_order(order_keys)
                module_logger.info("菜单设置保存成功")
            except Exception as e:
                module_logger.error(f"菜单设置保存失败: {e}")
        else:
            module_logger.info("取消选中")

    def show_parent_setting(self):
        dialog = ParentSettingMessageBox(self)
        if dialog.exec():
            parent_map = {}
            for key, combo in dialog.combo_map.items():
                text = combo.currentText()
                if text and text != '顶层菜单':
                    parent_key = dialog.display_to_key.get(text)
                    if parent_key:
                        parent_map[key] = parent_key
            parent_map = filter_circular_parents(parent_map)
            try:
                save_menu_parent(parent_map)
                create_success_info(self, self.tr('层级关系设置'), self.tr('重启软件生效'), 1000)
                module_logger.info(f"层级关系设置保存成功: {parent_map}")
            except Exception as e:
                module_logger.error(f"层级关系设置保存失败: {e}")
        else:
            module_logger.info("取消层级关系设置")


def text_to_html(text):
    lines = text.split('\n')
    html_lines = []
    for line in lines:
        if line.startswith("更新说明:"):
            # 更新说明行设置为黑体且字体大小增加一级
            html_line = f'<p><strong style="font-size: larger;">{line}</strong></p>'
        else:
            html_line = f'<p>{line}</p>'
        html_lines.append(html_line)
    return ''.join(html_lines)


class SubWindow(MessageBoxBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel()
        self.titleLabel.setText('关于')
        # 创建标签
        self.textBrowser = TextBrowser(self)

        # 增加标题和树
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.textBrowser)
        self.textBrowser.setHtml(text_to_html(VERSION_INFO + '\n' + VERSION_NEW + '\n' + VERSION_LOG))
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")

        self.widget.setMinimumWidth(500)
        self.widget.setMinimumHeight(600)


class MenuShowMessageBox(MessageBoxBase):
    """ Custom message box """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel()
        self.titleLabel.setText("选择需要显示的功能（可上下移动调整顺序）")

        self.registry = get_interface_registry()
        key_to_display = {item['key']: item['display_name'] for item in self.registry}
        key_to_enabled = {item['key']: getattr(cfg, item['key']).value for item in self.registry}

        saved_order = []
        raw = cfg.menu_order.value
        if raw and isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    saved_order = parsed
            except (json.JSONDecodeError, TypeError):
                pass

        ordered_keys = saved_order if saved_order else [item['key'] for item in self.registry]

        self.list_widget = ListWidget(self)
        self.list_widget.setDragDropMode(QListWidget.InternalMove)
        for key in ordered_keys:
            display_name = key_to_display.get(key, key)
            list_item = QListWidgetItem(display_name)
            list_item.setData(Qt.UserRole, key)
            if key_to_enabled.get(key, True):
                list_item.setCheckState(Qt.CheckState.Checked)
            else:
                list_item.setCheckState(Qt.CheckState.Unchecked)
            self.list_widget.addItem(list_item)

        self.upButton = PushButton()
        self.upButton.setText('▲ 上移')
        self.upButton.clicked.connect(self.move_up)

        self.downButton = PushButton()
        self.downButton.setText('▼ 下移')
        self.downButton.clicked.connect(self.move_down)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.upButton)
        btn_layout.addWidget(self.downButton)

        self.selectAllButton = PushButton()
        self.updateSelectAllText()
        self.selectAllButton.clicked.connect(self.selectItems)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.list_widget)
        self.viewLayout.addLayout(btn_layout)
        self.viewLayout.addWidget(self.selectAllButton)

        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")

        self.widget.setMinimumWidth(400)
        self.widget.setMinimumHeight(500)

    def move_up(self):
        current_row = self.list_widget.currentRow()
        if current_row > 0:
            item = self.list_widget.takeItem(current_row)
            self.list_widget.insertItem(current_row - 1, item)
            self.list_widget.setCurrentRow(current_row - 1)

    def move_down(self):
        current_row = self.list_widget.currentRow()
        if self.list_widget.count() - 1 > current_row >= 0:
            item = self.list_widget.takeItem(current_row)
            self.list_widget.insertItem(current_row + 1, item)
            self.list_widget.setCurrentRow(current_row + 1)

    def updateSelectAllText(self):
        all_checked = all(
            self.list_widget.item(i).checkState() == Qt.CheckState.Checked
            for i in range(self.list_widget.count())
        )
        self.selectAllButton.setText("全不选" if all_checked else "全选")

    def selectItems(self):
        flag = self.selectAllButton.text()
        if flag == '全不选':
            self.selectAllButton.setText("全选")
            check_state = Qt.CheckState.Unchecked
        else:
            self.selectAllButton.setText("全不选")
            check_state = Qt.CheckState.Checked
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(check_state)

    def areAllItemsChecked(self):
        for i in range(self.list_widget.count()):
            if self.list_widget.item(i).checkState() != Qt.CheckState.Checked:
                return False
        return True


class ParentSettingMessageBox(MessageBoxBase):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel()
        self.titleLabel.setText("设置菜单层级关系（为每个菜单项选择父级）")

        self.registry = get_interface_registry()
        active_keys = {item['key'] for item in self.registry if getattr(cfg, item['key']).value}
        self.active_items = [item for item in self.registry if item['key'] in active_keys]

        self.key_to_display = {item['key']: item['display_name'] for item in self.active_items}
        self.display_to_key = {item['display_name']: item['key'] for item in self.active_items}

        current_parent_map = get_parent_map()

        self.list_widget = ListWidget(self)
        self.combo_map = {}

        for item in self.active_items:
            key = item['key']
            display_name = item['display_name']

            list_item = QListWidgetItem()
            list_item.setData(Qt.UserRole, key)

            container = QWidget()
            row_layout = QHBoxLayout(container)
            row_layout.setContentsMargins(6, 2, 6, 2)

            label = BodyLabel(display_name)
            label.setMinimumWidth(120)

            combo = ComboBox()
            combo.addItem('顶层菜单')
            for other_item in self.active_items:
                if other_item['key'] != key:
                    combo.addItem(other_item['display_name'])

            saved_parent = current_parent_map.get(key)
            if saved_parent and saved_parent in self.key_to_display:
                parent_display_name = self.key_to_display[saved_parent]
                idx = combo.findText(parent_display_name)
                if idx >= 0:
                    combo.setCurrentIndex(idx)

            combo.setMinimumWidth(150)
            row_layout.addWidget(label)
            row_layout.addWidget(combo)
            row_layout.addStretch()

            list_item.setSizeHint(container.sizeHint())
            self.list_widget.addItem(list_item)
            self.list_widget.setItemWidget(list_item, container)
            self.combo_map[key] = combo

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.list_widget)

        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")

        self.widget.setMinimumWidth(500)
        self.widget.setMinimumHeight(500)
