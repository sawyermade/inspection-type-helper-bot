"""Settings loaded from environment variables / a local .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    pass


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    base_url: str
    username: str
    # repr=False keeps the password out of logs / tracebacks.
    password: str = field(repr=False)
    # The only host the bot is allowed to type the password into.
    idp_host: str = "identity.sjursolutions.com"
    # Where the bot lands once signed in.
    start_path: str = "/admin"
    headless: bool = False
    profile_dir: Path = PROJECT_ROOT / ".chrome-profile"
    chromedriver_path: str | None = None
    timeout: float = 45.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")

        username = os.getenv("VIP_USERNAME", "").strip()
        password = os.getenv("VIP_PASSWORD", "")
        missing = [
            name
            for name, value in (("VIP_USERNAME", username), ("VIP_PASSWORD", password))
            if not value
        ]
        if missing:
            raise ConfigError(
                f"Missing {', '.join(missing)}. Copy .env.example to .env and fill it in."
            )

        profile = Path(os.getenv("CHROME_PROFILE_DIR", ".chrome-profile"))
        if not profile.is_absolute():
            profile = PROJECT_ROOT / profile

        return cls(
            base_url=os.getenv("VIP_BASE_URL", "https://team.videoinspectionpro.com").rstrip("/"),
            username=username,
            password=password,
            idp_host=os.getenv("VIP_IDP_HOST", "identity.sjursolutions.com").strip(),
            start_path="/" + os.getenv("VIP_START_PATH", "/admin").strip().lstrip("/"),
            headless=_flag("HEADLESS"),
            profile_dir=profile,
            chromedriver_path=os.getenv("CHROMEDRIVER_PATH") or None,
            timeout=float(os.getenv("LOGIN_TIMEOUT", "45")),
        )
