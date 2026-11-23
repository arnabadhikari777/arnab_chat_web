"""Runs the REAL app on port 5000 but with a fake Cloudinary, so uploads work offline."""
import itertools

from app import create_app
from app.services import cloudinary_service

counter = itertools.count(1)


def fake_upload(stream, **options):
    n = next(counter)
    stream.read()
    return {"public_id": f"fake{n}", "secure_url": f"https://res.cloudinary.com/demo/{options['resource_type']}/fake{n}"}


cloudinary_service.cloudinary.uploader.upload = fake_upload
cloudinary_service.cloudinary.uploader.destroy = lambda *a, **k: {"result": "ok"}
create_app().run(port=5000)
