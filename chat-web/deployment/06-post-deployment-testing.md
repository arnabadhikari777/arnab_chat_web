# 06 – Post-deployment test checklist

Use **two** accounts (call them A and B) and two browsers (or one normal + one private window), plus your phone.
Tick each line. Anything that fails: note the exact message and look at the error log (guide 7).
Items marked **(manual)** need a real browser/phone/e-mail account and cannot be proven by the automated tests.

## A. Site and existing data

- [ ] `https://YOUR_USERNAME.pythonanywhere.com/` loads (no "Something went wrong").
- [ ] Page has styling (static mapping works) and no raw text such as `site.maintenance.title` anywhere.
- [ ] `https://YOUR_USERNAME.pythonanywhere.com/health` answers.
- [ ] **An existing user can log in** with their old password (nobody was asked to reset).
- [ ] Their old conversations and messages are all there; counts match `db_inspect_before.txt`.

## B. Authentication

- [ ] **Registration** with a new e-mail → OTP e-mail arrives (manual) → code accepted → account active.
- [ ] Wrong OTP is refused; expired/old OTP is refused.
- [ ] **Login** works; wrong password shows an error and is logged (Admin → Security → Failed sign-ins).
- [ ] **Logout** works, and the Back button does not show private pages.
- [ ] **Forgot password** → OTP e-mail (manual) → new password works → old password no longer works.
- [ ] After a password change, the old session on another browser is signed out.

## C. People and profiles

- [ ] People page lists verified users; search works.
- [ ] Profile page shows name, bio, avatar; **e-mail addresses are never visible** to other users.
- [ ] Edit own profile; upload an avatar (needs Cloudinary) (manual).

## D. Chat

- [ ] **Public account B**: A taps Message → chat opens immediately.
- [ ] **Private account B**: A sends a **message request**; B sees it, accepts → chat opens; B rejecting it blocks the chat.
- [ ] Send text from A → B sees it within a few seconds (polling). Duplicate tap does not create two messages.
- [ ] Ticks: one tick (sent) → two ticks (delivered, only after B's phone confirms a push) → blue/seen when B opens the chat.
- [ ] Scroll up loads older history. Delete for me / delete for everyone work.
- [ ] A third user C cannot open the A–B chat by guessing `/chat/<id>` (must be refused).

## E. Notice Box

- [ ] Notices appear; opening the Notice Box marks them read and the badge number drops (this was broken by the
      Content-Security-Policy before; now handled by `static/js/notices.js`).
- [ ] "Clear all" only clears **your** notices.

## F. Push notifications (manual, on a real phone)

- [ ] Settings → enable notifications → browser asks permission → allow.
- [ ] Lock B's phone / close the tab; A sends a message → B gets a notification.
- [ ] Tapping it opens the chat. B's ticks on A's screen go to "delivered" after the phone received it.
- [ ] Admin → Security shows the device under "Active push devices".
- [ ] Revoking a device (Admin → Users → user → Push devices) removes it.

## G. Admin (log in with an address in `ADMIN_EMAILS`)

- [ ] Admin dashboard opens with numbers. A normal user visiting `/admin` gets "forbidden".
- [ ] Users: search, open a user, suspend for N days, lift, force sign-out, add a note.
- [ ] **Settings**: toggle **"New messages"** off → a user cannot send (message "Sending new messages is switched off…")
      **but can still open the chat and read all old messages**; turn it on again.
- [ ] Toggle **"Chat open"** off → whole chat area shows "Chat is switched off" for normal users; admins still get in; turn it on again.
- [ ] Maintenance mode on → normal users see the maintenance page (HTTP 503), admins and login still work; turn it off.
- [ ] **Database** page (super admin): Make backup → Check → healthy; Download works; Delete backup works.
- [ ] **Security** page: shows failed sign-ins, sign-ins, push problems, recent admin actions.
- [ ] Audit log lists every admin action you just did.

## H. Mobile

- [ ] On a phone: layout fits, bottom navigation works, keyboard does not hide the message box.
- [ ] "Add to home screen" works (PWA). Reopening keeps you signed in.
- [ ] Switch language to Bengali: no raw keys, no broken `{placeholders}`.

## I. Last look at the logs

- [ ] `/var/log/YOUR_USERNAME.pythonanywhere.com.error.log` has **no new traceback** after all of the above.
- [ ] `~/arnab_chat_web-main/logs/app.log` has no `ERROR` lines you cannot explain.
