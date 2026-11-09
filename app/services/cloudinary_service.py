"""All Cloudinary calls live here. The API secret never leaves the server."""
import hashlib
import os
import time
from types import SimpleNamespace

import requests
from flask import current_app

from .errors import UploadError
from .upload_validator import ValidatedUpload

# Pictures are resized on upload so we never store huge originals.
_TRANSFORMS = {
    "avatar": [{"width": 512, "height": 512, "crop": "fill", "gravity": "auto"}],
    "cover": [{"width": 1600, "crop": "limit"}],
}
# Cloudinary stores audio under "video", and PDFs/zips/etc. as "raw".
_RESOURCE_TYPE = {"image": "image", "video": "video", "voice": "video", "document": "raw", "file": "raw"}
_FOLDER_KEY = {"image": "image", "video": "video", "voice": "voice", "document": "document", "file": "file"}


def is_configured() -> bool:
    cfg = current_app.config
    return all(cfg.get(k) for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))


# ---------------------------------------------------------------------------------------
# WHY THIS FILE DOES NOT USE THE "cloudinary" PYTHON PACKAGE FOR NETWORK CALLS
#
# On PythonAnywhere (free plan) the internet works ONLY through the proxy that PythonAnywhere
# puts in the environment (http_proxy / https_proxy). The `requests` library reads those
# variables by itself, so it just works - this is exactly what the Math Class site does.
# The cloudinary package talks through urllib3 directly and ignores them, so uploads failed.
# We now sign and send the request ourselves with `requests` (same as Math Class).
# ---------------------------------------------------------------------------------------
_API_BASE = "https://api.cloudinary.com/v1_1"
_TIMEOUT = (15, 180)          # (connect seconds, read seconds) - videos can be 25 MB


def _creds() -> tuple[str, str, str]:
    cfg = current_app.config
    return (str(cfg["CLOUDINARY_CLOUD_NAME"]).strip(), str(cfg["CLOUDINARY_API_KEY"]).strip(),
            str(cfg["CLOUDINARY_API_SECRET"]).strip())


def _sign(params: dict, secret: str) -> str:
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] not in (None, ""))
    return hashlib.sha1((to_sign + secret).encode("utf-8")).hexdigest()


def _str(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _transformation_string(spec) -> str:
    """[{"width": 512, "crop": "fill"}] -> "c_fill,g_auto,h_512,w_512"."""
    short = {"width": "w", "height": "h", "crop": "c", "gravity": "g"}
    parts = []
    for step in spec if isinstance(spec, list) else [spec]:
        parts.append(",".join(f"{short[k]}_{v}" for k, v in sorted(step.items(), key=lambda kv: short[kv[0]])))
    return "/".join(parts)


def _proxies() -> dict | None:
    """Optional: CLOUDINARY_API_PROXY in .env forces a proxy. Otherwise `requests` uses the environment."""
    forced = (os.environ.get("CLOUDINARY_API_PROXY") or "").strip()
    return {"http": forced, "https": forced} if forced else None


def _post(resource_type: str, action: str, params: dict, files: dict | None = None) -> dict:
    cloud, key, secret = _creds()
    params = {k: _str(v) for k, v in params.items() if v is not None}
    params["timestamp"] = str(int(time.time()))
    payload = dict(params, api_key=key, signature=_sign(params, secret))
    resp = requests.post(f"{_API_BASE}/{cloud}/{resource_type}/{action}", data=payload, files=files,
                         timeout=_TIMEOUT, proxies=_proxies())
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200:
        message = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else None
        raise RuntimeError(f"Cloudinary HTTP {resp.status_code}: {message or resp.text[:200]}")
    return body


def _api_upload(stream, **options) -> dict:
    """Same call shape the code (and the tests) always used: upload(stream, folder=..., resource_type=...)."""
    options = dict(options)
    resource_type = options.pop("resource_type", "image")
    params = {
        "folder": options.pop("folder", None),
        "unique_filename": options.pop("unique_filename", None),
        "overwrite": options.pop("overwrite", None),
        "use_filename": options.pop("use_filename", None),
    }
    transformation = options.pop("transformation", None)
    if transformation:
        params["transformation"] = _transformation_string(transformation)
    filename = options.pop("filename", None) or getattr(stream, "filename", None) or "upload"
    return _post(resource_type, "upload", params, files={"file": (filename, stream.read())})


def _api_destroy(public_id: str, **options) -> dict:
    return _post(options.get("resource_type", "image"), "destroy",
                 {"public_id": public_id, "invalidate": bool(options.get("invalidate", True))})


def ping() -> None:
    """Used by `flask check-services`. Raises on any problem."""
    cloud, key, secret = _creds()
    resp = requests.get(f"{_API_BASE}/{cloud}/ping", auth=(key, secret), timeout=_TIMEOUT[0] + 10,
                        proxies=_proxies())
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")


# Tests (and the browser test server) replace these two functions with fakes.
cloudinary = SimpleNamespace(uploader=SimpleNamespace(upload=_api_upload, destroy=_api_destroy))


def _upload(upload: ValidatedUpload, folder_key: str, resource_type: str, extra: dict | None = None) -> dict:
    if not is_configured():
        current_app.logger.error("Cloudinary is not configured (check .env)")
        raise UploadError("upload.not_configured")
    options = {
        "folder": current_app.config["CLOUDINARY_FOLDERS"][folder_key],
        "resource_type": resource_type,
        "unique_filename": True,
        "overwrite": False,
        "filename": upload.filename,
        **(extra or {}),
    }
    try:
        upload.stream.seek(0)
        result = cloudinary.uploader.upload(upload.stream, **options)
        return {
            "public_id": result["public_id"],
            "url": result["secure_url"],
            "resource_type": resource_type,
        }
    except Exception as exc:  # noqa: BLE001 - network, auth, quota... all become one friendly error
        # The REAL reason goes to the PythonAnywhere error log; the user sees only a friendly message.
        current_app.logger.error("Cloudinary upload failed (folder=%s): %r", folder_key, exc)
        raise UploadError("upload.failed") from exc


def upload_image(upload: ValidatedUpload, folder_key: str) -> dict:
    """Profile pictures. folder_key: avatar | cover."""
    extra = {"transformation": _TRANSFORMS[folder_key]} if folder_key in _TRANSFORMS else None
    return _upload(upload, folder_key, "image", extra)


def upload_media(upload: ValidatedUpload) -> dict:
    """Chat files. upload.kind decides folder and Cloudinary resource type."""
    kind = upload.kind
    extra = {"use_filename": True} if _RESOURCE_TYPE[kind] == "raw" else None
    return _upload(upload, _FOLDER_KEY[kind], _RESOURCE_TYPE[kind], extra)


def delete_asset(public_id: str | None, resource_type: str = "image") -> None:
    """Best effort clean-up. Never raises."""
    if not public_id or not is_configured():
        return
    try:
        cloudinary.uploader.destroy(public_id, resource_type=resource_type, invalidate=True)
    except Exception:  # noqa: BLE001
        current_app.logger.warning("Could not delete Cloudinary asset %s", public_id)
