# Math Class-এ চ্যাট যোগ করার সম্পূর্ণ সহজ গাইড
### (একদম নতুনদের জন্য — ধাপে ধাপে)

এই ফাইলটা পড়ে তুমি ধীরে ধীরে সব কাজ করতে পারবে।  
একসাথে সব করার দরকার নেই। একটা ধাপ শেষ করে তারপর পরের ধাপে যাবে।

---

## আগে বুঝে নাও — আমরা কী করতে যাচ্ছি?

তোমার দুটো আলাদা ওয়েবসাইট আছে:

1. **Math Class** → তোমার মূল সাইট (নোট, পরীক্ষা, ড্যাশবোর্ড ইত্যাদি)
2. **Goppo / arnabchatweb** → চ্যাট করার সাইট (মেসেজ, ফটো, ভিডিও, ভয়েস)

আমরা চাই:
- স্টুডেন্ট Math Class-এ লগইন করবে
- সেখানে একটা **“চ্যাট”** বাটন দেখবে
- বাটনে ক্লিক করলে **নতুন পেজ** খুলবে
- সাথে সাথে সে চ্যাট সাইটে চলে যাবে **আবার লগইন না করেই**
- চ্যাটের সব ডাটা চ্যাট সাইটেই থাকবে (Math Class-এর উপর চাপ পড়বে না)

এটা করার জন্য দুটো সাইটকে একটা **গোপন চাবি (Secret)** দিয়ে যোগ করতে হবে।  
সেই চাবি `.env` ফাইলে থাকবে।

---

## ধাপ ০: যা যা লাগবে (আগে প্রস্তুত রাখো)

- দুটো প্রজেক্টই PythonAnywhere-এ হোস্ট করা আছে এবং কাজ করছে
- Math Class-এর ফোল্ডার কম্পিউটারে খোলা আছে
- Chat (arnabchatweb) প্রজেক্টের ফোল্ডারও খোলা আছে
- দুটো সাইটেরই `.env` ফাইল আছে (যেখানে পাসওয়ার্ড, ক্লাউডিনারি ইত্যাদি লেখা থাকে)

---

## ধাপ ১: একটা গোপন চাবি (Secret) তৈরি করো

দুটো সাইটের মধ্যে কথা বলার জন্য একটা লম্বা গোপন কোড লাগবে।

### কীভাবে তৈরি করবে:

1. কম্পিউটারে Terminal / Command Prompt খোলো
2. নিচের কমান্ডটা লেখো এবং Enter চাপো:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

3. একটা লম্বা অক্ষর+সংখ্যার লাইন আসবে।  
   উদাহরণ: `a8f3c91e2b7d4e6f9a1c0b3d5e7f8a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f`

4. এই পুরো লাইনটা কপি করে একটা নোটপ্যাডে সেভ করে রাখো।  
   এটাই আমাদের **SSO_SHARED_SECRET**

**মনে রেখো:** এই কোডটা কাউকে দেখাবে না এবং GitHub-এ আপলোড করবে না।

---

## ধাপ ২: Math Class-এর `.env` ফাইলে নতুন জিনিস যোগ করো

1. Math Class ফোল্ডারে যাও
2. `.env` ফাইলটা খোলো (যদি না থাকে তাহলে `.env.example` কপি করে `.env` নাম দাও)
3. ফাইলের **একদম নিচে** নিচের লাইনগুলো যোগ করো:

```env
# ========== চ্যাট (Goppo) যোগ করার জন্য ==========
CHAT_APP_URL=https://তোমার-চ্যাট-সাইটের-ঠিকানা.pythonanywhere.com
CHAT_SSO_PATH=/auth/sso
SSO_SHARED_SECRET=এখানে_ধাপ_১_এর_গোপন_চাবি_পেস্ট_করো
SSO_TOKEN_TTL_SECONDS=90
```

### ব্যাখ্যা:
- `CHAT_APP_URL` → তোমার চ্যাট সাইটের পুরো লিংক (শেষে `/` থাকবে না)
- `SSO_SHARED_SECRET` → ধাপ ১-এ যে লম্বা কোড বানিয়েছিলে সেটা
- `SSO_TOKEN_TTL_SECONDS=90` → টোকেন ৯০ সেকেন্ডের জন্য কাজ করবে (পরিবর্তন করার দরকার নেই)

**উদাহরণ (ধরো তোমার চ্যাট সাইট `arnabchat.pythonanywhere.com`):**

```env
CHAT_APP_URL=https://arnabchat.pythonanywhere.com
CHAT_SSO_PATH=/auth/sso
SSO_SHARED_SECRET=a8f3c91e2b7d4e6f9a1c0b3d5e7f8a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f
SSO_TOKEN_TTL_SECONDS=90
```

সেভ করে ফাইল বন্ধ করো।

---

## ধাপ ৩: Chat সাইটের `.env` ফাইলেও একই চাবি দাও

1. arnabchatweb ফোল্ডারে যাও
2. `.env` ফাইল খোলো
3. নিচে এই লাইনগুলো যোগ করো:

```env
# ========== Math Class থেকে লগইন আসবে ==========
SSO_SHARED_SECRET=এখানে_একই_গোপন_চাবি_পেস্ট_করো
SSO_ENABLED=1
MATH_CLASS_URL=https://তোমার-ম্যাথ-ক্লাস-সাইটের-ঠিকানা.pythonanywhere.com
```

**খুব জরুরি:**  
`SSO_SHARED_SECRET` এর মান **দুটো `.env` ফাইলে একদম একই** হতে হবে।  
একটা অক্ষরও আলাদা হলে কাজ করবে না।

সেভ করো।

---

## ধাপ ৪: Math Class-এ নতুন পেজ (রুট) যোগ করো

এখন Math Class-এ একটা নতুন পেজ বানাবো।  
এই পেজ স্টুডেন্টকে চ্যাট সাইটে পাঠিয়ে দেবে।

1. Math Class ফোল্ডারে যাও
2. এই ফাইলটা খোলো:

```
backend/routes/web/student.py
```

3. ফাইলের **একদম উপরে** (অন্য import-গুলোর সাথে) এই লাইনগুলো যোগ করো:

```python
import time
import hmac
import hashlib
import json
import base64
import os
```

4. এখন ফাইলের **একদম শেষে** (শেষের ফাংশনের পরে) নিচের পুরো কোডটা কপি করে পেস্ট করো:

```python
# ============================================================
# চ্যাট সাইটে যাওয়ার জন্য SSO রুট
# ============================================================

def _make_sso_token(student):
    secret = os.getenv("SSO_SHARED_SECRET", "")
    if not secret:
        return None

    payload = {
        "sub": str(student["id"]),
        "email": student["email"],
        "name": student.get("name") or "",
        "class_name": student.get("class_name") or "",
        "iat": int(time.time()),
        "exp": int(time.time()) + int(os.getenv("SSO_TOKEN_TTL_SECONDS", "90")),
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")

    sig = hmac.new(
        secret.encode(),
        payload_b64.encode(),
        hashlib.sha256
    ).hexdigest()

    return f"{payload_b64}.{sig}"


@app.route("/student/chat")
@student_login_required
def go_to_chat():
    chat_url = os.getenv("CHAT_APP_URL", "").rstrip("/")
    sso_path = os.getenv("CHAT_SSO_PATH", "/auth/sso")
    secret = os.getenv("SSO_SHARED_SECRET", "")

    if not chat_url or not secret:
        return "চ্যাট এখনো সেটআপ করা হয়নি। .env ফাইল চেক করো।", 503

    with get_db() as conn:
        student = conn.execute(
            "SELECT id, email, name, class_name FROM students WHERE id=?",
            (session["student_id"],)
        ).fetchone()

    if not student:
        return "স্টুডেন্ট পাওয়া যায়নি", 403

    token = _make_sso_token(dict(student))
    if not token:
        return "টোকেন তৈরি করা যায়নি", 500

    target = f"{chat_url}{sso_path}?token={token}"
    return redirect(target)
```

5. ফাইল সেভ করো।

---

## ধাপ ৫: Math Class-এ “চ্যাট” বাটন যোগ করো

স্টুডেন্ট যাতে সহজে চ্যাট খুলতে পারে, তার জন্য একটা বাটন লাগবে।

1. এই ফাইলটা খোলো:

```
templates/student/student_dashboard.html
```

2. যেখানে অন্যান্য বাটন বা মেনু আছে (যেমন Notes, Profile ইত্যাদি), সেখানে নিচের কোডটা যোগ করো:

```html
<a href="/student/chat" class="btn btn-primary" style="margin: 8px;">
  চ্যাট করো
</a>
```

যদি তোমার সাইটে বাটনের ক্লাস আলাদা হয় (যেমন `button` বা অন্য কিছু), তাহলে সেই স্টাইল অনুযায়ী বদলে নিতে পারো।  
মূল কথা হলো `href="/student/chat"` থাকতে হবে।

সেভ করো।

---

## ধাপ ৬: Chat সাইটে SSO গ্রহণ করার কোড যোগ করো

এখন Chat সাইটকে শেখাতে হবে যে Math Class থেকে আসা টোকেন কীভাবে চেক করতে হয়।

1. arnabchatweb ফোল্ডারে যাও
2. এই ফাইলটা খোলো:

```
app/blueprints/auth/routes.py
```

3. ফাইলের উপরে অন্য import-গুলোর সাথে এইগুলো যোগ করো (যদি না থাকে):

```python
import time
import hmac
import hashlib
import json
import base64
from flask import current_app, redirect, request, url_for, abort
from flask_login import login_user
```

4. ফাইলের **শেষে** নিচের পুরো কোডটা পেস্ট করো:

```python
# ============================================================
# Math Class থেকে আসা SSO লগইন
# ============================================================

def _verify_sso_token(token: str):
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
        # টোকেন খারাপ বা মেয়াদ শেষ → সাধারণ লগইন পেজে পাঠাও
        return redirect(url_for("auth.login"))

    email = payload["email"].strip().lower()
    name = (payload.get("name") or "").strip() or email.split("@")[0]

    # ইউজার আছে কিনা খোঁজো
    from ...models import User   # যদি মডেলের লোকেশন আলাদা হয় তাহলে বদলাও
    from ...extensions import db

    user = User.query.filter_by(email=email).first()

    if user is None:
        # নতুন ইউজার তৈরি করো (পাসওয়ার্ড র‍্যান্ডম)
        import secrets
        from werkzeug.security import generate_password_hash

        user = User(
            email=email,
            display_name=name,
            password_hash=generate_password_hash(secrets.token_hex(16)),
            is_verified=True,          # Math Class ইতিমধ্যে ইমেইল চেক করেছে
            # অন্য যে কলামগুলো বাধ্যতামূলক, সেগুলো ডিফল্ট মান দাও
        )
        db.session.add(user)
        db.session.commit()
    else:
        # নাম খালি থাকলে আপডেট করো
        if not getattr(user, "display_name", None) and name:
            user.display_name = name
            db.session.commit()

    login_user(user)
    return redirect(url_for("main.home"))
```

**খেয়াল রাখো:**  
Chat প্রজেক্টে `User` মডেলের ফিল্ডের নাম যদি আলাদা হয় (যেমন `name` এর বদলে `full_name`, বা `is_verified` না থাকে), তাহলে সেই অনুযায়ী কোডটা একটু বদলাতে হতে পারে।  
এই অংশে সমস্যা হলে আমাকে `User` মডেলের কোডটা পাঠিও, আমি ঠিক করে দিব।

5. ফাইল সেভ করো।

---

## ধাপ ৭: Chat সাইটের config-এ নতুন জিনিস যোগ করো

1. এই ফাইলটা খোলো:

```
app/config.py
```

2. `class Config:` এর ভিতরে (অন্য ভেরিয়েবলগুলোর সাথে) এই তিনটা লাইন যোগ করো:

```python
SSO_SHARED_SECRET = env_str("SSO_SHARED_SECRET")
SSO_ENABLED = env_bool("SSO_ENABLED", False)
MATH_CLASS_URL = env_str("MATH_CLASS_URL")
```

সেভ করো।

---

## ধাপ ৮: দুটো সাইট রিস্টার্ট করো (PythonAnywhere)

কোড বদলানোর পর সাইট রিস্টার্ট করতে হয়।

### Math Class-এর জন্য:
1. PythonAnywhere-এ লগইন করো
2. যে অ্যাকাউন্টে Math Class হোস্ট করা, সেখানে যাও
3. **Web** ট্যাবে গিয়ে **Reload** বাটনে ক্লিক করো

### Chat সাইটের জন্য:
1. অন্য অ্যাকাউন্ট/ওয়েব অ্যাপে যাও
2. সেখানেও **Reload** করো

---

## ধাপ ৯: টেস্ট করো

1. Math Class সাইটে গিয়ে একজন স্টুডেন্ট অ্যাকাউন্ট দিয়ে লগইন করো
2. ড্যাশবোর্ডে **“চ্যাট করো”** বাটন দেখতে পাবে
3. বাটনে ক্লিক করো
4. দেখো:
   - নতুন পেজ খুলছে কিনা
   - সাথে সাথে চ্যাট সাইটে চলে যাচ্ছে কিনা
   - আবার লগইন চাইছে কিনা (চাইলে সমস্যা আছে)
   - চ্যাট ইন্টারফেস দেখাচ্ছে কিনা

### যদি সমস্যা হয়:

| সমস্যা | সম্ভাব্য কারণ | কী করবে |
|--------|----------------|---------|
| “চ্যাট এখনো সেটআপ করা হয়নি” | `.env`-এ `CHAT_APP_URL` বা `SSO_SHARED_SECRET` নেই | ধাপ ২ আবার চেক করো |
| চ্যাট সাইটে গিয়ে লগইন পেজ দেখায় | টোকেন ভুল বা Secret মিলছে না | দুটো `.env`-এ Secret একই কিনা দেখো |
| 404 Error | Chat সাইটে `/auth/sso` রুট কাজ করছে না | ধাপ ৬ চেক করো + Reload করেছো তো? |
| User তৈরি হচ্ছে না | Chat-এর User মডেলের ফিল্ড আলাদা | আমাকে User মডেলের কোড পাঠাও |

---

## সংক্ষেপে পুরো কাজের তালিকা

- [ ] ধাপ ১: গোপন চাবি তৈরি করে নোট করে রাখা
- [ ] ধাপ ২: Math Class `.env` আপডেট
- [ ] ধাপ ৩: Chat `.env` আপডেট (একই Secret)
- [ ] ধাপ ৪: Math Class-এ `/student/chat` রুট যোগ
- [ ] ধাপ ৫: ড্যাশবোর্ডে “চ্যাট করো” বাটন যোগ
- [ ] ধাপ ৬: Chat-এ SSO রুট যোগ
- [ ] ধাপ ৭: Chat config-এ নতুন ভেরিয়েবল যোগ
- [ ] ধাপ ৮: দুটো সাইট Reload
- [ ] ধাপ ৯: টেস্ট করা

---

## শেষ কথা

এই গাইড অনুযায়ী কাজ করলে Math Class-এর মূল লজিক প্রায় অপরিবর্তিত থাকবে।  
শুধু একটা নতুন পেজ + একটা বাটন যোগ হয়েছে।  
চ্যাটের সব ভারী কাজ আলাদা সাইটেই চলবে।

কোনো ধাপে আটকে গেলে বা এরর মেসেজ এলে সেটা **হুবহু** কপি করে আমাকে পাঠাও।  
আমি বলে দেব পরের কাজ কী।

সৌভাগ্য!
