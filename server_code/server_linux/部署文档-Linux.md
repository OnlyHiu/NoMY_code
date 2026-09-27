# NoMY 服务端 Linux 部署文档

本目录（`server_linux/`）是 NoMY 服务管理后台的 **Linux 命令行版**，与 Windows 版 `server/` 功能一致、代码同源；本目录为独立副本，方便直接拷贝到 Linux 服务器部署，互不影响。

服务端提供：

- 账号认证（`/api/auth/*`：邮箱验证码注册、登录、改密、找回密码）
- 好友 / 朋友圈 / 群聊中转（`/api/chat`、`/api/friends`、`/api/moments`、`/api/groups`）
- 版本检查与安装包下载（`/api/update/*`）
- 静态信息展示页（浏览器访问根地址：软件信息 + 下载按钮）

## 1. 环境要求

- Python **3.10+**（仅需标准库，**无任何 pip 依赖**）
- 开放 TCP 端口（默认 `18888`）

## 2. 部署目录

把整个 `server_linux/` 文件夹拷贝到服务器（示例位置 `/opt/NoMY/server_linux/`）：

```
/opt/NoMY/server_linux/
├── distribute_server.py      # 主程序（入口）
├── auth_store.py / db.py / chat_api.py / security.py / mail_service.py
├── web/index.html            # 信息展示页
├── noMY-server.service       # systemd 服务模板
├── 部署文档-Linux.md          # 本文档
└── server_data/              # 运行后自动生成：数据库与会话/配置
```

`Version.ini`（版本号与更新说明）按以下顺序自动查找，找到任意一个即可：

1. `server_linux/` **上级目录**下的 `Config/Version.ini`（与 Windows 版仓库布局一致）；
2. `server_linux/Config/Version.ini`（自包含部署，推荐：把 `Config/Version.ini` 拷进 `server_linux/Config/`）。

两个位置都没有时服务仍可运行（认证/聊天正常），仅 `/api/update/check` 返回 503；也可用 `--version-ini` 显式指定。

## 3. 手动运行

```bash
cd /opt/NoMY/server_linux

# 仅认证 + 聊天（无安装包分发）
python3 distribute_server.py --port 18888

# 认证 + 聊天 + 安装包分发
python3 distribute_server.py --port 18888 --package /opt/NoMY/setup.exe

# 限制允许访问的网段
python3 distribute_server.py --allowed-subnets "192.168.1.0/24,10.0.0.0/8"

# 关闭开放注册（账号只能由管理后台创建）
python3 distribute_server.py --no-registration

# 同时输出日志到文件
python3 distribute_server.py --log-file server.log
```

全部参数：

| 参数 | 说明 |
|---|---|
| `--port` | 监听端口（默认 18888） |
| `--host` | 监听地址（默认 0.0.0.0） |
| `--package` | 安装包路径（任意文件，通常为客户端安装程序） |
| `--version-ini` | Version.ini 路径覆盖 |
| `--allowed-subnets` | 允许访问的网段（CIDR，逗号分隔；默认不限制） |
| `--no-registration` | 关闭开放注册 |
| `--tls-cert` / `--tls-key` | TLS 证书/私钥 PEM（同时提供即以 **HTTPS** 服务） |
| `--config` / `-c` | JSON 配置文件（server.json，见主程序帮助） |
| `--log-file` | 同时输出日志到此文件 |

停止：`Ctrl+C`（会优雅退出）。

## 3.1 启用 HTTPS（公网部署强烈建议）

**为什么**：默认明文 HTTP 下，登录密码、会话令牌（`?token=...`）、聊天文字与图片在公网链路上可被任意中间设备读取。服务端已内置 TLS，只需配置证书。客户端 `Config\NetConfig.json` 本就支持 `"use_ssl": true`，服务端配好证书即可端到端加密。

#### 方案 A：有域名（推荐，Let's Encrypt 免费证书）

```bash
# 1. 安装 certbot（Ubuntu/Debian；域名需已解析到本服务器公网 IP）
sudo apt update && sudo apt install -y certbot

# 2. 签发证书（standalone 模式会短暂占用 80 端口，先停服务再签）
sudo certbot certonly --standalone -d 你的域名.com

# 3. 把证书放到服务目录并启动 HTTPS 服务
sudo cp /etc/letsencrypt/live/你的域名.com/fullchain.pem /opt/NoMY/server_linux/tls_fullchain.pem
sudo cp /etc/letsencrypt/live/你的域名.com/privkey.pem  /opt/NoMY/server_linux/tls_privkey.pem
sudo chown noMY:noMY /opt/NoMY/server_linux/tls_*.pem

python3 distribute_server.py --port 443 \
    --tls-cert tls_fullchain.pem --tls-key tls_privkey.pem
# systemd 部署：把 --tls-cert/--tls-key 两个参数追加到 noMY-server.service 的 ExecStart 后重启服务

# 4. 证书 90 天有效，配置每月自动续期并重启服务
echo '0 3 1 * * certbot renew --quiet && systemctl restart noMY-server' | sudo tee /etc/cron.d/nomy-cert-renew
```

客户端对接：编辑主软件 `Config\NetConfig.json` 后重启主软件：

```json
{
    "server_url": "你的域名.com",
    "port": 443,
    "use_ssl": true
}
```

#### 方案 B：无域名（自签证书，内网/小团队可用）

```bash
# 1. 服务器上生成 10 年期自签证书（CN 填服务器公网 IP 或主机名）
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout tls_privkey.pem -out tls_fullchain.pem \
    -subj "/CN=你的公网IP"
```

启动方式同方案 A 第 3 步。**自签证书必须让每台客户端电脑信任它**（否则主软件报证书错误）：
把 `tls_fullchain.pem` 拷贝到客户端电脑并改后缀为 `.crt` → 双击安装 → "将所有的证书都放入下列存储" → **受信任的根证书颁发机构** → 完成后客户端 `NetConfig.json` 填 `"use_ssl": true`。

#### 方案 C：反向代理终结 TLS（已用 Nginx/Caddy 时）

服务端只监听本机回环（`python3 distribute_server.py --host 127.0.0.1 --port 18888`），由 Nginx 在 443 挂证书并 `proxy_pass http://127.0.0.1:18888`。注意两点：客户端填 `"use_ssl": true` + 443 端口；Nginx 需放行较大请求体（`client_max_body_size 8m;`，图片以 base64 JSON 上传）。

## 3.2 保护 SMTP 授权码文件（防邮箱凭证泄露）

**为什么**：`server_data/server_config.json` 里**明文**保存着 SMTP 授权码（等于邮箱的发信登录凭证）。文件本身不加密，任何能读到它的程序或登录账号都可以冒用你的邮箱发信。防护思路：用文件系统权限把"能读这个文件的账号"限制到只剩服务运行账号。

#### Linux

```bash
# 数据目录与敏感文件只归服务运行账号所有、权限收窄
sudo chown -R noMY:noMY /opt/NoMY/server_linux/server_data
sudo chmod 700 /opt/NoMY/server_linux
sudo chmod 600 /opt/NoMY/server_linux/server_data/server_config.json
sudo chmod 600 /opt/NoMY/server_linux/server_data/sessions.json
sudo chmod 600 /opt/NoMY/server_linux/server_data/noon.db

# 验证：切换到普通用户读取应提示 Permission denied
sudo -u nobody cat /opt/NoMY/server_linux/server_data/server_config.json
```

#### Windows Server（使用 NoMY-ServerGUI.exe 时）

```bat
icacls server_data /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F"
```

或图形界面：右键 `server_data` → 属性 → 安全 → 删除普通 Users 组的读取权限，仅保留服务运行账户与管理员。

#### 通用纪律

- `server_data\` 整个目录（用户数据库、会话、SMTP 配置）**不要提交到 Git，不要打进安装包或压缩包分发**；
- SMTP 使用**专用小号邮箱**，不用个人主邮箱；怀疑泄露时到邮箱服务商后台**重置授权码**即可立即作废旧码；
- 备份 `server_data\` 时注意备份文件本身的存放权限。

## 3.3 已内置的安全机制（默认启用，无需配置）

登录失败锁定（同 IP 5 分钟 10 次）、接口限流（120 次/分/IP，认证类 20 次/分/IP）、验证码 10 分钟有效 + 60 秒冷却 + 错 5 次作废 + 密码学随机数、会话令牌磁盘存储仅存哈希、访问日志脱敏 query、图片属主/好友/同群访问校验、上传魔数嗅探 + 扩展名白名单 + 大小限制、子网白名单（`--allowed-subnets`）。

## 4. systemd 服务（推荐，开机自启）

```bash
# 创建非 root 专用用户
sudo useradd -r -s /usr/sbin/nologin noMY
sudo chown -R noMY:noMY /opt/NoMY

# 安装服务（按实际路径修改 service 文件中的 ExecStart/WorkingDirectory/User）
sudo cp noMY-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now noMY-server

# 查看状态与日志
systemctl status noMY-server
journalctl -u noMY-server -f
```

## 5. 防火墙放行

```bash
# ufw（Ubuntu/Debian）
sudo ufw allow 18888/tcp

# firewalld（CentOS/RHEL）
sudo firewall-cmd --permanent --add-port=18888/tcp
sudo firewall-cmd --reload
```

## 6. 数据迁移（Windows → Linux 或换机）

账号、会话、SMTP 配置、聊天数据全部存放在 `server_data/`，**拷贝该目录即完成完整迁移**：

```
server_data/
├── noon.db              # SQLite：用户（PBKDF2 加盐哈希）、好友、聊天、朋友圈
├── sessions.json        # 登录会话令牌
└── server_config.json   # SMTP 配置、next_user_id 等
```

建议在服务停止时拷贝。聊天图片等上传文件在 `server_data/uploads/`、`server_data/moments/`，随目录一起拷贝即可。

## 7. 常见问题

- **端口被占用**：`Address already in use` → 换 `--port`，或 `ss -tlnp | grep 18888` 找到占用进程。
- **`/api/update/check` 返回 503**：说明没找到 `Version.ini`，按第 2 节放置或用 `--version-ini` 指定；认证/聊天不受影响。
- **客户端收不到验证码邮件**：SMTP 配置存于 `server_data/server_config.json`（也可从 Windows 版后台配置好后拷贝过来）。
- **收不到客户端访问**：检查防火墙（第 5 节）与 `--allowed-subnets` 是否限制了客户端网段。
- **与 Windows 版 `server/` 的关系**：两份代码独立运行、数据各自独立（各自的 `server_data/`）；同一账号体系只需迁移 `server_data/`，不要同时运行两份并共享同一数据库文件。
