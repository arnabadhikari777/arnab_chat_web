# Push notifications: how it works, setup, limits

## The eight separate states (each has its own message in Settings)
1. Browser support / iOS Home Screen requirement  2. Notification permission  3. Service Worker  4. Push API
5. Server VAPID setup (private key, public key, contact - all three)  6. Browser subscription
7. Subscription saved on the server  8. Actual delivery

`GET /api/push/config` reports state 5 only. It never contains the private key; ordinary users only see
"configured: true/false", admins also see which variables are missing.

## PythonAnywhere (Free) setup - এই ধাপগুলো একবার করলেই হবে
1. Web ট্যাব নয়, **Consoles → Bash** খুলুন, প্রজেক্ট ফোল্ডারে যান, virtualenv চালু করুন, তারপর `pip install -r requirements.txt`.
2. `flask generate-vapid-keys` চালান। তিনটি লাইন ছাপা হবে।
3. `.env` ফাইলে `VAPID_PRIVATE_KEY` ও `VAPID_PUBLIC_KEY` পেস্ট করুন, `VAPID_CONTACT` এ **নিজের আসল ইমেইল** দিন (`mailto:you@gmail.com`).
4. `flask db upgrade` (এই আপডেটে নতুন মাইগ্রেশন নেই, তবুও নিরাপদ).
5. **Web ট্যাব → Reload**.
6. `flask push-status` চালান: `Web Push: configured.` দেখাতে হবে। না দেখালে ঠিক কোন ভ্যারিয়েবল ভুল তা লেখা থাকবে।
7. ফোনে সাইট খুলে Settings → "Enable push notifications"। আগে থেকেই অনুমতি দেওয়া থাকলে আবার অনুমতি চাওয়া হবে না।

Do **not** regenerate the keys later: every phone would have to press Enable again.

## What can and cannot be promised
* A push is **accepted** by Google/Mozilla/Apple, which then holds it for up to `PUSH_TTL_SECONDS` if the phone is off.
  Nobody can guarantee it appears (battery savers, "Don't keep activities", force-stopped browser, no Google services).
* Messages are always stored in the database. When the phone is back and the app opens, they are in the chat and
  in the Notice Box, whether or not any push arrived. That is the reliable recovery path.
* Many messages in one chat = one notice (counter goes up) and one push topic, so an offline phone is handed
  the newest push for that chat, not a pile.
* PythonAnywhere Free has no background workers: pushes are sent inline right after a message (short timeout, at most
  5 devices per person). Failed pushes are retried at most 5 times, only while the app is opened/online again
  (`/api/push/sync`), never in a loop. Announcements: the first `PUSH_ANNOUNCEMENT_INLINE_LIMIT` people are pushed
  immediately, everyone gets the Notice Box entry.
* iPhone/iPad: Web Push works only for the app **added to the Home Screen** (iOS 16.4+), not in a Safari tab.
* Free accounts can only call allow-listed hosts. Google (fcm.googleapis.com), Mozilla and Apple push endpoints are
  on PythonAnywhere's allow-list at the time of writing - confirm with `flask push-status` plus a real test message.

## Verified vs not verified
See the delivery report. Short version: logic is unit-tested; real delivery to a real phone must be checked on your live site.


## Delivered tick = the phone confirmed the push (device receipt)

Rule: a message becomes **Delivered** only when the recipient's phone/browser really received the push.

1. Every new-message push carries a signed, per-device receipt token (`ack`), bound to the recipient, the chat and the newest message that push announces. It contains no message text.
2. The service worker (`sw.js`) shows the notification, then POSTs the token to `/api/push/ack` (no cookies, so a background receipt never makes the person look "online").
3. The server verifies the signature and age (`PUSH_ACK_MAX_AGE_SECONDS`), re-checks that the person is an active member of the chat, then marks that chat's `sent` messages up to the announced id as `delivered` and stamps `push_deliveries.acked_at`.
4. If the phone has no connection at that moment, the token is kept in the service worker cache and re-sent on the next push or app start.

What no longer makes a message Delivered: opening the website, opening the chat list, `/api/conversations/<id>/delivered` (kept only as a harmless no-op for old cached pages). A push merely *accepted* by FCM / Mozilla / Apple stays `sent`.
Offline phone or failed push: the message stays **Sent**; when the phone comes back and the queued push arrives, the receipt arrives and it turns Delivered. **Seen** still implies Delivered.

Consequences you should know: no push subscription, notifications switched off by the recipient (`notify_messages`), or browser-blocked notifications = the message stays Sent until the chat is actually viewed (Seen). Real receipt delivery cannot be proven in automated tests; check it once on a real phone.

Migration: `flask db upgrade` (adds nullable `push_deliveries.acked_at`; reversible).
