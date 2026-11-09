from flask import current_app

from ..extensions import db
from . import cloudinary_service
from .errors import FieldErrors, ServiceError
from .upload_validator import validate_upload

BIO_MAX = 300


def _pick(file):
    return file is not None and bool(file.filename)


def update_profile(user, form, avatar_file, cover_file) -> None:
    """Edit name, bio, profile photo, cover photo. The username is never touched."""
    errors: dict = {}
    full_name = " ".join((form.get("full_name") or "").split())
    bio = "\n".join(line.rstrip() for line in (form.get("bio") or "").strip().splitlines())
    if not 2 <= len(full_name) <= 60:
        errors["full_name"] = ("err.full_name", {})
    if len(bio) > BIO_MAX:
        errors["bio"] = ("err.bio_long", {"max": BIO_MAX})

    # Check BOTH pictures before uploading either, so a bad one wastes nothing.
    uploads = {}
    for field, file in (("avatar", avatar_file), ("cover", cover_file)):
        if _pick(file):
            try:
                uploads[field] = validate_upload(file, "image")
            except ServiceError as exc:
                errors[field] = (exc.key, exc.values)
    if errors:
        raise FieldErrors(errors)

    new_assets = {}
    for field, upload in uploads.items():
        try:
            new_assets[field] = cloudinary_service.upload_image(upload, field)
        except ServiceError as exc:
            for asset in new_assets.values():  # undo the one that did upload
                cloudinary_service.delete_asset(asset["public_id"])
            raise FieldErrors({field: (exc.key, exc.values)})

    old_ids = []
    user.full_name = full_name
    user.bio = bio
    if "avatar" in new_assets:
        old_ids.append(user.avatar_public_id)
        user.avatar_url = new_assets["avatar"]["url"]
        user.avatar_public_id = new_assets["avatar"]["public_id"]
    if "cover" in new_assets:
        old_ids.append(user.cover_public_id)
        user.cover_url = new_assets["cover"]["url"]
        user.cover_public_id = new_assets["cover"]["public_id"]
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        for asset in new_assets.values():
            cloudinary_service.delete_asset(asset["public_id"])
        raise
    for public_id in old_ids:
        cloudinary_service.delete_asset(public_id)
    current_app.logger.info("Profile updated: %s", user.username)


def set_privacy(user, account_type: str) -> None:
    if account_type not in ("public", "private"):
        raise FieldErrors({"account_type": ("err.account_type", {})})
    user.is_public = account_type == "public"
    db.session.commit()
