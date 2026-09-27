import configparser
import json
import logging
import os
import sys

from qfluentwidgets import qconfig, ConfigItem, OptionsValidator, OptionsConfigItem, QConfig
from src.Config_ini import CONFIGINI

module_logger = logging.getLogger("flu_widget.__flui__init__")


def return_text_dict():
    try:
        if not os.path.exists('./Config/Text.ini'):
            print("请检查文本配置文件是否存在")
            # 异常提示框
            module_logger.error("请检查文本配置文件是否存在")
            return 0
        else:
            Text_ini = CONFIGINI('./Config/Text.ini')
            dict_text = Text_ini.get_section_text_dict('TEXT')
            return dict_text
    except configparser.NoSectionError as e:
        module_logger.info(f"Section not found in the configuration file: {e}")
        print("请检查文本配置文件是否存在")
        return 0


TEXT_DICT = return_text_dict()


# ── 图标库（Config/image/controls/*.png → {文件名: 路径}） ──
CONTROLS_DIR = "./Config/image/controls"
ICON_MAP: dict = {}
if os.path.isdir(CONTROLS_DIR):
    for _f in os.listdir(CONTROLS_DIR):
        if _f.lower().endswith(".png"):
            ICON_MAP[os.path.splitext(_f)[0]] = f"{CONTROLS_DIR}/{_f}"

# 各功能界面对应的 controls 图标（不含扩展名；gougou.png 仅作主题图）
INTERFACE_ICONS = {
    'P2PNWT': 'dianhua', 'MOMENTS': 'xiangji', 'AICHAT': 'mofaxin',
    'DIAINFO': 'qianbi', 'IMGSTUDIO': 'huahua', 'MUSIC': 'erji',
    'VIDEO': 'luxiangdai', 'SYSINFO': 'dengpao', 'WALLPAPER': 'pintu',
    'EXTEND': 'huojian',
}


def get_file_size(file_path):
    """
    获取指定路径下文件的大小（以字节为单位）

    :param file_path: 文件的路径
    :return: 文件大小（字节）
    """
    if not os.path.isfile(file_path):
        module_logger.error(f"{file_path} is not a file")
        return 0

    size = os.path.getsize(file_path) / (1024 * 1024)
    return size


# 配置设置
class Config(QConfig):
    nativebar = ConfigItem(
        "windowSystem", "Turn off the bar", False, ())
    appWindowSize = OptionsConfigItem(
        "windowSystem", "AppWindowSize", "1000x780",
        OptionsValidator(["1000x780", "1250x853", "1467x924", "1920x1035"]), restart=True)
    windowexit = OptionsConfigItem(
        "windowSystem", "windowExit", "每次询问", OptionsValidator(["每次询问", "最小化到托盘", "退出程序"]),
        restart=True)

    EXTEND = ConfigItem("MenuShow", '启动器', True, ())
    DIAINFO = ConfigItem("MenuShow", '备忘录', True, ())

    AICHAT = ConfigItem("MenuShow", 'AiChat', True, ())
    P2PNWT = ConfigItem("MenuShow", '好友', True, ())
    MOMENTS = ConfigItem("MenuShow", '朋友圈', True, ())
    IMGSTUDIO = ConfigItem("MenuShow", '图片工坊', True, ())
    MUSIC = ConfigItem("MenuShow", '音乐播放器', True, ())
    VIDEO = ConfigItem("MenuShow", '视频播放器', True, ())
    SYSINFO = ConfigItem("MenuShow", '电脑配置', True, ())
    WALLPAPER = ConfigItem("MenuShow", '壁纸设置', True, ())

    menu_order = ConfigItem("MenuShow", 'MenuOrder', '', ())

    menu_parent = ConfigItem("MenuShow", 'MenuParent', '', ())



    # ── 更新（仅检查 / 下载，分发服务器是独立脚本 distribute_server.py）───
    # 注：客户端不允许自定义服务器地址，写死在 src_ui/update_manager.py 的
    #     HARDCODED_UPDATE_SERVER 常量中
    update_auto_install = ConfigItem("Update", "AutoInstall", True, ())
    update_download_dir = ConfigItem("Update", "DownloadDir", "./update", ())

    # ── 账号认证（服务管理后台 /api/auth/*，地址同 VERSION_NEW_SERVER）───
    auth_username = ConfigItem("Auth", "Username", "", ())
    auth_token = ConfigItem("Auth", "Token", "", ())
    auth_remember = ConfigItem("Auth", "Remember", True, ())
    auth_user_id = ConfigItem("Auth", "UserId", "", ())
    auth_email = ConfigItem("Auth", "Email", "", ())
    auth_display_name = ConfigItem("Auth", "DisplayName", "", ())
    auth_avatar = ConfigItem("Auth", "Avatar", "", ())

    # ── 壁纸（记住上次加载的文件夹路径，重启后自动加载） ──
    wallpaper_folder = ConfigItem("Wallpaper", "Folder", "", ())

# 本次运行期的登录态（未勾选"记住登录"时令牌只存在内存，不落盘）
# guest=True 表示以游客身份进入（未登录），用户设置页此时显示登录界面
RUNTIME = {"token": "", "username": "", "user_id": "", "guest": False}


# 加载配置
cfg = Config()
try:
    qconfig.load('./Config/config.json', cfg)
except Exception as e:
    module_logger.error(e)
    sys.exit(1)


def get_config(group):
    """
    获取指定group下的所有配置项

    :param group:
    :param config: Config对象
    :param group_name: 组名
    :return: 该组下的所有配置项字典
    """
    group_items = {}
    for attr_name in dir(cfg):
        attr = getattr(cfg, attr_name)
        if isinstance(attr, ConfigItem) and attr.group == group:
            group_items[attr_name] = attr
    return group_items



# ── 接口注册表 ──────────────────────────────────────────────
def get_interface_registry():
    return [
        {'key': 'P2PNWT', 'widget_cls': 'FriendsWidget', 'obj_name': 'p2pnwt',
         'icon': 'dianhua', 'display_name': '好友', 'tooltip': '好友聊天（服务器中转）', 'parent_key': None},

        {'key': 'MOMENTS', 'widget_cls': 'MomentsWidget', 'obj_name': 'moments',
         'icon': 'xiangji', 'display_name': '朋友圈', 'tooltip': '分享与浏览好友动态', 'parent_key': None},

        {'key': 'AICHAT', 'widget_cls': 'ChatWindow', 'obj_name': 'AiChat',
         'icon': 'mofaxin', 'display_name': 'AiChat', 'tooltip': 'Ai模型对话', 'parent_key': None},

        {'key': 'DIAINFO', 'widget_cls': 'ShowDigInfoWidget', 'obj_name': 'show_dig_info',
         'icon': 'qianbi', 'display_name': '备忘录', 'tooltip': '备忘录', 'parent_key': None},

        {'key': 'IMGSTUDIO', 'widget_cls': 'ImageStudioWidget', 'obj_name': 'image_studio',
         'icon': 'huahua', 'display_name': '图片工坊', 'tooltip': '调色与修图', 'parent_key': None},

        {'key': 'MUSIC', 'widget_cls': 'MusicPlayerWidget', 'obj_name': 'music_player',
         'icon': 'erji', 'display_name': '音乐播放器', 'tooltip': '本地音乐播放', 'parent_key': None},

        {'key': 'VIDEO', 'widget_cls': 'VideoPlayerWidget', 'obj_name': 'video_player',
         'icon': 'luxiangdai', 'display_name': '视频播放器', 'tooltip': '本地视频播放', 'parent_key': None},

        {'key': 'SYSINFO', 'widget_cls': 'SysInfoWidget', 'obj_name': 'sysinfo',
         'icon': 'dengpao', 'display_name': '电脑配置', 'tooltip': '查看本机电脑配置', 'parent_key': None},

        {'key': 'WALLPAPER', 'widget_cls': 'WallpaperWidget', 'obj_name': 'wallpaper',
         'icon': 'pintu', 'display_name': '壁纸设置', 'tooltip': '桌面壁纸（静态/双屏/动态轮播）', 'parent_key': None},

        {'key': 'EXTEND', 'widget_cls': 'ExtendedCallScriptWidget', 'obj_name': 'extenddcallscript',
         'icon': 'huojian', 'display_name': '启动器', 'tooltip': '快速启动程序与脚本', 'parent_key': 'DIAINFO'},

    ]


CARD_ICONS = {
    'AICHAT': 'mofaxin', 'EXTEND': 'huojian',
    'DIAINFO': 'qianbi', 'P2PNWT': 'dianhua', 'MOMENTS': 'xiangji',
    'IMGSTUDIO': 'huahua', 'MUSIC': 'erji', 'VIDEO': 'luxiangdai',
    'SYSINFO': 'dengpao', 'WALLPAPER': 'pintu',
}


def _card_icon(key: str) -> str:
    return ICON_MAP.get(CARD_ICONS.get(key, ''), "X")


CARD_DEFINITIONS = {
    'AICHAT': {'icon': _card_icon('AICHAT'), 'title': "AiChat",
               'content': "AI模型对话", 'routeKey': "AiChat"},
    'EXTEND': {'icon': _card_icon('EXTEND'), 'title': "启动器",
               'content': "程序脚本启动", 'routeKey': "extenddcallscript"},
    'DIAINFO': {'icon': _card_icon('DIAINFO'), 'title': "备忘录",
                'content': "记录备忘", 'routeKey': "show_dig_info"},
    'P2PNWT': {'icon': _card_icon('P2PNWT'), 'title': "好友",
               'content': "好友聊天", 'routeKey': "p2pnwt"},
    'MOMENTS': {'icon': _card_icon('MOMENTS'), 'title': "朋友圈",
                'content': "分享与浏览好友动态", 'routeKey': "moments"},
    'IMGSTUDIO': {'icon': _card_icon('IMGSTUDIO'), 'title': "图片工坊",
                  'content': "调色与修图", 'routeKey': "image_studio"},
    'MUSIC': {'icon': _card_icon('MUSIC'), 'title': "音乐播放器",
              'content': "本地音乐·流畅播放", 'routeKey': "music_player"},
    'VIDEO': {'icon': _card_icon('VIDEO'), 'title': "视频播放器",
              'content': "本地视频播放器", 'routeKey': "video_player"},
    'SYSINFO': {'icon': _card_icon('SYSINFO'), 'title': "电脑配置",
                'content': "查看本机电脑配置", 'routeKey': "sysinfo"},
    'WALLPAPER': {'icon': _card_icon('WALLPAPER'), 'title': "壁纸设置",
                  'content': "静态/双屏·动态壁纸", 'routeKey': "wallpaper"},
}


def get_active_interfaces():
    registry = get_interface_registry()
    return [item for item in registry if getattr(cfg, item['key']).value]


def get_active_order():
    raw = cfg.menu_order.value
    if raw and isinstance(raw, str):
        try:
            order = json.loads(raw)
            if isinstance(order, list):
                registry = get_interface_registry()
                valid_keys = {item['key'] for item in registry}
                filtered_order = [k for k in order if k in valid_keys]
                for key in [item['key'] for item in registry]:
                    if key not in filtered_order:
                        filtered_order.append(key)
                if filtered_order:
                    return filtered_order
        except (json.JSONDecodeError, TypeError):
            pass
    return [item['key'] for item in get_interface_registry()]


def save_menu_order(order):
    cfg.set(cfg.menu_order, json.dumps(order, ensure_ascii=False))
    cfg.save()


def get_parent_map():
    raw = cfg.menu_parent.value
    if raw and isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return filter_circular_parents(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    return filter_circular_parents(
        {item['key']: item['parent_key'] for item in get_interface_registry()}
    )


def filter_circular_parents(parent_map):
    filtered = dict(parent_map)
    for key in list(filtered.keys()):
        visited = set()
        current = key
        has_cycle = False
        while filtered.get(current) is not None:
            parent = filtered[current]
            if parent in visited:
                module_logger.warning(
                    f"检测到循环层级依赖: {key} -> ... -> {parent} -> {filtered.get(parent, parent)}，"
                    f"已将 {parent} 的父级重置为顶级菜单"
                )
                filtered.pop(parent, None)
                has_cycle = True
                break
            visited.add(current)
            current = parent
        if has_cycle:
            visited.clear()
            current = key
            while filtered.get(current) is not None:
                parent = filtered[current]
                if parent in visited:
                    break
                visited.add(current)
                current = parent
    return filtered


def save_menu_parent(parent_map):
    cfg.set(cfg.menu_parent, json.dumps(parent_map, ensure_ascii=False))
    cfg.save()


# 配置更新和初始化
def ensure_config_updated():
    """确保配置文件包含所有最新的功能"""
    try:
        registry = get_interface_registry()
        all_keys = [item['key'] for item in registry]

        current_order = cfg.menu_order.value
        if current_order and isinstance(current_order, str):
            try:
                order_list = json.loads(current_order)
                valid_keys = set(all_keys)
                removed = [key for key in order_list if key not in valid_keys]
                if removed:
                    order_list = [key for key in order_list if key in valid_keys]
                    module_logger.info(f"移除已删除功能: {removed}")
                missing = [key for key in all_keys if key not in order_list]
                if missing:
                    order_list.extend(missing)
                    module_logger.info(f"添加新功能: {missing}")
                if removed or missing:
                    cfg.set(cfg.menu_order, json.dumps(order_list, ensure_ascii=False))
            except (json.JSONDecodeError, TypeError):
                cfg.set(cfg.menu_order, json.dumps(all_keys, ensure_ascii=False))
                module_logger.info("菜单顺序已重置")
        else:
            cfg.set(cfg.menu_order, json.dumps(all_keys, ensure_ascii=False))
            module_logger.info("初始化菜单顺序")

        cfg.save()
    except Exception as e:
        module_logger.error(f"配置更新失败: {e}")


# 在加载配置后调用
ensure_config_updated()
