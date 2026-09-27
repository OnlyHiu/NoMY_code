# -*- coding: utf-8 -*-
"""插件发现与加载：扫描 plugins/ 目录，导入每个子目录的 main.py。

目录约定::

    plugins/
    ├── 插件A/
    │   └── main.py          # 定义 PluginBase 子类（自动加载）
    └── 插件B/
        ├── main.py
        └── 其他资源文件/依赖（可选）

加载失败的插件会被跳过并记录日志，不影响主程序和其它插件。
"""
import importlib.util
import os
import sys

from .plugin_api import PluginBase


def discover_plugins(plugins_dir: str, logger=None, failures: list | None = None) -> list:
    """扫描插件目录，返回 [(插件实例, main.py 路径), ...]。

    每个插件子目录下的 main.py 必须导出且仅导出一个 PluginBase 子类。
    加载失败的插件可追加到 failures（[(目录名, 错误信息), ...]）。
    """
    plugins = []
    if not os.path.isdir(plugins_dir):
        return plugins

    for entry in sorted(os.listdir(plugins_dir)):
        plg_dir = os.path.join(plugins_dir, entry)
        main_py = os.path.join(plg_dir, "main.py")
        if not os.path.isdir(plg_dir) or not os.path.isfile(main_py):
            continue

        module_name = f"external_plugin_{entry}"
        try:
            sys.modules.pop(module_name, None)  # 清理旧模块，确保热重载执行新代码
            spec = importlib.util.spec_from_file_location(module_name, main_py)
            if spec is None or spec.loader is None:
                raise RuntimeError("无法解析模块")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            plugin = _extract_plugin(module)
            if plugin is None:
                raise RuntimeError("main.py 中没有定义 PluginBase 子类")
            plugins.append((plugin, main_py))
        except Exception as e:  # noqa: BLE001 - 单个插件失败不能拖垮整个应用
            if failures is not None:
                failures.append((entry, str(e)))
            if logger:
                logger.error(f"[插件] 加载失败: {entry} -> {e}")

    return plugins


def purge_loaded_modules():
    """清除所有已加载插件模块缓存（热重载前调用）。

    插件模块名统一为 external_plugin_<目录名>，重载前必须从 sys.modules
    移除，否则 exec_module 不会重新执行，改动不会生效。
    """
    for name in list(sys.modules):
        if name.startswith("external_plugin_"):
            sys.modules.pop(name, None)


def _extract_plugin(module) -> PluginBase | None:
    """从模块中提取第一个 PluginBase 子类（不包括基类本身）"""
    for value in vars(module).values():
        if (isinstance(value, type)
                and issubclass(value, PluginBase)
                and value is not PluginBase):
            try:
                return value()
            except Exception:
                continue
    return None