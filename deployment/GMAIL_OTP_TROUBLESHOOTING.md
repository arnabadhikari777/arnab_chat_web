# Gmail OTP troubleshooting

## 1. Gmail settings

For Gmail SMTP, set these values in the deployed site's `.env`:

```dotenv
MAIL_PROVIDER=smtp
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=youraddress@gmail.com
MAIL_PASSWORD=your-16-character-google-app-password
MAIL_FROM_NAME=Goppo
MAIL_FROM_ADDRESS=youraddress@gmail.com
```

Use a Google **App Password**, not the normal Gmail password. App Passwords require
2-Step Verification on the Google account. If you use port `465`, the repaired mail
service uses implicit SSL automatically; port `587` uses STARTTLS.

Never paste the App Password into chat, source code, screenshots, or a public repository.

## 2. Test from the actual hosting environment

Activate the same virtualenv used by the web app, go to the project directory, and run:

```bash
flask check-services
```

Expected result: `Email (Gmail SMTP): OK`. This checks SMTP connectivity and authentication;
it does not send a real OTP. Then reload the web app and try a new registration.

## 3. Interpret common failures

- `SMTPAuthenticationError` / `535`: wrong App Password, 2-Step Verification/App Password
  setup issue, or the Google account is blocking the sign-in.
- `TimeoutError`, `ConnectionError`, or connection refused: hosting/network policy or
  outbound SMTP restriction. On PythonAnywhere Free, use an HTTPS mail provider supported
  by the account instead of repeatedly changing Gmail passwords.
- `Email ... not configured`: `MAIL_USERNAME` or `MAIL_PASSWORD` is missing/empty, or
  the web app has not loaded the expected `.env`.
- `Email ... OK` but no message appears: check Gmail Spam/Promotions, the recipient address,
  and the server log for a provider rejection.

After changing `.env`, reload the web app. The `.env` file must be in the project root
(the same directory as `wsgi.py`), and the web app must use the virtualenv where the
project requirements are installed.

## 4. OTP behavior

OTP codes expire after `OTP_EXPIRY_MINUTES` (default 10 minutes). Resending is limited by
`OTP_RESEND_COOLDOWN_SECONDS` (default 60 seconds). A newly issued code invalidates the
previous code. If delivery fails, the newly created OTP record is discarded so the user
can retry without waiting for the cooldown.
