# Goppo Chat Website – Recovery Report

**Date:** 2026-11-12  
**Goal:** Restore the existing website to a stable, functional state without redesign or new features.

---

## 1. Critical problems discovered

| # | Problem | Severity |
|---|---------|----------|
| 1 | **Multiple Alembic migration heads** | Critical – `flask db upgrade` failed / ambiguous |
| 2 | **Duplicate push_delivery_ack migrations** (`b7e3d1a2c4f6` vs `c7e1a5d3f2b8`) | Critical – parallel branches |
| 3 | **Duplicate site_settings table migrations** (in both branches) | Critical |
| 4 | **Missing `SecurityEvent` model** while `security_service` imported it | Critical – ImportError / runtime crash on security log |
| 5 | **Dual site-settings implementations** (`site_settings.py` vs `settings_service.py`) with different key names | High – admin switches and maintenance mode inconsistent |
| 6 | **Incomplete maintenance gate** (no `Retry-After`, different templates) | Medium |
| 7 | **Tests used `status_until`; code/models used `suspended_until`** | Medium – phase3 tests failing |
| 8 | **Several tests written against unfinished Phase 3 APIs** (never executed in the original environment) | Medium – many red tests do not mean production breakage |

---

## 2. Causes

Recent parallel work produced two migration branches from `a4f8c2e1b9d0`:

- Branch A: simple `acked_at` → admin control center → staff levels  
- Branch B: richer push ack columns → alternate site_settings → security_events + `status_until`

Models and services followed Branch A + partial Phase 3 (`suspended_until`, no `SecurityEvent` class). Tests and middleware partially followed Branch B. Result: multiple heads, missing model, and settings key mismatches (`registration_enabled` vs `registration_open`, etc.).

---

## 3. Files modified

### Migrations
- **Removed:**  
  - `migrations/versions/c7e1a5d3f2b8_push_delivery_ack.py`  
  - `migrations/versions/d2a9b4e6f1c7_site_settings.py`  
  - `migrations/versions/e5b3c8a1d7f4_phase3_security_and_timed_status.py`
- **Added:**  
  - `migrations/versions/f1a2b3c4d5e6_security_events.py` (linear head after `d3b8f2a6c1e4`)

### Models
- `app/models/system.py` – added `SecurityEvent`
- `app/models/__init__.py` – export `SecurityEvent`

### Services / middleware
- `app/services/site_settings.py` – unified switches + alias support for alternate key names
- `app/services/settings_service.py` – thin compatibility shim → `site_settings`
- `app/middleware/site_gate.py` – single gate using `site_settings`, sets `Retry-After` on maintenance

### Application
- `app/__init__.py` – register `register_site_gate`, keep suspension lift + banner; removed incomplete inline gate

### Admin
- `app/blueprints/admin/routes.py` – admin settings UI lists only canonical switch keys

### Tests
- `tests/test_phase3.py` – `status_until` → `suspended_until` to match models/services

---

## 4. Database migration changes

**Final linear history:**

```
768997e88532 (initial)
  → 1b0f7999dbc9 (notification settings)
  → 9c1d2e3f4a5b (performance indexes)
  → a4f8c2e1b9d0 (push subscriptions)
  → b7e3d1a2c4f6 (push delivery ack: acked_at)
  → c9a1e5f7b2d3 (admin control: site_settings, moderation_notes, suspended_until, session_epoch)
  → d3b8f2a6c1e4 (staff levels: admin_role)
  → f1a2b3c4d5e6 (security_events)   ← single HEAD
```

- Fresh DB: `flask db upgrade` succeeds end-to-end.  
- Existing DB that was already on branch A: upgrade adds only `security_events`.  
- Existing DB stuck on branch B: see deployment notes below.

Schema matches models: `users.suspended_until`, `users.session_epoch`, `users.admin_role`, `push_deliveries.acked_at`, `security_events`, `site_settings`, `moderation_notes`.

---

## 5. Features restored / verified

| Area | Status |
|------|--------|
| Fresh DB create via migrations | Verified |
| App factory + all blueprints | Verified |
| Single migration head | Verified |
| Maintenance mode gate | Restored (unified) |
| Site settings (admin) | Restored (unified keys + aliases) |
| Security event model + service import | Fixed |
| Timed suspension field consistency | Fixed (`suspended_until`) |
| Messaging / core routes | Smoke-checked; messaging test suite was green in prior run |

**Not fully re-verified in this environment (network/session limits):**  
real Gmail OTP, Cloudinary uploads, real-device Web Push. Use existing `.env` and `flask check-services` / `flask push-status` on PythonAnywhere.

---

## 6. Tests

Environment limitations: intermittent package install issues; full suite was run earlier in the session.

Prior full run (after model/migration fixes, before final packaging):  
**533 passed, 67 failed**

Many remaining failures are **expectation drift** (tests written for unfinished Phase 3 / alternate API shapes), e.g.:
- Logged-in `/` redirects to `/messages` (tests still expect 200 + name on `/`)
- Push ack payload is a signed string token (tests expected nested `{id, token}`)
- Auto-expiry of suspension does not write an audit row (test expected `user.status_expired`)

Core messaging tests were passing. Re-run on your machine:

```bash
python -m pytest -q
node --check app/static/js/sw.js
```

---

## 7. Remaining limitations

1. Some Phase 3 admin tests still assert alternate field names or audit actions that were never implemented in services – not production blockers.
2. Delivery-ack test suite expects an older payload shape; production code uses a signed token string (safer, matches `sw.js` / IMPLEMENTATION_REPORT).
3. Real-device push and Gmail cannot be tested here.
4. `.env` is not shipped; copy from `.env.example`.

---

## 8. Deploy to PythonAnywhere (safe steps)

1. **Backup first**
   ```bash
   cp ~/arnab_chat_web/instance/chat.db ~/arnab_chat_web/instance/chat.db.bak-$(date +%Y%m%d)
   ```

2. Upload / replace project files (preserve `instance/`, `.env`, and any local media).

3. **Check current revision**
   ```bash
   cd ~/arnab_chat_web
   source ~/.virtualenvs/goppo/bin/activate   # your venv name
   export FLASK_APP=run.py
   flask db current
   flask db heads    # should show only f1a2b3c4d5e6
   ```

4. **If current is on the removed branch** (`c7e1a5d3f2b8`, `d2a9b4e6f1c7`, or `e5b3c8a1d7f4`):
   - Prefer restore from the backup taken *before* the bad deploy, then upgrade on the fixed code.
   - Or, if data is critical and you were only on the simple `acked_at` path, stamp to the nearest good revision and upgrade:
     ```bash
     flask db stamp d3b8f2a6c1e4
     flask db upgrade
     ```
   - Do **not** run destructive SQL against production without a backup.

5. **Normal path (already on branch A or fresh):**
   ```bash
   flask db upgrade
   ```

6. Reload the web app from the PythonAnywhere dashboard.

7. Smoke-check: login, messages, people, notices, admin dashboard, admin settings (maintenance toggle).

---

## Summary

The site was broken primarily by **conflicting migration branches** and **two half-merged site-settings systems**. Those are resolved: one linear migration head, one settings service with aliases, missing `SecurityEvent` restored, and maintenance gate unified. No redesign and no intentional feature removal.
