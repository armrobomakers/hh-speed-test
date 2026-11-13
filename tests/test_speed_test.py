import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from urllib.error import URLError

from speed_test import run_speed_test, validate_url


class ServerState:
    lock = threading.Lock()
    hits = 0
    active = 0
    max_active = 0
    payload = b"download-test-payload" * 1024

    @classmethod
    def reset(cls) -> None:
        with cls.lock:
            cls.hits = 0
            cls.active = 0
            cls.max_active = 0


class TestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API name
        with ServerState.lock:
            ServerState.hits += 1
            hit = ServerState.hits
            ServerState.active += 1
            ServerState.max_active = max(ServerState.max_active, ServerState.active)

        try:
            time.sleep(0.005)
            if self.path == "/flaky" and hit == 2:
                body = b"temporary error"
                declared_length = len(body)
                self.send_response(503, "Service Unavailable")
            elif self.path == "/truncated":
                body = ServerState.payload
                declared_length = len(body) + 10
                self.send_response(200)
            else:
                body = ServerState.payload
                declared_length = len(body)
                self.send_response(200)
            self.send_header("Content-Length", str(declared_length))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        finally:
            with ServerState.lock:
                ServerState.active -= 1

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class SpeedTestTests(unittest.TestCase):
    def setUp(self) -> None:
        ServerState.reset()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_runs_ten_full_requests_sequentially(self) -> None:
        result = run_speed_test(self.base_url + "/file")

        self.assertEqual(len(result.requests), 10)
        self.assertEqual(result.successful_requests, 10)
        self.assertEqual(result.failed_requests, 0)
        self.assertEqual(ServerState.hits, 10)
        self.assertEqual(ServerState.max_active, 1)
        self.assertEqual(result.total_bytes_downloaded, len(ServerState.payload) * 10)
        self.assertGreater(result.average_request_seconds, 0)
        self.assertGreater(result.speed_mbit_per_second, 0)

    def test_continues_after_http_error_and_reports_failure(self) -> None:
        result = run_speed_test(self.base_url + "/flaky", request_count=3)

        self.assertEqual(ServerState.hits, 3)
        self.assertEqual(result.successful_requests, 2)
        self.assertEqual(result.failed_requests, 1)
        self.assertIn("HTTP 503", result.requests[1].error or "")
        self.assertEqual(
            result.total_bytes_downloaded,
            len(ServerState.payload) * 2 + len(b"temporary error"),
        )

    def test_rejects_non_http_url(self) -> None:
        with self.assertRaises(ValueError):
            validate_url("ftp://example.com/file")

    def test_rejects_missing_host(self) -> None:
        with self.assertRaises(ValueError):
            validate_url("https:///file")

    def test_detects_truncated_body(self) -> None:
        result = run_speed_test(self.base_url + "/truncated", request_count=1)

        self.assertEqual(result.failed_requests, 1)
        self.assertEqual(result.total_bytes_downloaded, len(ServerState.payload))
        self.assertIn("неполный ответ", result.requests[0].error or "")

    def test_cli_prints_metrics_against_local_server(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(project_dir / "speed_test.py"),
                self.base_url + "/file",
            ],
            cwd=project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Запросы: 10 (успешно: 10, ошибок: 0)", completed.stdout)
        self.assertIn("Всего получено:", completed.stdout)
        self.assertIn("Mbit/s", completed.stdout)
        self.assertEqual(ServerState.hits, 10)

    def test_network_error_is_recorded_and_remaining_attempts_continue(self) -> None:
        with patch("speed_test.urlopen", side_effect=URLError("offline")) as mocked_urlopen:
            result = run_speed_test("https://example.com/file", request_count=2)

        self.assertEqual(mocked_urlopen.call_count, 2)
        self.assertEqual(result.successful_requests, 0)
        self.assertEqual(result.failed_requests, 2)
        self.assertEqual(result.total_bytes_downloaded, 0)


if __name__ == "__main__":
    unittest.main()

