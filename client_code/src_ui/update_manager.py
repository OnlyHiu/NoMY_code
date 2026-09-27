# -*- coding: utf-8 -*-
"""版本检测 / 安装包下载 / 静默安装。

通过 LAN 内的简易 HTTP 分发服务器（见 distribute_server.py）检查新版本并下载。
所有耗时操作都跑在子线程，通过 Qt Signal 与主线程通信。
"""
import hashlib
import json
import logging
import os
import sys
import socket
import subprocess
import threading
import time
import urllib.request
import urllib.error
from typing import Optional
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal

from src import UPDATE_SERVER, VERSION

module_logger = logging.getLogger("flu_widget.update_manager")



HARDCODED_UPDATE_SERVER = UPDATE_SERVER

# 模块加载时自检（写到日志，方便排查）
import os as _os
module_logger.info(
    "update_manager loaded from %s | HARDCODED_UPDATE_SERVER=%r | pid=%d",
    __file__, HARDCODED_UPDATE_SERVER, _os.getpid(),
)

DEFAULT_PORT = 18888
DEFAULT_TIMEOUT = 8
CHUNK_SIZE = 64 * 1024


def get_lan_ips() -> list[str]:
    """列出本机的局域网 IP（排除 127.x 和 link-local）。"""
    ips: list[str] = []
    # UDP 取路由出口 IP（不发包）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ":" not in ip and ip not in ips:
                ips.append(ip)
        finally:
            s.close()
    except Exception:
        pass
    # hostname 解析
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if (not ip.startswith("127.") and ":" not in ip
                    and not ip.startswith("169.254.") and ip not in ips):
                ips.append(ip)
    except Exception:
        pass
    return ips


def compare_version(v1: str, v2: str) -> int:
    """返回 1 if v1>v2, -1 if v1<v2, 0 if 相等。支持 Vx.y.z 形式。"""
    def parse(v: str) -> list[int]:
        s = v.lstrip("Vv").strip()
        parts = []
        for p in s.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(0)
        return parts
    a, b = parse(v1), parse(v2)
    for x, y in zip(a, b):
        if x > y:
            return 1
        if x < y:
            return -1
    if len(a) > len(b):
        return 1
    if len(a) < len(b):
        return -1
    return 0


class UpdateManager(QObject):
    """单例管理器（一个进程一个）。所有 Signal 在子线程 emit，主线程自动切回。"""
    progress = Signal(int, int)             # (received_bytes, total_bytes)
    status = Signal(str)                    # 状态文本
    finished = Signal(bool, str)            # (success, path_or_msg)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()


    # ── LAN 方式（查 + 下一体）））──────────────────────────
    def check_update(self, server_url: str = None) -> Optional[dict]:
        """调用 LAN 内分发服务器的 /api/update/check。
        server_url 不传时用硬编码的 HARDCODED_UPDATE_SERVER。
        返回 {"version": "V1.0.0", "info": "...", "package": "setup.exe",
              "package_size": 12345, "sha256": "...", "url": "..."}
        """
        # ── 诊断：首次调用时写一条日志，方便排查 ──
        if not getattr(self, "_check_update_diag", False):
            self._check_update_diag = True
            import sys, os
            module_logger.info(
                "[DIAG] check_update 首次调用 | __file__=%s | "
                "HARDCODED_UPDATE_SERVER=%r | pid=%d | python=%s",
                __file__, HARDCODED_UPDATE_SERVER, os.getpid(), sys.executable,
            )

        # ── 逐级兜底 ──
        effective_url = server_url or HARDCODED_UPDATE_SERVER or "http://127.0.0.1:18888"
        if effective_url != server_url:
            module_logger.info("[DIAG] server_url 原值=%r → 使用=%r", server_url, effective_url)
        server_url = effective_url

        parsed = urlparse(server_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if not parsed.scheme or not parsed.netloc:
            msg = (f"URL 解析失败：server_url={server_url!r} "
                   f"(scheme={parsed.scheme!r}, netloc={parsed.netloc!r})")
            module_logger.error(msg)
            return {"error": msg, "server": server_url}

        check_url = f"{base}/api/update/check"
        try:
            module_logger.info("检查更新 → %s", check_url)
            req = urllib.request.Request(check_url,
                                          headers={"User-Agent": "TestTools/1.0"})
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                data["url"] = f"{base}/api/update/download"
                data["server"] = server_url
                return data
        except urllib.error.HTTPError as e:
            module_logger.warning("check_update HTTP %s: %s", e.code, e.reason)
            return {"error": f"HTTP {e.code}", "server": server_url}
        except Exception as e:
            module_logger.error("check_update 失败: %s (server_url=%r)", e, server_url)
            return {"error": str(e), "server": server_url}

    # ── 下载 ──────────────────────────────────────────────
    def download(self, url: str, save_dir: str, expected_sha256: str = None) -> str:
        """下载到 save_dir，自动按 Content-Disposition 或 url 命名。
        返回本地文件路径。失败抛异常。
        安全：必须携带服务端 check 接口提供的 SHA256，缺失时拒绝下载安装（防篡改）。
        """
        if not expected_sha256:
            raise RuntimeError("服务器未提供安装包 SHA256 校验值，为安全起见已拒绝下载安装")
        self._cancel.clear()
        self.status.emit("开始下载...")
        os.makedirs(save_dir, exist_ok=True)

        req = urllib.request.Request(url, headers={"User-Agent": "TestTools/1.0"})
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            # 文件名
            filename = self._guess_filename(resp, url)
            save_path = os.path.join(save_dir, filename)

            received = 0
            h = hashlib.sha256()
            chunk_count = 0
            with open(save_path, "wb") as f:
                while True:
                    if self._cancel.is_set():
                        try:
                            os.remove(save_path)
                        except OSError:
                            pass
                        raise RuntimeError("用户取消下载")
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    received += len(chunk)
                    chunk_count += 1
                    # 进度信号节流：每 ~150ms 发一次
                    if chunk_count % 4 == 0 or received == total:
                        self.progress.emit(received, total)

        if h:
            actual = h.hexdigest().lower()
            if actual.lower() != expected_sha256.lower():
                try:
                    os.remove(save_path)
                except OSError:
                    pass

                raise RuntimeError(f"SHA256 校验失败：期望 {expected_sha256}\n实际 {actual}")
        self.status.emit(f"下载完成: {save_path}")
        return save_path

    @staticmethod
    def _guess_filename(resp, url: str) -> str:
        cd = resp.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            try:
                fname = cd.split("filename=", 1)[1].split(";", 1)[0].strip().strip('"').strip("'")
                # 安全净化：仅保留文件名本身，防止路径穿越写入
                fname = os.path.basename(fname.replace("\\", "/"))
                if fname and fname not in (".", ".."):
                    return fname
            except Exception:
                pass
        # 从 URL 路径取
        path = urllib.parse.urlparse(url).path
        name = os.path.basename(path)
        if name and name not in (".", ".."):
            return name
        return f"update_{int(time.time())}.exe"

    # ── 静默安装 ──────────────────────────────────────────
    @staticmethod
    def install_silently(installer_path: str, restart_exe: str = None) -> tuple[bool, str]:
        """通过 updater.bat 实现 self-update。

        NSIS /S 安装程序会杀同名进程，必须先退出主程序。
        方案：写 updater.bat → 用 cmd /c start 脱离 → 主程序退出 →
              bat 等主程序死透 → 运行安装 → 可选重启主程序。
        """
        # 转绝对路径（打包后 CWD 不一定是 exe 所在目录）
        abs_path = os.path.abspath(installer_path)
        if not os.path.isfile(abs_path):
            return False, f"安装包不存在: {abs_path}"
        installer_dir = os.path.dirname(abs_path)
        installer_name = os.path.basename(abs_path)

        # updater.bat 放在安装包同目录
        updater_bat = os.path.join(installer_dir, "_updater.bat")

        # bat 内容
        ext = os.path.splitext(installer_name)[1].lower()
        bat_lines = [
            "@echo off",
            "chcp 65001 >nul",
            "setlocal",
            "",
            "echo [updater] 等待主程序退出...",
            ":wait_main",
            '  tasklist /FI "IMAGENAME eq NoMY.exe" 2>nul | findstr /I "NoMY" >nul',
            "  if %ERRORLEVEL%==0 (",
            "    timeout /t 1 /nobreak >nul",
            "    goto wait_main",
            "  )",
            "",
            f'echo [updater] 运行安装程序: {installer_name}',
            f'cd /d "{installer_dir}"',
            "",
        ]
        if ext == ".msi":
            bat_lines.append(f'start /wait "" msiexec /i "{installer_name}" /qn /norestart')
        else:
            # /wait 等安装完，/S 静默（NSIS）；Inno 用 /VERYSILENT /SUPPRESSMSGBOXES
            bat_lines.append(f'start /wait "" "{installer_name}" /S')

        bat_lines += [
            "",
            "echo [updater] 安装完成，等待 2 秒...",
            "timeout /t 2 /nobreak >nul",
        ]
        bat_lines += [
            "",
            "del \"%~f0\"",  # 自删除 updater.bat
            "endlocal",
        ]
        bat_content = "\r\n".join(bat_lines)

        try:
            with open(updater_bat, "w", encoding="utf-8") as f:
                f.write(bat_content)

            # 用 cmd /c start 彻底脱离：updater.bat 启动后立刻返回
            subprocess.Popen(
                f'cmd /c start "" "{updater_bat}"',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x00000008,  # DETACHED_PROCESS
            )
            return True, (
                "已启动 updater。主程序将退出，安装在后台进行。"
                + ("完成后自动重启。" if restart_exe else "")
            )
        except Exception as e:
            return False, f"启动 updater 失败: {e}"

    # ── 一步到位：检查 +（必要时）下载 ──────────────────────
    def fetch_update_thread(self, save_dir: str, server_url: str = None):
        """子线程入口：检查 → 下载 → 完成。完成后 emit finished。
        server_url 不传时用硬编码的 HARDCODED_UPDATE_SERVER。
        """
        if not server_url:
            server_url = HARDCODED_UPDATE_SERVER
        try:
            self.status.emit(f"连接 {server_url} ...")
            info = self.check_update(server_url)
            if not info:
                self.finished.emit(False, f"无法连接 {server_url}")
                return
            if "error" in info:
                self.finished.emit(False, f"服务器返回: {info.get('error')}")
                return
            remote_v = info.get("version", "")
            cmp = compare_version(remote_v, VERSION)
            if cmp <= 0:
                self.finished.emit(True, f"已是最新版本 {VERSION}")
                return
            # 有新版本：下载
            path = self.download(info["url"], save_dir,
                                 expected_sha256=info.get("sha256"))
            self.finished.emit(True, path)
        except Exception as e:
            module_logger.exception("fetch_update failed")
            self.finished.emit(False, str(e))


# ── 工具函数 ──────────────────────────────────────────────
def fetch_in_thread(manager: UpdateManager, save_dir: str, server_url: str = None):
    """开子线程运行 fetch_update_thread，避免阻塞 UI。
    server_url 不传时用硬编码的 HARDCODED_UPDATE_SERVER。
    """
    t = threading.Thread(
        target=manager.fetch_update_thread,
        args=(save_dir, server_url),
        daemon=True,
    )
    t.start()


# ── 检查更新：UI 调用的统一入口 ──────────────────────────────
from PySide6.QtCore import QThread, Signal as _Signal


class _UpdateCheckThread(QThread):
    """检查更新后台线程；通过 result 信号回到主线程。"""

    result = _Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            mgr = UpdateManager()
            info = mgr.check_update()
        except Exception as e:
            module_logger.warning(f"检查更新异常: {e}")
            info = None
        if self._cancel:
            return
        self.result.emit(info)


def fetch_update_info_threaded(parent, on_result):
    """启动一个 QThread 检查更新；on_result(info) 回调在主线程执行。

    - info: dict（含 'version'/'info'/'package' 等）或 None 表示异常
    - 含 'error' 字段表示服务器返回失败
    - 重复调用会自动取消上一次（防止重复弹窗）
    """
    task = _UpdateCheckThread(parent)
    # 同一个 parent 下最多同时存在一个 _UpdateCheckThread；
    # 通过 parent 上的属性命名集中管理，避免悬挂线程。
    setattr(parent, "_update_check_thread", task)
    task.result.connect(on_result)
    task.finished.connect(task.deleteLater)
    task.start()
    return task


def show_update_dialog(parent, info):
    """统一的新版本弹窗逻辑：无论是从设置里手动触发还是启动时检查发现，
    都走这里。

    - info 含 'version' 且 > VERSION → 弹 UpdateProgressDialog
    - 否则记录日志不弹窗
    - 用户勾选「自动安装」退出主程序，调用方自行判断并退出
    """
    from src_ui.update_dialog import UpdateProgressDialog
    from src_ui import cfg

    if not info or "error" in info:
        module_logger.info("无新版本（检查失败或服务器不可达）")
        return False
    try:
        new_version = info["version"]
    except (KeyError, TypeError):
        module_logger.info("无新版本（响应缺少 version 字段）")
        return False
    if compare_version(new_version, VERSION) <= 0:
        module_logger.info("无新版本")
        return False

    save_dir = cfg.update_download_dir.value or "./update"
    dlg = UpdateProgressDialog(parent, info=info,
                              current_version=VERSION, save_dir=save_dir)
    dlg.autoInstallCheck.setChecked(bool(cfg.update_auto_install.value))
    dlg.exec()
    cfg.set(cfg.update_auto_install, dlg.autoInstallCheck.isChecked())
    cfg.save()
    return getattr(dlg, "_should_exit_app", False)