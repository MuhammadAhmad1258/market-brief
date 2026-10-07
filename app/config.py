"""All configuration lives here. Keys are read on the server, never in JavaScript."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    alpha_vantage_api_key: str = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    sec_user_agent: str = os.getenv("SEC_USER_AGENT", "")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    cache_seconds: int = int(os.getenv("CACHE_SECONDS", "3600"))
    requests_per_hour: int = int(os.getenv("REQUESTS_PER_HOUR", "12"))
    global_requests_per_hour: int = int(os.getenv("GLOBAL_REQUESTS_PER_HOUR", "30"))
    alpha_daily_budget: int = int(os.getenv("ALPHA_DAILY_BUDGET", "25"))

    @property
    def live_ready(self) -> bool:
        # SEC asks automated clients to identify their app and contact email.
        return bool(self.alpha_vantage_api_key and "@" in self.sec_user_agent)


settings = Settings()
