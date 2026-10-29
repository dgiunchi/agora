"""Runner: one headless agent turn, with artifacts, timeouts and rate-limit detection.

Mirrors the Claudex Loop runner contract: argv lists (never shell strings), a
per-turn artifact directory with prompt.txt / command.json / stdout.txt /
stderr.txt / result.json, process-tree kill on timeout, and no inference of
success from the mere existence of output. HTTP agents (FreeLLMAPI, Ollama,
vLLM, any OpenAI-compatible endpoint) go through the same path.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .registry import AgentSpec

RATE_LIMIT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\b429\b", r"rate.?limit", r"usage limit", r"quota (exceeded|exhausted|reached)",
        r"too many requests", r"resource.?exhausted", r"limit reached",
    )
]
ARTIFACT_FILES = ("prompt.txt", "command.json", "stdout.txt", "stderr.txt", "result.json")


@dataclass
class TurnResult:
    ok: bool
    agent_id: str
    role: str
    exit_code: int | None
    stdout: str
    stderr: str
    artifact_dir: str
    duration: float
    rate_limited: bool = False
    timed_out: bool = False
    error: str | None = None
    argv: list[str] = field(default_factory=list)
    parsed: Any = None
    observed_model: str | None = None

    def text(self) -> str:
        """Best-effort answer text (unwraps `claude -p --output-format json`)."""
        if isinstance(self.parsed, dict):
            for key in ("result", "response", "content", "text"):
                v = self.parsed.get(key)
                if isinstance(v, str):
                    return v
        return self.stdout


def build_argv(spec: AgentSpec, prompt: str, *, role: str = "", read_only: bool = False,
               platform: str = sys.platform, node: str = "node") -> list[str]:
    """Substitute placeholders and apply the Windows Node launch rule.

    On Windows npm-installed CLIs are .cmd shims; launching the shim through
    cmd.exe mangles quoting, so when the catalog names `node_entry` we launch
    `node <entry>` directly.
    """
    def sub(x: str) -> str:
        return x.replace("{prompt}", prompt).replace("{role}", role)

    if spec.kind == "http":
        return ["http", spec.endpoint or ""]
    argv = [sub(x) for x in spec.argv]
    if read_only and spec.read_only_argv:
        argv += [sub(x) for x in spec.read_only_argv]
    if platform.startswith("win") and spec.node_entry:
        argv = [node, spec.node_entry] + argv[1:]
    return argv


def looks_rate_limited(exit_code: int | None, stdout: str, stderr: str) -> bool:
    if exit_code == 429:
        return True
    blob = (stderr or "")[-4000:] + "\n" + (stdout or "")[-4000:]
    return any(p.search(blob) for p in RATE_LIMIT_PATTERNS)


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform.startswith("win"):
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except Exception:  # noqa: BLE001 - last resort
            proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _parse_output(spec: AgentSpec, stdout: str) -> tuple[Any, str | None]:
    parsed: Any = None
    observed: str | None = None
    if spec.output_format == "json" or spec.kind == "http":
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None
    elif spec.output_format == "stream-json":
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("type") in ("result", None):
                    parsed = obj
                    break
    if isinstance(parsed, dict):
        for key in ("model", "observed_model"):
            if isinstance(parsed.get(key), str):
                observed = parsed[key]
                break
    return parsed, observed


def http_turn(spec: AgentSpec, prompt: str, *, timeout: float, env: dict[str, str]
              ) -> tuple[int | None, str, str, str | None]:
    """One OpenAI-compatible chat completion.

    Returns (status, stdout_text, stderr_text, error). The answer is wrapped
    as {"result": ..., "model": ...} on stdout so CLI and HTTP agents look the
    same downstream; a 429 surfaces through the status code.
    """
    url = (spec.endpoint or "").rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    key = env.get(spec.api_key_env or "", "") if spec.api_key_env else ""
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = json.dumps({"model": spec.model, "messages": [{"role": "user", "content": prompt}],
                       "stream": False}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-configured URL
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, "", raw or str(exc), None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, "", str(exc), f"http request failed: {exc}"
    try:
        data = json.loads(raw)
        text = data["choices"][0]["message"]["content"]
        served = data.get("model")
    except (ValueError, KeyError, IndexError, TypeError):
        return status, raw, "", "unexpected response shape"
    return status, json.dumps({"result": text, "model": served}), "", None


def new_artifact_dir(root: str | Path | None, agent_id: str, role: str) -> Path:
    base = Path(root) if root else Path(tempfile.gettempdir()) / "agora"
    d = base / f"{time.strftime('%Y%m%d-%H%M%S')}-{role}-{agent_id}-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def run_turn(spec: AgentSpec, role: str, prompt: str, *, artifacts_root: str | Path | None = None,
             timeout: float = 600.0, read_only: bool = False, cwd: str | Path | None = None,
             env: dict[str, str] | None = None) -> TurnResult:
    art = new_artifact_dir(artifacts_root, spec.id, role)
    argv = build_argv(spec, prompt, role=role, read_only=read_only)
    (art / "prompt.txt").write_text(prompt, encoding="utf-8")
    (art / "command.json").write_text(json.dumps({
        "argv": argv, "cwd": str(cwd) if cwd else None, "timeout": timeout, "read_only": read_only,
        "agent": spec.id, "role": role, "requested_model": spec.model, "kind": spec.kind,
    }, indent=2), encoding="utf-8")
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    start = time.monotonic()
    stdout = stderr = ""
    exit_code: int | None = None
    timed_out = False
    error: str | None = None
    if spec.kind == "http":
        status, stdout, stderr, error = http_turn(spec, prompt, timeout=timeout, env=full_env)
        if status is not None:
            exit_code = 0 if 200 <= status < 300 else status
    else:
        try:
            if shutil.which(argv[0]) is None and not Path(argv[0]).exists():
                raise FileNotFoundError(argv[0])
            popen_kwargs: dict[str, Any] = {}
            if not sys.platform.startswith("win"):
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace", cwd=str(cwd) if cwd else None,
                                    env=full_env, **popen_kwargs)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                try:
                    out2, err2 = proc.communicate(timeout=5)
                    stdout, stderr = out2 or "", err2 or ""
                except Exception:  # noqa: BLE001
                    pass
                error = f"timeout after {timeout}s; process tree killed"
        except FileNotFoundError as exc:
            error = f"executable not found: {exc}"
        except OSError as exc:
            error = f"launch failed: {exc}"
    duration = time.monotonic() - start
    (art / "stdout.txt").write_text(stdout or "", encoding="utf-8")
    (art / "stderr.txt").write_text(stderr or "", encoding="utf-8")
    rate_limited = looks_rate_limited(exit_code, stdout, stderr)
    parsed, observed = _parse_output(spec, stdout or "")
    ok = error is None and exit_code == 0 and not rate_limited and not timed_out
    result = TurnResult(ok=ok, agent_id=spec.id, role=role, exit_code=exit_code, stdout=stdout or "",
                        stderr=stderr or "", artifact_dir=str(art), duration=duration,
                        rate_limited=rate_limited, timed_out=timed_out, error=error, argv=argv,
                        parsed=parsed, observed_model=observed)
    record = asdict(result)
    (art / "result.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return result