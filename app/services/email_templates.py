"""HTML version of the OTP email. Plain text is always sent too (multipart/alternative).

Email clients ignore <style> blocks and external CSS in many cases, so the layout uses a table and inline styles
only. It is one column, 600px wide at most, and shrinks on phones. Nothing is loaded from the internet (no
images, no fonts), so the code is visible even when the client blocks remote content.
"""
from html import escape

from ..i18n import t


def render_otp_html(*, app_name: str, code: str, minutes: int, purpose: str, lang: str) -> str:
    kind = "reset" if purpose in ("reset_password", "change_password") else "register"
    e = escape
    heading = e(t(f"email.otp.heading.{kind}", lang=lang))
    intro = e(t(f"email.otp.intro.{kind}", lang=lang, app=app_name))
    expires = e(t("email.otp.expires", lang=lang, minutes=minutes))
    warning = e(t("email.otp.warning", lang=lang, app=app_name))
    footer = e(t("email.otp.footer", lang=lang, app=app_name))
    brand = e(app_name)
    code_html = e(code)
    return f"""<!DOCTYPE html>
<html lang="{e(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>{brand}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f8;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{heading}: {code_html}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f8;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:16px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<tr><td style="background:#4f46e5;padding:22px 28px;color:#ffffff;font-size:22px;font-weight:700;letter-spacing:.3px;">{brand}</td></tr>
<tr><td style="padding:28px 28px 8px 28px;color:#111827;">
<h1 style="margin:0 0 12px 0;font-size:20px;line-height:1.3;">{heading}</h1>
<p style="margin:0 0 20px 0;font-size:15px;line-height:1.5;color:#374151;">{intro}</p>
<div style="text-align:center;margin:8px 0 20px 0;">
<span style="display:inline-block;background:#eef2ff;border:1px solid #c7d2fe;border-radius:12px;padding:14px 22px;font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace;font-size:32px;font-weight:700;letter-spacing:8px;color:#312e81;">{code_html}</span>
</div>
<p style="margin:0 0 20px 0;font-size:14px;line-height:1.5;color:#374151;text-align:center;">{expires}</p>
</td></tr>
<tr><td style="padding:0 28px 24px 28px;">
<div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:12px 14px;font-size:13px;line-height:1.5;color:#9a3412;">{warning}</div>
</td></tr>
<tr><td style="padding:16px 28px;background:#f9fafb;color:#6b7280;font-size:12px;line-height:1.5;text-align:center;">{footer}</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""
