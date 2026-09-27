# -*- coding: utf-8 -*-
"""音乐播放器（QtMultimedia 引擎）

本地音频播放：文件夹/单文件导入、播放列表、顺序/单曲/列表循环/随机、
进度拖动、音量、上一曲/下一曲。列表持久化到 Config/MusicPlaylist.json。
"""
import json
import logging
import os
import random

from PySide6.QtCore import QUrl, Qt, QTimer, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QStyle, QVBoxLayout, QWidget,
                               QHeaderView, QFileDialog)

from qfluentwidgets import (BodyLabel, CaptionLabel, FluentIcon as FIF,
                            IconWidget, PrimaryPushButton,
                            PushButton, Slider, SubtitleLabel, TableWidget,
                            TitleLabel, ToolButton)

module_logger = logging.getLogger("flu_widget.music_player")

PLAYLIST_FILE = "./Config/MusicPlaylist.json"
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".wma", ".aac")
MODES = ["顺序播放", "单曲循环", "列表循环", "随机播放"]


class ClickableSlider(Slider):
    """支持点击定位的进度条。"""
    seekRequested = Signal(int)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > 0:
            pos = event.position().toPoint()
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), pos.x(), max(self.width() - 1, 1))
            self.setValue(value)
            self.seekRequested.emit(value)
        super().mousePressEvent(event)


def _fmt_ms(ms: int) -> str:
    if ms <= 0:
        return "00:00"
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


class MusicPlayerWidget(QFrame):
    """音乐播放器主界面。"""

    def __init__(self, name: str, parent=None):
        super().__init__(parent=parent)
        self.setObjectName(name)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.7)
        self.playlist: list[dict] = []   # [{path, title}]
        self.current_index = -1
        self.mode_index = 0
        self._last_volume = 70  # 静音前记住的音量，取消静音时恢复
        self._pending_play = False  # 等待 LoadedMedia 后再 play() 的标志
        self._pending_play_token = 0  # 配合 _flush_pending_play 防止 stale 触发

        self._build_ui()
        self._bind()
        self._load_playlist()

    # ── UI ─────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 16)
        layout.setSpacing(12)

        # 上：播放区
        left = QVBoxLayout()
        left.setSpacing(10)
        layout.addLayout(left)

        art_row = QHBoxLayout()
        art_row.addStretch(1)
        self.coverIcon = IconWidget(FIF.MUSIC, self)
        self.coverIcon.setFixedSize(120, 120)
        art_row.addWidget(self.coverIcon)
        art_row.addStretch(1)
        left.addLayout(art_row)

        self.titleLabel = TitleLabel("未在播放")
        left.addWidget(self.titleLabel, 0, Qt.AlignmentFlag.AlignHCenter)
        self.statusLabel = CaptionLabel("选择本地音乐开始播放（支持 mp3/wav/flac/ogg/m4a 等系统可解码格式）")
        left.addWidget(self.statusLabel, 0, Qt.AlignmentFlag.AlignHCenter)

        # 进度
        progress_row = QHBoxLayout()
        self.posLabel = CaptionLabel("00:00")
        self.progressSlider = ClickableSlider(Qt.Orientation.Horizontal)
        self.progressSlider.setRange(0, 0)
        self.durLabel = CaptionLabel("00:00")
        progress_row.addWidget(self.posLabel)
        progress_row.addWidget(self.progressSlider, 1)
        progress_row.addWidget(self.durLabel)
        left.addLayout(progress_row)

        # 控制按钮
        ctrl_row = QHBoxLayout()
        ctrl_row.addStretch(1)
        self.prevBtn = ToolButton(FIF.PAGE_LEFT)
        self.prevBtn.setToolTip("上一曲")
        self.prevBtn.clicked.connect(self.play_previous)
        ctrl_row.addWidget(self.prevBtn)
        self.playBtn = PrimaryPushButton(FIF.PLAY, "播 放")
        self.playBtn.setFixedWidth(120)
        self.playBtn.clicked.connect(self._on_toggle_play)
        ctrl_row.addWidget(self.playBtn)
        self.nextBtn = ToolButton(FIF.PAGE_RIGHT)
        self.nextBtn.setToolTip("下一曲")
        self.nextBtn.clicked.connect(self.play_next)
        ctrl_row.addWidget(self.nextBtn)
        self.muteBtn = ToolButton(FIF.VOLUME)
        self.muteBtn.setToolTip("静音")
        self.muteBtn.clicked.connect(self._on_toggle_mute)
        ctrl_row.addWidget(self.muteBtn)
        self.volumeSlider = ClickableSlider(Qt.Orientation.Horizontal)
        self.volumeSlider.setRange(0, 100)
        self.volumeSlider.setValue(70)
        self.volumeSlider.setFixedWidth(120)
        self.volumeSlider.valueChanged.connect(self._on_volume_changed)
        ctrl_row.addWidget(self.volumeSlider)
        self.volumeLabel = CaptionLabel("70%")
        ctrl_row.addWidget(self.volumeLabel)
        ctrl_row.addStretch(1)
        left.addLayout(ctrl_row)

        # 播放模式：四个独立可选中的按钮，选中即高亮
        mode_row = QHBoxLayout()
        mode_row.addStretch(1)
        from PySide6.QtWidgets import QButtonGroup
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        for i, name in enumerate(MODES):
            b = PushButton(name)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setFixedHeight(30)
            self._mode_group.addButton(b, i)
            mode_row.addWidget(b)
        mode_row.addStretch(1)
        self._mode_group.idClicked.connect(self._on_mode_clicked)
        left.addLayout(mode_row)

        # 下：播放列表
        right = QVBoxLayout()
        right.setSpacing(8)
        layout.addLayout(right, 1)

        list_header = QHBoxLayout()
        list_header.addWidget(SubtitleLabel("播放列表"))
        list_header.addStretch(1)
        btn_add_files = PushButton(FIF.ADD, "添加文件")
        btn_add_files.clicked.connect(self._on_add_files)
        list_header.addWidget(btn_add_files)
        btn_add_dir = PushButton(FIF.FOLDER_ADD, "添加文件夹")
        btn_add_dir.clicked.connect(self._on_add_folder)
        list_header.addWidget(btn_add_dir)
        btn_remove = ToolButton(FIF.DELETE)
        btn_remove.setToolTip("移除选中")
        btn_remove.clicked.connect(self._on_remove_selected)
        list_header.addWidget(btn_remove)
        btn_clear = ToolButton(FIF.BROOM)
        btn_clear.setToolTip("清空列表")
        btn_clear.clicked.connect(self._on_clear_playlist)
        list_header.addWidget(btn_clear)
        right.addLayout(list_header)

        self.table = TableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["标题", "时长"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 90)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.doubleClicked.connect(self._on_table_double_clicked)
        right.addWidget(self.table, 1)

    def _bind(self):
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.errorOccurred.connect(self._on_error)
        self.progressSlider.sliderMoved.connect(self._on_seek)
        self.progressSlider.seekRequested.connect(self._on_seek)

    # ── 播放列表管理 ────────────────────────────────────
    def _load_playlist(self):
        try:
            with open(PLAYLIST_FILE, "r", encoding="utf-8") as f:
                items = json.load(f)
            for p in items or []:
                if os.path.isfile(p):
                    self._append_track(p)
        except Exception:
            pass
        self._refresh_table()

    def _save_playlist(self):
        try:
            with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
                json.dump([t["path"] for t in self.playlist], f,
                          ensure_ascii=False, indent=2)
        except Exception as e:
            module_logger.error(f"保存播放列表失败: {e}")

    def _append_track(self, path: str):
        self.playlist.append({"path": path, "title": os.path.splitext(os.path.basename(path))[0]})

    def _refresh_table(self):
        self.table.setRowCount(len(self.playlist))
        for r, t in enumerate(self.playlist):
            mark = "▶ " if r == self.current_index else ""
            self.table.setItem(r, 0, _tw_item(mark + t["title"]))
            self.table.setItem(r, 1, _tw_item("-"))

    # ── 导入 ────────────────────────────────────────────
    def _on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择音乐文件", "", f"音频文件 ({' '.join('*' + e for e in AUDIO_EXTS)})")
        added = False
        for p in files:
            self._append_track(p)
            added = True
        if added:
            self._after_playlist_change()

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择音乐文件夹")
        if not folder:
            return
        added = False
        for root, _dirs, files in os.walk(folder):
            for f in sorted(files):
                if f.lower().endswith(AUDIO_EXTS):
                    self._append_track(os.path.join(root, f))
                    added = True
        if added:
            self._after_playlist_change()
        else:
            self.statusLabel.setText("该文件夹中没有可播放的音频文件")

    def _after_playlist_change(self):
        self._refresh_table()
        self._save_playlist()

    def _on_remove_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.playlist):
            return
        was_current = row == self.current_index
        self.playlist.pop(row)
        self.table.removeRow(row)
        if was_current:
            self.player.stop()
            self.current_index = -1
            self._update_now_playing()
        elif row < self.current_index:
            self.current_index -= 1
        self._save_playlist()

    def _on_clear_playlist(self):
        self.player.stop()
        self.playlist.clear()
        self.current_index = -1
        self._refresh_table()
        self._update_now_playing()
        self._save_playlist()

    # ── 播放控制 ────────────────────────────────────────
    def _on_table_double_clicked(self, index):
        self._play_at(index.row())

    def _play_at(self, index: int):
        if not (0 <= index < len(self.playlist)):
            return
        self.current_index = index
        path = self.playlist[index]["path"]
        # 先 stop 把上一次状态彻底清理，再换源。Qt 后端的解码器是懒加载，
        # 第一次 setSource 后立刻 play() 会在解码器还没 warm up 时被丢弃，
        # 表现为"双击没反应"。监听 mediaStatusChanged 的 LoadedMedia 状态
        # 再触发 play，能避开这个冷启动坑；同时加 fallback：超过 800 ms
        # LoadedMedia 仍未触发就直接 play()，免得在 fast-startup 文件上白等。
        self.player.stop()
        self._pending_play = True
        self._pending_play_token += 1
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.setLoops(1)
        self._pending_play_token += 1
        QTimer.singleShot(800, lambda tok=self._pending_play_token: self._flush_pending_play(tok))
        self._refresh_table()
        self._update_now_playing()

    def _flush_pending_play(self, token: int):
        if token != self._pending_play_token:
            return
        if not self._pending_play:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._pending_play = False
            return
        self._pending_play = False
        self.player.play()

    def play_next(self):
        if not self.playlist:
            return
        if self.mode_index == 1:  # 单曲循环
            self._play_at(max(self.current_index, 0))
            return
        if self.mode_index == 3:  # 随机
            self._play_at(random.randrange(len(self.playlist)))
            return
        nxt = (self.current_index + 1) % len(self.playlist)
        if self.mode_index == 0 and nxt == 0 and self.current_index == len(self.playlist) - 1:
            self.player.stop()  # 顺序播放到末尾停止
            return
        self._play_at(nxt)

    def play_previous(self):
        if not self.playlist:
            return
        if self.mode_index == 3:
            self._play_at(random.randrange(len(self.playlist)))
            return
        self._play_at((self.current_index - 1) % len(self.playlist))

    def _on_toggle_play(self):
        if not self.playlist:
            self.statusLabel.setText("请先添加音乐文件")
            return
        if self.current_index < 0:
            self._play_at(0)
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_mode_clicked(self, index: int):
        self.mode_index = index
        self.statusLabel.setText(f"播放模式：{MODES[index]}")

    def _on_volume_changed(self, v: int):
        self.audio.setVolume(v / 100)
        self.volumeLabel.setText(f"{v}%")
        if v > 0:
            self._last_volume = v
        if v == 0:
            self.audio.setMuted(True)
        elif self.audio.isMuted():
            self.audio.setMuted(False)
        self._update_volume_icon(v)

    def _update_volume_icon(self, v: int):
        muted = self.audio.isMuted() or v == 0
        self.muteBtn.setIcon(FIF.MUTE if muted else FIF.VOLUME)
        self.muteBtn.setToolTip("取消静音" if muted else "静音")

    def _on_toggle_mute(self):
        if self.audio.isMuted():
            # 取消静音：恢复之前的音量（_on_volume_changed 内会解除静音并刷新图标）
            self.volumeSlider.setValue(max(self._last_volume, 1))
        else:
            # 静音：记住当前音量，滑条归 0（_on_volume_changed 内会置为静音）
            self.volumeSlider.setValue(0)

    # ── 播放器事件 ──────────────────────────────────────
    def _on_position_changed(self, pos: int):
        if not self.progressSlider.isSliderDown():
            self.progressSlider.blockSignals(True)
            self.progressSlider.setValue(pos)
            self.progressSlider.blockSignals(False)
        self.posLabel.setText(_fmt_ms(pos))

    def _on_seek(self, pos: int):
        self.player.setPosition(pos)

    def _on_duration_changed(self, dur: int):
        self.progressSlider.blockSignals(True)
        self.progressSlider.setRange(0, max(dur, 0))
        self.progressSlider.blockSignals(False)
        self.durLabel.setText(_fmt_ms(dur))
        row = self.current_index
        if 0 <= row < len(self.playlist):
            self.table.setItem(row, 1, _tw_item(_fmt_ms(dur)))

    def _on_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.playBtn.setText("暂 停" if playing else "播 放")
        self.playBtn.setIcon(FIF.PAUSE if playing else FIF.PLAY)

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            # 解码器已完成媒体解析。在此处 play() 能避开首次冷启动被丢弃的问题。
            if self._pending_play:
                self._pending_play = False
                self.player.play()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_next()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            # 部分 MP3（VBR/损坏/CBR 头异常）Qt 后端解码失败，状态停在
            # InvalidMedia 但不触发 errorOccurred。自动跳到下一首避免卡死。
            self._pending_play = False
            current = (self.playlist[self.current_index]["title"]
                       if 0 <= self.current_index < len(self.playlist) else "?")
            self.statusLabel.setText(f"无法播放：{current}（已自动跳过）")
            self.play_next()

    def _on_error(self, err, msg=""):
        from PySide6.QtMultimedia import QMediaPlayer as _QP
        names = {
            _QP.Error.NoError: "无错误",
            _QP.Error.ResourceError: "资源错误（文件可能已损坏或被占用）",
            _QP.Error.FormatError: "格式错误（编解码器不支持）",
            _QP.Error.NetworkError: "网络错误",
            _QP.Error.AccessDeniedError: "访问被拒绝",
        }
        current = (self.playlist[self.current_index]["title"]
                   if 0 <= self.current_index < len(self.playlist) else "?")
        self.statusLabel.setText(f"播放出错：{names.get(err, err)} - {current}")
        module_logger.warning(f"播放出错 {err} ({msg})：{current}")
        # 自动跳过，避免卡在同一首死文件上
        if self.playlist:
            self.play_next()

    def _update_now_playing(self):
        if 0 <= self.current_index < len(self.playlist):
            self.titleLabel.setText(self.playlist[self.current_index]["title"])
        else:
            self.titleLabel.setText("未在播放")
            self.statusLabel.setText("选择本地音乐开始播放")


def _tw_item(text: str):
    from PySide6.QtWidgets import QTableWidgetItem
    return QTableWidgetItem(text)
