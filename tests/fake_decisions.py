"""A fake decision endpoint for tests: loopback only, zero quota.

Answers every question in the request from a script: `choices` maps a question id to
the option to pick, `noul_p` is the yes probability, scores take the top level,
`confidence` is reported on choice and score answers. `mode` selects failures:
"429" (with Retry-After: 7), "500", "wrong_model", "malformed", "invalid_choice".
`api` selects the response shape: systemone (default), cloudflare, openrouter.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeDecisionServer:
    def __init__(self, *, mode: str = "ok", model: str = "fake-1", choices: dict | None = None,
                 confidence: float = 0.9, noul_p: float = 0.9, sleep: float = 0.0, api: str = "systemone") -> None:
        self.mode, self.model, self.choices = mode, model, choices or {}
        self.confidence, self.noul_p, self.sleep, self.api = confidence, noul_p, sleep, api
        self.requests: list[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: D401 - silence
                pass

            def _send(self, status: int, payload: dict, headers: dict | None = None) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                server.requests.append({"path": self.path, "body": body,
                                        "auth": self.headers.get("Authorization")})
                if server.sleep:
                    time.sleep(server.sleep)
                if server.mode == "429":
                    return self._send(429, {"error": "rate limited"}, {"Retry-After": "7"})
                if server.mode == "500":
                    return self._send(500, {"error": "boom"})
                answers: dict = {}
                for qid, q in (body.get("questions") or {}).items():
                    t = q.get("type")
                    if t == "choice":
                        keys = list((q.get("criteria") or {}).keys())
                        c = server.choices.get(qid, keys[0] if keys else "")
                        if server.mode == "invalid_choice":
                            c = "not-an-option"
                        rest = (1.0 - server.confidence) / max(len(keys) - 1, 1)
                        answers[qid] = {"type": "choice", "choice": c, "confidence": server.confidence,
                                        "probabilities": {k: (server.confidence if k == c else rest) for k in keys}}
                    elif t == "noul":
                        answers[qid] = {"type": "noul", "noul": server.noul_p}
                    else:
                        levels = q.get("criteria") or []
                        answers[qid] = {"type": "score", "score": max(len(levels) - 1, 0),
                                        "confidence": server.confidence}
                if server.mode == "malformed":
                    answers = {"nothing": {}}
                requested = body.get("model")
                served = "other-model" if server.mode == "wrong_model" else (
                    server.model if requested in (None, "auto") else requested)
                usage = {"input_tokens": 10, "output_tokens": 1}
                if server.api == "cloudflare":
                    return self._send(200, {"success": True, "result": {"answers": answers, "usage": usage}})
                if server.api == "openrouter":
                    return self._send(200, {"answers": answers, "usage": usage, "model": served})
                return self._send(200, {"model": served, "answers": answers, "usage": usage})

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def start(self) -> "FakeDecisionServer":
        self._thread.start()
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/v1/decisions"

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
