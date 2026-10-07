import copy
import json
from pathlib import Path
import unittest

from hermes_second_brain.quality_eval import evaluate, validate_spec


class QualityEvalTests(unittest.TestCase):
    def setUp(self):
        self.spec = {"scope": "personal_memory", "cases": [{
            "id": "current", "category": "correction", "split": "held_out", "query": "Current owner?",
            "answerable": True, "expected_all": ["viking://resources/brain/current"],
            "accepted_answers": ["Robin"], "label_status": "reviewed", "label_reviewer": "fixture-reviewer"}]}
        self.prediction = {"cases": [{"id": "current", "status": "ok", "seconds": 1.2,
            "retrieved": [{"ref": "viking://resources/brain/current", "snippet": "Owner: Robin"}],
            "answer": {"text": "Robin", "abstained": False,
                       "citations": ["viking://resources/brain/current"]}}]}

    def test_retrieval_success_does_not_count_as_answer_success(self):
        self.prediction["cases"][0]["answer"] = None
        result = evaluate(self.spec, self.prediction)
        self.assertEqual(result["avg_recall_at_k"], 1)
        self.assertIsNone(result["answer_accuracy_on_scored"])
        self.assertFalse(result["quality_gate_passed"])

    def test_exact_answer_still_needs_grounding_review_on_real_data(self):
        result = evaluate(self.spec, self.prediction)
        self.assertEqual(result["answer_accuracy_on_scored"], 1)
        self.assertIsNone(result["grounding_on_scored"])
        self.assertFalse(result["quality_gate_passed"])
        self.prediction["cases"][0]["answer"]["review"] = {
            "reviewer": "fixture-reviewer", "correct": True, "grounded": True}
        self.assertTrue(evaluate(self.spec, self.prediction)["quality_gate_passed"])

    def test_unknown_citation_cannot_pass_even_with_positive_review(self):
        answer = self.prediction["cases"][0]["answer"]
        answer["citations"] = ["invented"]
        answer["review"] = {"reviewer": "fixture-reviewer", "correct": True, "grounded": True}
        result = evaluate(self.spec, self.prediction)
        self.assertFalse(result["quality_gate_passed"])
        self.assertEqual(result["grounding_on_scored"], 0)

    def test_negated_answer_does_not_pass_exact_matching(self):
        self.prediction["cases"][0]["answer"]["text"] = "Not Robin"
        self.assertEqual(evaluate(self.spec, self.prediction)["answer_accuracy_on_scored"], 0)

    def test_missing_cases_remain_in_denominator(self):
        second = {**self.spec["cases"][0], "id": "missing"}
        self.spec["cases"].append(second)
        result = evaluate(self.spec, self.prediction)
        self.assertEqual(result["avg_recall_at_k"], .5)
        self.assertEqual(result["status_counts"], {"ok": 1, "missing": 1})
        self.assertFalse(result["quality_gate_passed"])

    def test_synthetic_scores_cannot_be_presented_as_reviewed_personal_quality(self):
        self.spec["scope"] = "synthetic_regression"
        self.spec["cases"][0]["label_status"] = "generated_fixture"
        result = evaluate(self.spec, self.prediction)
        self.assertEqual(result["grounding_on_scored"], 1)
        self.assertFalse(result["labels_reviewed"])
        self.assertFalse(result["quality_gate_passed"])

    def test_unanswerable_requires_abstention_without_citations(self):
        case = self.spec["cases"][0]
        case.update(answerable=False, expected_all=[], accepted_answers=[])
        result = evaluate(self.spec, self.prediction)
        self.assertEqual(result["correct_abstention_on_scored"], 0)
        self.prediction["cases"][0]["answer"] = {"text": "", "abstained": True, "citations": []}
        self.assertEqual(evaluate(self.spec, self.prediction)["correct_abstention_on_scored"], 1)

    def test_empty_source_text_does_not_count_as_usable_evidence(self):
        self.prediction["cases"][0]["retrieved"][0]["snippet"] = " "
        result = evaluate(self.spec, self.prediction)
        self.assertEqual(result["avg_recall_at_k"], 0)
        self.assertFalse(result["cases"][0]["citations_valid"])

    def test_abstention_flag_cannot_hide_a_factual_answer(self):
        self.spec["cases"][0].update(answerable=False, expected_all=[])
        self.prediction["cases"][0]["answer"] = {"text": "The phone is 12345", "abstained": True, "citations": []}
        self.assertEqual(evaluate(self.spec, self.prediction)["correct_abstention_on_scored"], 0)

    def test_all_required_sources_needed_for_multisession(self):
        self.spec["cases"][0]["expected_all"].append("other-session")
        self.assertEqual(evaluate(self.spec, self.prediction)["avg_recall_at_k"], .5)

    def test_duplicate_and_unknown_predictions_are_rejected(self):
        self.prediction["cases"].append(copy.deepcopy(self.prediction["cases"][0]))
        with self.assertRaises(ValueError):
            evaluate(self.spec, self.prediction)
        self.prediction["cases"][1]["id"] = "unknown"
        with self.assertRaises(ValueError):
            evaluate(self.spec, self.prediction)

    def test_case_without_ground_truth_is_rejected(self):
        self.spec["cases"][0]["expected_all"] = []
        with self.assertRaises(ValueError):
            validate_spec(self.spec)

    def test_sixty_controlled_cases_detect_correct_and_wrong_answers(self):
        spec = json.loads((Path(__file__).parents[1] / "config/quality-eval.synthetic.json").read_text())
        self.assertEqual(len(spec["cases"]), 60)
        docs = {doc["id"]: doc for doc in spec["documents"]}
        predictions = {"cases": []}
        for case in spec["cases"]:
            predictions["cases"].append({"id": case["id"], "status": "ok", "seconds": .1,
                "retrieved": [{"ref": key, "snippet": docs[key]["text"]} for key in case["expected_all"]],
                "answer": {"text": case["accepted_answers"][0] if case["answerable"] else "",
                           "abstained": not case["answerable"], "citations": case["expected_all"]}})
        correct = evaluate(spec, predictions)
        self.assertEqual(correct["answer_scored_count"], 60)
        self.assertEqual(correct["answer_accuracy_on_scored"], 1)
        self.assertEqual(correct["grounding_on_scored"], 1)
        self.assertFalse(correct["quality_gate_passed"])
        self.assertEqual(correct["by_split"]["held_out"]["cases"], 12)
        for row in predictions["cases"]:
            row["answer"]["text"] = "A deliberately wrong answer"
            row["answer"]["abstained"] = False
        self.assertEqual(evaluate(spec, predictions)["answer_accuracy_on_scored"], 0)

    def test_timeouts_never_receive_answer_credit(self):
        self.prediction["cases"][0]["status"] = "failed"
        result = evaluate(self.spec, self.prediction)
        self.assertEqual(result["avg_recall_at_k"], 0)
        self.assertIsNone(result["answer_accuracy_on_scored"])
        self.assertFalse(result["quality_gate_passed"])


if __name__ == "__main__":
    unittest.main()
