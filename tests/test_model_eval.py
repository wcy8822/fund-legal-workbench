import json
from pathlib import Path
import tempfile
import unittest

from scripts.model_eval import blind, calculate_scores, prepare, score


class ModelEvalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.input_dir = self.root / "input"
        self.prepared = prepare(self.input_dir)

    def candidates(self, completed=False):
        paths = []
        for number, provider in ((1, "FixtureOne"), (2, "FixtureTwo")):
            path = self.input_dir / f"candidate-{number}-template.json"
            candidate = json.loads(path.read_text())
            if completed:
                candidate.update(provider=provider, model_version=f"fixture-version-{number}", run_at="2026-11-21T00:00:00Z", status="completed", output={"findings": [f"{provider} produced a synthetic test response"], "provider": provider})
                path.write_text(json.dumps(candidate))
            paths.append(path)
        return paths

    def test_prepare_contains_three_versions_and_no_gold_answers(self):
        data = json.loads((self.input_dir / "input.json").read_text())
        self.assertEqual([1, 2, 3], [doc["stage"] for doc in data["documents"]])
        self.assertEqual(0, self.prepared["model_calls"])
        self.assertNotIn("verification_note", data["legal_sources"][0])
        self.assertEqual("not_run", json.loads((self.input_dir / "candidate-1-template.json").read_text())["status"])

    def test_not_run_remains_unmeasured(self):
        output = self.root / "round"
        blind(self.input_dir / "input.json", self.candidates(), output)
        result = score(output / "review-template.json", output / "scores.json")
        for row in result["scores"]:
            for value in row["metrics"].values():
                self.assertEqual("not_run", value["status"])
                self.assertIsNone(value["value"])

    def test_anonymity_and_private_mapping(self):
        output = self.root / "round"
        blind(self.input_dir / "input.json", self.candidates(completed=True), output)
        for name in ("anonymous-outputs.json", "review-template.json"):
            value = (output / name).read_text()
            self.assertNotIn("FixtureOne", value)
            self.assertNotIn("FixtureTwo", value)
            self.assertNotIn("fixture-version", value)
        mappings = json.loads((output / "private-mapping.json").read_text())["mappings"]
        self.assertEqual({"A", "B"}, {item["anonymous_provider"] for item in mappings})
        self.assertEqual({"FixtureOne", "FixtureTwo"}, {item["provider"] for item in mappings})
        self.assertEqual(0o600, (output / "private-mapping.json").stat().st_mode & 0o777)

    def test_four_metrics_and_zero_denominator(self):
        output = self.root / "round"
        blind(self.input_dir / "input.json", self.candidates(completed=True), output)
        review = json.loads((output / "review-template.json").read_text())
        review["reviews"][0].update(status="completed", citations_verified=3, citations_total=4, critical_issues_missed=1, critical_issues_total=2, false_positives=2, risk_suggestions_total=5, human_review_minutes=7.5, reviewer_note="Synthetic arithmetic fixture, not model results.")
        review["reviews"][1].update(status="completed", citations_verified=0, citations_total=0, critical_issues_missed=0, critical_issues_total=0, false_positives=0, risk_suggestions_total=0, human_review_minutes=0, reviewer_note="Zero-denominator fixture.")
        result = calculate_scores(review)
        first = result["scores"][0]["metrics"]
        self.assertEqual(75, first["citation_accuracy"]["percent"])
        self.assertEqual(50, first["critical_omission_rate"]["percent"])
        self.assertEqual(40, first["false_positive_rate"]["percent"])
        self.assertEqual(7.5, first["human_review_minutes"]["value"])
        for name in ("citation_accuracy", "critical_omission_rate", "false_positive_rate"):
            self.assertEqual("not_applicable", result["scores"][1]["metrics"][name]["status"])

    def test_mismatched_input_and_tampering_rejected(self):
        candidates = self.candidates(completed=True)
        first = json.loads(candidates[0].read_text())
        first["input_sha256"] = "wrong"
        candidates[0].write_text(json.dumps(first))
        with self.assertRaisesRegex(ValueError, "同一份"):
            blind(self.input_dir / "input.json", candidates, self.root / "wrong")
        material = json.loads((self.input_dir / "input.json").read_text())
        material["prompt"] = "tampered"
        (self.input_dir / "input.json").write_text(json.dumps(material))
        with self.assertRaisesRegex(ValueError, "输入材料已改变"):
            blind(self.input_dir / "input.json", candidates, self.root / "tampered")

    def test_invalid_scores_and_accidental_overwrite_rejected(self):
        output = self.root / "round"
        blind(self.input_dir / "input.json", self.candidates(completed=True), output)
        with self.assertRaises(FileExistsError):
            blind(self.input_dir / "input.json", self.candidates(), output)
        review = json.loads((output / "review-template.json").read_text())
        review["reviews"][0]["citations_total"] = 3
        with self.assertRaisesRegex(ValueError, "not_run"):
            calculate_scores(review)


if __name__ == "__main__":
    unittest.main()
