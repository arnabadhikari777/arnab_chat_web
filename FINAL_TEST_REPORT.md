# Goppo – final test report

Environment of this repair: Python 3.12.3, Flask 3.1.3, Node 22, **no network**. `Flask-SQLAlchemy`, `Flask-Migrate`, `Flask-Login`,
`Flask-WTF`, `Flask-Limiter`, `SQLAlchemy`, `Alembic`, `pytest` and `pywebpush` were **not installed and could not be installed**.
Consequence: nothing that imports the application or runs migrations could be executed here. This report lists only what was really run.

## 1. Executed and passed

Evidence: `python3 /tmp/verify.py` against the exact tree that was packaged (11/11), plus the ad-hoc runs named below.

| # | Check | Result | What it does and does not prove |
|---|---|---|---|
| 1 | Python syntax, `ast.parse` on all 101 `.py` files | PASS | Files are syntactically valid. Not imported, not executed |
| 2 | Jinja syntax parse of 42 templates | PASS | Templates parse. **Not rendered** (no app); variable/filter errors would not show |
| 3 | `url_for(...)` cross-check: every endpoint named in templates/Python exists in a blueprint (static regex, 77 endpoints) | PASS | Catches `BuildError` of the kind the old admin templates had |
| 4 | JavaScript syntax, `node --check` on 10 files | PASS | Syntax only |
| 5 | `node --test tests/browser/push_state.test.js` | PASS 16/16 | Real `push.js` logic |
| 6 | `node tests/browser/sw_ack.test.js` (real `sw.js` in a sandbox) | PASS 10/10 | After rewriting the harness (it failed 5/9 before: stale design). `sw.js` itself was not changed |
| 7 | CSS brace balance (3 files) | PASS | Structure only; not rendered |
| 8 | CSP rules replicated from `tests/test_static.py` (no inline script/style/handlers in any template) | PASS | Equivalent logic run by hand, not via pytest |
| 9 | Migration graph (static parse of `revision`/`down_revision`): 12 revisions, **one head `a9f0c3d7b1e2`, one straight chain** | PASS | Alembic was not run, so this is not an Alembic confirmation |
| 10 | Every column in `app/models/*.py` (16 tables) is created by the migration chain, and no migration creates a column the models lack (AST comparison, names only) | PASS | Types, indexes, constraints not compared |
| 11 | i18n: `en.json` and `bn.json` have identical 484 keys and identical `{placeholders}`; the 8 audited `site.*` keys exist; every literal `t('…')` key used exists; dynamic key families (`admin.set.*`, `admin.nav.*`, `admin.stat.push_*`, `admin.db.*`, `admin.status.*`, `admin.level.*`) exist | PASS | Bengali wording not reviewed by a native speaker |
| 12 | SSRF validator `push_endpoint.check_endpoint`, run with a stubbed `flask.current_app`: 30 parametrized cases from `tests/test_push_endpoint.py` (allowed hosts, userinfo, port, IP literals, look-alike hosts, private/loopback/link-local/CGNAT/IPv4-mapped/ULA addresses, empty DNS) with the **exact reason codes** | PASS 30/30 | Pure logic. The Flask-level tests (subscribe API, send-time recheck) were **not** run |
| 13 | `_absolute_sqlite_url` on 7 URL shapes | PASS | Relative → project-relative; absolute, memory, non-sqlite untouched |
| 14 | `tools/db_inspect.py` on four synthetic SQLite files (stuck at `a4f8`, second-branch tip `e5b3`, both old heads stamped, healthy at head) | PASS | Reports the correct missing columns/tables, harmless legacy columns, the two-row warning, and exits 1/1/1/0. The synthetic files were built from the model column lists, not from real Alembic output |
| 15 | `tools/db_backup.py` on a synthetic DB | PASS | Backup written with mode 600, integrity `ok`, row counts equal |
| 16 | `tools/db_restore.py`: dry run changes nothing; real restore saves a `pre-restore-*.db` first and restores; a corrupt backup is refused | PASS | Synthetic DBs |

## 2. Written but NOT executed

| Item | Why not run | How you run it |
|---|---|---|
| `python -m pytest -q` (the whole suite, incl. the new `test_migrations.py` (8), `test_push_endpoint.py` (8), `test_delivery_ack.py` (13), `test_schema_consistency.py` (2), and the edited `test_admin_control.py`) | pytest and the Flask extensions are not installable offline | `pip install -r requirements-dev.txt && python -m pytest -q` |
| Real Alembic upgrade on a fresh DB, on a second-branch DB, on a two-row-stamped DB | Alembic/SQLAlchemy not installed | The tests above, and guide 3 sections 5–6 on the server |
| Application factory start, blueprint registration, WSGI import | Flask extensions missing | `python -c "from wsgi import application"` after `pip install -r requirements.txt` |
| Admin Database/Security pages, settings gate behaviour, ack endpoint, SSRF at API level | Need the running app | pytest + guide 6 |
| Browser jsdom harness (`tests/browser/run.sh`) | Needs `npm install` (network) | `cd tests/browser && npm install && ./run.sh` |
| Authentication, OTP, forgot-password, messaging, message requests, privacy rules, people/profile, notices | Need the running app; code not re-audited line by line | pytest + guide 6 |
| Gmail/SMTP, Cloudinary, real Web Push, live PythonAnywhere (WSGI, static mapping, env, permissions, logs) | Need credentials / a live account | Guides 2, 4, 6 |
| Dependency compatibility with Python 3.13 on PythonAnywhere | No network | First `pip install -r requirements.txt` there |

## 3. Failed tests

* Before repair, `tests/browser/sw_ack.test.js` failed 5 of 9 checks. Cause: the harness described an abandoned payload design (`ack` as an object with `endpoint`) while `sw.js` and the server use a token string. Fixed in the harness; **10/10 pass now**.
* My own first syntax-check script reported 95 "errors": that was a mistake in the script (it passed `/dev/null` as the bytecode path), not in the code. Re-run with `ast.parse`: 0 errors.
* In my SSRF ad-hoc run one case looked like a mismatch (`evilfcm.googleapis.com`). The validator's answer (blocked) is correct; my expectation was wrong. The committed test encodes the correct expectation.
* No pytest result exists, so no pytest test can be listed as passed or failed.

## 4. Database migration verification

| Question | Status |
|---|---|
| One valid head? | Yes by static parse (`a9f0c3d7b1e2`). Alembic confirmation: **pending** (`flask db heads`) |
| Fresh DB reaches head and equals models? | Column names: yes by static comparison. Real run: **pending** (`tests/test_migrations.py`) |
| Existing DB upgrade safe? | By construction: guarded, additive, no drop/rewrite. Real run on your copy: **pending** (guide 3, with backup first) |
| Reproduces the original crash? | Written as a test (`test_database_left_by_the_second_branch…`); not run |
| Two stamped heads in `alembic_version`? | Test written; documented SQL fallback in guide 3 §5 if Alembic refuses |

## 5. Acceptance conditions (honest status)

| # | Condition | Status |
|---|---|---|
| 1 | Application starts | **Not verified** |
| 2 | Factory works | **Not verified** |
| 3 | Blueprints register | Endpoint names cross-checked statically; **registration not executed** |
| 4 | Models and migrations consistent | Column names: verified statically; full: pending |
| 5 | One Alembic head | Verified statically; Alembic run pending |
| 6 | Documented safe path for existing DB | Done (guide 3, tools, tested on synthetic files) |
| 7–12 | Login, messages, notices, admin work | **Not verified** (need the running app); code paths changed: admin pages, settings gate, notices script |
| 13 | Security protections remain | CSRF untouched; one exemption (signed ack token) unchanged; auth/authorisation code unchanged; SSRF guard added |
| 14 | Translation keys consistent | **Verified** |
| 15 | Templates compile | **Syntax verified**; rendering not run |
| 16 | JavaScript passes syntax checks | **Verified** |
| 17 | Automated tests executed and documented | **Partly**: Node tests and static checks executed; pytest not executable here |
| 18 | PythonAnywhere instructions complete | Done (`deployment/`), not tried on a live account |
| 19 | ZIP preserves structure | Done; see section 6 |

## 6. Production deployment readiness

**Ready to attempt, not proven.** The fix for the most probable cause is in place, the data-safety tooling works, and the upgrade is additive. Before treating the site as restored you must
(1) back up, (2) run `python tools/db_inspect.py`, (3) `flask db heads` → one line, (4) `flask db upgrade`, (5) `python -m pytest -q`, (6) walk through guide 6.
If `db_inspect.py` shows no missing column on your server, the outage has another cause: send the last 60 lines of the error log.
