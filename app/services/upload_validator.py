"""Backend checks for every uploaded file. The browser's checks are only a convenience.

We check the extension (from .env), the size (from .env) and the first bytes of the file
(so a renamed .exe or a text file called .png is rejected).
"""
from dataclasses import dataclass
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from .errors import UploadError


# ---- "what do the first bytes of a real file look like" -----------------------------
def _is_jpeg(h): return h[:3] == b"\xff\xd8\xff"
def _is_png(h): return h[:8] == b"\x89PNG\r\n\x1a\n"
def _is_gif(h): return h[:6] in (b"GIF87a", b"GIF89a")
def _is_webp(h): return h[:4] == b"RIFF" and h[8:12] == b"WEBP"
def _is_pdf(h): return h[:5] == b"%PDF-"
def _is_ftyp(h): return h[4:8] == b"ftyp"                      # mp4, mov, m4a, 3gp ...
def _is_ebml(h): return h[:4] == b"\x1a\x45\xdf\xa3"          # webm, mkv
def _is_ogg(h): return h[:4] == b"OggS"
def _is_wav(h): return h[:4] == b"RIFF" and h[8:12] == b"WAVE"
def _is_avi(h): return h[:4] == b"RIFF" and h[8:12] == b"AVI "
def _is_mp3(h): return h[:3] == b"ID3" or (len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0)
def _is_aac(h): return len(h) > 1 and h[0] == 0xFF and (h[1] & 0xF0) == 0xF0
def _is_zip(h): return h[:4] == b"PK\x03\x04"
def _is_ole(h): return h[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # old .doc .xls .ppt
def _is_rtf(h): return h[:5] == b"{\\rtf"
def _is_rar(h): return h[:4] == b"Rar!"
def _is_7z(h): return h[:6] == b"7z\xbc\xaf\x27\x1c"

_SNIFF = {
    "image": {"jpg": _is_jpeg, "jpeg": _is_jpeg, "png": _is_png, "gif": _is_gif, "webp": _is_webp},
    "video": {"mp4": _is_ftyp, "mov": _is_ftyp, "m4v": _is_ftyp, "3gp": _is_ftyp,
              "webm": _is_ebml, "mkv": _is_ebml, "avi": _is_avi},
    "voice": {"webm": _is_ebml, "weba": _is_ebml, "ogg": _is_ogg, "opus": _is_ogg, "mp3": _is_mp3,
              "m4a": _is_ftyp, "mp4": _is_ftyp, "aac": _is_aac, "wav": _is_wav},
    "document": {"pdf": _is_pdf, "docx": _is_zip, "xlsx": _is_zip, "pptx": _is_zip,
                 "doc": _is_ole, "xls": _is_ole, "ppt": _is_ole, "rtf": _is_rtf},
    "file": {"zip": _is_zip, "rar": _is_rar, "7z": _is_7z},
}
# Pictures, video and audio must PROVE they are real. Documents and "other files"
# are checked when we know their signature (plain .txt/.csv have none).
_STRICT = {"image", "video", "voice"}


@dataclass
class ValidatedUpload:
    stream: object
    filename: str
    extension: str
    size: int
    kind: str


def kind_for_extension(ext: str, hint: str | None = None) -> str | None:
    """Which group (image/video/document/file) does this extension belong to?"""
    allowed = current_app.config["UPLOAD_ALLOWED_EXTENSIONS"]
    order = ["image", "video", "document", "file"]
    if hint in ("voice",):
        order = ["voice"]
    for kind in order:
        if ext in allowed.get(kind, []):
            return kind
    return None


def validate_upload(file_storage, kind: str) -> ValidatedUpload:
    """kind: image | video | voice | document | file. Raises UploadError(translation key)."""
    cfg = current_app.config
    if file_storage is None or not file_storage.filename:
        raise UploadError("upload.required")

    filename = secure_filename(file_storage.filename) or "file"
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in cfg["UPLOAD_ALLOWED_EXTENSIONS"].get(kind, []):
        raise UploadError("upload.bad_type")

    stream = file_storage.stream
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    if size == 0:
        raise UploadError("upload.empty")
    limit = cfg["UPLOAD_MAX_BYTES"][kind]
    if size > limit:
        raise UploadError("upload.too_large", size=max(1, limit // (1024 * 1024)))

    check = _SNIFF.get(kind, {}).get(ext)
    if check is None and kind in _STRICT:
        raise UploadError("upload.corrupt")        # we cannot prove it is real
    if check is not None:
        head = stream.read(16)
        stream.seek(0)
        if not check(head):
            raise UploadError("upload.corrupt")

    return ValidatedUpload(stream=stream, filename=filename, extension=ext, size=size, kind=kind)
