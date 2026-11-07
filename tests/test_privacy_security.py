import os
import socket
import stat
import threading

import pytest

from esglocal.config import ConfigError, Settings, is_loopback_host
from esglocal.privacy import network_guard
from esglocal.privacy.redact import redact
from esglocal.security.audit import AuditLog
from esglocal.security.vault import Vault, VaultError, WrongPassphraseError

# ------------------------------------------------------------------ config


def test_loopback_detection():
    assert is_loopback_host("localhost")
    assert is_loopback_host("127.0.0.5")
    assert is_loopback_host("[::1]")
    assert not is_loopback_host("10.0.0.1")
    assert not is_loopback_host("api.example.com")


def test_settings_reject_remote_endpoint():
    with pytest.raises(ConfigError, match="not local"):
        Settings(base_url="https://api.example.com").validate()
    Settings(base_url="http://ollama:11434", allowed_hosts=["ollama"]).validate()
    Settings(provider="fake", base_url="https://anything").validate()


def test_settings_from_env_defaults_are_private(monkeypatch):
    for k in list(os.environ):
        if k.startswith("ESGLOCAL_") and k != "ESGLOCAL_HOME":
            monkeypatch.delenv(k)
    s = Settings.from_env()
    assert s.offline and s.redact_pii
    assert is_loopback_host("127.0.0.1")


# ----------------------------------------------------------- network guard


@pytest.fixture
def guard():
    network_guard.install()
    yield
    network_guard.uninstall()


def test_guard_blocks_external_dns_and_ip(guard):
    with pytest.raises(network_guard.NetworkBlockedError):
        socket.create_connection(("example.com", 80), timeout=1)
    with pytest.raises(network_guard.NetworkBlockedError):
        socket.create_connection(("93.184.216.34", 80), timeout=1)
    assert "example.com" in network_guard.blocked_attempts()


def test_guard_allows_loopback(guard):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    accepted = []
    t = threading.Thread(target=lambda: accepted.append(srv.accept()[0]), daemon=True)
    t.start()
    conn = socket.create_connection(("127.0.0.1", port), timeout=2)
    t.join(timeout=2)
    assert accepted, "loopback connection should be accepted"
    accepted[0].close()
    conn.close()
    srv.close()


def test_guard_uninstall_restores():
    network_guard.install()
    network_guard.uninstall()
    assert not network_guard.is_installed()
    assert socket.getaddrinfo is network_guard._orig_getaddrinfo


# ---------------------------------------------------------------- redaction


def test_redacts_pii():
    text = (
        "Contact jane.doe@corp.example or +44 20 7946 0958. IBAN DE89 3704 0044 0532 0130 00, "
        "card 4111 1111 1111 1111, SSN 123-45-6789, host 192.168.1.20."
    )
    r = redact(text)
    for secret in ("jane.doe", "7946", "DE89", "4111", "123-45", "192.168"):
        assert secret not in r.text
    assert r.counts["EMAIL"] == 1 and r.total >= 6


def test_does_not_redact_esg_figures():
    text = "Scope 1 fell from 22000 to 19000 tCO2e between 2022 and 2024; 1234567890123 MWh."
    assert redact(text).text == text


# --------------------------------------------------------------------- vault


def test_vault_roundtrip_and_encrypted_at_rest(tmp_path):
    v = Vault(tmp_path / "vault.db")
    v.put("workspace:a", {"secret_metric": "PLAINTEXT-MARKER-123"})
    assert v.get("workspace:a") == {"secret_metric": "PLAINTEXT-MARKER-123"}
    v.close()
    raw = (tmp_path / "vault.db").read_bytes()
    assert b"PLAINTEXT-MARKER-123" not in raw
    key = tmp_path / "vault.key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_vault_passphrase(tmp_path):
    v = Vault(tmp_path / "v.db", passphrase="correct horse battery")
    v.put("x", [1, 2, 3])
    v.close()
    with pytest.raises(WrongPassphraseError):
        Vault(tmp_path / "v.db", passphrase="wrong passphrase!")
    with pytest.raises(WrongPassphraseError):
        Vault(tmp_path / "v.db")
    assert Vault(tmp_path / "v.db", passphrase="correct horse battery").get("x") == [1, 2, 3]


def test_vault_change_passphrase_and_purge(tmp_path):
    v = Vault(tmp_path / "v.db")
    v.put("workspace:a", {"k": 1})
    v.put("report:b", "text")
    assert v.names("workspace:") == ["workspace:a"]
    v.change_passphrase("a much better passphrase")
    v.close()
    with pytest.raises(VaultError):
        Vault(tmp_path / "v.db")
    v2 = Vault(tmp_path / "v.db", passphrase="a much better passphrase")
    assert v2.get("workspace:a") == {"k": 1}
    assert v2.purge() == 2
    assert v2.names() == []


def test_vault_names_prefix_is_literal(tmp_path):
    v = Vault(tmp_path / "v.db")
    v.put("a_b", 1)
    v.put("axb", 2)
    assert v.names("a_") == ["a_b"]  # '_' must not act as a SQL wildcard


# --------------------------------------------------------------------- audit


def test_audit_chain_detects_tampering(tmp_path):
    log = AuditLog(tmp_path / "audit.log")
    for i in range(3):
        log.append("event", n=i)
    assert log.verify() == (True, 3, None)
    lines = (tmp_path / "audit.log").read_text().splitlines()
    lines[1] = lines[1].replace('"n": 1', '"n": 99')
    (tmp_path / "audit.log").write_text("\n".join(lines) + "\n")
    ok, _, broken = log.verify()
    assert not ok and broken == 1
