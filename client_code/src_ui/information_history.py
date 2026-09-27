# -*- coding: utf-8 -*-
from PySide6.QtCore import Qt, QFile, QTextStream

from qfluentwidgets import InfoBar, InfoBarPosition


def create_error_info(parent, title, content, duration=4000,position=InfoBarPosition.TOP):
    InfoBar.error(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=position,
        duration=duration,
        parent=parent
    )


def create_success_info(parent, title, content, duration=4000,position=InfoBarPosition.TOP):
    InfoBar.success(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=position,
        duration=duration,
        parent=parent
    )


def create_Warning_info(parent, title, content, duration=4000,position=InfoBarPosition.TOP):
    InfoBar.warning(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=position,
        duration=duration,
        parent=parent
    )

def create_infomation_info(parent, title, content, duration=4000,position=InfoBarPosition.TOP):
    InfoBar.info(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=position,
        duration=duration,
        parent=parent
    )



def load_last_directory(file):
    file = QFile(file)
    if file.exists() and file.open(QFile.OpenModeFlag.ReadOnly | QFile.OpenModeFlag.Text, ):
        stream = QTextStream(file)
        directory = stream.readLine()
        file.close()
        return directory or ''
    else:
        return ''


def save_last_directory(file, directory):
    file = QFile(file)
    if file.open(QFile.OpenModeFlag.WriteOnly | QFile.OpenModeFlag.Text):
        stream = QTextStream(file)
        stream << directory
        file.close()
