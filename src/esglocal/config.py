"""Runtime settings.

All settings come from environment variables (prefix ``ESGLOCAL_``) so nothing
sensitive needs to live in a config file. Defaults are the *most private*
option: offline mode on, loopback-only model endpoints, encryption on.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class ConfigError(ValueError):
    """Raised when settings would violate the local-only guarantees."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.strip("[]").lower()
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class Settings:
    # LLM provider: "ollama" | "openai_compat" | "fake"
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    chat_model: str = ""  # empty = pick the first installed model
    embed_model: str = "nomic-embed-text"
    temperature: float = 0.2
    num_ctx: int = 8192
    request_timeout: float = 600.0
    think: bool = False  # disable "thinking" traces for reasoning models by default

    # Privacy
    offline: bool = True
    allowed_hosts: list[str] = field(default_factory=list)
    redact_pii: bool = True

    # Storage
    home: Path = field(default_factory=lambda: Path.home() / ".esglocal")
    passphrase: str | None = None

    # Agents
    agent_max_steps: int = 6
    agent_concurrency: int = 1

    @classmethod
    def from_env(cls) -> Settings:
        s = cls(
            provider=os.environ.get("ESGLOCAL_PROVIDER", cls.provider),
            base_url=os.environ.get("ESGLOCAL_BASE_URL", cls.base_url),
            chat_model=os.environ.get("ESGLOCAL_CHAT_MODEL", ""),
            embed_model=os.environ.get("ESGLOCAL_EMBED_MODEL", cls.embed_model),
            temperature=float(os.environ.get("ESGLOCAL_TEMPERATURE", cls.temperature)),
            num_ctx=int(os.environ.get("ESGLOCAL_NUM_CTX", cls.num_ctx)),
            request_timeout=float(os.environ.get("ESGLOCAL_TIMEOUT", cls.request_timeout)),
            think=_env_bool("ESGLOCAL_THINK", False),
            offline=_env_bool("ESGLOCAL_OFFLINE", True),
            allowed_hosts=_env_list("ESGLOCAL_ALLOWED_HOSTS"),
            redact_pii=_env_bool("ESGLOCAL_REDACT_PII", True),
            home=Path(os.environ.get("ESGLOCAL_HOME", str(Path.home() / ".esglocal"))),
            passphrase=os.environ.get("ESGLOCAL_PASSPHRASE") or None,
            agent_max_steps=int(os.environ.get("ESGLOCAL_AGENT_MAX_STEPS", 6)),
            agent_concurrency=int(os.environ.get("ESGLOCAL_AGENT_CONCURRENCY", 1)),
        )
        s.validate()
        return s

    def validate(self) -> None:
        """Refuse model endpoints that are not on this machine (unless allow-listed)."""
        if self.provider == "fake":
            return
        host = urlparse(self.base_url).hostname
        if not host:
            raise ConfigError(f"Invalid model endpoint URL: {self.base_url!r}")
        if not is_loopback_host(host) and host not in self.allowed_hosts:
            raise ConfigError(
                f"Model endpoint host {host!r} is not local. ESG Local AI only talks to models "
                "on this machine. If you run the model on a trusted host in your own network, "
                "add it to ESGLOCAL_ALLOWED_HOSTS explicitly."
            )
        if not 0.0 <= self.temperature <= 2.0:
            raise ConfigError("temperature must be between 0 and 2")
        if self.agent_concurrency < 1:
            raise ConfigError("agent_concurrency must be >= 1")

    @property
    def vault_path(self) -> Path:
        return self.home / "vault.db"

    @property
    def rag_path(self) -> Path:
        return self.home / "documents.db"

    @property
    def audit_path(self) -> Path:
        return self.home / "audit.log"

    def ensure_home(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.home, 0o700)
        except OSError:  # pragma: no cover - e.g. Windows
            pass
        return self.home
