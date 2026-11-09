"""Handy commands:  flask check-services"""
import smtplib
import ssl

import click
from flask import current_app

from .services import cloudinary_service


def register_cli(app) -> None:
    @app.cli.command("check-services")
    def check_services():
        """Test the Gmail login and the Cloudinary keys without sending anything."""
        cfg = current_app.config

        if cfg["MAIL_USERNAME"] and cfg["MAIL_PASSWORD"]:
            try:
                with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15) as smtp:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
                click.echo("Gmail SMTP: OK")
            except Exception as exc:  # noqa: BLE001
                click.echo(f"Gmail SMTP: FAILED ({exc.__class__.__name__}: {exc})")
        else:
            click.echo("Gmail SMTP: not configured (MAIL_USERNAME / MAIL_PASSWORD)")

        if cloudinary_service.is_configured():
            try:
                cloudinary_service.ping()
                click.echo("Cloudinary: OK")
            except Exception as exc:  # noqa: BLE001
                click.echo(f"Cloudinary: FAILED ({exc.__class__.__name__}: {exc})")
        else:
            click.echo("Cloudinary: not configured (CLOUDINARY_* values)")
