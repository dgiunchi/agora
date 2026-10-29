"""A fake agentic CLI. Consumes zero quota.

Driven by a JSON script file named in FAKE_SCRIPT:
  {"<agent id or *>": {"<role or *>": [response, response, ...]}}
Each response may have: "json" (object printed as stdout), "stdout", "stderr",
"exit", "sleep" (seconds), "write_files" ({relative path: content}, written in
the current directory, which the coordinator sets to the target repo).
Responses are consumed in order per (agent, role); the last one repeats.
"""
import argparse
import json
import os
import sys
import time

DEFAULTS = {
    "plan": {"json": {"plan": "# Plan\n\nGoal: do the task.\n\nProof: see proof command.\n"}},
    "review": {"json": {"verdict": "APPROVED", "summary": "fine", "findings": [],
                        "coverage": ["PLAN.md"], "limitations": []}},
    "inspect": {"json": {"verdict": "APPROVED", "summary": "fine", "findings": [],
                         "coverage": ["PLAN.md"], "limitations": []}},
    "judge": {"json": {"unresolved": [], "notes": "all addressed"}},
    "build": {"json": {"summary": "built", "files_changed": []}},
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--agent", default="*")
    p.add_argument("--role", default="*")
    p.add_argument("--prompt", default="")
    args, _ = p.parse_known_args()
    script_path = os.environ.get("FAKE_SCRIPT")
    data = {}
    if script_path and os.path.exists(script_path):
        with open(script_path, encoding="utf-8") as fh:
            data = json.load(fh)
    queue = None
    matched = (args.agent, args.role)
    for agent_key in (args.agent, "*"):
        for role_key in (args.role, "*"):
            q = data.get(agent_key, {}).get(role_key)
            if q:
                queue = q
                matched = (agent_key, role_key)
                break
        if queue:
            break
    if not queue:
        queue = [DEFAULTS.get(args.role, {"json": {"ok": True}})]
    n = 0
    # The counter follows the script entry that matched, so a "*" entry is a
    # single sequence shared by every agent (round 1 REVISE, round 2 APPROVED).
    safe = tuple(k.replace("*", "any") for k in matched)  # "*" is illegal in Windows filenames
    counter = f"{script_path}.{safe[0]}.{safe[1]}.n" if script_path else None
    if counter and os.path.exists(counter):
        with open(counter, encoding="utf-8") as fh:
            n = int(fh.read().strip() or 0)
    resp = queue[min(n, len(queue) - 1)]
    if counter:
        with open(counter, "w", encoding="utf-8") as fh:
            fh.write(str(n + 1))
    if resp.get("sleep"):
        time.sleep(float(resp["sleep"]))
    for rel, content in (resp.get("write_files") or {}).items():
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        with open(rel, "w", encoding="utf-8") as fh:
            fh.write(content)
    if "json" in resp:
        sys.stdout.write(json.dumps(resp["json"]))
    else:
        sys.stdout.write(resp.get("stdout", ""))
    sys.stderr.write(resp.get("stderr", ""))
    sys.stdout.flush()
    sys.stderr.flush()
    return int(resp.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())