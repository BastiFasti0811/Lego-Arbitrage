import pytest

from app.security.url_policy import UnsafeUrlError, validate_marketplace_url


def test_url_policy_blocks_localhost():
    with pytest.raises(UnsafeUrlError):
        validate_marketplace_url("http://localhost:8000/internal", None)


def test_url_policy_blocks_private_ip():
    with pytest.raises(UnsafeUrlError):
        validate_marketplace_url("http://192.168.1.10/admin", None)


def test_url_policy_rejects_wrong_host_for_platform():
    with pytest.raises(UnsafeUrlError):
        validate_marketplace_url("https://example.com/l/123", "CATAWIKI")


def test_url_policy_allows_platform_subdomain(monkeypatch):
    monkeypatch.setattr("app.security.url_policy._ensure_resolved_ips_are_public", lambda _host: None)

    assert validate_marketplace_url("https://www.catawiki.com/de/l/123", "CATAWIKI").endswith("/123")

