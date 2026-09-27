# NoMY 客户端 / NoMY Client

> 桌面客户端：Python + PySide6 + qfluentwidgets（Fluent 风格），Windows 平台。
> Desktop client: Python + PySide6 + qfluentwidgets (Fluent style), Windows.

---

## 中文

### 目录结构

| 目录/文件 | 说明 |
|---|---|
| `fluwidget.py` | **主入口**：FluentWindow 主窗口、导航注册、系统托盘、崩溃日志（`LOG/crash.log` + psutil 快照）、更新流程 |
| `src_ui/` | 各功能页面，一文件一功能（详见下表） |
| `src/` | 基础设施：`auth_client.py`（服务端 REST API 封装）、`net_config.py`、`Config_ini.py`、版本信息 |
| `core/` | 插件系统：`plugin_api.py`（`PluginBase`/`HostApi`）、`plugin_manager.py`（发现与热重载） |
| `plugins/` | 外置插件目录，`example_tool/` 为模板 |
| `Config/` | `Aisetting.json`（AI 配置）、`config.json`（登录令牌等）、`NetConfig.json`（服务器地址）、`Version.ini`（版本/更新信息）、图标与 QSS 主题 |
| `LOG/` | `app_log.log` / `crash.log` / `startup.log` |
| `build_app.bat` | 打包脚本（onedir，产物 `dist\NoMY\NoMY.exe`） |

### 功能模块（src_ui/）

| 文件 | 功能 |
|---|---|
| `home_ui.py` | 首页卡片导航 |
| `ai_chat_app.py` | AI 对话（OpenAI 兼容 API、流式输出、Markdown 渲染、多会话历史存 `chat_histories/`） |
| `memo_ui.py` | 富文本备忘录（粘贴截图、超链接、拖拽排序、自动保存） |
| `friends_ui.py` | 好友/私聊/群聊（微信式界面、图片消息、好友请求、备注、在线状态） |
| `moments_ui.py` | 朋友圈（图文动态、好友 feed、删除自己的动态、图片查看器） |
| `image_studio.py` | 图片工坊（调色滑杆、曲线编辑、直方图、滤镜、实时预览渲染线程） |
| `music_player.py` / `video_player.py` | 本地音乐/视频播放器 |
| `wallpaper_ui.py` | 壁纸设置（静态/双屏独立/跨屏拼接/定时轮播；双屏依赖系统 `IDesktopWallpaper`，不可用时降级单壁纸） |
| `sysinfo_ui.py` | 电脑配置信息 |
| `Extended_call_script_ui.py` | 启动器（程序/脚本快捷启动，分页） |
| `login_dialog.py` / `user_setting_ui.py` | 登录/注册/找回密码；用户设置（改密、显示名、退出登录） |
| `setting_ui.py` | 设置页（菜单排序/分组、外观、更新检查） |
| `update_manager.py` / `update_dialog.py` | 检查更新、下载安装包、自动安装 |
| `information_history.py` | InfoBar 通知封装 |

### 运行

```bash
# 1. 安装依赖（在仓库根目录）
pip install -r requirements.txt

# 2. 在 client_code/ 目录下运行（依赖 ./Config、./LOG 相对路径）
cd client_code
python fluwidget.py
```

**配置说明：**

- AI 对话：`Config/Aisetting.json` 配置 `api_key` 与 `base_url`（OpenAI 兼容接口）；
- 服务器地址：`Config/NetConfig.json`（`server_url`/`port`/`use_ssl`/`api_prefix`，认证与更新同址）；未配置 `server_url` 时回退 `Config/Version.ini` 的 `VERSION_NEW_SERVER`；
- 界面尺寸、记住登录等保存在 `Config/config.json`。

### 登录说明

- 注册采用邮箱验证码（服务端 SMTP 发码），密码要求 **至少 8 位且包含字母/数字/符号中的两类**；
- 每个账号有 8 位用户 ID（00000001 起），ID 与邮箱绑定，可凭邮箱验证码找回密码；
- 勾选「记住登录」后令牌保存在本地，7 天内滑动续期免输密码（绝对寿命 30 天）；
- **修改密码后所有旧令牌注销**，当前设备自动换用新令牌，其他设备需重新登录；
- 服务器不可达时可凭本地令牌「离线进入」；游客模式可使用本地功能（好友/朋友圈需登录）；
- 找回密码对未绑定邮箱也会返回「已发送」（防邮箱枚举），未绑定的邮箱不会收到邮件。

### 插件开发

1. 新建 `plugins/<插件名>/main.py`，实现 `core.plugin_api.PluginBase` 子类；
2. 主界面「插件」页点击「重新加载插件」即可热重载；
3. 模板见 `plugins/example_tool/main.py`（含宿主接口 `HostApi` 用法）；
4. 插件需要的第三方库按插件自带 `requirements.txt` 单独安装。

### 打包

```bash
build_app.bat
# 产物：dist\NoMY\NoMY.exe + 同目录依赖库/资源（含 Config 与 plugins 目录）
# 整个 NoMY 文件夹拷贝到目标机器即可运行
```

> 分发给他人前请清理 `Config/config.json` 中的个人令牌。

---

## English

### Layout

| Path | Description |
|---|---|
| `fluwidget.py` | **Entry point**: FluentWindow shell, navigation, system tray, crash logging (`LOG/crash.log` + psutil snapshot), update flow |
| `src_ui/` | Feature pages, one file per feature (see table below) |
| `src/` | Infrastructure: `auth_client.py` (REST client for the server), `net_config.py`, `Config_ini.py`, version info |
| `core/` | Plugin system: `plugin_api.py` (`PluginBase`/`HostApi`), `plugin_manager.py` (discovery & hot reload) |
| `plugins/` | External plugins; `example_tool/` is the template |
| `Config/` | `Aisetting.json` (AI), `config.json` (stored token etc.), `NetConfig.json` (server address), `Version.ini` (version/update info), icons & QSS themes |
| `LOG/` | `app_log.log` / `crash.log` / `startup.log` |
| `build_app.bat` | Packaging script (one-dir → `dist\NoMY\NoMY.exe`) |

### Feature Modules (`src_ui/`)

| File | Feature |
|---|---|
| `home_ui.py` | Home cards |
| `ai_chat_app.py` | AI chat (OpenAI-compatible, streaming, Markdown, session history in `chat_histories/`) |
| `memo_ui.py` | Rich-text memo (paste images, hyperlinks, drag-sort, autosave) |
| `friends_ui.py` | Friends / private & group chat (WeChat-style, image messages, requests, remarks, presence) |
| `moments_ui.py` | Moments feed (image posts, friends feed, delete own posts, image viewer) |
| `image_studio.py` | Image studio (sliders, curves, histogram, filters, threaded live preview) |
| `music_player.py` / `video_player.py` | Local music/video players |
| `wallpaper_ui.py` | Wallpapers (static / per-monitor / spanning / timed rotation; per-monitor needs `IDesktopWallpaper`, falls back to single wallpaper) |
| `sysinfo_ui.py` | Hardware & OS info |
| `Extended_call_script_ui.py` | Launcher for programs/scripts |
| `login_dialog.py` / `user_setting_ui.py` | Login / register / recovery; user settings (change password, display name, logout) |
| `setting_ui.py` | Settings (menu order/groups, appearance, update check) |
| `update_manager.py` / `update_dialog.py` | Update check, download, auto-install |
| `information_history.py` | InfoBar helpers |

### Run

```bash
# 1. Install deps (from repo root)
pip install -r requirements.txt

# 2. Run from client_code/ (uses ./Config, ./LOG relative paths)
cd client_code
python fluwidget.py
```

**Configuration:**

- AI chat: set `api_key` and `base_url` (OpenAI-compatible) in `Config/Aisetting.json`;
- Server address: `Config/NetConfig.json` (`server_url`/`port`/`use_ssl`/`api_prefix`; auth + update share the host); falls back to `Config/Version.ini` (`VERSION_NEW_SERVER`) when `server_url` is empty;
- Window size / remember-login and similar persist in `Config/config.json`.

### Login

- Registration uses an emailed code (server-side SMTP). Passwords must be **≥8 chars with at
  least two of letters/digits/symbols**.
- Each account has an 8-digit user ID (from 00000001), bound to an email for password recovery.
- With "remember login", the token is stored locally: 7-day sliding renewal, 30-day absolute life.
- **Changing your password revokes every old token**; the current device rotates to a new token,
  other devices must sign in again.
- Offline entry with a local token when the server is unreachable; guest mode for local-only features.
- Password recovery always answers "sent" even for unbound emails (anti-enumeration); no mail is sent then.

### Plugin Development

1. Create `plugins/<name>/main.py` implementing `core.plugin_api.PluginBase`;
2. Hit "Reload Plugins" on the Plugins page to hot-reload;
3. See `plugins/example_tool/main.py` for the `HostApi` usage;
4. Install any third-party dependencies the plugin declares.

### Build

```bash
build_app.bat
# Output: dist\NoMY\NoMY.exe + sibling libs/resources (incl. Config and plugins)
# Copy the whole NoMY folder to the target machine and run.
```

> Clean personal tokens from `Config/config.json` before distributing the package.
