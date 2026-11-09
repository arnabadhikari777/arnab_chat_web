# Used by PythonAnywhere: point the WSGI file to "from wsgi import application".
from app import create_app

application = create_app()
