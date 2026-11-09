Math Class ↔ Chat (Goppo) integration is implemented in code.

What was added in THIS project:
1. backend/config.py  → CHAT_APP_URL, SSO_SHARED_SECRET, etc.
2. backend/routes/web/student.py → /student/chat route (SSO redirect)
3. templates/student/student_dashboard.html → Chat button
4. templates/base.html → Chat in top nav + bottom nav
5. .env.example → documented variables

YOU MUST still set in your real .env on PythonAnywhere:
  CHAT_APP_URL=https://YOUR-CHAT-SITE.pythonanywhere.com
  SSO_SHARED_SECRET=<same long secret as in Chat .env>
  SSO_TOKEN_TTL_SECONDS=90

And in Chat app .env:
  SSO_SHARED_SECRET=<same secret>
  SSO_ENABLED=1

Then Reload both web apps.

Students: open Math Class → login → press Chat → land in Goppo already logged in → message other students who also use Chat.
