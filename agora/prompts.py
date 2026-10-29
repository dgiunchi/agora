"""Prompt templates for each role. Kept in one place so they are easy to tune."""

REVIEW_SCHEMA = (
    'Respond with ONLY a JSON object: {{"verdict": "APPROVED|REVISE|BLOCKED", "summary": str, '
    '"findings": [{{"id": str, "severity": "high|medium|low", "path": str, "evidence": str, '
    '"fix": str}}], "coverage": [str], "limitations": [str]}}. Every finding needs concrete '
    'evidence. APPROVED must not carry high or medium findings.'
)

PLAN_PROMPT = (
    "You are the planner in a multi-agent loop. The task may ask for code, a document, data, a "
    "configuration or any other deliverable that ends up as files in the current directory. Task:\n\n"
    "{task}\n\nInspect the working directory (read-only) and write a plan with: goal, observable "
    "acceptance criteria, concrete approach, key decisions and trade-offs, non-goals, assumptions "
    "with sources, and exact verification commands. Respond with ONLY a JSON object "
    "{{\"plan\": \"<markdown plan>\"}}."
)
REVIEW_PROMPT = (
    "You are an independent reviewer (round {round}). Task:\n\n{task}\n\nPlan under review:\n\n{plan}\n\n"
    "Read the relevant files in the working directory (read-only). Challenge the plan with concrete "
    "evidence: wrong facts, unmet requirements, missing acceptance criteria, untestable claims. "
    + REVIEW_SCHEMA
)
REDTEAM_PROMPT = (
    "You are the RED TEAM (round {round}). Your job is to break this plan: find counter-examples, "
    "failing inputs, security holes, missed requirements, unsupported claims, contradictions. "
    "Task:\n\n{task}\n\nPlan:\n\n{plan}\n\n"
    "Read the working directory (read-only). Every finding must carry evidence a judge can check. "
    + REVIEW_SCHEMA
)
JUDGE_PROMPT = (
    "You are the JUDGE. Decide which red-team findings still stand against this plan. Proof command "
    "status: {proof}.\n\nPlan:\n\n{plan}\n\nFindings:\n{findings}\n\n"
    "Respond with ONLY a JSON object {{\"unresolved\": [finding ids that still stand], \"notes\": str}}."
)
REVISE_PROMPT = (
    "You are the planner. Revise the plan to address every finding below or justify rejecting it inside "
    "the plan. Search the whole plan for passages that still state replaced behaviour. Task:\n\n{task}\n\n"
    "Current plan:\n\n{plan}\n\nFindings:\n{findings}\n\nRespond with ONLY a JSON object "
    "{{\"plan\": \"<revised markdown plan>\", \"dispositions\": [{{\"id\": str, \"action\": str}}]}}."
)
BUILD_PROMPT = (
    "You are the builder. Produce exactly the deliverable in this approved plan, as files in the current "
    "working directory (code, document, data or whatever the task names). Task:\n\n{task}\n\n"
    "Plan:\n\n{plan}\n\nProof command: {proof}. Make the proof pass. Respond with ONLY a JSON object "
    "{{\"summary\": str, \"files_changed\": [str]}}."
)
FIX_PROMPT = (
    "You are the builder. The proof command failed. Fix the deliverable so it passes.\n\nPlan:\n\n{plan}\n\n"
    "Proof output:\n{output}\n\nRespond with ONLY a JSON object {{\"summary\": str, \"files_changed\": [str]}}."
)
FIX_FINDINGS_PROMPT = (
    "You are the builder. Address these inspection findings in the deliverable:\n{findings}\n\n"
    "Respond with ONLY a JSON object {{\"summary\": str, \"files_changed\": [str]}}."
)
INSPECT_PROMPT = (
    "You are an independent inspector in a FRESH session. Builder: {builder}. Pre-build commit: "
    "{pre_build_commit}. Task:\n\n{task}\n\nPlan:\n\n{plan}\n\nInspect every changed file (read-only; use "
    "git diff against the pre-build commit plus untracked files). Judge the deliverable against the "
    "task and the plan, whether it is code, prose, data or configuration. " + REVIEW_SCHEMA
)
INSPECT_REDTEAM_PROMPT = (
    "You are the RED TEAM inspector in a FRESH session. Builder: {builder}. Pre-build commit: "
    "{pre_build_commit}. Task:\n\n{task}\n\nPlan:\n\n{plan}\n\nTry to break the deliverable: edge cases, "
    "injection, races, false statements, unmet acceptance criteria. Read-only. " + REVIEW_SCHEMA
)