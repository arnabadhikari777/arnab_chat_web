"""Central configuration. Every value comes from the environment (.env).

To change a limit or a folder name, edit ".env" -- never this file.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


# ---- small parsers -------------------------------------------------------
def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    return env_str(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


def env_mb(name: str, default_mb: int) -> int:
    """Read a size in MB and return bytes."""
    return env_int(name, default_mb) * 1024 * 1024


def env_list(name: str, default: str = "") -> list[str]:
    """Comma-separated list -> clean lowercase list (no dots, no blanks)."""
    raw = env_str(name, default)
    return [item.strip().lower().lstrip(".") for item in raw.split(",") if item.strip()]


def _default_db_url() -> str:
    return "sqlite:///" + str(BASE_DIR / "instance" / "chat.db")


class Config:
    # ---- app ----
    APP_NAME = env_str("APP_NAME", "Goppo")
    APP_ENV = env_str("APP_ENV", "development")
    SECRET_KEY = env_str("SECRET_KEY")
    LOG_LEVEL = env_str("LOG_LEVEL", "INFO").upper()
    DEFAULT_LANGUAGE = env_str("DEFAULT_LANGUAGE", "en")
    SUPPORTED_LANGUAGES = env_list("SUPPORTED_LANGUAGES", "en,bn")

    # ---- database ----
    SQLALCHEMY_DATABASE_URI = env_str("DATABASE_URL", _default_db_url())
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---- sessions & cookies ----
    PERMANENT_SESSION_LIFETIME = env_int("SESSION_LIFETIME_DAYS", 14) * 24 * 3600
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = APP_ENV == "production"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = APP_ENV == "production"
    WTF_CSRF_TIME_LIMIT = None  # token lives as long as the session

    # ---- admins ----
    ADMIN_EMAILS = frozenset(env_list("ADMIN_EMAILS"))

    # ---- mail / OTP ----
    MAIL_SERVER = env_str("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = env_int("MAIL_PORT", 587)
    MAIL_USERNAME = env_str("MAIL_USERNAME")
    MAIL_PASSWORD = env_str("MAIL_PASSWORD")
    MAIL_FROM_NAME = env_str("MAIL_FROM_NAME", APP_NAME)
    OTP_LENGTH = env_int("OTP_LENGTH", 6)
    OTP_EXPIRY_MINUTES = env_int("OTP_EXPIRY_MINUTES", 10)
    OTP_RESEND_COOLDOWN_SECONDS = env_int("OTP_RESEND_COOLDOWN_SECONDS", 60)
    OTP_MAX_ATTEMPTS = env_int("OTP_MAX_ATTEMPTS", 5)
    OTP_MAX_SENDS_PER_HOUR = env_int("OTP_MAX_SENDS_PER_HOUR", 5)
    # Local testing only: print emails to the console instead of sending.
    # Ignored unless APP_ENV=development.
    MAIL_DEV_CONSOLE = env_bool("MAIL_DEV_CONSOLE", False)
    # Unverified sign-ups older than this are removed so names/emails are not blocked forever.
    UNVERIFIED_ACCOUNT_TTL_MINUTES = env_int("UNVERIFIED_ACCOUNT_TTL_MINUTES", 60)

    # ---- cloudinary ----
    CLOUDINARY_CLOUD_NAME = env_str("CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_API_KEY = env_str("CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET = env_str("CLOUDINARY_API_SECRET")
    MESSAGE_MAX_CHARS = env_int("MESSAGE_MAX_CHARS", 4000)
    MAX_VOICE_SECONDS = env_int("MAX_VOICE_SECONDS", 300)
    CHAT_PAGE_SIZE = env_int("CHAT_PAGE_SIZE", 30)
    PASSWORD_HASH_METHOD = env_str("PASSWORD_HASH_METHOD", "") or None  # None = Werkzeug default
    CLOUDINARY_FOLDERS = {
        "avatar": env_str("CLOUDINARY_FOLDER_AVATARS", "goppo/avatars"),
        "cover": env_str("CLOUDINARY_FOLDER_COVERS", "goppo/covers"),
        "image": env_str("CLOUDINARY_FOLDER_CHAT_IMAGES", "goppo/chat/images"),
        "video": env_str("CLOUDINARY_FOLDER_CHAT_VIDEOS", "goppo/chat/videos"),
        "voice": env_str("CLOUDINARY_FOLDER_CHAT_VOICE", "goppo/chat/voice"),
        "document": env_str("CLOUDINARY_FOLDER_CHAT_FILES", "goppo/chat/files"),
        "file": env_str("CLOUDINARY_FOLDER_CHAT_OTHER", "goppo/chat/other"),
    }

    # ---- upload rules (size in bytes, extensions without dots) ----
    UPLOAD_MAX_BYTES = {
        "image": env_mb("MAX_IMAGE_MB", 5),
        "video": env_mb("MAX_VIDEO_MB", 25),
        "document": env_mb("MAX_DOCUMENT_MB", 10),
        "voice": env_mb("MAX_VOICE_MB", 5),
        "file": env_mb("MAX_FILE_MB", 10),
    }
    UPLOAD_ALLOWED_EXTENSIONS = {
        "image": env_list("ALLOWED_IMAGE_EXTENSIONS", "jpg,jpeg,png,webp,gif"),
        "video": env_list("ALLOWED_VIDEO_EXTENSIONS", "mp4,mov,webm"),
        "document": env_list(
            "ALLOWED_DOCUMENT_EXTENSIONS", "pdf,doc,docx,xls,xlsx,ppt,pptx,txt"
        ),
        "voice": env_list("ALLOWED_VOICE_EXTENSIONS", "webm,ogg,mp3,m4a,wav"),
        "file": env_list("ALLOWED_FILE_EXTENSIONS", "zip,rar,7z,csv"),
    }
    # Flask rejects bigger request bodies before our code even runs.
    MAX_CONTENT_LENGTH = max(UPLOAD_MAX_BYTES.values()) + 1024 * 1024

    # ---- real-time ----
    REALTIME_MODE = env_str("REALTIME_MODE", "polling")
    POLL_INTERVAL_ACTIVE_MS = env_int("POLL_INTERVAL_ACTIVE_MS", 2500)
    POLL_INTERVAL_IDLE_MS = env_int("POLL_INTERVAL_IDLE_MS", 12000)
    PRESENCE_TIMEOUT_SECONDS = env_int("PRESENCE_TIMEOUT_SECONDS", 30)

    # ---- rate limiting ----
    RATELIMIT_STORAGE_URI = env_str("RATELIMIT_STORAGE_URI", "memory://")

    # ---- Math Class SSO ----
    SSO_SHARED_SECRET = env_str("SSO_SHARED_SECRET")
    SSO_ENABLED = env_bool("SSO_ENABLED", False)
    MATH_CLASS_URL = env_str("MATH_CLASS_URL")
    RATELIMIT_HEADERS_ENABLED = True


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-not-for-real-use"
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # in-memory
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    RATELIMIT_ENABLED = False
    ADMIN_EMAILS = frozenset({"boss@example.com"})
    PASSWORD_HASH_METHOD = "pbkdf2:sha256:1000"  # fast hashing keeps the tests quick


_CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name: str | None = None):
    return _CONFIGS.get(name or Config.APP_ENV, DevelopmentConfig)
