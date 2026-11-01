import unittest

from usiphus_ledger import conflicts


class ReleaseLedgerConflictTests(unittest.TestCase):
    def make_ledger(self, prior, prior_cid="COMP-OLD"):
        return {
            "schema_version": 1,
            "salt": "0" * 64,
            "releases": [{
                "release_id": "release-1",
                "generated_at": "2026-01-01T00:00:00Z",
                "cohorts": {prior_cid: sorted(prior)},
            }],
        }

    def test_overlapping_cohorts_with_small_prior_only_difference_are_suppressed(self):
        prior = {"a", "b", "c", "d", "e"}
        current = {"a", "b", "c", "d", "e", "f", "g"}
        result = conflicts({"COMP-NEW": frozenset(current)}, self.make_ledger(prior, "COMP-NEW"), 3)
        self.assertIn("COMP-NEW", result)

    def test_overlapping_cohorts_with_small_current_only_difference_are_suppressed(self):
        prior = {"a", "b", "c", "d", "e", "f", "g"}
        current = {"a", "b", "c", "h", "i"}
        result = conflicts(
            {"COMP-NEW": frozenset(current)},
            self.make_ledger(prior, "COMP-NEW"),
            3,
            mode="strict",
        )
        self.assertIn("COMP-NEW", result)

    def test_nested_cohort_with_difference_at_least_k_is_allowed(self):
        prior = {"a", "b", "c"}
        current = {"a", "b", "c", "d", "e", "f"}
        result = conflicts({"COMP-NEW": frozenset(current)}, self.make_ledger(prior), 3)
        self.assertNotIn("COMP-NEW", result)

    def test_nested_cohort_with_difference_below_k_is_suppressed(self):
        prior = {"a", "b", "c", "d", "e"}
        current = {"a", "b", "c", "d", "e", "f"}
        result = conflicts({"COMP-NEW": frozenset(current)}, self.make_ledger(prior), 3)
        self.assertIn("COMP-NEW", result)

    def test_disjoint_cohorts_are_not_suppressed(self):
        prior = {"a", "b", "c"}
        current = {"x", "y", "z"}
        result = conflicts({"COMP-NEW": frozenset(current)}, self.make_ledger(prior), 3)
        self.assertNotIn("COMP-NEW", result)

    def test_both_differences_equal_to_k_are_allowed(self):
        prior = {"a", "b", "c", "d", "e"}
        current = {"a", "b", "x", "y", "z"}
        result = conflicts({"COMP-NEW": frozenset(current)}, self.make_ledger(prior), 3)
        self.assertNotIn("COMP-NEW", result)

    def test_strict_same_competency_churn_reason_is_retained(self):
        prior = {"a", "b", "c", "d", "e", "f"}
        current = {"a", "b", "c", "g", "h"}
        result = conflicts(
            {"COMP-1": frozenset(current)},
            self.make_ledger(prior, "COMP-1"),
            3,
            mode="strict",
        )
        self.assertIn("COMP-1", result)
        self.assertIn("strict", result["COMP-1"].lower())


if __name__ == "__main__":
    unittest.main()
