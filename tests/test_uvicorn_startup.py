"""Prueba HTTP de Uvicorn con un secreto temporal, sin modificar .env."""

from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen


class UvicornStartupTests(unittest.TestCase):
    def test_uvicorn_serves_login_and_styles(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        directory = tempfile.TemporaryDirectory(prefix="atenea-startup-")
        self.addCleanup(directory.cleanup)
        env_path = Path(directory.name) / ".env"
        env_path.write_text("SESSION_SECRET=" + secrets.token_urlsafe(32) + "\n", encoding="utf-8")
        script = """
from pathlib import Path
from unittest.mock import patch
import uvicorn
with patch('app.config.ENV_PATH', Path(ENV_FILE_PLACEHOLDER)):
    uvicorn.run('app.main:create_app', factory=True, host='127.0.0.1', port=PORT, access_log=False, log_level='critical')
""".replace("PORT", str(port)).replace("ENV_FILE_PLACEHOLDER", repr(str(env_path)))
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                self.assertIsNone(process.poll(), "Uvicorn terminó antes de responder; no se muestran detalles sensibles.")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/login", timeout=1) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn('name="password"', response.read().decode())
                    break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                self.fail("Uvicorn no respondió dentro de 30 segundos.")
            with urlopen(f"http://127.0.0.1:{port}/static/styles.css", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("text/css", response.headers["Content-Type"])
            for path in ("/integrations/microsoft", "/integrations/microsoft/callback?state=invalid&code=mock", "/mail", "/mail/mock-message", "/mail/mock-message/create-task"):
                with urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
                    self.assertTrue(response.url.endswith("/login"))
            for path in ("/dashboard", "/clients", "/projects", "/hours", "/hours/week", "/hours/history", "/settings/workdays", "/settings/timezone", "/reports/month", "/reports/week/export", "/reports/month/export", "/tasks", "/tasks/new"):
                with urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
                    self.assertTrue(response.url.endswith("/login"))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
