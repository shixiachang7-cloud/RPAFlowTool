"""邮件自动发送模块（纯 SMTP 账号密码认证）。

对外接口保持扁平函数不变，供 run_engine.py 的 email_send 动作调用。
不做 OAuth2 认证，不做完整 IMAP 收信框架，只做最简单的 SMTP 发信。
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import os


def send_email(smtp_server, smtp_port, use_ssl, account, password,
               to_addresses, subject, body,
               cc_addresses=None, bcc_addresses=None, attachment_path=None):
    """发送邮件（纯文本正文 + 可选单个附件）。

    Args:
        smtp_server: SMTP 服务器地址，如 "smtp.qq.com"
        smtp_port: SMTP 端口，如 465(SSL) 或 587(STARTTLS)
        use_ssl: True=SSL直连(465)，False=STARTTLS(587)
        account: 发件账号
        password: 发件密码/授权码（明文传入，由调用方管理存储安全）
        to_addresses: 收件人列表，如 ["user1@example.com", "user2@example.com"]
        subject: 邮件主题
        body: 纯文本正文
        cc_addresses: 抄送列表（可选）
        bcc_addresses: 密送列表（可选）
        attachment_path: 附件文件路径（可选，留空则不带附件）

    Raises:
        ValueError: 收件人列表为空
        FileNotFoundError: 附件路径不存在（仅当 attachment_path 非空时）
        smtplib.SMTPException: SMTP 连接/认证/发送错误
    """
    if not to_addresses:
        raise ValueError("收件人列表为空，无法发送邮件")

    cc_addresses = cc_addresses or []
    bcc_addresses = bcc_addresses or []

    msg = MIMEMultipart()
    msg['From'] = account
    msg['To'] = ', '.join(to_addresses)
    if cc_addresses:
        msg['Cc'] = ', '.join(cc_addresses)
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'html', 'utf-8'))

    if attachment_path:
        if not os.path.exists(attachment_path):
            raise FileNotFoundError(f"附件文件不存在: {attachment_path}")
        with open(attachment_path, 'rb') as f:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(attachment_path)}"')
        msg.attach(part)

    if use_ssl:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
        server.starttls()

    try:
        server.login(account, password)
        all_recipients = to_addresses + cc_addresses + bcc_addresses
        server.sendmail(account, all_recipients, msg.as_string())
    finally:
        server.quit()
