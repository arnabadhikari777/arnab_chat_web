#!/usr/bin/env python3
"""Builds the Android app's bundled frontend (android-app/www) FROM THE REAL WEBSITE TEMPLATES, CSS and JavaScript.

There is no second copy of the UI: this script renders the website's own Jinja templates (base.html, chat/inbox.html,
chat/room.html, settings/index.html, auth/login.html) with a placeholder user and no data, in "bundle" mode
(`bundle` = True switches off only what cannot exist inside the APK: the CSRF meta tag, the remote font host and the server-side
forms), copies static/css, static/js, static/icons, and adds the bundled fonts and the two language files. The pages fill
themselves with the person's data at run time from the local database (js/local-first.js). Run it before every APK build
(`npm run build:web` in android-app/ does); new frontend changes therefore reach the next APK automatically.

Nothing secret goes in: no SECRET_KEY, no CSRF token, no API key, no user data. The backend URL is the public site address.

    python tools/build_android_web.py                     # writes ../android-app/www
    python tools/build_android_web.py --out /tmp/www --backend-url https://example.org
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
ANDROID = REPO / "android-app"
DEFAULT_BACKEND = "https://arnabchatweb.pythonanywhere.com"
LANGS = ("en", "bn")
SKIP_STATIC = {"manifest.webmanifest", "sw.js"}          # PWA-only files

# website path -> bundled page. MUST match LocalRoutes.toLocal in UrlPolicy.kt (both are unit-tested with the same cases).
_CHAT = re.compile(r"^/chat/(\d{1,18})/?$")


def to_local(path: str) -> str | None:
    p = path.split("?")[0].split("#")[0]
    if "hosted=1" in path:
        return None
    m = _CHAT.match(p)
    if m:
        return f"chat.html?c={m.group(1)}"
    return {"/": "messages.html", "/messages": "messages.html", "/settings": "settings.html", "/login": "login.html"}.get(p.rstrip("/") or "/")


_ATTR = re.compile(r'(\s(?:href|action)=")(/[^"]*)(")')


def rewrite_links(html: str, backend: str) -> str:
    """Links to pages that exist in the bundle stay local; every other site path points at the website (opened inside the app)."""
    def fix(m: re.Match) -> str:
        path = m.group(2)
        if path.startswith("//") or path.startswith("/static/") or path.startswith("/__goppo_media__/"):
            return m.group(0)
        local = to_local(path)
        return m.group(1) + ("/" + local if local else backend + path) + m.group(3)
    return _ATTR.sub(fix, html)


INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b6e78"><title>Goppo</title>
<style>html,body{margin:0;height:100%;background:#0b6e78;color:#fff;font:600 18px system-ui,sans-serif;display:grid;place-items:center}</style></head>
<body><noscript>Goppo needs JavaScript.</noscript><span id="s">Goppo</span><script src="/static/js/bundle-boot.js"></script><script src="/static/js/bundle-index.js"></script></body></html>
"""


def render_pages(backend: str) -> dict[str, str]:
    os.environ.setdefault("SECRET_KEY", "bundle-build-only-not-a-secret")
    from app import create_app
    from app.extensions import db
    from app.models import User
    from flask import render_template
    import flask_login.utils as flu

    tmp = tempfile.mkdtemp(prefix="goppo-bundle-")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/build.db"
    from app import config as cfgmod
    cfgmod.TestingConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp}/build.db"
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = False
    pages: dict[str, str] = {}
    with app.app_context():
        db.create_all()
        for lang in LANGS:
            user = User(id=0, full_name="", email="", username="", status="active", language=lang)
            user.is_public = True
            user.notify_messages = True
            user.notify_announcements = True
            flu._get_user = lambda u=user: u                   # the page is built for a placeholder user; real data arrives at run time
            cfg = app.config
            ctx = dict(max_chars=cfg["MESSAGE_MAX_CHARS"], max_voice=cfg["MAX_VOICE_SECONDS"], max_bytes=dict(cfg["UPLOAD_MAX_BYTES"]))
            other = User(id=0, full_name="", email="", username="user", status="active")
            jobs = {
                "login": ("auth/login.html", dict(error=None, identifier="", next_url="", errors={}), "/login"),
                "messages": ("chat/inbox.html", {}, "/messages"),
                "chat": ("chat/room.html", dict(conversation=SimpleNamespace(id=0), other=other, **ctx), "/chat/0"),
                "settings": ("settings/index.html", dict(errors={}), "/settings/"),
            }
            for name, (tpl, extra, path) in jobs.items():
                with app.test_request_context(path, headers={"Cookie": f"lang={lang}"}):
                    from flask import g, session
                    session["lang"] = lang
                    html = render_template(tpl, bundle=True, **extra)
                html = rewrite_links(html, backend)
                pages[f"{name}.html" if lang == "en" else f"{name}.{lang}.html"] = html
        bundles = {}
        client = app.test_client()
        for lang in LANGS:
            u = User(id=0, full_name="", email="", username="", status="active", language=lang)
            flu._get_user = lambda u=u: u                      # the language of the placeholder user decides, as for a real person
            res = client.get("/i18n/bundle.json", headers={"Cookie": f"lang={lang}"})
            data = res.get_json()
            data["lang"] = lang
            bundles[lang] = data
    shutil.rmtree(tmp, ignore_errors=True)
    pages["__i18n__"] = json.dumps(bundles)
    return pages


def build(out: Path, backend: str, write_backend_file: bool = True) -> list[str]:
    if not backend.startswith("https://") or backend.endswith("/"):
        raise SystemExit("backend URL must be an https URL without a trailing slash")
    pages = render_pages(backend)
    bundles = json.loads(pages.pop("__i18n__"))
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name, html in pages.items():
        (out / name).write_text(html, encoding="utf-8")
    (out / "index.html").write_text(INDEX, encoding="utf-8")
    shutil.copy(ANDROID / "bundle-assets" / "offline.html", out / "offline.html")
    for lang, data in bundles.items():
        (out / "i18n").mkdir(exist_ok=True)
        (out / "i18n" / f"bundle-{lang}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    static = ROOT / "app" / "static"
    for sub in ("css", "js", "icons"):
        src = static / sub
        if src.is_dir():
            shutil.copytree(src, out / "static" / sub, ignore=shutil.ignore_patterns("*.map", "*.md"))
    for f in static.iterdir():
        if f.is_file() and f.name not in SKIP_STATIC and f.suffix in (".png", ".ico", ".svg", ".webp"):
            shutil.copy(f, out / "static" / f.name)
    shutil.copytree(ANDROID / "bundle-assets" / "fonts", out / "static" / "fonts", ignore=shutil.ignore_patterns("*.md"))
    for name in SKIP_STATIC:
        (out / "static" / name).unlink(missing_ok=True)
    problems = check(out)
    if write_backend_file:
        target = ANDROID / "android" / "app" / "src" / "main" / "assets" / "goppo-backend.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"backendUrl": backend}, indent=2) + "\n")
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    (out / "bundle-manifest.json").write_text(json.dumps(
        {"files": {f: hashlib.sha256((out / f).read_bytes()).hexdigest() for f in files}}, indent=1), encoding="utf-8")
    if problems:
        raise SystemExit("bundle check failed:\n  " + "\n  ".join(problems))
    return files


REQUIRED = ["index.html", "login.html", "messages.html", "chat.html", "settings.html", "offline.html", "login.bn.html", "messages.bn.html",
            "chat.bn.html", "settings.bn.html", "static/js/chat.js", "static/js/chatlist.js", "static/js/app.js", "static/js/platform.js",
            "static/js/calling.js", "static/js/device.js", "static/js/local-first.js", "static/js/bundle-boot.js", "static/js/bundle-index.js",
            "static/css/tokens.css", "static/css/base.css", "static/css/components.css", "static/fonts/fonts.css",
            "i18n/bundle-en.json", "i18n/bundle-bn.json"]
FORBIDDEN = [re.compile(p, re.I) for p in (r"<meta[^>]+csrf-token", r"fonts\.googleapis|fonts\.gstatic", r"https?://cdn\.", r"SECRET_KEY", r"api[_-]?key\s*[:=]", r"BEGIN (RSA |EC )?PRIVATE KEY",
                                             r"cloudinary://", r"service[_-]account")]


def check(out: Path) -> list[str]:
    bad = [f"missing {f}" for f in REQUIRED if not (out / f).is_file()]
    for p in out.rglob("*"):
        if p.is_file() and p.suffix in (".html", ".js", ".json", ".css"):
            text = p.read_text(encoding="utf-8", errors="ignore")
            for rx in FORBIDDEN:
                if rx.search(text):
                    bad.append(f"{p.relative_to(out)} matches forbidden pattern {rx.pattern!r}")
    for page in ("messages.html", "chat.html", "settings.html", "login.html"):
        f = out / page
        if f.is_file():
            for ref in re.findall(r'(?:src|href)="(/static/[^"?#]+)', f.read_text(encoding="utf-8")):
                if not (out / ref.lstrip("/")).is_file():
                    bad.append(f"{page} references missing {ref}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=ANDROID / "www")
    ap.add_argument("--backend-url", default=os.environ.get("GOPPO_BACKEND_URL", DEFAULT_BACKEND))
    ap.add_argument("--no-backend-file", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    files = build(a.out, a.backend_url.strip(), not a.no_backend_file)
    print(f"bundled {len(files)} files into {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
