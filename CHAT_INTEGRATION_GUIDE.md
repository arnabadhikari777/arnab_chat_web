# Math Class ↔ Goppo (arnabchatweb) চ্যাট ইন্টিগ্রেশন গাইড

এই ডকুমেন্ট দুটো প্রজেক্টেই রাখা হয়েছে।  
লক্ষ্য: Math Class-এর স্টুডেন্টরা **একবার লগইন করে** চ্যাট খুলতে পারবে, কিন্তু চ্যাটের ডাটাবেস, মিডিয়া (Cloudinary) এবং ভারী লজিক **আলাদা PythonAnywhere**-এ থাকবে।

---

## ১. কোন পদ্ধতি বেছে নেওয়া হয়েছে এবং কেন (সবচেয়ে সুরক্ষিত)

**পদ্ধতি: Signed Short-lived Token (SSO Token) + Redirect**

- Math Class স্টুডেন্ট লগইন করা অবস্থায় একটা **স্বল্পকালীন স্বাক্ষরিত টোকেন** তৈরি করে।
- সেই টোকেন নিয়ে স্টুডেন্টকে Chat অ্যাপের SSO এন্ডপয়েন্টে রিডাইরেক্ট করা হয়।
- Chat অ্যাপ টোকেন যাচাই করে → ইউজার খুঁজে পায় বা তৈরি করে → সেশন লগইন করে → চ্যাট হোমে পাঠায়।

**কেন এটা সবচেয়ে ভালো:**
- দুটো অ্যাপের ডাটাবেস আলাদা থাকে (Math Class-এর উপর চাপ পড়ে না)।
- পাসওয়ার্ড শেয়ার হয় না।
- টোকেন মাত্র ৬০–১২০ সেকেন্ডের জন্য বৈধ → চুরি হলেও ক্ষতি কম।
- Shared Secret শুধু `.env`-এ থাকে, কোডে হার্ডকোড নয়।
- Math Class-এর মূল লজিক (exam, notes, AI ইত্যাদি) প্রায় অপরিবর্তিত থাকে — শুধু একটা নতুন রুট + একটা বোতাম যোগ হয়।

**বিকল্প যেগুলো বাদ দেওয়া হয়েছে:**
- ডাটাবেস শেয়ার করা → নিরাপদ নয় + PythonAnywhere-এ কঠিন।
- iframe এমবেড → কুকি/CORS/SSO জটিল + মোবাইলে খারাপ অভিজ্ঞতা।
- দীর্ঘকালীন টোকেন বা পাসওয়ার্ড পাস করা → ঝুঁকিপূর্ণ।

---

## ২. সামগ্রিক ফ্লো (স্টুডেন্ট কী দেখবে)

1. স্টুডেন্ট Math Class-এ লগইন করে।
2. ড্যাশবোর্ড / নেভিগেশনে **“চ্যাট”** বা **“Goppo”** বোতাম দেখে।
3. ক্লিক করলে Math Class-এর `/student/chat` (বা `/go-to-chat`) পেজ খোলে (সম্পূর্ণ আলাদা পেজ)।
4. সেই পেজ সেকেন্ডের মধ্যে টোকেন তৈরি করে Chat অ্যাপে রিডাইরেক্ট করে।
5. Chat অ্যাপ টোকেন যাচাই করে স্টুডেন্টকে লগইন করে এবং চ্যাট ইন্টারফেস দেখায়।
6. এরপর সব মেসেজ, ফটো, ভিডিও, ভয়েস Chat অ্যাপের SQLite + Cloudinary-তে যায়।

Math Class শুধু “দরজা” খুলে দেয় — ভিতরের কাজ Chat অ্যাপ করে।

---

## ৩. .env ভেরিয়েবল (দুটো অ্যাপেই)

### Math Class-এর `.env` / `.env.example`-এ যোগ করো:

```env
# ========== Chat SSO (Goppo) ==========
# Chat অ্যাপের পূর্ণ বেস URL (শেষে স্ল্যাশ ছাড়া)
CHAT_APP_URL=https://yourusername.pythonanywhere.com

# SSO এন্ডপয়েন্ট (সাধারণত /auth/sso)
CHAT_SSO_PATH=/auth/sso

# দুটো অ্যাপে একই লম্বা র‍্যান্ডম সিক্রেট (কমপক্ষে ৩২ বাইট)
# তৈরি করতে: python -c "import secrets; print(secrets.token_hex(32))"
SSO_SHARED_SECRET=এখানে_লম্বা_র‍্যান্ডম_স্ট্রিং_দাও

# টোকেন কত সেকেন্ড বৈধ থাকবে (৬০–১২০ রেকমেন্ডেড)
SSO_TOKEN_TTL_SECONDS=90
```

### Chat অ্যাপের (arnabchatweb) `.env`-এ যোগ করো:

```env
# ========== Math Class SSO ==========
# Math Class থেকে আসা টোকেন যাচাই করার জন্য একই সিক্রেট
SSO_SHARED_SECRET=এখানে_একই_লম্বা_র‍্যান্ডম_স্ট্রিং_দাও

# Math Class-এর বেস URL (অপশনাল, লগ/অডিটের জন্য)
MATH_CLASS_URL=https://your-math-class.pythonanywhere.com

# SSO চালু আছে কিনা (1 = চালু)
SSO_ENABLED=1
```

**গুরুত্বপূর্ণ:**  
`SSO_SHARED_SECRET` দুটো জায়গায় **একদম একই** হতে হবে।  
এটা কখনো GitHub-এ কমিট করবে না।

---

## ৪. Math Class-এ কী কী পরিবর্তন করতে হবে (মিনিমাল)

Math Class-এর মূল ফাইলগুলোর লজিক যতটা সম্ভব অপরিবর্তিত রাখা হয়েছে।

### ধাপ ৪.১ — কনফিগ পড়া
`backend/config.py`-তে নতুন ভেরিয়েবল যোগ করো (অথবা যেখানে env পড়া হয়):

```python
import os

CHAT_APP_URL = os.getenv("CHAT_APP_URL", "").rstrip("/")
CHAT_SSO_PATH = os.getenv("CHAT_SSO_PATH", "/auth/sso")
SSO_SHARED_SECRET = os.getenv("SSO_SHARED_SECRET", "")
SSO_TOKEN_TTL_SECONDS = int(os.getenv("SSO_TOKEN_TTL_SECONDS", "90"))
```

### ধাপ ৪.২ — নতুন রুট (আলাদা পেজ)
`backend/routes/web/student.py`-এর শেষে (বা উপযুক্ত জায়গায়) নতুন রুট যোগ করো:

```python
import time
import hmac
import hashlib
import json
import base64
from flask import redirect, abort, current_app
from backend.config import CHAT_APP_URL, CHAT_SSO_PATH, SSO_SHARED_SECRET, SSO_TOKEN_TTL_SECONDS
from backend.middleware.auth import student_login_required
from backend.db.connection import get_db

def _make_sso_token(student: dict) -> str:
    """Create a short-lived signed token for the chat app."""
    if not SSO_SHARED_SECRET:
        abort(503)  # SSO not configured

    payload = {
        "sub": str(student["id"]),
        "email": student["email"],
        "name": student.get("name") or student.get("full_name") or "",
        "class_name": student.get("class_name") or "",
        "iat": int(time.time()),
        "exp": int(time.time()) + SSO_TOKEN_TTL_SECONDS,
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")

    sig = hmac.new(
        SSO_SHARED_SECRET.encode(),
        payload_b64.encode(),
        hashlib.sha256
    ).hexdigest()

    return f"{payload_b64}.{sig}"


@app.route("/student/chat")
@student_login_required
def go_to_chat():
    """
    Completely separate page on Math Class.
    Generates SSO token and redirects the student to the Chat app.
    No heavy logic here — just the door.
    """
    if not CHAT_APP_URL or not SSO_SHARED_SECRET:
        # Optional: render a simple error template instead of abort
        abort(503)

    with get_db() as conn:
        student = conn.execute(
            "SELECT id, email, name, class_name FROM students WHERE id=?",
            (session["student_id"],)
        ).fetchone()

    if not student:
        abort(403)

    token = _make_sso_token(dict(student))
    target = f"{CHAT_APP_URL}{CHAT_SSO_PATH}?token={token}"
    return redirect(target)
```

> নোট: যদি `student` টেবিলে কলামের নাম আলাদা হয় (যেমন `full_name`), সেই অনুযায়ী মানিয়ে নাও।

### ধাপ ৪.৩ — UI-তে বোতাম যোগ করা
স্টুডেন্ট ড্যাশবোর্ড বা নেভিগেশনে একটা লিঙ্ক/বোতাম দাও:

```html
<a href="{{ url_for('go_to_chat') }}" class="btn btn-primary">
  চ্যাট / Goppo
</a>
```

`templates/student/student_dashboard.html` বা যেখানে নেভিগেশন আছে সেখানে যোগ করলেই হবে।  
এটা একটা সাধারণ লিঙ্ক — কোনো জটিল JS লাগবে না।

### ধাপ ৪.৪ — .env.example আপডেট
`.env.example`-এ উপরের Chat SSO সেকশনটা কমেন্টসহ যোগ করে দাও যাতে পরে ভুলে না যাও।

---

## ৫. Chat অ্যাপ (arnabchatweb / Goppo)-এ কী কী করতে হবে

### ধাপ ৫.১ — কনফিগ
`app/config.py`-এর `Config` ক্লাসে যোগ করো:

```python
SSO_SHARED_SECRET = env_str("SSO_SHARED_SECRET")
SSO_ENABLED = env_bool("SSO_ENABLED", False)
MATH_CLASS_URL = env_str("MATH_CLASS_URL")
```

### ধাপ ৫.২ — SSO এন্ডপয়েন্ট
`app/blueprints/auth/routes.py`-এ (বা নতুন ফাইলে) যোগ করো:

```python
import time
import hmac
import hashlib
import json
import base64
from flask import current_app, redirect, request, url_for, abort
from flask_login import login_user
from ...services import auth_service   # বা যেখানে user তৈরি/খোঁজা হয়
from ...models import User            # প্রজেক্ট অনুযায়ী ঠিক করো
from . import bp

def _verify_sso_token(token: str) -> dict | None:
    secret = current_app.config.get("SSO_SHARED_SECRET") or ""
    if not secret or not token or "." not in token:
        return None

    try:
        payload_b64, sig = token.rsplit(".", 1)
        expected = hmac.new(
            secret.encode(),
            payload_b64.encode(),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None

        # pad base64
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))

        if payload.get("exp", 0) < int(time.time()):
            return None
        if not payload.get("email"):
            return None
        return payload
    except Exception:
        return None


@bp.route("/sso")
def sso_login():
    if not current_app.config.get("SSO_ENABLED"):
        abort(404)

    token = request.args.get("token", "").strip()
    payload = _verify_sso_token(token)
    if not payload:
        # Invalid or expired — send to normal login
        return redirect(url_for("auth.login"))

    email = payload["email"].strip().lower()
    name = (payload.get("name") or "").strip() or email.split("@")[0]

    # Find or create user (project-specific — adjust to your models/services)
    user = User.query.filter_by(email=email).first()
    if user is None:
        # Auto-create a private account linked by email.
        # Password can be random because they will always come via SSO.
        user = auth_service.create_sso_user(
            email=email,
            display_name=name,
            # optional: mark as coming from math-class
        )
    else:
        # Optional: update name if empty
        if not user.display_name and name:
            user.display_name = name
            # db.session.commit()

    login_user(user)
    return redirect(url_for("main.home"))   # or chat inbox route
```

**গুরুত্বপূর্ণ:**  
`auth_service.create_sso_user` বা সমতুল্য ফাংশন তোমাকে একবার লিখতে হবে (যদি না থাকে)।  
এটা সাধারণ রেজিস্ট্রেশনের মতোই ইউজার তৈরি করবে, কিন্তু:
- পাসওয়ার্ড র‍্যান্ডম সেট করবে (বা null রাখবে যদি মডেল সাপোর্ট করে)
- ইমেইল ভেরিফাইড ধরে নেবে (কারণ Math Class ইতিমধ্যে OTP দিয়ে ভেরিফাই করেছে)
- প্রোফাইল ফটো বাধ্যতামূলক না রাখলে ভালো (SSO দিয়ে আসা ইউজারের জন্য)

### ধাপ ৫.৩ — ইউজার মডেল / সার্ভিসে ছোট অ্যাডজাস্টমেন্ট
যদি রেজিস্ট্রেশনে প্রোফাইল ফটো বাধ্যতামূলক থাকে, SSO পাথের জন্য সেটা স্কিপ করার অপশন রাখো।  
নাম/ডিসপ্লে নেম Math Class থেকে আসা `name` দিয়ে সেট করো।

---

## ৬. সিকিউরিটি চেকলিস্ট (ডিপ্লয়ের আগে)

- [ ] `SSO_SHARED_SECRET` দুটো `.env`-এ একই এবং যথেষ্ট লম্বা (৩২+ বাইট hex)
- [ ] টোকেন TTL ৬০–১২০ সেকেন্ডের মধ্যে
- [ ] Chat অ্যাপে `SSO_ENABLED=1` শুধু প্রোডাকশনে
- [ ] HTTPS দুটো সাইটেই চালু (PythonAnywhere-এ ডিফল্ট আছে)
- [ ] টোকেন URL-এ যায় বলে লগে সেনসিটিভ ডাটা না রাখা
- [ ] Invalid/expired টোকেনে সাধারণ লগইন পেজে পাঠানো
- [ ] Math Class-এর `student_login_required` ঠিকমতো কাজ করছে কিনা টেস্ট করা

---

## ৭. টেস্টিং অর্ডার (লোকাল → প্রোডাকশন)

1. **লোকাল:** দুটো অ্যাপ আলাদা পোর্টে চালাও।  
   Math Class `.env`-এ `CHAT_APP_URL=http://127.0.0.1:5001` দাও।
2. Math Class-এ লগইন করে `/student/chat` হিট করো।
3. Chat অ্যাপে নতুন ইউজার তৈরি হচ্ছে কিনা এবং সেশন হচ্ছে কিনা দেখো।
4. টোকেন এক্সপায়ার হওয়ার পর আবার ট্রাই করে দেখো (invalid হওয়া উচিত)।
5. তারপর PythonAnywhere-এ একই `.env` ভ্যালু দিয়ে ডিপ্লয় করো।

---

## ৮. ভবিষ্যতে আরও ভালো করতে চাইলে (অপশনাল)

- Math Class-এর ড্যাশবোর্ডে “অপঠিত মেসেজ” কাউন্ট দেখানো (Chat অ্যাপের একটা পাবলিক API থেকে)।
- Chat থেকে Math Class-এ ফিরে আসার বোতাম (MATH_CLASS_URL ব্যবহার করে)।
- একই Cloudinary অ্যাকাউন্ট ব্যবহার করলে ফোল্ডার আলাদা রাখা (ইতিমধ্যে `.env`-এ আছে)।

কিন্তু উপরের মূল প্ল্যান দিয়েই তোমার চাহিদা পূরণ হয়ে যাবে — Math Class হালকা থাকবে, চ্যাট আলাদা সার্ভারে থাকবে, এবং স্টুডেন্টদের দ্বিতীয়বার লগইনের ঝামেলা থাকবে না।

---

## ৯. সংক্ষিপ্ত সারাংশ

| বিষয়                    | সিদ্ধান্ত                                      |
|-------------------------|------------------------------------------------|
| অথেন্টিকেশন             | Short-lived HMAC/JWT-style signed token       |
| ডাটাবেস                 | সম্পূর্ণ আলাদা (Chat-এর SQLite)                |
| মিডিয়া                  | Cloudinary (Chat অ্যাপের `.env` অনুযায়ী)      |
| Math Class-এ পরিবর্তন   | ১টা রুট + ১টা বোতাম + `.env` ভেরিয়েবল         |
| UI                      | Math Class-এ আলাদা পেজ → সাথে সাথে Chat-এ রিডাইরেক্ট |
| কনফিগ                   | সব `.env` দিয়ে নিয়ন্ত্রিত                    |

কোনো ধাপে আটকে গেলে বা `create_sso_user` ফাংশন লিখতে সাহায্য লাগলে বলো — সেই অংশের কোড আলাদা করে দিয়ে দেব।
