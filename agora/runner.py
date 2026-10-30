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
        # a bare 429 that is not a line number or part of a version / identifier
        r"(?<!line )(?<![\w.])429(?![\w.])", r"rate[ -]?limit", r"usage limit",
        r"quota (exceeded|exhausted|reached)", r"too many requests", r"resource.?exhausted",
        r"(usage|quota|rate|request) limit reached",
    )
]
ARTIFACT_FILES = ("prompt.txt", "command.json", "stdout.txt", "stderr.txt", "result.json")
# npm's Windows shims end in a line like: "%_prog%" "%dp0%\node_modules\<pkg>\bin\cli.js" %*
_SHIM_ENTRY = re.compile(r'"%~?dp0%?\\([^"]+\.(?:js|cjs|mjs|exe))"', re.IGNORECASE)


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
    tokens_in: int = 0
    tokens_out: int = 0

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
    if not read_only and spec.write_argv:
        argv += [sub(x) for x in spec.write_argv]
    if platform.startswith("win") and spec.node_entry:
        argv = [node, spec.node_entry] + argv[1:]
    return argv


def npm_shim_target(shim: Path) -> list[str] | None:
    """The command an npm `.cmd` shim would run: `[node, entry.js]` or `[entry.exe]`.
    None when the shim does not look like npm's or its target is missing."""
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # The shim first probes for a bundled node.exe ("IF EXIST ...\node.exe"); the real
    # entry is the last quoted path that exists and is not that probe.
    for m in _SHIM_ENTRY.finditer(text):
        entry = shim.parent / m.group(1).replace("\\", "/")   # a Windows path, resolved on any OS
        if entry.name.lower() == "node.exe" or not entry.exists():
            continue
        if entry.suffix.lower() == ".exe":
            return [str(entry)]
        node = shim.parent / "node.exe"
        return [str(node) if node.exists() else "node", str(entry)]
    return None


def resolve_launch(argv: list[str], *, platform: str = sys.platform,
                   which: Any = shutil.which) -> list[str]:
    """Resolve argv[0] to the executable that will actually run.

    `shutil.which` honours PATHEXT, so doctor and run agree on Windows. A `.cmd` /
    `.bat` result is never launched: CreateProcess would hand it to cmd.exe, which
    re-parses every argument, including the prompt. npm shims are resolved to
    their `node <entry.js>` target instead; anything else fails loudly.
    """
    exe = argv[0]
    found = str(Path(exe)) if Path(exe).is_file() else which(exe)
    if not found:
        raise FileNotFoundError(exe)
    if platform.startswith("win") and found.lower().endswith((".cmd", ".bat")):
        target = npm_shim_target(Path(found))
        if target is None:
            raise OSError(f"{found} is a cmd.exe shim; refusing to run the prompt through cmd.exe. "
                          f"Set node_entry in the catalog to the CLI's .js entry file")
        return target + list(argv[1:])
    return [found] + list(argv[1:])


def _usage_tokens(parsed: Any) -> tuple[int, int]:
    usage = parsed.get("usage") if isinstance(parsed, dict) else None
    if not isinstance(usage, dict):
        return 0, 0

    def pick(*keys: str) -> int:
        for k in keys:
            v = usage.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return 0
    return pick("input_tokens", "prompt_tokens"), pick("output_tokens", "completion_tokens")


def looks_rate_limited(exit_code: int | None, stdout: str, stderr: str) -> bool:
    """Keyword detection for CLIs that do not report quota errors structurally.

    A turn that exited 0 is never rate limited by keyword: agents read files and echo
    them (including Agora's own log, which contains `rate_limited=false`), so on a
    clean exit only a structured error from the CLI itself may say so.
    """
    if exit_code == 429:
        return True
    if exit_code == 0:
        return False
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
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, 15)          # a short grace so the CLI can flush and clean up
            try:
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                os.killpg(pgid, 9)
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
        # Claude: a final {"type": "result", "result": ...} line.
        # Codex: JSONL events; the answer is the last item.completed agent_message.
        last_msg: str | None = None
        usage: Any = None
        err_msg: str | None = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            if kind in ("result", None):
                parsed = obj
            elif kind == "item.completed":
                item = obj.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    last_msg = item["text"]
            elif kind == "turn.completed":
                usage = obj.get("usage")
            elif kind in ("error", "turn.failed"):
                msg = obj.get("message") or (obj.get("error") or {}).get("message") if isinstance(obj.get("error"), dict) else obj.get("message")
                if isinstance(msg, str):
                    err_msg = msg
        if parsed is None and (last_msg is not None or err_msg is not None):
            parsed = {"result": last_msg, "usage": usage, "error": err_msg}
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
    return status, json.dumps({"result": text, "model": served, "usage": data.get("usage")}), "", None


def default_artifacts_root() -> Path:
    """Per-user, not the shared temp dir: artifacts hold prompts, plans and code."""
    return Path.home() / ".agora" / "artifacts"


def new_artifact_dir(root: str | Path | None, agent_id: str, role: str) -> Path:
    base = Path(root) if root else default_artifacts_root()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    d = base / f"{time.strftime('%Y%m%d-%H%M%S')}-{role}-{agent_id}-{uuid.uuid4().hex[:8]}"
    d.mkdir(mode=0o700, exist_ok=False)
    return d


def run_turn(spec: AgentSpec, role: str, prompt: str, *, artifacts_root: str | Path | None = None,
             timeout: float = 600.0, read_only: bool = False, cwd: str | Path | None = None,
             env: dict[str, str] | None = None) -> TurnResult:
    art = new_artifact_dir(artifacts_root, spec.id, role)
    argv = build_argv(spec, prompt, role=role, read_only=read_only)
    launch: list[str] | None = None
    error: str | None = None
    if spec.kind != "http":
        try:
            launch = resolve_launch(argv)
        except FileNotFoundError as exc:
            error = f"executable not found: {exc}"
        except OSError as exc:
            error = f"launch refused: {exc}"
    (art / "prompt.txt").write_text(prompt, encoding="utf-8")
    (art / "command.json").write_text(json.dumps({
        "argv": argv, "resolved_argv": launch, "cwd": str(cwd) if cwd else None, "timeout": timeout,
        "read_only": read_only, "agent": spec.id, "role": role, "requested_model": spec.model,
        "kind": spec.kind,
    }, indent=2), encoding="utf-8")
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    start = time.monotonic()
    stdout = stderr = ""
    exit_code: int | None = None
    timed_out = False
    if spec.kind == "http":
        status, stdout, stderr, error = http_turn(spec, prompt, timeout=timeout, env=full_env)
        if status is not None:
            exit_code = 0 if 200 <= status < 300 else status
    elif launch is not None:
        try:
            popen_kwargs: dict[str, Any] = {}
            if not sys.platform.startswith("win"):
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(launch, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                    cwd=str(cwd) if cwd else None, env=full_env, **popen_kwargs)
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
            except BaseException:
                _kill_tree(proc)   # Ctrl-C or any other interruption: never leave an agent writing
                raise
        except OSError as exc:
            error = f"launch failed: {exc}"
    duration = time.monotonic() - start
    (art / "stdout.txt").write_text(stdout or "", encoding="utf-8")
    (art / "stderr.txt").write_text(stderr or "", encoding="utf-8")
    # A killed turn is never classified by keyword: its partial output is whatever the
    # agent was reading or echoing when it died.
    rate_limited = False if timed_out else looks_rate_limited(exit_code, stdout, stderr)
    parsed, observed = _parse_output(spec, stdout or "")
    tokens_in, tokens_out = _usage_tokens(parsed)
    ok = error is None and exit_code == 0 and not rate_limited and not timed_out
    if not ok and error is None and isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
        error = parsed["error"][:300]  # the CLI explained the failure; keep it in the log
        if looks_rate_limited(None, "", error):
            rate_limited = True
    result = TurnResult(ok=ok, agent_id=spec.id, role=role, exit_code=exit_code, stdout=stdout or "",
                        stderr=stderr or "", artifact_dir=str(art), duration=duration,
                        rate_limited=rate_limited, timed_out=timed_out, error=error, argv=argv,
                        parsed=parsed, observed_model=observed, tokens_in=tokens_in, tokens_out=tokens_out)
    record = asdict(result)
    (art / "result.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return result