> **Historic report from an earlier repair phase.** The current migration head is `d4f6a8c0e2b3` (chain: …→ `b2c4e6f8a1d3` → `c3d5e7f9a1b2` → `d4f6a8c0e2b3`). Current test status: `../docs/TESTING_REPORT.md`.

# Goppo – Gmail OTP repair and project audit (this round)

## Honest scope statement
- This sandbox had **no network**: Flask-SQLAlchemy, Flask-Login, Flask-WTF, Flask-Limiter, Flask-Migrate, pytest and
  pywebpush could not be installed. **The app was not started, the pytest suite was not run, migrations were not run.**
- **No real Gmail email was sent. Gmail delivery is NOT verified.** Only simulated SMTP (recorded calls) was used.
- The project already had several earlier repair rounds. This round did not rewrite it; it changed only what the audit found.

## Bugs / gaps found in the OTP system
| # | Finding | Severity |
|---|---|---|
| 1 | Verification emails were plain text only: no branding, no visible expiry block, no security warning | Medium |
| 2 | An expired code showed the same message as a wrong code ("wrong or has expired"); users could not tell what to do | Low |
| 3 | `verify_otp` read the row, then wrote `used_at` / `attempts` with a Python-side update. Two simultaneous requests could both spend the same code (replay) or lose a wrong-try count | Medium (security) |
| 4 | "Change password" codes used the sign-up wording ("if you did not create an account") | Low |
| 5 | SMTP failures were logged with the raw exception only; a wrong App Password, blocked port and TLS mix-up looked alike | Low |
| 6 | `.env.example` lacked `MAIL_PROVIDER`, `MAIL_TIMEOUT_SECONDS`, `MAIL_FROM_ADDRESS`, API-provider keys and all `OTP_*` settings | Low |

## Fixed
1. New `app/services/email_templates.py`: responsive one-column HTML email (branding, large code, expiry, security warning, footer; inline styles, no remote images/fonts, all text translated EN/BN, app name HTML-escaped). Sent as multipart/alternative with the **unchanged plain-text part first**, for SMTP, SendGrid, Mailgun, Brevo and Resend.
2. `otp.expired` message (EN + BN). Missing/used code still says `otp.invalid`.
3. Wrong tries are counted with `attempts = attempts + 1` in SQL; a right code is spent with `UPDATE ... WHERE used_at IS NULL` and only the request that changes one row succeeds.
4. `change_password` now uses the reset-style wording and heading.
5. `_hint()` adds one plain-language line to the server log (auth refused, timeout, TLS, unreachable host, sender/recipient refused). Password never logged.
6. `.env.example` completed with placeholders only.
7. Test doubles accept the new optional `html` argument (`tests/conftest.py`, `tests/test_auth.py`); the expiry test now expects the new message.

## Already correct (checked by reading, not changed)
`secrets.choice` OTP generation; HMAC-SHA256 hash of the code (code never stored); configurable length/expiry/cooldown/max attempts/sends per hour; older codes invalidated on a new issue; code discarded when email fails; 587 = STARTTLS, 465 = implicit SSL; timeout; App Password spaces stripped; credentials only from env; same-looking reset response for unknown emails; per-IP and per-account limits on login/register/verify/resend; session rotated at login; unverified users cannot log in or become admin.

## Test results
| Test | Result | Kind |
|---|---|---|
| `ast.parse` on 103 Python files | PASS | real |
| JSON validity of all `.json`, EN/BN i18n key parity | PASS | real |
| Jinja parse of all templates | PASS (parse only, not rendered) | real |
| `node --check` on all static JS | PASS | real |
| Harness `/tmp/harness.py` (real `email_service`, `email_templates`, `i18n` code with stubbed Flask-Login): 587 STARTTLS, 465 SSL, multipart order, code still parseable by existing tests, HTML content, escaping, Bengali, change-password wording, auth-failure log hint without password, 4 HTTP providers carry HTML, plain-only send | PASS 11/11 | **simulated SMTP/HTTP** |
| `tests/test_otp_email.py` (new, 16 tests) | **NOT RUN** (needs pytest + Flask-SQLAlchemy) | – |
| Existing pytest suite | **NOT RUN** | – |
| Real Gmail delivery | **NOT TESTED** | – |
| Migrations / DB tests / browser tests | **NOT RUN** | – |

## Remaining items needing you
1. On the server: `pip install -r requirements-dev.txt && pytest -q` and send me any failure. Run `flask check-services`, then register with a real address.
2. Set `MAIL_USERNAME`, `MAIL_PASSWORD` (App Password), `MAIL_FROM_NAME`, `SECRET_KEY`, `ADMIN_EMAILS` in `.env`; reload the web app.
3. PythonAnywhere Free may block SMTP; if `check-services` fails with a timeout/connection error, use `MAIL_PROVIDER=brevo` (or another) instead.
4. Per-IP limits (register 10/h, verify 15/h, resend 10/h) are shared by everyone behind one school/mobile NAT; many students on one network may hit them. Not changed; raise them if this happens.
5. SSO tokens (`/sso?token=`) expire but are not single-use, and an SSO login marks an unverified account verified. This relies on the shared secret staying private. Not changed.
6. The wider audit (admin panel, database, front-end) was done by reading only for the OTP/auth path; I did not find or fix issues outside it this round.
