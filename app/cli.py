"""Handy commands:  flask check-services"""
import click
from flask import current_app

from .services import cloudinary_service


def register_cli(app) -> None:
    @app.cli.command("check-services")
    def check_services():
        """Test the Gmail login and the Cloudinary keys without sending anything."""
        cfg = current_app.config

        from .services import email_service

        provider = cfg["MAIL_PROVIDER"]
        if not email_service.is_configured():
            click.echo(f"Email ({provider}): not configured (see .env.example)")
        elif provider == "smtp":
            try:
                smtp = email_service._smtp_connect(cfg)
                smtp.quit()
                click.echo("Email (Gmail SMTP): OK")
            except Exception as exc:  # noqa: BLE001
                click.echo(f"Email (Gmail SMTP): FAILED ({exc.__class__.__name__}: {exc}) - "
                           "on PythonAnywhere Free try MAIL_PROVIDER=sendgrid or mailgun")
        else:
            import requests

            hosts = {"sendgrid": "https://api.sendgrid.com", "mailgun": "https://api.mailgun.net",
                     "brevo": "https://api.brevo.com", "resend": "https://api.resend.com"}
            try:
                requests.get(hosts[provider], timeout=15)
                click.echo(f"Email ({provider}): host reachable (the key is checked when the first email is sent)")
            except Exception as exc:  # noqa: BLE001
                click.echo(f"Email ({provider}): FAILED ({exc.__class__.__name__}) - the host may not be on "
                           "your PythonAnywhere allow-list")

        if cloudinary_service.is_configured():
            try:
                cloudinary_service.ping()
                click.echo("Cloudinary: OK")
            except Exception as exc:  # noqa: BLE001
                click.echo(f"Cloudinary: FAILED ({exc.__class__.__name__}: {exc})")
        else:
            click.echo("Cloudinary: not configured (CLOUDINARY_* values)")
