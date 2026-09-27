# NoMY 服务端（Linux）/ NoMY Server (Linux)

> 纯 Python 标准库实现的 HTTP 服务（Linux 版）：账号认证、更新分发、好友/聊天/朋友圈 API、静态信息展示页。
> 与 Windows 版功能一致，路径查找与部署方式针对 Linux 优化（无 GUI 管理后台，命令行 + systemd）。
>
> Pure-stdlib HTTP server for Linux: auth, update distribution, social API and a static info page.
> Same features as the Windows build, with Linux-oriented paths and deployment (CLI + systemd, no GUI).

---

## 中文

### 与 Windows 版的差异

| 项目 | 说明 |
|---|---|
| 管理后台 | 无 GUI，使用命令行参数管理（用户管理可调用 `auth_store`/`db` 或临时起 GUI 版） |
| 部署 | 提供 `noMY-server.service`（systemd 单元），支持开机自启与崩溃拉起 |
| Version.ini 查找 | 按候补顺序：`../Config/Version.ini`（与仓库布局一致）→ `Config/Version.ini`（自包含部署） |
| 路径 | 全部使用 `/opt/NoMY` 风格示例；数据目录 `server_data/` 位于脚本旁 |

### 文件说明

| 文件 | 说明 |
|---|---|
| `distribute_server.py` | HTTP 主服务（路由、IP 白名单、TLS、限流、并发上限） |
| `auth_store.py` | 账号与会话存储（PBKDF2 密码、令牌会话、防爆破、验证码、SMTP 配置加密） |
| `chat_api.py` | 好友/私聊/群聊/朋友圈/文件取回 API |
| `db.py` | SQLite 数据库 |
| `mail_service.py` | SMTP 邮件（验证码/测试邮件） |
| `security.py` | 限流器、图片魔数校验、敏感配置落盘加密 |
| `web/index.html` | 软件信息展示页 |
| `noMY-server.service` | systemd 服务单元 |
| `Config/Version.ini` | 版本与更新信息（自包含部署用） |
| `部署文档-Linux.md` | 详细部署指南（Nginx 反代 / HTTPS / 防火墙） |

### 运行

```bash
# 直接运行（前台）
python3 distribute_server.py --port 18888
python3 distribute_server.py --package /opt/NoMY/setup.exe --port 18888   # 含更新分发
python3 distribute_server.py --no-registration                            # 关闭开放注册
python3 distribute_server.py --tls-cert cert.pem --tls-key key.pem        # 启用 HTTPS

# systemd（推荐生产部署）
sudo cp noMY-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now noMY-server
journalctl -u noMY-server -f        # 查看日志
```

### 数据与安全

数据存于脚本旁 `server_data/`：SQLite 业务数据、`sessions.json`（**仅存令牌哈希**）、
`server_config.json`（SMTP **授权码加密存储**）、`secret.key`（0600 权限，**须与数据一并备份**）。

安全机制与 Windows 版一致：

- PBKDF2-HMAC-SHA256（12 万次迭代 + 随机盐）+ `hmac.compare_digest` 防时序
- 密码 ≥8 位含两类字符；会话 7 天滑动 + 30 天绝对过期，改密后全量注销并轮换令牌
- 验证码 8 位 / 10 分钟 / 5 次作废 / 发信限流；找回密码统一响应防邮箱枚举
- 每 IP 限流（120 普通 / 20 认证每分钟）+ 并发线程上限 200（防 DoS）
- 文件按「消息收发关系/好友关系」授权；上传魔数校验、随机文件名、2MB 上限
- 响应头 `nosniff` / `X-Frame-Options: DENY` / `Referrer-Policy: no-referrer`，日志剥离 query
- **TLS 必须**：未启用且监听非回环地址时打印显著告警

### 部署建议

1. 公网环境必须启用 HTTPS（`--tls-cert/--tls-key` 或 Nginx/Caddy 反代终止 TLS）；
2. 用 `--allowed-subnets` 限制访问来源，不需要开放注册时加 `--no-registration`；
3. SMTP 授权码可用环境变量 `NOMY_SMTP_PASSWORD` 注入，避免写入配置；
4. 详见 [`部署文档-Linux.md`](部署文档-Linux.md)。

---

## English

### Differences from the Windows build

| Item | Description |
|---|---|
| Admin console | No GUI; manage via CLI flags (user management via `auth_store`/`db` or the Windows GUI build) |
| Deployment | `noMY-server.service` systemd unit for autostart and auto-restart |
| Version.ini lookup | `../Config/Version.ini` (repo layout) → `Config/Version.ini` (self-contained deploy) |
| Paths | `/opt/NoMY`-style examples; data in `server_data/` beside the script |

### Files

| File | Description |
|---|---|
| `distribute_server.py` | HTTP server (routing, IP allowlist, TLS, rate limiting, concurrency cap) |
| `auth_store.py` | Account & session store (PBKDF2, hashed tokens, brute-force guard, codes, encrypted SMTP config) |
| `chat_api.py` | Friends / chat / moments / file retrieval API |
| `db.py` | SQLite database |
| `mail_service.py` | SMTP mail |
| `security.py` | Rate limiter, image magic sniffing, at-rest secret protection |
| `web/index.html` | Software info page |
| `noMY-server.service` | systemd unit |
| `Config/Version.ini` | Version/update info (self-contained deploys) |
| `部署文档-Linux.md` | Deployment guide (Nginx / HTTPS / firewall, Chinese) |

### Run

```bash
# Foreground
python3 distribute_server.py --port 18888
python3 distribute_server.py --package /opt/NoMY/setup.exe --port 18888   # with distribution
python3 distribute_server.py --no-registration                            # disable open registration
python3 distribute_server.py --tls-cert cert.pem --tls-key key.pem        # enable HTTPS

# systemd (recommended for production)
sudo cp noMY-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now noMY-server
journalctl -u noMY-server -f
```

### Data & Security

Data lives in `server_data/` beside the script: SQLite business data, `sessions.json`
(**token hashes only**), `server_config.json` (**SMTP password encrypted**), and
`secret.key` (0600 — **back it up with your data**).

Same security model as the Windows build: PBKDF2-HMAC-SHA256 + `hmac.compare_digest`;
password ≥8 chars with two character classes; 7-day sliding + 30-day absolute sessions with
token rotation on password change; 8-digit codes with cooldown/attempt limits and
anti-enumeration recovery; per-IP rate limits and a 200-thread concurrency cap;
relationship-based file authorization; magic-byte upload validation; hardened response
headers and query-stripped logs. **TLS is mandatory** — a loud warning is printed for
plaintext listeners on non-loopback addresses.

### Deployment Advice

1. Always enable HTTPS on public networks (own certs or Nginx/Caddy TLS termination);
2. Restrict access with `--allowed-subnets`; disable open registration when appropriate;
3. Inject the SMTP app password via `NOMY_SMTP_PASSWORD` instead of storing it;
4. See [`部署文档-Linux.md`](部署文档-Linux.md) for details (Chinese).
