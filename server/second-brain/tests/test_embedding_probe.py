import unittest
from unittest.mock import patch

from hermes_second_brain.embedding_probe import run_probe, unit


class EmbeddingProbeTests(unittest.TestCase):
    def spec(self):
        return {"scope": "synthetic_regression", "documents": [{"id": "a", "text": "alpha"},
                 {"id": "b", "text": "beta"}], "cases": [{"id": "q", "category": "exact",
                 "query": "alpha?", "split": "development", "answerable": True, "expected_all": ["a"]}]}

    def test_normalization_rejects_invalid_vectors(self):
        self.assertEqual(unit([3, 4], 2), [.6, .8])
        for vector in ([0, 0], [1], [float("nan"), 1]):
            with self.assertRaises(ValueError):
                unit(vector, 2)

    def test_isolated_comparison_skips_missing_models_and_compares_query_styles(self):
        calls = []
        def request(path, body=None):
            calls.append((path, body))
            if path == "/api/tags":
                return {"models": [{"name": "qwen3-embedding:8b", "digest": "fixed-version"}]}
            if path == "/api/ps":
                return {"models": [{"name": "qwen3-embedding:8b", "size_vram": 100}]}
            return {"embeddings": [[1, 0] if "alpha" in text else [0, 1] for text in body["input"]],
                    "prompt_eval_count": 2}
        with patch("hermes_second_brain.embedding_probe.request", request):
            result = run_probe(self.spec(), ["qwen3-embedding:8b", "qwen3-embedding:4b"], 1, 2, 2)
        self.assertEqual([v["avg_recall_at_5"] for v in result["variants"]], [1, 1])
        self.assertEqual([v["query_style"] for v in result["variants"]], ["plain", "instruction"])
        self.assertEqual(result["skipped_models"][0]["model"], "qwen3-embedding:4b")
        self.assertTrue(all(path in {"/api/tags", "/api/embed", "/api/ps"} for path, _ in calls))
        inputs = [body["input"] for path, body in calls if path == "/api/embed"]
        self.assertEqual(inputs[0], ["alpha", "beta"])
        self.assertTrue(inputs[-1][0].startswith("Instruct:"))

    def test_excluding_expected_documents_fails_instead_of_inflating_score(self):
        spec = self.spec()
        spec["cases"][0]["expected_all"] = ["b"]
        with self.assertRaises(ValueError):
            run_probe(spec, [], 1, 1, 2)

    def test_real_memory_corpus_is_not_silently_used_for_synthetic_probe(self):
        spec = self.spec()
        spec["scope"] = "personal_memory"
        with self.assertRaises(ValueError):
            run_probe(spec, [], 1, 2, 2)


if __name__ == "__main__":
    unittest.main()
