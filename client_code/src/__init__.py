import configparser
import logging
import os
import sys

from src.Config_ini import CONFIGINI

module_logger = logging.getLogger("flu_widget.__src_init__")
print(f"当前python版本为：{sys.version}")


def get_version_dict():
    try:
        if not os.path.exists('./Config/Version.ini'):
            print("请检查版本配置文件是否存在")
            module_logger.info(f"请检查版本配置文件是否存在")
        else:
            Text_ini = CONFIGINI('./Config/Version.ini')
            version_dict = Text_ini.get_section_version_dict('VERSION')
            return version_dict

    except configparser.NoSectionError as e:
        module_logger.info(f"Section not found in the configuration file: {e}")
        print("请检查版本配置文件是否存在")


#获取版本
version_dict = get_version_dict()
VERSION = version_dict.get('version')
VERSION_INFO = version_dict.get('version_info')
VERSION_LOG = version_dict.get('version_log')
VERSION_NEW = version_dict.get('version_new')
try:
    UPDATE_SERVER=version_dict.get('version_new_server')
    print(UPDATE_SERVER)
    if UPDATE_SERVER:
        pass
    else:
        UPDATE_SERVER = "http://127.0.0.1:18888"
except:
    UPDATE_SERVER = "http://127.0.0.1:18888"

# NetConfig.json 优先（便于接入自有公网服务器），Version.ini 作回退
try:
    from src.net_config import build_base_url
    _override = build_base_url()
    if _override:
        UPDATE_SERVER = _override
except Exception as _e:
    module_logger.warning(f"NetConfig 加载失败: {_e}")
