# NoMY 服务端 Windows 桌面部署文档

适用对象：`NoMY-ServerGUI.exe`（图形管理后台）或 `NoMY-server.exe`（命令行版），均为绿色免安装文件夹。

服务端提供：账号认证（`/api/auth/*`）、好友/朋友圈/群聊中转、版本检查与安装包下载（`/api/update/*`）、信息展示页（浏览器访问根地址）。

## 1. 环境要求

- Windows 10/11 或 Windows Server 2016+（64 位）
- 无需安装 Python 或任何依赖——整个文件夹自带运行库
- 开放 TCP 端口（默认 `18888`）

## 2. 部署目录

把整个产物文件夹拷贝到服务器（示例 `D:\NoMY\`）：

```
D:\NoMY\
├── NoMY-ServerGUI.exe        # 图形管理后台（推荐，双击即用）
├── NoMY-server.exe           # 命令行版服务端
├── （同目录的大量 .dll/.pyd） # 运行库，必须与 exe 在一起
├── Config\
│   ├── Version.ini           # 版本号/更新说明（更新分发用）
│   └── image\menu.ico
├── web\index.html            # 信息展示页
└── server_data\              # 运行后自动生成：数据库/会话/SMTP 配置
```

> **注意**：exe 必须和同目录的库文件一起拷贝，单独复制 exe 无法运行。

## 3. 启动服务

### 图形管理后台（推荐）

双击 `NoMY-ServerGUI.exe`，打开界面后会**自动启动服务**（等效点击「启动服务」）。可配置项：

| 配置项 | 说明 |
|---|---|
| 监听端口 | 默认 18888 |
| 允许子网 | CIDR 限制访问来源，留空不限制。例：`192.168.1.0/24` |
| Version.ini | 版本信息文件（默认已指向本目录 Config\） |
| 安装包 | 要分发的客户端安装包路径（可选，仅更新分发用） |
| 版本号覆盖 | 留空用 Version.ini 中的版本号 |
| TLS 证书 / 私钥 | 填写后以 **HTTPS** 服务（见第 5 节，公网部署强烈建议） |
| 开放注册 | 关闭后账号只能由管理后台创建 |

### 命令行版

```bat
NoMY-server.exe --port 18888
NoMY-server.exe --port 18888 --package D:\NoMY\setup.exe
NoMY-server.exe --port 18888 --allowed-subnets "192.168.1.0/24"
NoMY-server.exe --no-registration
NoMY-server.exe --tls-cert tls_fullchain.pem --tls-key tls_privkey.pem   :: HTTPS
```

## 4. 开机自启

### 命令行版（任务计划程序，SYSTEM 账户后台运行）

```bat
schtasks /create /tn "NoMY-Server" /tr "D:\NoMY\NoMY-server\NoMY-server.exe --port 18888" /sc onstart /ru SYSTEM /rl highest
schtasks /run /tn "NoMY-Server"          :: 立即启动一次
schtasks /delete /tn "NoMY-Server" /f    :: 取消自启
```

### 图形管理后台（启动文件夹，登录桌面后自动打开）

`Win+R` 输入 `shell:startup` 回车，把 `NoMY-ServerGUI.exe` 的**快捷方式**放进去。

## 5. 防火墙放行（管理员命令行）

```bat
netsh advfirewall firewall add rule name="NoMY Server" dir=in action=allow protocol=TCP localport=18888
```

图形方式：控制面板 → Windows Defender 防火墙 → 高级设置 → 入站规则 → 新建规则 → 端口 → TCP 18888 → 允许连接。

> **云服务器额外必做**：阿里云/腾讯云控制台 → 实例 → **安全组** → 添加入方向规则：协议 TCP、端口 18888、源 0.0.0.0/0。安全组不放行的话，系统防火墙开了也连不上。

## 6. 启用 HTTPS（公网部署强烈建议）

**为什么**：明文 HTTP 下，登录密码、会话令牌、聊天内容在公网链路可被窃听。客户端 `Config\NetConfig.json` 支持 `"use_ssl": true`，服务端配好证书即可端到端加密。

### 方案 A：有域名（Certbot for Windows，Let's Encrypt 免费证书）

1. 下载安装 Certbot for Windows：<https://certbot.eff.org/instructions>（选 Windows 版）；
2. 管理员命令行签发（域名需已解析到本机公网 IP，签发时短暂占用 80 端口）：

```bat
certbot certonly --standalone -d 你的域名.com
:: 证书生成在 C:\Certbot\live\你的域名.com\ 下（.pem 格式，可直接使用）
```

3. 启动服务时带上证书：

```bat
NoMY-server.exe --port 443 --tls-cert "C:\Certbot\live\你的域名.com\fullchain.pem" --tls-key "C:\Certbot\live\你的域名.com\privkey.pem"
```

GUI 方式：在「TLS 证书 / TLS 私钥」两个输入框分别填上述 `.pem` 路径后启动。

4. 证书 90 天有效，添加任务计划自动续期：

```bat
schtasks /create /tn "Certbot renew" /tr "certbot renew" /sc monthly
```

客户端对接：编辑主软件 `Config\NetConfig.json` 后重启主软件：

```json
{
    "server_url": "你的域名.com",
    "port": 443,
    "use_ssl": true
}
```

### 方案 B：无域名（自签证书，内网/小团队可用）

1. 用 PowerShell 生成自签证书（管理员）：

```powershell
$cert = New-SelfSignedCertificate -DnsName "你的公网IP" -NotAfter (Get-Date).AddYears(10) `
    -CertStoreLocation "Cert:\LocalMachine\My"
$pwd = ConvertTo-SecureString -String "tmp123" -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath D:\NoMY\tls.pfx -Password $pwd
Export-Certificate  -Cert $cert -FilePath D:\NoMY\NoMY-server.crt   # 给客户端信任用
```

2. 把 `.pfx` 转成服务端需要的 `.pem`（用 Git Bash 自带的 openssl）：

```bash
openssl pkcs12 -in D:/NoMY/tls.pfx -clcerts -nokeys -out D:/NoMY/tls_fullchain.pem
openssl pkcs12 -in D:/NoMY/tls.pfx -nocerts -nodes -out D:/NoMY/tls_privkey.pem
```

3. 服务端启动时填入两个 `.pem` 路径（命令行参数或 GUI 输入框）。

4. **每台客户端电脑必须信任此证书**（否则主软件报证书错误）：把 `NoMY-server.crt` 拷贝到客户端 → 双击安装 → "将所有的证书都放入下列存储" → **受信任的根证书颁发机构**。客户端 `NetConfig.json` 填 `"use_ssl": true`。

### 方案 C：已有 Nginx 时反向代理

服务端监听 `127.0.0.1:18888`（GUI 端口照旧，命令行加 `--host 127.0.0.1`），Nginx 挂证书监听 443 并 `proxy_pass http://127.0.0.1:18888`。Nginx 需 `client_max_body_size 8m;`（图片 base64 上传）。客户端 `use_ssl: true` + 443。

## 7. 保护 SMTP 授权码文件（防邮箱凭证泄露）

`server_data\server_config.json` 里明文保存着 SMTP 授权码（等于邮箱的发信凭证）。用文件权限把"能读它的账号"限制到只剩服务运行账号：

```bat
:: 管理员命令行，在 D:\NoMY 目录执行；NoMY-Svc 为专用运行账户（可选）
icacls server_data /inheritance:r /grant:r "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F"
icacls server_data\server_config.json /inheritance:r /grant:r "Administrators:F" "SYSTEM:F"
```

图形方式：右键 `server_data` → 属性 → 安全 → 删除普通 Users 组的读取权限。

通用纪律：

- `server_data\` 不要提交 Git、不要打进安装包/压缩包分发；
- SMTP 用专用小号邮箱；怀疑泄露时到邮箱后台**重置授权码**即可立即作废旧码；
- 备份 `server_data\` 时注意备份文件本身的权限。

## 8. 数据迁移与备份

`server_data\` 一个目录即全部状态（noon.db 用户/聊天/朋友圈、sessions.json 会话、server_config.json SMTP 配置、uploads\ moments\ 图片）。**迁移/备份 = 停止服务后拷贝该目录**。

## 9. 已内置的安全机制（默认启用，无需配置）

登录失败锁定（同 IP 5 分钟 10 次）、接口限流（120 次/分/IP，认证类 20 次/分/IP）、验证码 10 分钟有效 + 60 秒冷却 + 错 5 次作废 + 密码学随机数、会话令牌磁盘仅存哈希、访问日志脱敏 query、图片属主/好友/同群访问校验、上传魔数嗅探 + 扩展名白名单 + 大小限制、子网白名单。

## 10. 常见问题

- **端口被占用**：`netstat -ano | findstr 18888` 找到 PID，`taskkill /PID <pid> /F`，或换端口；
- **客户端连不上**：按顺序检查——服务在本机 `curl http://127.0.0.1:18888/health` → Windows 防火墙规则 → 云安全组 → 客户端 `NetConfig.json` 是否填的**公网 IP**（不是 172.16/10. 开头的内网 IP）→ 改完要重启主软件；
- **浏览器打开信息页但客户端登录失败**：确认客户端 `NetConfig.json` 的 `use_ssl` 与服务端是否启用 TLS 一致（HTTP 服务不要开 use_ssl，HTTPS 服务必须开）；
- **证书报错**：自签证书未在客户端安装信任，见方案 B 第 4 步；
- **Linux 部署**：见 `server_linux\部署文档-Linux.md`。
