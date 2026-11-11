"""WSGI entry point, used by gunicorn."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'linkpulse.settings')
application = get_wsgi_application()
