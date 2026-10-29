"""安全工具测试：URL 校验 / CSV 公式注入 / 会话令牌。"""
from backend.core.security import sanitize_csv_cell, validate_public_http_url, SESSION_TOKEN, require_token


def test_url_allows_public_https():
    ok, _ = validate_public_http_url("https://example.com/careers/1")
    assert ok


def test_url_blocks_private_and_loopback():
    for url in [
        "http://127.0.0.1:8000/admin", "http://localhost/x", "http://192.168.1.5/x",
        "http://10.0.0.2/x", "http://169.254.1.1/x", "http://[::1]/x",
        "file:///etc/passwd", "ftp://example.com/x", "https://user:pw@example.com/x",
        "", "not a url",
    ]:
        ok, _ = validate_public_http_url(url)
        assert not ok, url


def test_csv_formula_injection_guard():
    assert sanitize_csv_cell("=SUM(A1:A9)").startswith("'")
    assert sanitize_csv_cell("+1").startswith("'")
    assert sanitize_csv_cell("-2").startswith("'")
    assert sanitize_cell_none_safe()


def sanitize_cell_none_safe():
    assert sanitize_csv_cell(None) == ""
    assert sanitize_csv_cell("普通文本") == "普通文本"
    return True


def test_session_token_compare():
    assert require_token(SESSION_TOKEN)
    assert not require_token("wrong")
    assert not require_token(None)
