# NoMY 服务端（Windows）/ NoMY Server (Windows)

> 纯 Python 标准库实现的 HTTP 服务：账号认证、更新分发、好友/聊天/朋友圈 API、静态信息展示页。
> 同时提供 PySide6 图形管理后台。
>
> Pure-stdlib HTTP server: auth, update distribution, social (friends/chat/moments) API and a
> static info page — plus a PySide6 GUI admin console.

---

## 中文

### 文件说明

| 文件 | 说明 |
|---|---|
| `distribute_server.py` | HTTP 主服务（`ThreadingHTTPServer`）：路由、IP 白名单、TLS、限流 |
| `distribute_server_gui.py` | 图形管理后台（服务控制/用户管理/在线会话/运行日志） |
| `auth_store.py` | 账号与会话存储（PBKDF2 密码、令牌会话、防爆破、验证码、SMTP 配置） |
| `chat_api.py` | 好友/私聊/群聊/朋友圈/文件取回 API |
| `db.py` | SQLite 数据库（users/friends/messages/groups/moments） |
| `mail_service.py` | SMTP 邮件（验证码邮件、测试邮件） |
| `security.py` | 限流器、图片魔数校验、敏感配置落盘加密 |
| `web/index.html` | 软件信息展示页（浏览器访问服务器根地址） |
| `build_exe.bat` | Nuitka 打包脚本（standalone 目录模式） |
| `部署文档-Windows.md` | Windows 部署 / HTTPS / 开机自启 |

### 运行

**图形管理后台（推荐）：**

```bash
python distribute_server_gui.py
```

五个页面：**服务控制**（启动/停止、端口、允许子网、安装包、版本号覆盖、开放注册开关）、
**用户管理**（新建账号/重置密码/禁用/删除）、**邮箱设置**（SMTP 发信配置/授权码）、
**在线会话**（查看/踢下线）、**运行日志**。
打开界面会自动启动服务；账号/会话/聊天数据保存在 exe 旁 `server_data\`。

**命令行版：**

```bash
python distribute_server.py --port 18888                      # 仅认证（无安装包）
python distribute_server.py --package setup.exe --port 18888  # 认证 + 更新分发
python distribute_server.py --no-registration                 # 关闭开放注册
python distribute_server.py --tls-cert cert.pem --tls-key key.pem   # 启用 HTTPS
```

### API 概览

| 路由 | 说明 |
|---|---|
| `POST /api/auth/register` `login` `verify` `logout` | 注册 / 登录 / 令牌校验 / 注销 |
| `POST /api/auth/send_code` `reset_password` `change_password` | 邮箱验证码 / 找回密码 / 修改密码 |
| `GET  /api/chat/poll` `POST /api/chat/send` `upload` | 消息轮询 / 发送 / 图片上传 |
| `GET/POST /api/friends/*` `/api/groups/*` `/api/moments/*` | 好友 / 群 / 朋友圈 |
| `GET  /api/files?p=…&token=…` | 图片取回（按收发关系/好友关系授权） |
| `GET  /api/update/check` `download` | 更新检查 / 安装包下载（公开） |
| `GET  /` `…` | `web/` 静态展示页 |
| `GET  /health` | 健康检查 |

### 数据与安全

**数据存储**（`server_data/`，位于 exe/脚本同目录）：

- `users` 等业务数据 → SQLite（首次启动自动从旧版 `users.json` 迁移）
- `sessions.json` → 会话（**只存令牌哈希**，文件泄露不等于会话泄露）
- `server_config.json` → 注册开关 / 邀请码 / SMTP 配置（**SMTP 授权码加密存储**）
- `secret.key` → 落盘加密密钥（0600 权限；**请与数据一并备份**，丢失后 SMTP 授权码需重填）
- `uploads/`、`moments/` → 聊天与朋友圈图片（按用户 ID 建目录、随机文件名）

**安全机制（已实现）：**

- 认证：PBKDF2-HMAC-SHA256（12 万次迭代 + 16 字节随机盐），比较使用 `hmac.compare_digest` 防时序攻击
- 密码策略：≥8 位且含字母/数字/符号中至少两类
- 会话：256 位 CSPRNG 令牌，7 天滑动 + **30 天绝对过期**；改密后全部旧令牌注销并轮换新令牌；单用户最多 5 个会话
- 验证码：8 位、10 分钟有效、5 次错误作废、60 秒冷却、单 IP/全局发信限流（防邮件轰炸）
- 防枚举：找回密码对未绑定邮箱统一返回成功；邮箱绑定冲突返回中性文案
- 限流：单 IP 每分钟普通 120 次 / 认证 20 次；**并发线程上限 200**（防连接洪水 DoS）；连接读超时 30 秒
- 文件授权：聊天图片按「消息收发关系」、朋友圈图片按「本人+好友」、头像按「好友/请求双方/群成员」
- 上传：2MB（头像 200KB）上限、图片魔数校验、扩展名取自魔数、随机文件名
- HTTP 响应：`nosniff` / `X-Frame-Options: DENY` / `Referrer-Policy: no-referrer`；日志剥离 query（防令牌落日志）
- **TLS**：`--tls-cert/--tls-key` 启用 HTTPS（TLS ≥1.2）；未启用且监听非回环地址时打印显著告警

### 部署建议

1. **公网/非可信网段必须启用 HTTPS**（自带证书或 Nginx/Caddy 反代终止 TLS）；
2. 用 `--allowed-subnets` 限制可访问网段；不需要开放注册时加 `--no-registration`；
3. SMTP 授权码可在服务后台「邮箱设置」填写（自动加密），或用环境变量 `NOMY_SMTP_PASSWORD` 注入；
4. Windows 部署 / HTTPS / 开机自启详见 [`部署文档-Windows.md`](部署文档-Windows.md)。

### 打包

```bash
build_exe.bat        # 命令行版 → dist\NoMY-server\
build_exe.bat gui    # 图形管理后台 → dist\NoMY-ServerGUI\
```

产物为「主程序 exe + 同目录依赖/资源」，整目录拷贝到目标机器即可运行，无需安装 Python。

---

## English

### Files

| File | Description |
|---|---|
| `distribute_server.py` | HTTP server (`ThreadingHTTPServer`): routing, IP allowlist, TLS, rate limiting |
| `distribute_server_gui.py` | GUI admin console (service control / users / sessions / logs) |
| `auth_store.py` | Account & session store (PBKDF2 passwords, token sessions, brute-force guard, codes, SMTP config) |
| `chat_api.py` | Friends / private & group chat / moments / file retrieval API |
| `db.py` | SQLite database (users/friends/messages/groups/moments) |
| `mail_service.py` | SMTP mail (verification codes, test mail) |
| `security.py` | Rate limiter, image magic sniffing, at-rest secret protection |
| `web/index.html` | Software info page (visit server root) |
| `build_exe.bat` | Nuitka packaging (standalone dir mode) |
| `部署文档-Windows.md` | Windows deployment / HTTPS / autostart (Chinese) |

### Run

**GUI admin console (recommended):**

```bash
python distribute_server_gui.py
```

Pages: **Service Control** (start/stop, port, allowed subnets, package, version override,
open-registration switch), **User Management** (create/reset/disable/delete), **Mail Settings**
(SMTP config, app password), **Sessions** (view/kick), **Logs**. Data lives in `server_data\` next to the exe.

**CLI:**

```bash
python distribute_server.py --port 18888                      # auth only
python distribute_server.py --package setup.exe --port 18888  # auth + distribution
python distribute_server.py --no-registration                 # disable open registration
python distribute_server.py --tls-cert cert.pem --tls-key key.pem   # enable HTTPS
```

### API Overview

| Route | Description |
|---|---|
| `POST /api/auth/register` `login` `verify` `logout` | Register / login / token verify / logout |
| `POST /api/auth/send_code` `reset_password` `change_password` | Email code / recovery / change password |
| `GET  /api/chat/poll` `POST /api/chat/send` `upload` | Poll / send messages / image upload |
| `GET/POST /api/friends/*` `/api/groups/*` `/api/moments/*` | Friends / groups / moments |
| `GET  /api/files?p=…&token=…` | Image retrieval (authorized per message/friend relationship) |
| `GET  /api/update/check` `download` | Update check / package download (public) |
| `GET  /` | Static info page |
| `GET  /health` | Health check |

### Data & Security

**Storage** (`server_data/` beside the exe/script):

- Business data → SQLite (auto-migrated from legacy `users.json`)
- `sessions.json` → sessions (**token hashes only**)
- `server_config.json` → registration switch / invite code / SMTP (**password encrypted at rest**)
- `secret.key` → encryption key (0600; **back it up with your data**)
- `uploads/`, `moments/` → chat & moments images (per-user folders, random names)

**Security measures implemented:**

- Auth: PBKDF2-HMAC-SHA256 (120k iterations, 16-byte random salt); `hmac.compare_digest` everywhere
- Password policy: ≥8 chars with at least two character classes
- Sessions: 256-bit CSPRNG tokens, 7-day sliding + **30-day absolute expiry**; password change
  revokes and rotates all tokens; max 5 sessions per user
- Codes: 8 digits, 10-minute TTL, invalidated after 5 wrong tries, 60s cooldown, per-IP/global send limits
- Anti-enumeration: recovery answers "ok" for unbound emails; neutral messages for email conflicts
- Rate limits: 120 general / 20 auth requests per IP per minute; **200 concurrent threads** (DoS guard);
  30s socket read timeout
- File authorization: chat images per message participants, moments per self+friends, avatars per
  friends/pending/group members
- Uploads: 2MB cap (avatars 200KB), magic-byte validation, extension from magic bytes, random names
- HTTP headers: `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`; logs strip query strings
- **TLS**: `--tls-cert/--tls-key` (TLS ≥1.2); loud warning when listening in plaintext on non-loopback hosts

### Deployment Advice

1. **Always enable HTTPS on untrusted networks** (own certs or Nginx/Caddy TLS termination);
2. Restrict access with `--allowed-subnets`; use `--no-registration` when appropriate;
3. Set the SMTP app password in the GUI (encrypted automatically) or via `NOMY_SMTP_PASSWORD`;
4. See [`部署文档-Windows.md`](部署文档-Windows.md) for Windows deployment details (Chinese).

### Build

```bash
build_exe.bat        # CLI -> dist\NoMY-server\
build_exe.bat gui    # GUI -> dist\NoMY-ServerGUI\
```

Output is "exe + sibling libs/resources"; copy the folder to the target machine and run.
