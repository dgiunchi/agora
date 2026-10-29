"""Structured review schema and validation (the Claudex Loop contract).

A review is `verdict`, `summary`, `findings`, `coverage`, `limitations`.
Validation rejects what cannot count as evidence: malformed output, duplicate
finding ids, bad severities, material findings paired with APPROVED, missing
coverage on approval. It cannot prove a model is right; it only refuses the
shapes that are certainly wrong.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

VERDICTS = ("APPROVED", "REVISE", "BLOCKED")
SEVERITIES = ("high", "medium", "low")
MATERIAL = ("high", "medium")


class ReviewError(ValueError):
    """The output cannot be accepted as a review."""


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str
    path: str
    evidence: str
    fix: str

    @property
    def material(self) -> bool:
        return self.severity in MATERIAL


@dataclass
class Review:
    verdict: str
    summary: str
    findings: list[Finding] = field(default_factory=list)
    coverage: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    @property
    def material_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.material]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict, "summary": self.summary,
            "findings": [f.__dict__ for f in self.findings],
            "coverage": list(self.coverage), "limitations": list(self.limitations),
        }


def extract_json(text: str) -> Any:
    """Find the outermost JSON object in free text (CLIs wrap JSON in prose)."""
    text = text.strip()
    if not text:
        raise ReviewError("empty output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ReviewError("no JSON object in output")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ReviewError(f"malformed JSON: {exc}") from exc


def validate_review(data: Any) -> Review:
    if not isinstance(data, dict):
        raise ReviewError("review must be a JSON object")
    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        raise ReviewError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ReviewError("summary must be a non-empty string")
    raw_findings = data.get("findings", [])
    if not isinstance(raw_findings, list):
        raise ReviewError("findings must be a list")
    findings: list[Finding] = []
    seen: set[str] = set()
    for i, f in enumerate(raw_findings):
        if not isinstance(f, dict):
            raise ReviewError(f"finding {i} must be an object")
        fid = f.get("id")
        if not isinstance(fid, str) or not fid:
            raise ReviewError(f"finding {i} missing id")
        if fid in seen:
            raise ReviewError(f"duplicate finding id {fid!r}")
        seen.add(fid)
        sev = f.get("severity")
        if sev not in SEVERITIES:
            raise ReviewError(f"finding {fid}: severity must be one of {SEVERITIES}")
        for key in ("path", "evidence", "fix"):
            if not isinstance(f.get(key, ""), str):
                raise ReviewError(f"finding {fid}: {key} must be a string")
        if not f.get("evidence"):
            raise ReviewError(f"finding {fid}: evidence is required")
        findings.append(Finding(id=fid, severity=sev, path=f.get("path", ""),
                                evidence=f["evidence"], fix=f.get("fix", "")))
    coverage = data.get("coverage", [])
    limitations = data.get("limitations", [])
    if not isinstance(coverage, list) or not all(isinstance(x, str) for x in coverage):
        raise ReviewError("coverage must be a list of strings")
    if not isinstance(limitations, list) or not all(isinstance(x, str) for x in limitations):
        raise ReviewError("limitations must be a list of strings")
    review = Review(verdict=verdict, summary=summary, findings=findings,
                    coverage=coverage, limitations=limitations)
    if verdict == "APPROVED":
        if review.material_findings:
            ids = ", ".join(f.id for f in review.material_findings)
            raise ReviewError(f"APPROVED cannot carry material findings ({ids})")
        if not coverage:
            raise ReviewError("APPROVED requires non-empty coverage")
    return review


def parse_review(text: str) -> Review:
    data = extract_json(text)
    if isinstance(data, dict) and "verdict" not in data and isinstance(data.get("result"), str):
        data = extract_json(data["result"])  # claude -p --output-format json wraps the answer
    return validate_review(data)