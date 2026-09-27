# -*- coding: utf-8 -*-
import ast
import logging
from configparser import ConfigParser  # 配置文件相关库

module_logger = logging.getLogger("flu_widget.Config_ini")


class CONFIGINI(object):
    def __init__(self, ini_path):
        # 初始化
        self.conf = ConfigParser()  # 需要实例化一个ConfigParser对象
        try:
            self.conf.read(ini_path, 'UTF-8')
        except Exception as e:
            module_logger.error(e)
            exit(1)

    def get_ini_sections(self):  # 获取配置文件的sections
        return self.conf.sections()

    def add_ini_sections(self, section):  # 添加新的sections
        if section in self.get_ini_sections():
            return False
        else:
            self.conf.add_section(section)
            return True

    def set_ini_sections(self, section, member, value):  # 设置值
        self.conf.set(section, member, value)

    def save_ini(self, ini_path):  # 保存文件
        with open(ini_path, 'w', encoding='utf-8') as f:
            self.conf.write(f)

    def get_section_option(self, section):  # 获取部分下面的成员列表
        return self.conf.options(section)

    def get_section_item(self, section):  # 获取对应键值
        return self.conf.items(section)

    def get_item(self, section, option):  # 获取对应部分的值
        return self.conf.get(section, option)

    def get_item_dict(self, section, option):
        return ast.literal_eval(self.conf.get(section, option))

    def get_section_text_dict(self, section):
        return {int(k): v for k, v in self.get_section_item(section)}

    def get_section_version_dict(self, section):
        return {k: v for k, v in self.get_section_item(section)}

