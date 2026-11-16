"""Runtime settings.

Values come from process environment variables, optionally loaded from a
`.env` file in the project root or in `backend/`. Settings are read lazily so
tests can change them with `monkeypatch.setenv`.
"""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"

try:  # python-dotenv is optional; real environment variables always win.
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(BACKEND / ".env", override=False)
except ImportError:  # pragma: no cover
    pass


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Lazy settings: every property reads the environment when accessed."""

    @property
    def database_url(self) -> str:
        return os.getenv("DATABASE_URL", f"sqlite:///{(BACKEND / 'infrasight.db').as_posix()}")

    @property
    def cors_origins(self) -> list[str]:
        return os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

    # --- Ollama / LLM -----------------------------------------------------
    @property
    def ollama_url(self) -> str:
        return os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")

    @property
    def vision_model(self) -> str:
        return os.getenv("QWEN_MODEL", "qwen3-vl:4b-instruct")

    @property
    def text_model(self) -> str:
        return os.getenv("TEXT_MODEL", "qwen3:4b-instruct-2507-q4_K_M")

    @property
    def num_ctx(self) -> int:
        return int(os.getenv("OLLAMA_NUM_CTX", "8192"))

    @property
    def num_predict(self) -> int:
        return int(os.getenv("OLLAMA_NUM_PREDICT", "2048"))

    @property
    def llm_timeout(self) -> float:
        return float(os.getenv("OLLAMA_TIMEOUT", "600"))

    @property
    def keep_alive(self) -> str:
        return os.getenv("OLLAMA_KEEP_ALIVE", "30m")

    @property
    def image_max_side(self) -> int:
        return int(os.getenv("IMAGE_MAX_SIDE", "1600"))

    # --- Documents / OCR --------------------------------------------------
    @property
    def upload_dir(self) -> Path:
        return Path(os.getenv("UPLOAD_DIR", str(BACKEND / "uploads")))

    @property
    def ocr_enabled(self) -> bool:
        return _bool("OCR_ENABLED", True)

    @property
    def max_text_chunk_chars(self) -> int:
        return int(os.getenv("MAX_TEXT_CHUNK_CHARS", "6000"))

    @property
    def max_chunks(self) -> int:
        return int(os.getenv("MAX_CHUNKS", "8"))

    # --- Matching ----------------------------------------------------------
    @property
    def matcher_backend(self) -> str:
        """auto | sentence-transformers | ollama | lexical"""
        return os.getenv("MATCHER_BACKEND", "auto").lower()

    @property
    def matcher_model(self) -> str:
        return os.getenv("MATCHER_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

    @property
    def ollama_embed_model(self) -> str:
        return os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

    @property
    def match_high(self) -> float:
        return float(os.getenv("MATCH_HIGH_CONFIDENCE", "0.80"))

    @property
    def match_low(self) -> float:
        return float(os.getenv("MATCH_LOW_CONFIDENCE", "0.40"))

    # --- Auth ---------------------------------------------------------------
    @property
    def auth_enabled(self) -> bool:
        return _bool("AUTH_ENABLED", True)

    @property
    def jwt_secret(self) -> str:
        value = os.getenv("JWT_SECRET")
        if value:
            return value
        path = BACKEND / ".secret_key"
        if not path.exists():
            path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        return path.read_text(encoding="utf-8").strip()

    @property
    def token_hours(self) -> int:
        return int(os.getenv("TOKEN_HOURS", "12"))

    # --- ML -----------------------------------------------------------------
    @property
    def model_dir(self) -> Path:
        return Path(os.getenv("MODEL_DIR", str(ROOT / "ml" / "models")))


settings = Settings()
