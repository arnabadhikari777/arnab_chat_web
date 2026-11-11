# Arnab Chat Web — Push Notification & UI Upgrade Report

Date: 2026-11-11

## Summary

This release adds an advanced Web Push notification system (VAPID + Service Worker + durable delivery tracking), improves the profile page, moves the default landing page to Messages, relocates the notification icon to the top-right, auto-marks displayed notices as read, improves PWA install detection, and adds a desktop-friendly responsive layout — without breaking existing features or requiring paid infrastructure.

## 1. Advanced Push Notification System

### New models (`app/models/push.py`)
- **PushSubscription** — endpoint, p256dh, auth, status (active/expired/failed), failure counts.
- **PushDelivery** — per notification × subscription delivery record with status:
  - `pending` → created
  - `accepted` → push service accepted (not proof the user viewed it)
  - `failed` / `expired` — retryable or terminal

### New service (`app/services/push_service.py`)
- VAPID configuration check
- Subscribe / unsubscribe
- Inline send after notification commit (no background workers — PythonAnywhere Free compatible)
- Grouped message notices reuse the existing `dedupe_key` (`msg:<conversation_id>`) so offline batches appear as “User B sent you N new messages”
- `/api/push/sync` retries pending deliveries when the client comes back online
- Invalid (404/410) subscriptions are marked expired and stopped

### Service worker (`app/static/js/sw.js`)
- Handles `push` and `notificationclick`
- Opens the correct conversation / notices URL
- Uses notification `tag` = dedupe key so OS can coalesce

### Frontend (`app/static/js/push.js`)
- Permission only after explicit Enable click
- Does not re-prompt when denied; shows instructions
- Hides Enable when already subscribed
- PWA `beforeinstallprompt` / `appinstalled` / standalone detection
- Online event triggers sync of pending pushes

### API endpoints
- `GET /api/push/vapid-public-key`
- `POST /api/push/subscribe`
- `POST /api/push/unsubscribe`
- `POST /api/push/sync`
- `POST /api/notices/mark-displayed` — marks only supplied IDs as read

### Integration points
- Message send → `upsert_message_notice` → after commit → `push_service.notify_after_commit`
- Announcement create → bulk notice rows → limited fan-out push
- Preferences `notify_messages` / `notify_announcements` still respected

### Environment variables (see `.env.example`)
```
VAPID_PRIVATE_KEY=
VAPID_PUBLIC_KEY=
VAPID_CONTACT=mailto:you@example.com
PUSH_TTL_SECONDS=86400
```

### Dependency
- `pywebpush>=1.14,<3` added to `requirements.txt`

### Migration
- `migrations/versions/a4f8c2e1b9d0_push_subscriptions.py`
- Safe additive migration; does not drop or alter existing tables.

## 2. Default page after login

- `/` (authenticated) redirects to `/messages` (chat inbox)
- Profile remains available at `/profile`
- Bottom nav Profile tab points to `/profile`

## 3. Profile improvements

- Own profile (`/profile` and other-users profile when `is_me`) shows registered email in small muted text under the username
- Cover photo is shown as the banner on the profile page with the avatar overlapping it (existing `.cover` + `.profile-body__avatar` layout)
- Private email is never shown on other users’ public profiles

## 4. Notification box behaviour

- Opening the Notice Box marks **only the notifications rendered on that page** as read via `/api/notices/mark-displayed`
- Redundant “Mark All as Read” button removed
- Unread badge is updated from the API response

## 5. Notification icon position

- Top-right of the topbar on Messages, People, Profile, and Settings
- Still available in the bottom nav for mobile habit continuity
- Shows unread count badge

## 6. Responsive UI

- Mobile: existing phone-width experience preserved (`max-width` behaviour under 900px)
- Desktop (≥900px): app expands up to 1100–1200px, more padding, taller cover, 4-column tiles, chat area uses full width
- Does not merely scale the mobile column

## 7. Performance / hosting notes

- No Redis, no always-on workers
- Push attempts are inline and bounded
- Announcement fan-out push is capped (200)
- Delivery retries on reconnect are capped (attempt_count ≥ 5 stops)
- Indexes on `push_subscriptions` and `push_deliveries` for efficient lookup

## 8. Known limitations

- Browser push services do **not** guarantee delivery while a device is fully offline; the server-side pending queue + reconnect sync recovers missed notifications when the device is online again.
- PythonAnywhere Free has limited outbound networking; Web Push to FCM/Mozilla/Apple endpoints generally works over HTTPS but should be verified on the target account.
- Safari iOS requires the site to be added to the Home Screen (PWA) before Web Push is available.
- VAPID keys must be generated and set in `.env` before push works.
- `pywebpush` must be installed in the deployment environment (`pip install -r requirements.txt`).

## 9. Deployment (PythonAnywhere)

1. Upload / pull the updated project.
2. `pip install -r requirements.txt` (includes `pywebpush`).
3. Add VAPID keys to `.env` (see generation notes in `.env.example`).
4. `flask db upgrade` (applies `a4f8c2e1b9d0`).
5. Reload the web app.
6. In Settings → Enable push notifications (user gesture required).
7. Send a test message from another account while the first is backgrounded.

## 10. Testing checklist (manual)

| Case | Status |
|------|--------|
| Permission enable / detect granted | Code path implemented — verify in browser |
| Permission denied shows help | Implemented |
| PWA install button hides after install | Implemented |
| New message push | Implemented (needs VAPID + real browser) |
| Grouped multi-message notice | Uses existing dedupe counter |
| Offline recovery via /api/push/sync | Implemented |
| No duplicate accepted deliveries | Unique constraint + status check |
| Invalid subscription handling | 404/410 → expired |
| Announcement push | Implemented |
| Notice box auto mark-read | Implemented |
| Unread counts | Updated via API |
| Profile email (owner only) | Implemented |
| Cover photo on profile | Implemented |
| Login → Messages | Implemented |
| Desktop layout | CSS media queries added |
| Regression (auth, chat, etc.) | Existing code paths preserved; full automated suite not re-run in this environment |

Honest note: full browser push delivery could not be end-to-end tested in this sandbox (no real multi-device browsers / VAPID push services). All server paths, models, migrations, and client wiring are in place for verification on your PythonAnywhere deployment.
