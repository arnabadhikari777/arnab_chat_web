# 04 – WSGI and static-file configuration

Web tab on PythonAnywhere. Use **your** username and project folder.

## 1. The values on the Web tab

| Box | Value |
|---|---|
| **Source code** | `/home/YOUR_USERNAME/arnab_chat_web-main` |
| **Working directory** | `/home/YOUR_USERNAME/arnab_chat_web-main` |
| **Virtualenv** | `/home/YOUR_USERNAME/.virtualenvs/goppo-venv` |
| **Python version** | `3.13` (same as the virtualenv) |
| **Force HTTPS** | **Enabled** (the app sets secure cookies when `APP_ENV=production`) |

## 2. The WSGI file

Click the link **WSGI configuration file** (it is `/var/www/YOUR_USERNAME_pythonanywhere_com_wsgi.py`).
Replace its **entire content** with exactly this:

```python
import sys

path = "/home/YOUR_USERNAME/arnab_chat_web-main"
if path not in sys.path:
    sys.path.insert(0, path)

from wsgi import application  # noqa: E402
```

* `wsgi.py` (in the project) creates the Flask app with `create_app()` and switches to the project folder, so `.env`,
  `instance/` and `logs/` are always found.
* The object PythonAnywhere uses must be named **`application`**. Do not rename it.
* Do **not** put `SECRET_KEY` or any other secret in this file: it lives in `.env`.

## 3. Static files mapping

Web tab → **Static files** → add:

| URL | Directory |
|---|---|
| `/static/` | `/home/YOUR_USERNAME/arnab_chat_web-main/app/static` |

(The service worker `/sw.js` and the manifest are served by the application itself, so they need no mapping.)

Check: open `https://YOUR_USERNAME.pythonanywhere.com/static/js/app.js`. You should see JavaScript, not a 404.

## 4. Reload

Click the green **Reload** button after every change to the WSGI file, the virtualenv, `.env` or the code.

## 5. Common mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Wrong path in the WSGI file (folder missing or misspelled) | `ModuleNotFoundError: No module named 'wsgi'` or `'app'` in the error log | Fix `path`; it must be the folder that contains `wsgi.py` |
| Virtualenv box empty or wrong | `No module named 'flask_sqlalchemy'` | Set the Virtualenv path; reinstall with `pip install -r requirements.txt` while `workon goppo-venv` is active |
| `.env` missing | `RuntimeError: SECRET_KEY is missing` | Create `.env` (guide 2) |
| Static mapping missing | Page has no style; `/static/...` is 404 | Add the mapping above |
| Forgot to click Reload | Old behaviour persists | Reload |
| Python version on the Web tab differs from the virtualenv | Strange import errors | Make both the same (3.13) |
| Relative `DATABASE_URL` from an older setup | A brand-new empty database appears | Use the absolute form (guide 2); the repaired config resolves relative paths against the project folder |

## 6. Real-time behaviour

The web app uses **polling** (the browser asks every few seconds). Do not install or enable WebSockets or Flask-SocketIO on
PythonAnywhere; the chat does not need them.
