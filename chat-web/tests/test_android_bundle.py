"""Task 2, Part A: the Android app's bundled frontend (android-app/www) is built FROM the website's own templates / CSS / JS.

The build runs in a subprocess (tools/build_android_web.py) exactly as CI runs it. These tests check the generated files; they do not
prove the APK opens on a phone (see the report's manual checklist)."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = "https://backend.example.org"


@pytest.fixture(scope="module")
def www(tmp_path_factory):
    out = tmp_path_factory.mktemp("www")
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "build_android_web.py"), "--out", str(out), "--backend-url", BACKEND, "--no-backend-file"],
                         cwd=ROOT, capture_output=True, text=True, timeout=240)
    assert res.returncode == 0, res.stdout + res.stderr
    return out


def read(www, name):
    return (www / name).read_text(encoding="utf-8")


PAGES = ["login", "messages", "chat", "settings"]


def test_every_page_exists_in_both_languages_plus_start_and_offline_pages(www):
    for p in PAGES:
        assert (www / f"{p}.html").is_file() and (www / f"{p}.bn.html").is_file()
    assert (www / "index.html").is_file() and (www / "offline.html").is_file()
    assert 'lang="bn"' in read(www, "chat.bn.html") and 'lang="en"' in read(www, "chat.html")


def test_fonts_icons_css_js_and_language_files_are_bundled(www):
    for f in ["static/fonts/fonts.css", "static/fonts/figtree-latin-400-normal.woff2", "static/fonts/hind-siliguri-bengali-400-normal.woff2",
              "static/css/tokens.css", "static/css/base.css", "static/css/components.css", "static/icons/icon-192.png",
              "static/js/app.js", "static/js/chat.js", "static/js/chatlist.js", "static/js/calling.js", "static/js/platform.js",
              "static/js/device.js", "static/js/voice.js", "static/js/badge.js", "static/js/push.js", "static/js/local-first.js",
              "static/js/bundle-boot.js", "static/js/bundle-index.js", "i18n/bundle-en.json", "i18n/bundle-bn.json"]:
        assert (www / f).is_file(), f


def test_every_static_file_a_page_refers_to_is_in_the_bundle(www):
    for p in PAGES + ["index"]:
        for lang in ("", ".bn"):
            f = www / f"{p}{lang}.html"
            if f.exists():
                for ref in re.findall(r'(?:src|href)="(/static/[^"?#]+)', f.read_text(encoding="utf-8")):
                    assert (www / ref.lstrip("/")).is_file(), f"{f.name} -> {ref}"
    css = read(www, "static/fonts/fonts.css")
    for ref in re.findall(r"url\('([^']+)'\)", css):
        assert (www / "static/fonts" / ref).is_file(), ref


def test_no_single_byte_comes_from_a_remote_host_and_no_secret_is_bundled(www):
    for f in www.rglob("*"):
        if f.is_file() and f.suffix in (".html", ".css", ".js", ".json"):
            text = f.read_text(encoding="utf-8", errors="ignore")
            assert not re.search(r"fonts\.(googleapis|gstatic)", text), f
            assert not re.search(r"<meta[^>]+csrf-token", text), f"{f}: a CSRF token must never be baked into the APK"
            for secret in ("SECRET_KEY", "CLOUDINARY_API_SECRET", "FCM_SERVICE_ACCOUNT", "PRIVATE KEY", "bundle-build-only-not-a-secret"):
                assert secret not in text, f"{f} contains {secret}"
    for p in PAGES:
        html = read(www, f"{p}.html")
        assert not re.findall(r'<script[^>]+src="https?://', html), p
        assert 'rel="manifest"' not in html            # PWA manifest is for the website only


def test_every_link_is_either_a_bundled_page_a_static_file_or_an_absolute_link_to_the_website(www):
    allowed = re.compile(r"^(/static/|/(login|messages|chat|settings)\.html|#|" + re.escape(BACKEND) + r"/)")
    for p in PAGES:
        for lang in ("", ".bn"):
            for ref in re.findall(r'(?:href|action)="([^"]*)"', read(www, f"{p}{lang}.html")):
                assert allowed.match(ref), f"{p}{lang}: {ref}"
    inbox = read(www, "messages.html")
    assert f'href="{BACKEND}/people' in inbox                         # people list is a hosted page
    assert 'href="/settings.html"' in inbox                           # settings has a bundled twin


def test_script_order_is_boot_then_platform_then_app_then_local_first_then_page_scripts(www):
    html = read(www, "chat.html")
    order = [m for m in re.findall(r'src="/static/js/([a-z\-]+)\.js"', html)]
    assert order[0] == "bundle-boot"
    assert order.index("platform") < order.index("app") < order.index("local-first") < order.index("calling") < order.index("chat")


def test_the_pages_are_the_websites_own_markup_not_a_second_ui(www):
    chat = read(www, "chat.html")
    for marker in ['id="chat-messages"', 'id="composer"', 'id="msg-input"', 'id="btn-attach"', 'id="load-older"', 'id="chat-offline"', 'id="file-media"']:
        assert marker in chat
    assert 'id="chatlist"' in read(www, "messages.html")
    settings = read(www, "settings.html")
    assert 'id="android-settings"' in settings and 'data-android-open="calls"' in settings and 'data-android-open="messages"' in settings and 'data-android-open="allow"' in settings
    assert 'id="media-settings"' in settings and 'id="media-auto"' in settings and 'id="media-photos"' in settings and 'id="media-videos"' in settings and 'id="media-clear"' in settings
    assert 'id="bundle-logout"' in settings
    assert 'method="post"' not in settings and "push-settings" not in settings          # server forms / Web Push do not exist in the app
    # the JavaScript and CSS are copied byte for byte: one copy of the UI, not two
    for rel in ["static/js/chat.js", "static/js/app.js", "static/js/calling.js", "static/css/components.css"]:
        a = hashlib.sha256((www / rel).read_bytes()).hexdigest()
        b = hashlib.sha256((ROOT / "app" / rel).read_bytes()).hexdigest()
        assert a == b, rel


def test_language_bundles_match_the_server_and_carry_the_exact_deletion_text(www):
    en = json.loads(read(www, "i18n/bundle-en.json"))
    bn = json.loads(read(www, "i18n/bundle-bn.json"))
    assert en["lang"] == "en" and bn["lang"] == "bn"
    assert en["messages"]["chat.deleted"] == "This message was deleted for everyone."
    for k in ("media.title", "media.auto", "media.photos", "media.videos", "media.clear", "local.waiting", "local.offline"):
        assert k in en["messages"] and k in bn["messages"]


def test_manifest_lists_every_file_with_its_hash(www):
    manifest = json.loads(read(www, "bundle-manifest.json"))["files"]
    assert "chat.html" in manifest and "static/js/local-first.js" in manifest
    for rel, digest in manifest.items():
        assert hashlib.sha256((www / rel).read_bytes()).hexdigest() == digest


# ---------------------------------------------------------------- the website itself must not change
def test_the_website_pages_are_unchanged_in_normal_mode(app):
    from tests.conftest import login_as, verified
    b = login_as(app, verified("webuser"))
    for path in ("/messages", "/settings"):
        html = b.get(path).get_data(as_text=True)
        assert 'name="csrf-token"' in html and "fonts.googleapis.com" in html
        assert "bundle-boot.js" not in html and "local-first.js" not in html
    settings = b.get("/settings").get_data(as_text=True)
    assert "push-settings" in settings and 'method="post"' in settings and "media-settings" not in settings


# ---------------------------------------------------------------- routing table shared with Kotlin (UrlPolicyTest has the same cases)
@pytest.mark.parametrize("path,expected", [
    ("/messages", "messages.html"), ("/", "messages.html"), ("/chat/12", "chat.html?c=12"), ("/chat/12/?x=1", "chat.html?c=12"),
    ("/settings/", "settings.html"), ("/login", "login.html"), ("/settings/?hosted=1", None), ("/people", None), ("/chat/abc", None), ("/admin/users", None),
])
def test_route_table_matches_the_kotlin_one(path, expected):
    sys.path.insert(0, str(ROOT / "tools"))
    import build_android_web as b
    assert b.to_local(path) == expected


def test_build_refuses_a_non_https_backend(tmp_path):
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "build_android_web.py"), "--out", str(tmp_path / "x"), "--backend-url", "http://insecure.example", "--no-backend-file"],
                         cwd=ROOT, capture_output=True, text=True)
    assert res.returncode != 0 and "https" in (res.stdout + res.stderr)
