"""本地安全工具：会话令牌、URL 校验（防 SSRF）、CSV 公式注入防护。"""
import ipaddress
import secrets
from urllib.parse import urlparse

# 每次进程启动生成，修改类接口必须携带 X-JobMap-Token 头
SESSION_TOKEN = secrets.token_urlsafe(24)


def require_token(provided: str | None) -> bool:
    return bool(provided) and secrets.compare_digest(provided, SESSION_TOKEN)


_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "metadata.google.internal"}


def validate_public_http_url(url: str) -> tuple[bool, str]:
    """仅允许 http/https 且不指向内部/回环/链路本地地址的外部 URL。"""
    if not isinstance(url, str) or not url.strip():
        return False, "URL 为空"
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False, "URL 无法解析"
    if parsed.scheme not in ("http", "https"):
        return False, "仅允许 http/https 协议"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "缺少主机名"
    if host in _BLOCKED_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        return False, "不允许访问本地/内部主机名"
    # 显式 IP：拦截回环、私网、链路本地等
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, "不允许访问内部网络地址"
    except ValueError:
        pass  # 普通域名
    if parsed.username or parsed.password:
        return False, "不允许携带内嵌凭据的 URL"
    return True, "ok"


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t")


def sanitize_csv_cell(value) -> str:
    """防止 Excel/WPS 公式注入：以 = + - @ 或制表符开头的单元格前置单引号。"""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text
