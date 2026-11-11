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


    @app.cli.command("generate-vapid-keys")
    def generate_vapid_keys():
        """Print a fresh VAPID key pair for Web Push. Put BOTH lines in .env (never share the private one)."""
        from .services import vapid

        private, public = vapid.generate_keypair()
        click.echo(f"VAPID_PRIVATE_KEY={private}")
        click.echo(f"VAPID_PUBLIC_KEY={public}")
        click.echo("VAPID_CONTACT=mailto:your-real-email@gmail.com   # edit this one yourself")

    @app.cli.command("push-status")
    def push_status():
        """Say exactly which Web Push setting is missing or wrong."""
        from .services import push_service

        st = push_service.vapid_status()
        if st.configured:
            click.echo("Web Push: configured. (Keys match; contact looks valid.)")
            return
        for name in st.missing:
            click.echo(f"MISSING  {name}")
        for problem in st.problems:
            click.echo(f"PROBLEM  {problem}")
        click.echo("Fix .env, reload the web app, then run this again. New keys: flask generate-vapid-keys")
