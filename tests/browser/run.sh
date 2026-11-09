#!/bin/bash
# Browser-level tests: the real JavaScript (chat, voice, uploads, delete dialogs, badges)
# runs in jsdom against the real server. Needs Node 18+.
#   cd tests/browser && npm install && ./run.sh
# It uses its own temporary database and never touches instance/chat.db or your .env.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"
[ -f .venv/bin/activate ] && source .venv/bin/activate
export FLASK_APP=run.py PYTHONPATH="$ROOT" APP_ENV=development
export SECRET_KEY="browser-test-secret-$RANDOM$RANDOM"
export POLL_INTERVAL_ACTIVE_MS=700 POLL_INTERVAL_IDLE_MS=1500
export CLOUDINARY_CLOUD_NAME=x CLOUDINARY_API_KEY=x CLOUDINARY_API_SECRET=x
DB="$(mktemp -u /tmp/goppo_browser_XXXX.db)"
export DATABASE_URL="sqlite:///$DB"
status=0
for harness in harness.js harness2.js; do
  rm -f "$DB"; flask db upgrade >/dev/null 2>&1
  python - <<'PY'
from app import create_app
from app.extensions import db
from app.models import User
with create_app().app_context():
    for n in ("alice", "bob"):
        u = User(full_name=n.title(), email=f"{n}@example.com", username=n, is_email_verified=True)
        u.set_password("secret123"); db.session.add(u)
    db.session.commit()
PY
  python "$HERE/fake_server.py" >/tmp/goppo_browser_server.log 2>&1 &
  SRV=$!; sleep 4
  echo "== $harness"
  (cd "$HERE" && node "$harness" 2>&1 | grep -v "Could not load link") || status=1
  kill $SRV 2>/dev/null; wait $SRV 2>/dev/null
done
rm -f "$DB"
exit $status
