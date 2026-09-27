# -*- coding: utf-8 -*-
"""网络配置（客户端）

使用方法：编辑 Config/NetConfig.json 填入你的服务器地址，例如：
{
    "server_url": "123.45.67.89",     // 公网 IP 或域名（不带 http://）
    "port": 18888,
    "use_ssl": false,                  // true = https（需服务端配 TLS/反代）
    "api_prefix": "",                  // 反向代理路径前缀，如 "/noon-api"
    "timeout": 8,
    "poll_interval": 800
}
server_url 留空时回退到 Config/Version.ini 的 VERSION_NEW_SERVER。
"""
import json
import logging
import os

logger = logging.getLogger("flu_widget.net_config")

CFG_FILE = "./Config/NetConfig.json"

_config: dict = {}
try:
    with open(CFG_FILE, "r", encoding="utf-8") as f:
        _config = json.load(f) or {}
except FileNotFoundError:
    pass
except Exception as e:
    logger.warning(f"NetConfig.json 解析失败: {e}")

SERVER_URL = str(_config.get("server_url", "")).strip()
PORT = int(_config.get("port", 18888) or 18888)
USE_SSL = bool(_config.get("use_ssl", False))
API_PREFIX = str(_config.get("api_prefix", "")).strip()
TIMEOUT = int(_config.get("timeout", 8) or 8)
POLL_INTERVAL = int(_config.get("poll_interval", 800) or 800)


def build_base_url() -> str | None:
    """按配置拼出服务器基地址；未配置返回 None（调用方使用默认）。"""
    if not SERVER_URL:
        return None
    scheme = "https" if USE_SSL else "http"
    default_ports = {"https": 443, "http": 80}
    url = f"{scheme}://{SERVER_URL}"
    if PORT != default_ports[scheme]:
        url += f":{PORT}"
    if API_PREFIX:
        url += "/" + API_PREFIX.strip("/")
    return url
