"""Central translation system (English + Bengali).

Templates use   {{ t("nav.messages") }}
Python uses     t("error.404.title")
JavaScript uses window.i18n.t("toast.network_error")  (loaded from /i18n/bundle.json)

If a key is missing in the chosen language we fall back to English, then to the key.
"""
import json
from pathlib import Path

from flask import current_app, has_request_context, request, session
from flask_login import current_user

_DIR = Path(__file__).parent
_CACHE: dict[str, dict[str, str]] = {}


def load_messages(lang: str) -> dict[str, str]:
    if lang not in _CACHE:
        path = _DIR / f"{lang}.json"
        _CACHE[lang] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _CACHE[lang]


def get_language() -> str:
    cfg = current_app.config
    supported = cfg["SUPPORTED_LANGUAGES"]
    default = cfg["DEFAULT_LANGUAGE"]
    if not has_request_context():
        return default
    if current_user.is_authenticated and current_user.language in supported:
        return current_user.language
    lang = session.get("lang") or request.cookies.get("lang")
    return lang if lang in supported else default


def t(key: str, lang: str | None = None, **values) -> str:
    lang = lang or get_language()
    text = load_messages(lang).get(key)
    if text is None:
        text = load_messages("en").get(key, key)
    if values:
        try:
            return text.format(**values)
        except (KeyError, IndexError):
            return text
    return text


def bundle(lang: str | None = None) -> dict[str, str]:
    """English merged with the chosen language, for the browser."""
    lang = lang or get_language()
    return {**load_messages("en"), **load_messages(lang)}


def init_i18n(app) -> None:
    @app.context_processor
    def _inject():
        return {"t": t, "lang": get_language(), "app_name": app.config["APP_NAME"]}
