"""Exercise the workload clients against a real local HTTP fixture; no GPU needed."""
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def import_workload(name, relative_path, environment):
    with patch.dict(os.environ, environment):
        spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class WorkloadTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.fail_model = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                model = payload["model"]
                owner.calls.append((model, self.headers.get("Authorization"), self.client_address))
                failed = owner.fail_model in (model, "all")
                body = json.dumps(
                    {"error": {"message": "fixture failure"}} if failed
                    else {"choices": [{"message": {"content": "Hello from the fixture."}}]}
                ).encode()
                self.send_response(503 if failed else 200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def smoke(self):
        return import_workload("smoke", "workloads/smoke-client/smoke_test.py", {
            "BASE_URL": self.base_url, "OPENAI_API_KEY": "fixture-key",
        })

    def load(self):
        return import_workload("load", "workloads/text-load/load.py", {
            "LOAD_API_BASE": self.base_url, "LOAD_API_KEY": "fixture-key",
            "LOAD_USERS": "3", "LOAD_DURATION_SEC": "0.20",
            "LOAD_THINK_TIME_SEC": "0.005", "LOAD_TIMEOUT_SEC": "2",
            "LOAD_MODEL_MODE": "all", "LOAD_MAX_TOKENS": "8",
        })

    def test_smoke_checks_all_models_with_auth_and_one_connection(self):
        with redirect_stdout(io.StringIO()):
            result = self.smoke().main()
        self.assertEqual(result, 0)
        self.assertEqual([call[0] for call in self.calls], ["general-chat", "fast-chat", "tiny-chat"])
        self.assertTrue(all(call[1] == "Bearer fixture-key" for call in self.calls))
        self.assertEqual(len({call[2] for call in self.calls}), 1)

    def test_smoke_failure_is_nonzero_and_other_models_are_still_checked(self):
        self.fail_model = "fast-chat"
        errors = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(errors):
            result = self.smoke().main()
        self.assertEqual(result, 1)
        self.assertEqual(len(self.calls), 3)
        self.assertIn("fast-chat", errors.getvalue())

    def test_load_reuses_bounded_pool_and_reports_success_latency(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = asyncio.run(self.load().main())
        self.assertEqual(result, 0)
        self.assertGreater(len(self.calls), 3)
        self.assertLessEqual(len({call[2] for call in self.calls}), 3)
        self.assertTrue(all(call[1] == "Bearer fixture-key" for call in self.calls))
        self.assertEqual({call[0] for call in self.calls}, {"general-chat", "fast-chat", "tiny-chat"})
        self.assertIn("success_p95=", output.getvalue())
        self.assertIn(f"successful_requests={len(self.calls)}", output.getvalue())

    def test_load_reports_http_failures_without_treating_them_as_fast_successes(self):
        self.fail_model = "all"
        output = io.StringIO()
        with redirect_stdout(output):
            result = asyncio.run(self.load().main())
        self.assertEqual(result, 1)
        self.assertIn("successful_requests=0", output.getvalue())
        self.assertIn("success_avg_latency=0.000s", output.getvalue())
        self.assertIn("http_503", output.getvalue())

    def test_invalid_load_configuration_is_rejected(self):
        module = self.load()
        for key, value in (("USERS", 0), ("DURATION_SEC", float("nan")),
                           ("THINK_TIME_SEC", -1), ("TIMEOUT_SEC", 0),
                           ("MAX_TOKENS", 0), ("MODEL_MODE", "unknown")):
            with self.subTest(key=key), patch.object(module, key, value):
                with self.assertRaises(ValueError):
                    module.validate_config()


if __name__ == "__main__":
    unittest.main()
