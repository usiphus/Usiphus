import json
import tempfile
import unittest
from pathlib import Path

import usiphus_review


class CardReviewTests(unittest.TestCase):
    def setUp(self):
        self.cards = [
            {"card_id": "OPH-001", "prompt": "Question one?", "answer": "Answer one.", "evidence_status": "unreviewed"},
            {"card_id": "OPH-002", "prompt": "Question two?", "answer": "Answer two.", "evidence_status": "unreviewed"},
        ]
        self.store = {"schema_version": 1, "reviewer_profiles": {}}

    def test_reviews_are_separate_per_local_profile(self):
        usiphus_review.record_decision(self.store, "reviewer-a", "OPH-001", "keep")
        usiphus_review.record_decision(self.store, "reviewer-b", "OPH-001", "reject")
        first = self.store["reviewer_profiles"]["reviewer-a"]["decisions"]["OPH-001"]["decision"]
        second = self.store["reviewer_profiles"]["reviewer-b"]["decisions"]["OPH-001"]["decision"]
        self.assertEqual(first, "keep")
        self.assertEqual(second, "reject")

    def test_edit_export_is_personal_and_does_not_mark_evidence_reviewed(self):
        usiphus_review.record_decision(
            self.store, "reviewer-a", "OPH-001", "edit", {"answer": "My edited answer."}
        )
        export = usiphus_review.build_export(
            self.cards, self.store["reviewer_profiles"]["reviewer-a"], "reviewer-a"
        )
        self.assertEqual(len(export["cards"]), 1)
        self.assertEqual(export["cards"][0]["answer"], "My edited answer.")
        self.assertEqual(export["cards"][0]["evidence_status"], "unreviewed")
        self.assertEqual(self.cards[0]["answer"], "Answer one.")

    def test_reject_is_not_in_personal_export(self):
        usiphus_review.record_decision(self.store, "reviewer-a", "OPH-001", "reject")
        export = usiphus_review.build_export(
            self.cards, self.store["reviewer_profiles"]["reviewer-a"], "reviewer-a"
        )
        self.assertEqual(export["cards"], [])

    def test_interactive_review_can_keep_edit_reject_and_skip(self):
        inputs = iter(["keep", "edit", "Edited question?", "", ""])
        usiphus_review.review_cards(
            self.cards,
            self.store,
            "reviewer-a",
            input_fn=lambda _prompt: next(inputs),
            output_fn=lambda _message: None,
        )
        decisions = self.store["reviewer_profiles"]["reviewer-a"]["decisions"]
        self.assertEqual(decisions["OPH-001"]["decision"], "keep")
        self.assertEqual(decisions["OPH-002"]["decision"], "edit")
        self.assertEqual(decisions["OPH-002"]["edited_fields"], {"prompt": "Edited question?"})

    def test_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reviews.json"
            usiphus_review.record_decision(self.store, "reviewer-a", "OPH-001", "keep")
            usiphus_review.atomic_write(path, json.dumps(self.store))
            loaded = usiphus_review.load_store(path)
            self.assertEqual(
                loaded["reviewer_profiles"]["reviewer-a"]["decisions"]["OPH-001"]["decision"],
                "keep",
            )

    def test_invalid_edits_are_rejected(self):
        with self.assertRaises(usiphus_review.ReviewError):
            usiphus_review.record_decision(
                self.store, "reviewer-a", "OPH-001", "edit", {"references": "fake"}
            )


if __name__ == "__main__":
    unittest.main()
