"""WSGI entrypoint. Run locally with `python wsgi.py` or behind Gunicorn."""
from wsgiref.simple_server import make_server

from pyh.app import Application
from pyh.config import Config

application = Application()

if __name__ == "__main__":
    problems = Config.validate_production()
    if problems:
        raise SystemExit("Configuration errors: " + "; ".join(problems))
    host = __import__("os").getenv("HOST", "127.0.0.1")
    port = int(__import__("os").getenv("PORT", "8000"))
    with make_server(host, port, application) as server:
        print(f"Plan Your Home running on http://{host}:{port} (Ctrl+C to stop)")
        server.serve_forever()
