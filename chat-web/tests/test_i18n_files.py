"""The translation files must stay flat (the loader does not understand nested objects) and match each other."""
import json
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "app" / "i18n"


def load(lang):
    return json.loads((DIR / f"{lang}.json").read_text(encoding="utf-8"))


def test_every_value_is_a_plain_string():
    for lang in ("en", "bn"):
        bad = [k for k, v in load(lang).items() if not isinstance(v, str)]
        assert bad == [], f"{lang}.json has nested or non-string entries (they would show as raw keys): {bad}"


def test_english_and_bengali_have_the_same_keys():
    en, bn = set(load("en")), set(load("bn"))
    assert sorted(en - bn) == [] and sorted(bn - en) == []
