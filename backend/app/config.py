import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parent.parent
# backend/.env takes precedence over the repo-root .env; real environment variables win over both.
load_dotenv(BACKEND_ROOT / ".env")
load_dotenv(BACKEND_ROOT.parent / ".env")


@dataclass(frozen=True)
class LLMSettings:
    enabled: bool
    model: str
    reasoning_effort: str
    max_retries: int = 3
    timeout_s: float = 120.0
    max_parallel_calls: int = 8


@dataclass(frozen=True)
class Settings:
    storage_dir: Path
    max_files: int
    max_file_size_bytes: int
    cors_origins: list[str]
    llm: LLMSettings = LLMSettings(enabled=False, model="gpt-5.6-terra", reasoning_effort="medium")


def load_settings() -> Settings:
    origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    llm_enabled = os.getenv("LLM_ENABLED", "true").lower() not in ("0", "false", "no")
    return Settings(
        storage_dir=Path(os.getenv("STORAGE_DIR", BACKEND_ROOT / "storage")).resolve(),
        max_files=int(os.getenv("MAX_FILES", "20")),
        max_file_size_bytes=int(os.getenv("MAX_FILE_SIZE_MB", "100")) * 1024 * 1024,
        cors_origins=[origin.strip() for origin in origins.split(",") if origin.strip()],
        llm=LLMSettings(
            enabled=llm_enabled and bool(os.getenv("OPENAI_API_KEY")),
            model=os.getenv("OPENAI_MODEL", "gpt-5.6-terra"),
            reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "medium"),
        ),
    )
