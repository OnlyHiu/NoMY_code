# coding:utf-8
import logging
from enum import Enum

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout

from src_ui import cfg, get_active_order, CARD_DEFINITIONS
from qfluentwidgets import ScrollArea, FlowLayout, IconWidget, TextWrap, CardWidget
from qfluentwidgets import StyleSheetBase, Theme, qconfig

module_logger = logging.getLogger("flu_widget.home_ui")


class StyleSheet(StyleSheetBase, Enum):
    """ Style sheet  """
    SAMPLE_CARD = "sample_card"
    HOME_INTERFACE = "home_interface"

    def path(self, theme=Theme.AUTO):
        theme = qconfig.theme if theme == Theme.AUTO else theme
        return f"./Config/resource/qss/{theme.value.lower()}/{self.value}.qss"



class HomeInterface(ScrollArea):
    """ Home interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        self.__initWidget()
        self.loadSamples()

    def __initWidget(self):
        self.view.setObjectName('view')
        self.setObjectName('homeInterface')
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        StyleSheet.HOME_INTERFACE.apply(self)

    def loadSamples(self):
        """ load samples """
        dateTimeView = SampleCardView(self.tr(''), self.view)
        ordered_keys = get_active_order()

        for index, key in enumerate(ordered_keys):
            card_def = CARD_DEFINITIONS.get(key)
            if not card_def:
                continue
            cfg_value = getattr(cfg, key).value
            if cfg_value:
                module_logger.info(f"{card_def['title'].replace(chr(10), '')} 加载到主页")
                dateTimeView.addSampleCard(
                    icon=card_def['icon'],
                    title=card_def['title'],
                    content=card_def['content'],
                    routeKey=card_def['routeKey'],
                    index=index
                )

        self.vBoxLayout.addWidget(dateTimeView)


class SignalBus(QObject):
    """ Signal bus """

    switchToSampleCard = Signal(str, int)
    # print("SignalBus")


signalBus = SignalBus()


class SampleCard(CardWidget):
    """ Sample card """

    def __init__(self, icon, title, content, routeKey, index, parent=None):
        super().__init__(parent=parent)
        self.index = index
        self.routekey = routeKey

        self.iconWidget = IconWidget(icon)
        self.titleLabel = QLabel(title, self)
        self.contentLabel = QLabel(TextWrap.wrap(content, 45, False)[0], self)

        self.hBoxLayout = QHBoxLayout(self)
        self.vBoxLayout = QVBoxLayout()

        self.setFixedSize(233, 160)
        self.iconWidget.setFixedSize(48, 48)

        self.hBoxLayout.setSpacing(28)
        self.hBoxLayout.setContentsMargins(20, 0, 0, 0)
        self.vBoxLayout.setSpacing(2)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.hBoxLayout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.hBoxLayout.addWidget(self.iconWidget)
        self.hBoxLayout.addLayout(self.vBoxLayout)
        self.vBoxLayout.addStretch(1)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.contentLabel)
        self.vBoxLayout.addStretch(1)

        self.titleLabel.setObjectName('titleLabel')
        self.contentLabel.setObjectName('contentLabel')

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        signalBus.switchToSampleCard.emit(self.routekey, self.index)
        # print(self.routekey, self.index)


class SampleCardView(QWidget):
    """ Sample card view """

    def __init__(self, title: str, parent=None):
        super().__init__(parent=parent)
        self.titleLabel = QLabel(title, self)
        self.vBoxLayout = QVBoxLayout(self)
        self.flowLayout = FlowLayout()

        self.vBoxLayout.setContentsMargins(36, 0, 36, 0)
        self.vBoxLayout.setSpacing(10)
        self.flowLayout.setContentsMargins(0, 0, 0, 0)
        self.flowLayout.setHorizontalSpacing(12)
        self.flowLayout.setVerticalSpacing(12)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addLayout(self.flowLayout, 1)
        self.titleLabel.setObjectName('viewTitleLabel')
        StyleSheet.SAMPLE_CARD.apply(self)

    def addSampleCard(self, icon, title, content, routeKey, index):
        """ add sample card """
        card = SampleCard(icon, title, content, routeKey, index, self)
        self.flowLayout.addWidget(card)
