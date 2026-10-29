import json
import unittest

from agent_commons.review import ReviewError, extract_json, parse_review, validate_review


def base(**kw):
    d = {"verdict": "APPROVED", "summary": "ok", "findings": [], "coverage": ["PLAN.md"], "limitations": []}
    d.update(kw)
    return d


def finding(fid, sev="high"):
    return {"id": fid, "severity": sev, "path": "x.py", "evidence": "line 3", "fix": "do y"}


class ReviewTests(unittest.TestCase):
    def test_valid_approved(self):
        r = validate_review(base())
        self.assertEqual(r.verdict, "APPROVED")

    def test_revise_with_findings(self):
        r = validate_review(base(verdict="REVISE", findings=[finding("F1"), finding("F2", "low")]))
        self.assertEqual([f.id for f in r.material_findings], ["F1"])

    def test_rejects_empty_and_malformed(self):
        with self.assertRaises(ReviewError):
            parse_review("")
        with self.assertRaises(ReviewError):
            parse_review("{not json")
        with self.assertRaises(ReviewError):
            parse_review("no braces here")

    def test_rejects_bad_verdict(self):
        with self.assertRaises(ReviewError):
            validate_review(base(verdict="LGTM"))

    def test_rejects_duplicate_ids(self):
        with self.assertRaisesRegex(ReviewError, "duplicate"):
            validate_review(base(verdict="REVISE", findings=[finding("F1"), finding("F1")]))

    def test_rejects_bad_severity(self):
        with self.assertRaisesRegex(ReviewError, "severity"):
            validate_review(base(verdict="REVISE", findings=[finding("F1", "critical")]))

    def test_rejects_approved_with_material_findings(self):
        with self.assertRaisesRegex(ReviewError, "APPROVED cannot carry"):
            validate_review(base(findings=[finding("F1", "medium")]))

    def test_approved_may_carry_low_findings(self):
        r = validate_review(base(findings=[finding("F1", "low")]))
        self.assertEqual(r.verdict, "APPROVED")

    def test_rejects_approved_without_coverage(self):
        with self.assertRaisesRegex(ReviewError, "coverage"):
            validate_review(base(coverage=[]))

    def test_rejects_missing_evidence(self):
        f = finding("F1")
        f["evidence"] = ""
        with self.assertRaisesRegex(ReviewError, "evidence"):
            validate_review(base(verdict="REVISE", findings=[f]))

    def test_extracts_json_from_prose(self):
        text = "Here is my review:\n" + json.dumps(base()) + "\nThanks."
        self.assertEqual(parse_review(text).verdict, "APPROVED")

    def test_unwraps_claude_json_output(self):
        wrapped = json.dumps({"type": "result", "result": "```json\n" + json.dumps(base(verdict="REVISE")) + "\n```"})
        self.assertEqual(parse_review(wrapped).verdict, "REVISE")

    def test_extract_json_non_object(self):
        self.assertEqual(extract_json("[1, 2]"), [1, 2])
        with self.assertRaises(ReviewError):
            validate_review([1, 2])


if __name__ == "__main__":
    unittest.main()