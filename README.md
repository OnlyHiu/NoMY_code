# NoMY

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 桌面应用，基于 Python + PySide6（qfluentwidgets Fluent 风格界面），运行于 Windows。
> 采用「客户端 + 自建服务端」架构，服务端核心为纯 Python 标准库（图形管理后台基于 PySide6）。
>
> Desktop application built with Python + PySide6 (qfluentwidgets Fluent-style UI) for Windows.
> Client/server architecture with a pure-stdlib Python server core (the GUI admin console is built with PySide6).

本项目以 **MIT License** 开源，详见 [LICENSE](LICENSE)。
所依赖的第三方库及各自许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

## 中文

### 项目结构

```
no-mycode/
├── client_code/            # 桌面客户端（PySide6 + qfluentwidgets）
│   ├── fluwidget.py        # 主入口（主窗口、导航、托盘、崩溃日志）
│   ├── src_ui/             # 各功能页面（AI对话/备忘/好友/朋友圈/图片工坊/播放器/壁纸等）
│   ├── src/                # 基础设施（认证客户端、网络配置、版本信息）
│   ├── core/               # 插件系统（PluginBase / HostApi / 热重载）
│   ├── plugins/            # 外置插件目录（含 example_tool 模板）
│   ├── Config/             # 配置文件、图标资源、明暗主题 QSS
│   └── LOG/                # 运行日志 / 崩溃日志 / 启动日志
├── server_code/
│   ├── server/             # Windows 服务端（含 GUI 管理后台）
│   └── server_linux/       # Linux 服务端（含 systemd 单元）
├── requirements.txt        # 客户端 Python 依赖
└── 技术框架文档.md          # 详细架构文档（含核心机制、关键文件索引）
```

各子项目详细说明：

- 客户端：[`client_code/README.md`](client_code/README.md)
- Windows 服务端：[`server_code/server/README.md`](server_code/server/README.md)
- Linux 服务端：[`server_code/server_linux/README.md`](server_code/server_linux/README.md)

### 功能总览

| 功能 | 说明 |
|---|---|
| 登录认证 | 邮箱验证码注册、启动登录（可游客进入，好友/朋友圈需登录）、令牌 7 天滑动/30 天绝对有效期、离线进入、忘记密码（数据存 SQLite） |
| AiChat | AI 模型对话（OpenAI 兼容 API，流式输出，多会话历史） |
| 备忘录 | 富文本备忘（粘贴截图、超链接、自动保存、空态引导页） |
| 启动器 | 常用程序/脚本独立页面，PipsPager 分页切换 |
| 好友 | 服务器中转聊天（微信式界面）：加好友需对方同意、文本/图片消息、在线状态 |
| 朋友圈 | 与好友联动：发布图文动态、浏览好友动态、删除自己的动态 |
| 图片工坊 | Photoshop 式调色修图（亮度/对比度/饱和度/色温/高光/阴影/清晰度 + 滤镜 + 曲线） |
| 音乐播放器 | 本地音乐播放（列表/模式/进度/音量） |
| 视频播放器 | 本地视频播放（进度/倍速/全屏/截图） |
| 电脑配置 | 本机硬件与系统信息一览 |
| 壁纸设置 | 静态/双屏独立/一图跨屏拼接/定时轮播动态壁纸 |
| 插件系统 | 外置插件热重载（`plugins/` 目录） |

### 快速开始

**客户端：**

```bash
pip install -r requirements.txt
cd client_code
python fluwidget.py          # 必须在 client_code/ 目录下运行（依赖 ./Config、./LOG 相对路径）
```

- AI 功能需在 `client_code/Config/Aisetting.json` 配置 `api_key` 与 `base_url`；
- 认证/更新服务器地址配置于 `client_code/Config/NetConfig.json`（`server_url`/`port`/`use_ssl`/`api_prefix`），未配置 `server_url` 时回退 `client_code/Config/Version.ini` 的 `VERSION_NEW_SERVER`；
- 服务器不可达时可凭本地令牌「离线进入」，或以游客身份使用本地功能。

**服务端（以 Windows 版为例）：**

```bash
cd server_code/server
python distribute_server_gui.py        # 图形管理后台（推荐）
python distribute_server.py --port 18888   # 命令行版
```

### 安全须知（部署必读）

- **生产环境必须启用 HTTPS**：`--tls-cert/--tls-key` 指定 PEM 证书，或置于 TLS 反向代理之后；
  未启用 TLS 且监听非本机地址时服务端会打印显著告警（口令/令牌明文传输可被同网段嗅探）。
- 密码策略：至少 8 位，且包含字母/数字/符号中的两类；存储为 PBKDF2-HMAC-SHA256（12 万次迭代加盐）。
- 会话令牌：256 位密码学随机数，磁盘仅存哈希；7 天滑动 + 30 天绝对过期；修改密码后全部旧令牌注销并轮换新令牌。
- 邮箱验证码：8 位、10 分钟有效、连续输错 5 次作废、发信限流防轰炸；找回密码对未绑定邮箱统一响应防枚举。
- 文件取回按「消息收发关系/好友关系」授权，防止横向越权读取他人图片。
- SMTP 授权码加密落盘（`server_data/secret.key` 为密钥，请一并备份；也可用环境变量 `NOMY_SMTP_PASSWORD` 注入）。

### 打包

```bash
# 客户端 → dist\NoMY\NoMY.exe（build_app.bat 使用 Nuitka standalone 目录模式）
cd client_code && build_app.bat

# 服务端 → server\dist\NoMY-server\（命令行版）/ NoMY-ServerGUI\（图形版）
cd server_code/server && build_exe.bat        # 或 build_exe.bat gui
```

### 插件开发

插件放置于 `client_code/plugins/<插件名>/main.py`，实现 `PluginBase` 子类，主界面点击「重新加载插件」即可热重载。
模板见 `plugins/example_tool/`；插件如需额外第三方库请按其自带 `requirements.txt` 单独安装。

---

## English

### Project Structure

```
no-mycode/
├── client_code/            # Desktop client (PySide6 + qfluentwidgets)
│   ├── fluwidget.py        # Entry point (main window, navigation, tray, crash log)
│   ├── src_ui/             # Feature pages (AI chat / memo / friends / moments / image studio / players / wallpaper…)
│   ├── src/                # Infrastructure (auth client, network config, version info)
│   ├── core/               # Plugin system (PluginBase / HostApi / hot reload)
│   ├── plugins/            # External plugins (incl. example_tool template)
│   ├── Config/             # Settings, icons, light/dark QSS themes
│   └── LOG/                # Runtime / crash / startup logs
├── server_code/
│   ├── server/             # Windows server (with GUI admin console)
│   └── server_linux/       # Linux server (with systemd unit)
├── requirements.txt        # Client Python dependencies
└── 技术框架文档.md          # Detailed architecture document (Chinese)
```

Per-component guides:

- Client: [`client_code/README.md`](client_code/README.md)
- Windows server: [`server_code/server/README.md`](server_code/server/README.md)
- Linux server: [`server_code/server_linux/README.md`](server_code/server_linux/README.md)

### Features

| Feature | Description |
|---|---|
| Auth | Email-code registration, login at startup (guest entry available; friends/moments require login), 7-day sliding / 30-day absolute tokens, offline mode, password recovery (SQLite) |
| AiChat | AI chat (OpenAI-compatible API, streaming, multi-session history) |
| Memo | Rich-text notes (paste screenshots, hyperlinks, autosave) |
| Launcher | Quick-launch page for programs/scripts with paged navigation |
| Friends | Server-relayed chat (WeChat-style): friend requests with approval, text/image messages, online status |
| Moments | Feed of friends' posts with images, publish/delete |
| Image Studio | Photoshop-style color grading (exposure/contrast/saturation/temperature/highlights/shadows/clarity + filters + curves) |
| Music Player | Local music playback |
| Video Player | Local video playback (progress/speed/fullscreen/screenshot) |
| System Info | Hardware and OS overview |
| Wallpaper | Static / per-monitor / spanning / timed rotating wallpapers |
| Plugins | Hot-reloadable external plugins |

### Quick Start

**Client:**

```bash
pip install -r requirements.txt
cd client_code
python fluwidget.py          # must run from client_code/ (uses ./Config, ./LOG relative paths)
```

- Configure `api_key` / `base_url` in `client_code/Config/Aisetting.json` for AI features;
- Server address is set in `client_code/Config/NetConfig.json` (`server_url`/`port`/`use_ssl`/`api_prefix`); when `server_url` is empty it falls back to `client_code/Config/Version.ini` (`VERSION_NEW_SERVER`);
- Offline entry with a local token is available when the server is unreachable; guest mode covers local-only features.

**Server (Windows example):**

```bash
cd server_code/server
python distribute_server_gui.py        # GUI admin console (recommended)
python distribute_server.py --port 18888   # CLI version
```

### Security Notes (read before deploying)

- **Enable HTTPS in production** via `--tls-cert/--tls-key` (PEM) or terminate TLS at a reverse proxy.
  The server prints a loud warning when listening in plaintext on a non-loopback address.
- Password policy: ≥8 chars with at least two of letters/digits/symbols; stored as PBKDF2-HMAC-SHA256 (120k iterations, per-user salt).
- Session tokens: 256-bit CSPRNG, stored hashed; 7-day sliding + 30-day absolute expiry; changing password revokes and rotates all tokens.
- Email codes: 8 digits, 10-minute TTL, invalidated after 5 wrong attempts, send-rate limited; password recovery responses are uniform to prevent email enumeration.
- File downloads are authorized by message/friend relationships to prevent horizontal privilege escalation.
- SMTP credentials are encrypted at rest (back up `server_data/secret.key`; or inject via `NOMY_SMTP_PASSWORD`).

### Build

```bash
# Client -> dist\NoMY\NoMY.exe (build_app.bat uses Nuitka standalone, one-dir)
cd client_code && build_app.bat

# Server -> server\dist\NoMY-server\ (CLI) / NoMY-ServerGUI\ (GUI)
cd server_code/server && build_exe.bat        # or build_exe.bat gui
```

### Plugin Development

Drop a plugin at `client_code/plugins/<name>/main.py` implementing `PluginBase`, then hit
"Reload Plugins" in the app. See `plugins/example_tool/` for the template; install any extra
third-party dependencies the plugin declares.
