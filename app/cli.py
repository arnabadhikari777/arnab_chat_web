"""Handy commands:  flask check-services"""
import click
from flask import current_app

from .services import cloudinary_service


def register_cli(app) -> None:
    @app.cli.command("restore-db")
    @click.argument("backup", type=click.Path(exists=True, dir_okay=False))
    def restore_db(backup):
        """Replace the live database with a backup file (a safety copy of the current one is made first)."""
        import sqlite3
        from pathlib import Path

        from .extensions import db
        from .services import db_admin_service as dbs

        if not dbs.supported():
            raise click.ClickException("Only a SQLite database file can be restored with this command.")
        check = dbs.verify_file(Path(backup))
        if not check["ok"]:
            raise click.ClickException(f"That backup is damaged ({check['integrity']}). Nothing was changed.")
        click.echo(f"Backup is healthy and has {check['users']} users.")
        click.echo("This REPLACES the current database. Everything written after the backup will be lost.")
        if click.prompt("Type RESTORE (capital letters) to continue") != "RESTORE":
            raise click.ClickException("Cancelled. Nothing was changed.")
        safety = dbs.create_backup("-pre-restore")
        click.echo(f"Safety copy of the current database: instance/backups/{safety}")
        db.session.remove()
        db.engine.dispose()
        src = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
        dst = sqlite3.connect(str(dbs._db_file()))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        click.echo("Done. Now press the green Reload button on the PythonAnywhere Web tab.")

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

    @app.cli.command("push-selfcheck")
    def push_selfcheck():
        """Test every server-side step of Web Push and print the exact failure (run on PythonAnywhere)."""
        import time
        from importlib import metadata

        from sqlalchemy import inspect

        from .extensions import db
        from .models import PushDelivery, PushSubscription, User
        from .services import push_service

        def ok(msg): click.echo(f"OK      {msg}")
        def bad(msg): click.echo(f"PROBLEM {msg}")

        st = push_service.vapid_status()
        if st.configured:
            ok("VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY and VAPID_CONTACT are valid and match")
        else:
            for n in st.missing: bad(f"{n} is missing in .env")
            for pr in st.problems: bad(pr)
        try:
            ok("pywebpush " + metadata.version("pywebpush"))
        except Exception as exc:  # noqa: BLE001
            bad(f"pywebpush is not installed in this virtualenv ({exc}). Run: pip install -r requirements.txt")
        if st.configured:
            try:
                from py_vapid import Vapid
                Vapid.from_string(private_key=st.private_raw).sign(
                    {"sub": st.contact, "aud": "https://fcm.googleapis.com", "exp": int(time.time()) + 3600})
                ok("a VAPID signature can be created with your private key")
            except Exception as exc:  # noqa: BLE001
                bad(f"signing failed: {exc.__class__.__name__}: {exc}")
        insp = inspect(db.engine)
        for model in (PushSubscription, PushDelivery):
            table = model.__table__.name
            if table not in insp.get_table_names():
                bad(f"table {table} does not exist in the database. Run: flask db upgrade")
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            missing = [c.name for c in model.__table__.columns if c.name not in have]
            if missing:
                bad(f"table {table} lacks columns {missing}. Run: flask db upgrade")
            else:
                ok(f"table {table} has every column")
        user = User.query.first()
        if user is None:
            bad("no users in the database to test with")
            return
        try:
            row = PushSubscription(user_id=user.id, endpoint="https://selfcheck.invalid/x", p256dh="p", auth="a", status="active")
            db.session.add(row)
            db.session.flush()
            ok("a subscription row can be written (this test row is rolled back, nothing is kept)")
        except Exception as exc:  # noqa: BLE001
            bad(f"writing a subscription row failed: {exc.__class__.__name__}: {exc}")
        finally:
            db.session.rollback()
