# -*- coding: utf-8 -*-
"""电脑配置查看页

展示本机绝大多数硬件与系统信息：处理器/内存/显卡/主板/BIOS/操作系统/磁盘/网络。
仅使用标准库 + psutil + PowerShell CIM 查询（显卡等），无新依赖。
"""
import logging
import os
import platform
import re
import socket
import subprocess
import sys
import threading

import psutil
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QFrame, QVBoxLayout, QWidget

from qfluentwidgets import (BodyLabel, CaptionLabel, FluentIcon as FIF, IconWidget,
                            InfoBar, InfoBarPosition, ProgressRing, PushButton,
                            ScrollArea, SettingCardGroup, SubtitleLabel,
                            PushSettingCard, TitleLabel)

module_logger = logging.getLogger("flu_widget.sysinfo")


def _winreg_query(path: str, key: str) -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as h:
            value, _ = winreg.QueryValueEx(h, key)
            return str(value)
    except Exception:
        return ""


def _cpu_name() -> str:
    name = _winreg_query(
        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
    return name or platform.processor() or "未知处理器"


def _os_info() -> dict:
    cur_ver = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    display = _winreg_query(cur_ver, "DisplayVersion") or \
        _winreg_query(cur_ver, "ReleaseId")
    build = _winreg_query(cur_ver, "CurrentBuildNumber")
    name = _winreg_query(cur_ver, "ProductName") or platform.system()
    return {
        "系统": f"{name} {display} (Build {build})" if display else platform.platform(),
        "架构": platform.machine(),
        "Python": sys.version.split()[0],
    }


GPU_CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def _registry_hardware() -> dict:
    """注册表直查硬件（快速稳定，不依赖 PowerShell/WMI 服务）。"""
    out = {"gpu": [], "board": "", "bios": "", "system": ""}
    # 显卡：显示适配器类驱动 0000..0009（有显存的排前面，虚拟适配器殿后）
    gpus = []
    for i in range(10):
        path = f"{GPU_CLASS_KEY}\\{i:04d}"
        desc = _winreg_query(path, "DriverDesc")
        if not desc:
            continue
        mem = 0
        raw = _winreg_query(path, "HardwareInformation.qwMemorySize")
        try:
            if isinstance(raw, bytes) and len(raw) == 8:
                mem = int.from_bytes(raw, "little")
            elif raw:
                mem = int(raw)
        except Exception:
            mem = 0
        gpus.append({"name": desc, "mem": mem})
    gpus.sort(key=lambda g: g["mem"] == 0)
    for g in gpus:
        mem_text = f"（{_human_size(g['mem'])} 显存）" if g["mem"] else ""
        out["gpu"].append(f"{g['name']}{mem_text}")

    bios_key = r"HARDWARE\DESCRIPTION\System\BIOS"
    out["board"] = (f"{_winreg_query(bios_key, 'BaseBoardManufacturer')} "
                    f"{_winreg_query(bios_key, 'BaseBoardProduct')}").strip()
    out["bios"] = (f"{_winreg_query(bios_key, 'BIOSVendor')} "
                   f"{_winreg_query(bios_key, 'BIOSVersion')} "
                   f"{_winreg_query(bios_key, 'BIOSReleaseDate')}").strip()
    out["system"] = (f"{_winreg_query(bios_key, 'SystemManufacturer')} "
                     f"{_winreg_query(bios_key, 'SystemProductName')}").strip()
    return out


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"


class SysInfoInterface(ScrollArea):
    """电脑配置界面。"""

    cimReady = Signal(dict)  # CIM 查询线程 → UI 线程

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = QVBoxLayout(self.scrollWidget)
        self.expandLayout.setContentsMargins(16, 0, 16, 0)
        self.cimReady.connect(self._apply_cim_result)
        self._build_ui()
        self._refresh_static()
        self._start_cim_query()

        self._usage_timer = QTimer(self)
        self._usage_timer.timeout.connect(self._refresh_usage)
        self._usage_timer.start(2000)
        self._refresh_usage()

        self.resize(1000, 760)
        self.setViewportMargins(0, 24, 0, 24)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)

    # ── UI ─────────────────────────────────────────────
    def _build_ui(self):
        self.expandLayout.setSpacing(16)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("电脑配置"))
        header.addStretch(1)
        self.refreshBtn = PushButton(FIF.SYNC, "刷新")
        self.refreshBtn.clicked.connect(self._on_refresh)
        header.addWidget(self.refreshBtn)
        self.expandLayout.addLayout(header)

        # 处理器 / 内存 占用环
        ring_row = QHBoxLayout()
        ring_row.setSpacing(20)
        self.cpuRing, self.cpuValue = _make_ring("处理器", ring_row)
        self.memRing, self.memValue = _make_ring("内存", ring_row)
        self.expandLayout.addLayout(ring_row)

        # 分组卡片
        self.cpuGroup = SettingCardGroup("处理器与内存", self.scrollWidget)
        self.cpuCard = _info_card(FIF.SPEED_HIGH, "处理器", "查询中…", self.cpuGroup)
        self.memCard = _info_card(FIF.INFO, "内存", "查询中…", self.cpuGroup)
        for c in (self.cpuCard, self.memCard):
            self.cpuGroup.addSettingCard(c)

        self.hwGroup = SettingCardGroup("硬件", self.scrollWidget)
        self.gpuCard = _info_card(FIF.VIDEO, "显卡", "查询中…", self.hwGroup)
        self.boardCard = _info_card(FIF.IOT, "主板", "查询中…", self.hwGroup)
        self.biosCard = _info_card(FIF.SETTING, "BIOS", "查询中…", self.hwGroup)
        for c in (self.gpuCard, self.boardCard, self.biosCard):
            self.hwGroup.addSettingCard(c)

        self.osGroup = SettingCardGroup("操作系统与网络", self.scrollWidget)
        self.osCard = _info_card(FIF.INFO, "操作系统", "查询中…", self.osGroup)
        self.netCard = _info_card(FIF.GLOBE, "网络", "查询中…", self.osGroup)
        for c in (self.osCard, self.netCard):
            self.osGroup.addSettingCard(c)

        self.diskGroup = SettingCardGroup("磁盘", self.scrollWidget)
        self.diskCard = _info_card(FIF.TILES, "磁盘分区", "查询中…", self.diskGroup)
        self.diskGroup.addSettingCard(self.diskCard)

        for g in (self.cpuGroup, self.hwGroup, self.osGroup, self.diskGroup):
            self.expandLayout.addWidget(g)
        self.expandLayout.addStretch(1)

    # ── 信息刷新 ────────────────────────────────────────
    def _refresh_usage(self):
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            self.cpuRing.setValue(int(cpu))
            self.cpuValue.setText(f"{cpu:.0f}%")
            self.memRing.setValue(int(mem.percent))
            self.memValue.setText(f"{mem.percent:.0f}%")
        except Exception as e:
            module_logger.error(f"占用率刷新失败: {e}")

    def _refresh_static(self):
        # 处理器
        cores = psutil.cpu_count(logical=False) or "?"
        threads = psutil.cpu_count(logical=True) or "?"
        freq = psutil.cpu_freq()
        freq_text = f" @ {freq.max / 1000:.2f}GHz" if freq and freq.max else ""
        self.cpuCard.setContent(
            f"{_cpu_name()}{freq_text}\n{cores} 核心 / {threads} 线程")

        # 内存
        mem = psutil.virtual_memory()
        self.memCard.setContent(
            f"总容量 {_human_size(mem.total)}，"
            f"可用 {_human_size(mem.available)}（已用 {mem.percent}%）")

        # 操作系统
        os_info = _os_info()
        self.osCard.setContent("\n".join(f"{k}：{v}" for k, v in os_info.items()))

        # 网络
        try:
            host = socket.gethostname()
            ips = _lan_ips()
            adapters = list(psutil.net_if_addrs().keys())
            self.netCard.setContent(
                f"主机名：{host}\nIP：{', '.join(ips) or '未联网'}\n"
                f"适配器：{', '.join(adapters[:6])}{'…' if len(adapters) > 6 else ''}")
        except Exception as e:
            self.netCard.setContent(f"网络信息获取失败：{e}")

        # 磁盘
        lines = []
        for p in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(p.mountpoint)
                lines.append(f"{p.device} {_human_size(usage.total)}（剩余 "
                             f"{_human_size(usage.free)}）{p.fstype}")
            except (PermissionError, OSError):
                continue
        self.diskCard.setContent("\n".join(lines) or "未检测到分区")

    def _start_cim_query(self):
        """显卡/主板/BIOS：注册表直查（即时），查不到的字段再用 CIM 兜底。"""
        reg = _registry_hardware()
        self._apply_cim_result(reg)
        missing = (not reg["gpu"]) or (not reg["board"]) or (not reg["bios"])
        if not missing:
            return

        def _work():
            result = _cim_query({
                "gpu": ("Win32_VideoController", ["Name", "AdapterRAM", "DriverVersion"]),
                "board": ("Win32_BaseBoard", ["Manufacturer", "Product"]),
                "bios": ("Win32_BIOS", ["Manufacturer", "SMBIOSBIOSVersion", "ReleaseDate"]),
            })
            self.cimReady.emit(result)

        threading.Thread(target=_work, daemon=True, name="sysinfo-cim").start()

    def _apply_cim_result(self, result: dict):
        try:
            gpus = result.get("gpu") or []
            gpu_lines = []
            for g in gpus:
                if isinstance(g, dict):
                    ram = int(g.get("mem") or g.get("AdapterRAM") or 0)
                    name = g.get("name") or g.get("Name") or "未知"
                    mem_text = f"（{_human_size(ram)} 显存）" if ram > 0 else ""
                    gpu_lines.append(f"{name}{mem_text}")
                else:
                    gpu_lines.append(str(g))
            if gpu_lines:
                self.gpuCard.setContent("\n".join(gpu_lines))

            board = result.get("board") or ""
            if isinstance(board, list):
                b = (board or [{}])[0]
                board = f"{b.get('Manufacturer', '')} {b.get('Product', '')}".strip()
            if board:
                self.boardCard.setContent(board)

            bios = result.get("bios") or ""
            if isinstance(bios, list):
                bios = (bios or [{}])[0]
                bios = f"{bios.get('Manufacturer', '')} {bios.get('SMBIOSBIOSVersion', '')}".strip()
            if bios:
                self.biosCard.setContent(bios)
        except Exception as e:
            module_logger.error(f"硬件信息应用失败: {e}")

    def _on_refresh(self):
        self._refresh_static()
        self._start_cim_query()
        InfoBar.success("已刷新", "配置信息已更新", parent=self,
                        position=InfoBarPosition.TOP, duration=2000)


def _make_ring(title: str, layout: QHBoxLayout):
    """在 layout 中放一个 ProgressRing + 标签组，返回 (ring, value_label)。"""
    box = QFrame()
    box_lay = QHBoxLayout(box)
    box_lay.setContentsMargins(8, 0, 8, 0)
    ring = ProgressRing()
    ring.setFixedSize(72, 72)
    ring.setTextVisible(True)
    box_lay.addWidget(ring)
    text_col = QVBoxLayout()
    title_label = SubtitleLabel(title)
    value_label = BodyLabel("…")
    text_col.addWidget(title_label)
    text_col.addWidget(value_label)
    box_lay.addLayout(text_col)
    layout.addWidget(box)
    return ring, value_label


def _info_card(icon, title: str, content: str, parent) -> PushSettingCard:
    card = PushSettingCard("复制", icon, title, content, parent)
    card.clicked.connect(lambda: _copy_card(card))
    return card


def _copy_card(card: PushSettingCard):
    try:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(card.contentLabel.text())
        InfoBar.success("已复制", f"「{card.titleLabel.text()}」信息已复制到剪贴板",
                        parent=card.parent(), position=InfoBarPosition.TOP, duration=2000)
    except Exception as e:
        module_logger.error(f"复制失败: {e}")


def _lan_ips() -> list:
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


class SysInfoWidget(QFrame):
    """导航容器。"""

    def __init__(self, name: str, parent=None):
        super().__init__(parent=parent)
        self.setObjectName(name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.sysInfoInterface = SysInfoInterface(self)
        layout.addWidget(self.sysInfoInterface)
