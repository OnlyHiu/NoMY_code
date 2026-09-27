# -*- coding: utf-8 -*-
"""邮件服务（服务管理后台）

纯标准库 smtplib/email 实现。SMTP 配置存于 server_data/server_config.json，
由管理后台「邮箱设置」页维护；验证码邮件与测试邮件共用同一发送入口。
"""
import logging
import secrets
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

logger = logging.getLogger("mail_service")

EMAIL_RE = __import__("re").compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")


def validate_email(email: str) -> str | None:
    if not email or not EMAIL_RE.match(email):
        return "邮箱格式不正确"
    return None


def send_mail(smtp_cfg: dict, to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    """按配置发送邮件。返回 (ok, err)。smtp_cfg: {host, port, ssl, user, password, sender_name}"""
    host = (smtp_cfg.get("host") or "").strip()
    user = (smtp_cfg.get("user") or "").strip()
    password = smtp_cfg.get("password") or ""
    port = int(smtp_cfg.get("port") or (465 if smtp_cfg.get("ssl") else 25))
    sender_name = (smtp_cfg.get("sender_name") or "NoMY 服务管理后台").strip()
    if not host or not user:
        return False, "SMTP 未配置，请在服务管理后台「邮箱设置」中填写"
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((Header(sender_name, "utf-8").encode(), user))
        msg["To"] = to_addr
        if smtp_cfg.get("ssl"):
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
        try:
            server.login(user, password)
            server.sendmail(user, [to_addr], msg.as_string())
        finally:
            server.quit()
        logger.info("邮件已发送: %s -> %s", subject, to_addr)
        return True, ""
    except Exception as e:
        # L1：内部异常仅入日志，对外返回中性文案（防泄露 SMTP 配置/网络细节）
        logger.error("邮件发送失败: %s", e)
        return False, "邮件发送失败，请稍后再试"


def gen_code() -> str:
    # 密码学安全随机数，防止验证码被预测；8 位数字（L3：提高熵）
    return f"{secrets.randbelow(100000000):08d}"


def code_body(code: str, purpose: str, minutes: int) -> str:
    action = {"register": "注册账号", "reset": "找回密码"}.get(purpose, "验证身份")
    return (
        f"您正在进行【{action}】操作。\n\n"
        f"验证码：{code}\n\n"
        f"{minutes} 分钟内有效，请勿泄露给他人。若非本人操作请忽略本邮件。\n\n"
        f"—— NoMY 服务管理后台"
    )
