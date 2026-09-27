# Third-Party Notices

NoMY 项目使用以下第三方库与资源。本项目自身以 **MIT License** 发布，
所列依赖各自的许可证保持原状。

---

## Runtime Dependencies（运行时依赖）

### PySide6
- **版本**: 6.11.x
- **许可证**: LGPL v3
- **项目地址**: https://www.qt.io/qt-for-python
- **说明**: Qt for Python（Qt 6 的官方 Python 绑定）。LGPL 要求以动态链接形式使用；项目本身仅以 `from PySide6 import ...` 调用，不修改其源码，符合 LGPL 分发条件。
- **版权**: © 2024 The Qt Company Ltd.

### PySide6-Fluent-Widgets（qfluentwidgets）
- **版本**: 1.11.x
- **许可证**: MIT / GPL v3 dual license
- **项目地址**: https://github.com/zhiyiYo/PyQt-Fluent-Widgets
- **版权**: © 2021-present zhiyiYo

### NumPy
- **版本**: 2.x
- **许可证**: BSD-3-Clause
- **项目地址**: https://numpy.org/
- **版权**: © 2005-2024 NumPy Developers

### Pillow（PIL Fork）
- **版本**: 12.x
- **许可证**: HPND (Historical Permission Notice and Disclaimer)
- **项目地址**: https://python-pillow.org/
- **版权**: © 1995-2011 Secret Labs AB © 1997-2011 Fredrik Lundh and contributors

### psutil
- **版本**: 7.x
- **许可证**: BSD-3-Clause
- **项目地址**: https://github.com/giampaolo/psutil
- **版权**: © 2009-2024 Giampaolo Rodola

### openai（Python SDK）
- **版本**: 3.x
- **许可证**: Apache-2.0
- **项目地址**: https://github.com/openai/openai-python
- **版权**: © OpenAI

### httpx
- **版本**: 0.27.x（通过 `httpx2` 间接安装）
- **许可证**: BSD-3-Clause
- **项目地址**: https://github.com/encode/httpx
- **版权**: © 2019-2024 Encode OSS Ltd.

### requests
- **版本**: 2.34.x
- **许可证**: Apache-2.0
- **项目地址**: https://requests.readthedocs.io
- **版权**: © Kenneth Reitz

### urllib3
- **版本**: 2.x
- **许可证**: MIT
- **项目地址**: https://github.com/urllib3/urllib3
- **版权**: © 2008-2024 Andrey Petrov and the urllib3 contributors

### certifi
- **版本**: 2026.x
- **许可证**: MPL-2.0
- **项目地址**: https://github.com/certifi/python-certifi
- **版权**: © Kenneth Reitz

### charset-normalizer
- **版本**: 3.x
- **许可证**: MIT
- **项目地址**: https://github.com/Ousret/charset_normalizer
- **版权**: © Ahmed TAHRI

---

## Python 标准库

项目使用的部分 Python 标准库模块（非外部依赖）：

- `http.server` / `socketserver`（服务端 HTTP）
- `sqlite3`（本地数据存储）
- `threading` / `multiprocessing`
- `email` / `smtplib`（邮件服务）
- `hashlib` / `hmac` / `secrets`（认证与安全）
- `argparse` / `configparser` / `logging`
- `base64` / `json` / `re` / `urllib.parse`

标准库许可证遵循 **PSF License Agreement** 与 **Zero-Clause BSD (0BSD)**，
无附加通知要求。

---

## 分发与合规要点

1. **PySide6 LGPL 合规**
   - 项目以 **动态链接/调用** 形式使用 PySide6（`from PySide6 import ...`），
     源码未修改，符合 LGPL 允许的分发方式。
   - 在分发二进制时建议随附 PySide6 的 LGPL 文本与（或）其官方安装包链接：
     https://www.qt.io/licensing/open-source-lgpl-obligations

2. **第三方 LICENSE 文本随附**
   - 通过 `pip` 安装的依赖其 `LICENSE`/`NOTICE` 文件位于各自包目录下。
   - 推荐在最终分发包中保留 `site-packages/<pkg>-<version>.dist-info/` 目录
     以便查阅各依赖完整许可证。

3. **不使用 GPL 强制传染**
   - 本项目自身采用 MIT License。
   - 所有依赖（LGPL / MIT / BSD / Apache-2.0 / HPND / MPL-2.0）均兼容 MIT 发布。

4. **关于 Qt 商标**
   - Qt® 与 PySide® 是 The Qt Company Ltd. 的注册商标。
   - 本项目非官方 Qt 衍生品，商标使用遵循 Qt Company 的商标政策。

---

## 许可证变更记录

| 日期 | 变更 |
|---|---|
| 2024-12 | 首版第三方通知清单（基于 PySide6 6.11.x + Python 3.13） |

---

如果发现遗漏或版本过时，欢迎通过 Issue 反馈。
