import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from agora.registry import AgentSpec
from agora.runner import ARTIFACT_FILES, build_argv, looks_rate_limited, run_turn

from .helpers import fake_spec, write_script


class BuildArgvTests(unittest.TestCase):
    def test_substitutes_placeholders_and_read_only(self):
        s = AgentSpec(id="x", provider="p", argv=("tool", "-p", "{prompt}", "--role", "{role}"),
                      read_only_argv=("--safe-mode",), write_argv=("-s", "workspace-write"))
        self.assertEqual(build_argv(s, "hi", role="build", platform="linux"),
                         ["tool", "-p", "hi", "--role", "build", "-s", "workspace-write"])
        ro = build_argv(s, "hi", role="review", read_only=True, platform="linux")
        self.assertEqual(ro[-1], "--safe-mode")
        self.assertNotIn("workspace-write", ro)

    def test_windows_launches_npm_cli_through_node(self):
        s = AgentSpec(id="x", provider="p", argv=("claude", "-p", "{prompt}"), node_entry="C:/npm/claude/cli.js")
        self.assertEqual(build_argv(s, "hi", platform="win32"), ["node", "C:/npm/claude/cli.js", "-p", "hi"])
        self.assertEqual(build_argv(s, "hi", platform="linux")[0], "claude")

    def test_parses_codex_jsonl_and_claude_stream_json(self):
        from agora.runner import _parse_output
        codex = AgentSpec(id="c", provider="openai", argv=("codex", "exec", "--json", "{prompt}"),
                          output_format="stream-json")
        out = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "thinking"}}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"verdict": "APPROVED"}'}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}),
        ])
        parsed, _ = _parse_output(codex, out)
        self.assertEqual(parsed["result"], '{"verdict": "APPROVED"}')
        self.assertEqual(parsed["usage"]["output_tokens"], 5)
        claude = AgentSpec(id="a", provider="anthropic", argv=("claude", "-p", "{prompt}"), output_format="stream-json")
        out = json.dumps({"type": "system"}) + "\n" + json.dumps({"type": "result", "result": "hi", "model": "m1"})
        parsed, observed = _parse_output(claude, out)
        self.assertEqual((parsed["result"], observed), ("hi", "m1"))

    def test_rate_limit_detection(self):
        self.assertTrue(looks_rate_limited(429, "", ""))
        self.assertTrue(looks_rate_limited(1, "", "HTTP 429 Too Many Requests"))
        self.assertTrue(looks_rate_limited(1, "You have hit your usage limit", ""))
        self.assertTrue(looks_rate_limited(1, "", "Rate-limit exceeded, retry later"))
        self.assertFalse(looks_rate_limited(0, "all good", ""))
        # a clean exit is never rate limited by keyword: agents echo files, including our own log
        echoed_log = "- [2026-10-29T15:01:59Z] TURN role=plan agent=x ok=true rate_limited=false"
        self.assertFalse(looks_rate_limited(0, echoed_log, "HTTP 429 seen in a file"))
        self.assertFalse(looks_rate_limited(1, echoed_log, ""))   # rate_limited= is not a rate limit
        self.assertFalse(looks_rate_limited(1, "", 'File "x.py", line 429, in main'))   # a line number
        self.assertFalse(looks_rate_limited(1, "", "context limit reached, truncating"))
        self.assertTrue(looks_rate_limited(1, "", "usage limit reached for this window"))


class RunTurnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.spec = fake_spec("alpha", "anthropic")

    def _env(self, script):
        return {"FAKE_SCRIPT": str(write_script(self.tmp, script))}

    def test_artifacts_and_stderr_preserved(self):
        env = self._env({"alpha": {"plan": [{"json": {"plan": "# P"}, "stderr": "warn: something"}]}})
        r = run_turn(self.spec, "plan", "do it", artifacts_root=self.tmp / "art", env=env, timeout=30)
        self.assertTrue(r.ok)
        self.assertEqual(r.parsed, {"plan": "# P"})
        self.assertIn("warn: something", r.stderr)
        for name in ARTIFACT_FILES:
            self.assertTrue((Path(r.artifact_dir) / name).exists(), name)
        cmd = json.loads((Path(r.artifact_dir) / "command.json").read_text())
        self.assertEqual(cmd["argv"], r.argv)
        self.assertEqual(json.loads((Path(r.artifact_dir) / "result.json").read_text())["ok"], True)

    def test_timeout_kills_process_tree(self):
        env = self._env({"alpha": {"plan": [{"sleep": 30, "json": {"plan": "late"}}]}})
        t0 = time.monotonic()
        r = run_turn(self.spec, "plan", "x", artifacts_root=self.tmp / "art", env=env, timeout=1.0)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)
        self.assertIn("timeout", r.error)

    def test_rate_limited_turn_is_not_ok(self):
        env = self._env({"alpha": {"review": [{"stdout": "", "stderr": "429 rate limit exceeded", "exit": 1}]}})
        r = run_turn(self.spec, "review", "x", artifacts_root=self.tmp / "art", env=env, timeout=30)
        self.assertTrue(r.rate_limited)
        self.assertFalse(r.ok)

    def test_missing_executable(self):
        s = AgentSpec(id="nope", provider="p", argv=("definitely-not-a-real-binary-xyz", "{prompt}"))
        r = run_turn(s, "plan", "x", artifacts_root=self.tmp / "art", timeout=5)
        self.assertFalse(r.ok)
        self.assertIn("not found", r.error)

    def test_nonzero_exit_is_not_ok(self):
        env = self._env({"alpha": {"build": [{"stdout": "boom", "exit": 3}]}})
        r = run_turn(self.spec, "build", "x", artifacts_root=self.tmp / "art", env=env, timeout=30)
        self.assertEqual(r.exit_code, 3)
        self.assertFalse(r.ok)


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.mode == "429":
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b'{"error": "rate limit"}')
            return
        payload = {"model": "served-model", "choices": [{"message": {"role": "assistant",
                   "content": json.dumps({"plan": "# from http " + body["model"]})}}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # silence
        pass


class HttpAgentTests(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.tmp = Path(tempfile.mkdtemp())
        port = self.server.server_address[1]
        self.spec = AgentSpec(id="router", provider="freellmapi", kind="http", tier="free",
                              endpoint=f"http://127.0.0.1:{port}/v1", model="auto", api_key_env="FLA_KEY")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_http_turn_ok(self):
        _Handler.mode = "ok"
        r = run_turn(self.spec, "plan", "hello", artifacts_root=self.tmp, env={"FLA_KEY": "k"}, timeout=10)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.observed_model, "served-model")
        self.assertIn("from http auto", r.text())

    def test_http_429_is_rate_limited(self):
        _Handler.mode = "429"
        r = run_turn(self.spec, "plan", "hello", artifacts_root=self.tmp, env={}, timeout=10)
        self.assertTrue(r.rate_limited)
        self.assertFalse(r.ok)


if __name__ == "__main__":
    unittest.main()