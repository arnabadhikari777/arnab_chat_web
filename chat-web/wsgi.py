"""WSGI entry point for PythonAnywhere.

In the Web tab's WSGI configuration file, the only app lines needed are:

    import sys
    path = "/home/YOUR_USERNAME/arnab_chat_web"      # the folder that contains wsgi.py
    if path not in sys.path:
        sys.path.insert(0, path)
    from wsgi import application

(See deployment/04-wsgi-and-static-configuration.md.)
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)          # relative paths (logs/, instance/) always mean the project folder

from app import create_app  # noqa: E402

application = create_app()
