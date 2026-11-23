"""Rules for the files the browser receives, plus the file-sniffing checks."""
import io
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = list((ROOT / "app" / "templates").rglob("*.html"))
SCRIPTS = list((ROOT / "app" / "static" / "js").glob("*.js"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_templates_follow_the_content_security_policy(path):
    """The CSP forbids inline scripts, inline styles and inline event handlers."""
    html = path.read_text(encoding="utf-8")
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert "src=" in tag, f"inline <script> in {path.name}"
    assert not re.search(r"\sstyle\s*=", html), f"inline style in {path.name}"
    assert not re.search(r"\son[a-z]+\s*=\s*[\"']", html), f"inline event handler in {path.name}"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_javascript_has_valid_syntax(path):
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_javascript_never_writes_html_from_data(path):
    """User text must only go through textContent. innerHTML is allowed only for fixed markup."""
    code = path.read_text(encoding="utf-8")
    for line in code.splitlines():
        if "innerHTML" in line or "insertAdjacentHTML" in line or "document.write" in line:
            assert "${" not in line and "+" not in line.split("innerHTML")[-1], line


def test_secrets_are_not_in_frontend_files():
    for path in SCRIPTS + TEMPLATES:
        text = path.read_text(encoding="utf-8").lower()
        assert "api_secret" not in text and "cloudinary_api" not in text




def test_every_env_variable_in_config_is_documented():
    config = (ROOT / "app" / "config.py").read_text()
    names = set(re.findall(r'env_(?:str|int|mb|list|bool)\(\s*"([A-Z_]+)"', config))
    example = (ROOT / ".env.example").read_text()
    missing = {n for n in names if not re.search(rf"^{n}=", example, re.M)}
    assert not missing, f"missing from .env.example: {sorted(missing)}"


# ---------------------------------------------------------------- upload sniffing
from app.services.errors import UploadError  # noqa: E402
from app.services.upload_validator import kind_for_extension, validate_upload  # noqa: E402
from werkzeug.datastructures import FileStorage  # noqa: E402


def upload(app, data, name, kind):
    with app.test_request_context():
        return validate_upload(FileStorage(io.BytesIO(data), filename=name), kind)


@pytest.mark.parametrize("kind,name,data", [
    ("image", "a.jpg", b"\xff\xd8\xff\xe0" + b"0" * 20), ("image", "a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20),
    ("image", "a.gif", b"GIF89a" + b"0" * 20), ("image", "a.webp", b"RIFF0000WEBP" + b"0" * 20),
    ("video", "a.mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 20), ("video", "a.webm", b"\x1a\x45\xdf\xa3" + b"0" * 20),
    ("voice", "a.mp3", b"ID3" + b"0" * 20), ("voice", "a.mp3", b"\xff\xfb" + b"0" * 20),
    ("voice", "a.wav", b"RIFF0000WAVE" + b"0" * 20), ("voice", "a.ogg", b"OggS" + b"0" * 20),
    ("voice", "a.m4a", b"\x00\x00\x00\x18ftypM4A " + b"0" * 20),
    ("document", "a.pdf", b"%PDF-1.7" + b"0" * 20), ("document", "a.docx", b"PK\x03\x04" + b"0" * 20),
    ("document", "a.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 20), ("document", "a.txt", b"plain"),
    ("file", "a.zip", b"PK\x03\x04" + b"0" * 20), ("file", "a.rar", b"Rar!" + b"0" * 20),
    ("file", "a.7z", b"7z\xbc\xaf\x27\x1c" + b"0" * 20), ("file", "a.csv", b"a,b"),
])
def test_real_files_are_accepted(app, kind, name, data):
    assert upload(app, data, name, kind).size == len(data)


@pytest.mark.parametrize("kind,name,data", [
    ("image", "a.png", b"\xff\xd8\xff\xe0" + b"0" * 20),       # a jpeg renamed to png
    ("image", "a.jpg", b"<?php echo 1; ?>"), ("video", "a.mp4", b"%PDF-1.7" + b"0" * 20),
    ("voice", "a.webm", b"OggS" + b"0" * 20), ("document", "a.pdf", b"MZ" + b"0" * 20),
    ("file", "a.zip", b"%PDF-1.7" + b"0" * 20), ("image", "a.svg", b"<svg/>"),
])
def test_disguised_files_are_rejected(app, kind, name, data):
    with pytest.raises(UploadError):
        upload(app, data, name, kind)


def test_extension_groups(app):
    with app.app_context():
        assert kind_for_extension("png") == "image" and kind_for_extension("pdf") == "document"
        assert kind_for_extension("zip") == "file" and kind_for_extension("webm") == "video"
        assert kind_for_extension("webm", "voice") == "voice" and kind_for_extension("exe") is None


def test_dangerous_names_are_cleaned(app):
    out = upload(app, b"\x89PNG\r\n\x1a\n" + b"0" * 20, "../../etc/passwd.png", "image")
    assert "/" not in out.filename and ".." not in out.filename
