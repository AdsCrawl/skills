"""Run the shipped CLI against HTTP mocks; no live AdsCrawl account is used."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SCRIPT = Path(__file__).resolve().parents[1] / "skills/scripts/cloud_browser.py"
KEY = "fake-api-key-do-not-print"
USER = "fake-proxy-user-do-not-print"
PASSWORD = "fake-proxy-password-do-not-print"
COOKIE = "fake-cookie-do-not-print"
TOKEN = "fake-cdp-token-do-not-print"
PROFILE_ID = "profile-1"


class MockAPI:
    def __init__(self):
        self.calls = []
        self.responses = []
        self.failures = []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw) if raw else None
                mock.calls.append((self.command, self.path, dict(self.headers), body))
                if not mock.responses:
                    mock.failures.append("Unexpected request")
                    status, response, headers = 500, {}, {}
                else:
                    method, path, status, response, headers = mock.responses.pop(0)
                    if (self.command, self.path) != (method, path):
                        mock.failures.append("Request method/path did not match expectation")
                if response is None:
                    self.close_connection = True
                    return
                encoded = response if isinstance(response, bytes) else json.dumps(response).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(encoded)

            do_GET = handle_request
            do_POST = handle_request

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever)

    def expect(self, method, path, status, data, headers=None):
        self.responses.append((method, path, status, data, headers or {}))

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def profile(state):
    return {"id": PROFILE_ID, "browserSettings": {
        "cookies": [{"name": "session", "value": COOKIE}],
        "proxy": {"username": USER, "password": PASSWORD}},
        "runtime": {"status": state, "runtimeKind": "neko",
                    "cdpBaseUrl": "https://example.com?token=" + TOKEN,
                    "connectUrl": "https://example.com?token=" + TOKEN}}


class LifecycleTests(unittest.TestCase):
    def cli(self, mock, *args, success=True, env=None):
        # Do not inherit real AdsCrawl credentials or real proxy credentials.
        process_env = {k: v for k, v in os.environ.items() if not k.startswith("ADSCRAWL_")}
        process_env.update(ADSCRAWL_BASE_URL=mock.url, ADSCRAWL_API_KEY=KEY,
                           ADSCRAWL_PROXY_SERVER="http://proxy.example.com:8080",
                           ADSCRAWL_PROXY_USERNAME=USER, ADSCRAWL_PROXY_PASSWORD=PASSWORD)
        for key, value in (env or {}).items():
            if value is None:
                process_env.pop(key, None)
            else:
                process_env[key] = value
        result = subprocess.run([sys.executable, str(SCRIPT), "--request-timeout", "2", *args],
                                env=process_env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        for secret in (KEY, USER, PASSWORD, COOKIE, TOKEN):
            self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        if not success:
            self.assertEqual(result.stdout, "")
        return result

    def assert_complete(self, mock):
        self.assertEqual(mock.failures, [])
        self.assertEqual(mock.responses, [])
        for _, _, headers, _ in mock.calls:
            headers = {k.lower(): v for k, v in headers.items()}
            self.assertEqual(headers.get("x-api-key"), KEY)
            self.assertNotIn("authorization", headers)
            self.assertNotIn("cookie", headers)

    def test_full_lifecycle_and_restart_resend_proxy(self):
        path = "/cloud-browsers/" + PROFILE_ID
        with MockAPI() as api:
            api.expect("POST", "/cloud-browsers", 201, {"ok": True, "id": PROFILE_ID})
            result = self.cli(api, "create", "--remark", "test lifecycle")
            self.assertEqual(json.loads(result.stdout)["id"], PROFILE_ID)
            self.assertEqual(api.calls[-1][3], {"remark": "test lifecycle",
                "browserSettings": {"viewport": {"width": 1440, "height": 900}}})
            for scheme in ("http", "socks5"):
                server = scheme + "://proxy.example.com:8080"
                api.expect("POST", path + "/start", 200, {"ok": True, **profile("running")})
                self.cli(api, "start", PROFILE_ID, env={"ADSCRAWL_PROXY_SERVER": server})
                self.assertEqual(api.calls[-1][3], {"proxy": {
                    "server": server, "username": USER, "password": PASSWORD}})
                self.assertEqual(api.calls[-1][2].get("Content-Type"), "application/json")
                api.expect("GET", path, 200, profile("running"))
                self.assertEqual(json.loads(self.cli(api, "get", PROFILE_ID).stdout), {
                    "id": PROFILE_ID, "runtime": {"status": "running", "runtimeKind": "neko"}})
                api.expect("GET", "/cloud-browsers?page=2&pageSize=10", 200, {
                    "ok": True, "data": [profile("running")], "limit": 10,
                    "runningLimit": 1, "runningCount": 1})
                listing = json.loads(self.cli(api, "list", "--page", "2").stdout)
                self.assertEqual([listing[k] for k in ("limit", "runningLimit", "runningCount")], [10, 1, 1])
                api.expect("POST", path + "/stop", 202, {"ok": True, "runtime": {"status": "stopping"}})
                api.expect("GET", path, 200, profile("stopping"))
                api.expect("GET", path, 200, profile("stopped"))
                stopped = self.cli(api, "stop", PROFILE_ID, "--poll-interval", "0.001")
                self.assertEqual(json.loads(stopped.stdout)["runtime"]["status"], "stopped")
            api.expect("POST", path + "/stop", 200, {"ok": True, "runtime": {"status": "stopped"}})
            self.cli(api, "stop", PROFILE_ID, env={"ADSCRAWL_PROXY_SERVER": None})
            self.assertTrue(all(body is None for method, url, _, body in api.calls if url.endswith("/stop")))
            self.assert_complete(api)

    def test_missing_or_invalid_proxy_fails_before_network(self):
        cases = [None, "", "http://proxy.example.com", "https://proxy.example.com:8080",
                 "socks5://proxy.example.com:0", "http://proxy.example.com:65536",
                 "http://user:password@proxy.example.com:8080", "http://proxy.example.com:8080/path"]
        with MockAPI() as api:
            for server in cases:
                with self.subTest(server=server):
                    self.cli(api, "start", PROFILE_ID, success=False, env={"ADSCRAWL_PROXY_SERVER": server})
            for user, password in [(None, PASSWORD), (USER, None), ("", ""), (USER, " ")]:
                self.cli(api, "start", PROFILE_ID, success=False,
                         env={"ADSCRAWL_PROXY_USERNAME": user, "ADSCRAWL_PROXY_PASSWORD": password})
            self.assertEqual(api.calls, [])

    def test_unauthenticated_proxy_omits_both_credentials(self):
        with MockAPI() as api:
            api.expect("POST", "/cloud-browsers/profile-1/start", 200, {"ok": True, **profile("running")})
            self.cli(api, "start", PROFILE_ID, env={"ADSCRAWL_PROXY_USERNAME": None, "ADSCRAWL_PROXY_PASSWORD": None})
            self.assertEqual(api.calls[-1][3], {"proxy": {"server": "http://proxy.example.com:8080"}})
            self.assert_complete(api)

    def test_errors_are_not_retried_or_leaked(self):
        cases = [(400, "PROXY_REQUIRED"), (400, "INVALID_PROXY"), (400, "COUNTRY_PROXY_CONFLICT"),
                 (401, KEY), (403, "FORBIDDEN"), (404, "NOT_FOUND"),
                 (402, "PAID_PLAN_REQUIRED"), (402, "INSUFFICIENT_CREDITS"),
                 (409, "CLOUD_BROWSER_CONCURRENCY_LIMIT"), (409, None),
                 (503, "CLUSTER_NO_CAPACITY"), (504, "CLOUD_RUNTIME_TIMEOUT")]
        with MockAPI() as api:
            for status, code in cases:
                api.expect("POST", "/cloud-browsers/profile-1/start", status,
                           {"code": code, "error": f"{PASSWORD} {COOKIE} {TOKEN}"})
                result = self.cli(api, "start", PROFILE_ID, success=False)
                self.assertIn(f"HTTP {status}", result.stderr)
                if code in ("PROXY_REQUIRED", "INVALID_PROXY", "COUNTRY_PROXY_CONFLICT", "CLOUD_BROWSER_CONCURRENCY_LIMIT"):
                    self.assertIn(code, result.stderr)
            self.assertEqual(len(api.calls), len(cases))
            self.assert_complete(api)

    def test_stop_deadline_and_error_never_report_stopped(self):
        path = "/cloud-browsers/profile-1"
        with MockAPI() as api:
            api.expect("POST", path + "/stop", 202, {"ok": True, "runtime": {"status": "stopping"}})
            for _ in range(2):
                api.expect("GET", path, 200, profile("stopping"))
            result = self.cli(api, "stop", PROFILE_ID, "--poll-attempts", "2", "--poll-interval", "0.001", success=False)
            self.assertIn("unconfirmed", result.stderr)
            for status, code in ((409, "CDP_SESSION_STARTING"), (504, "CLOUD_RUNTIME_TIMEOUT")):
                api.expect("POST", path + "/stop", status, {"code": code, "error": PASSWORD})
                self.assertIn(code, self.cli(api, "stop", PROFILE_ID, success=False).stderr)
            api.expect("POST", path + "/stop", 202, {"ok": True, "runtime": {"status": "stopping"}})
            api.expect("GET", path, 503, {"error": TOKEN})
            self.cli(api, "stop", PROFILE_ID, success=False)
            self.assert_complete(api)

    def test_redirect_does_not_forward_api_key(self):
        with MockAPI() as api, MockAPI() as other:
            api.expect("GET", "/cloud-browsers/profile-1", 302, {}, {"Location": other.url + "/stolen"})
            self.cli(api, "get", PROFILE_ID, success=False)
            self.assertEqual(other.calls, [])
            self.assert_complete(api)

    def test_malformed_and_premature_success_responses(self):
        with MockAPI() as api:
            for response in (b"not-json " + PASSWORD.encode(), [PASSWORD], {"runtime": TOKEN},
                             {"runtime": {"status": TOKEN}}, {"ok": True, **profile("starting")}):
                api.expect("POST", "/cloud-browsers/profile-1/start", 200, response)
                self.cli(api, "start", PROFILE_ID, success=False)
            api.expect("POST", "/cloud-browsers", 200, {"ok": True, "id": PROFILE_ID})
            self.cli(api, "create", success=False)
            self.assert_complete(api)

    def test_configuration_failures_do_not_contact_api(self):
        with MockAPI() as api:
            for env in ({"ADSCRAWL_API_KEY": None}, {"ADSCRAWL_API_KEY": "bad\nkey"},
                        {"ADSCRAWL_BASE_URL": "http://api.example.com"},
                        {"ADSCRAWL_BASE_URL": "https://api.example.com?token=" + TOKEN}):
                self.cli(api, "get", PROFILE_ID, success=False, env=env)
            self.cli(api, "stop", "../other", success=False)
            self.cli(api, "stop", PROFILE_ID, "--poll-attempts", "0", success=False)
            self.cli(api, "stop", PROFILE_ID, "--poll-interval", "nan", success=False)
            self.assertEqual(api.calls, [])

    def test_disconnect_leaves_mutation_outcome_unconfirmed(self):
        with MockAPI() as api:
            for operation in ("start", "stop"):
                api.expect("POST", f"/cloud-browsers/profile-1/{operation}", 200, None)
                result = self.cli(api, operation, PROFILE_ID, success=False)
                self.assertIn("query profile state", result.stderr)
            self.assertEqual(len(api.calls), 2)
            self.assert_complete(api)

    def test_list_reports_zero_and_changed_quota_without_local_inference(self):
        with MockAPI() as api:
            for limit, count in ((0, 1), (1, 1), (3, 2)):
                api.expect("GET", "/cloud-browsers?page=1&pageSize=10", 200,
                           {"ok": True, "data": [], "limit": 10,
                            "runningLimit": limit, "runningCount": count})
                result = json.loads(self.cli(api, "list").stdout)
                self.assertEqual(result["runningLimit"], limit)
                self.assertEqual(result["runningCount"], count)
            self.assert_complete(api)


if __name__ == "__main__":
    unittest.main()
