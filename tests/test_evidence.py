import unittest

from usiphus_evidence import assess_card_evidence, build_evidence_bundle


class EvidenceBundleTests(unittest.TestCase):
    def test_url_only_reference_is_not_treated_as_evidence(self):
        bundle = build_evidence_bundle([{
            "id": "guideline-1",
            "title": "Clinical guideline",
            "url": "https://example.org/guideline",
        }])
        self.assertFalse(bundle["evidence_available"])
        self.assertEqual(bundle["usable_sources"], [])

    def test_required_evidence_rejects_empty_or_url_only_sources(self):
        with self.assertRaisesRegex(ValueError, "URLs alone are not evidence"):
            build_evidence_bundle([{
                "id": "guideline-1",
                "title": "Clinical guideline",
                "url": "https://example.org/guideline",
            }], require_evidence=True)

    def test_excerpt_source_supports_traceable_claim_but_not_auto_approval(self):
        bundle = build_evidence_bundle([{
            "id": "source-a",
            "title": "Faculty-approved excerpt",
            "url": "https://example.org/source-a",
            "excerpt": "A curated excerpt about the clinical finding.",
        }])
        result = assess_card_evidence({
            "claims": [{
                "claim": "The finding is associated with condition X.",
                "source_ids": ["source-a"],
            }],
        }, bundle)
        self.assertEqual(result["evidence_review_state"], "source_linked_pending_human_verification")
        self.assertEqual(result["claim_provenance"][0]["source_ids"], ["source-a"])
        self.assertFalse(result["evidence_flags"])

    def test_unknown_source_and_unlinked_claim_are_flagged(self):
        bundle = build_evidence_bundle([{
            "id": "source-a", "title": "Source A", "excerpt": "Relevant excerpt."
        }])
        result = assess_card_evidence({
            "claims": [
                {"claim": "Claim one.", "source_ids": ["made-up-source"]},
                {"claim": "Claim two.", "source_ids": []},
            ],
        }, bundle)
        self.assertEqual(result["evidence_review_state"], "needs_evidence_review")
        self.assertTrue(any("unknown" in flag for flag in result["evidence_flags"]))
        self.assertTrue(any("no usable source" in flag for flag in result["evidence_flags"]))

    def test_model_reported_conflicts_are_preserved_for_faculty(self):
        bundle = build_evidence_bundle([{
            "id": "source-a", "title": "Source A", "excerpt": "Excerpt A."
        }])
        result = assess_card_evidence({
            "claims": [{"claim": "A claim.", "source_ids": ["source-a"]}],
            "potential_conflicts": [{
                "description": "The supplied sources appear to differ.",
                "source_ids": ["source-a"],
            }],
        }, bundle)
        self.assertEqual(len(result["potential_conflicts"]), 1)
        self.assertEqual(result["evidence_review_state"], "needs_evidence_review")


if __name__ == "__main__":
    unittest.main()
