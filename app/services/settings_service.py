"""Compatibility shim – prefer site_settings. Kept so older imports keep working."""
from . import site_settings

BOOLS = site_settings.SWITCHES
TEXTS = {k: ("", v) for k, v in site_settings.TEXTS.items()}
DEFAULTS = {**BOOLS, **{k: "" for k in site_settings.TEXTS}}


def all_settings() -> dict:
    return site_settings.current()


def get(key: str):
    return site_settings.get(key)


def enabled(key: str) -> bool:
    return site_settings.enabled(key)


def site_name() -> str:
    return site_settings.site_name()


def update(admin, form) -> dict:
    return site_settings.update(admin, form)
