from __future__ import annotations

import unittest

from director_orchestrator.approvals import owner_identity_matches


class OwnerIdentityTests(unittest.TestCase):
    def test_unconfigured_owner_rejects_every_approval(self):
        # hmac.compare_digest("", "") is True, so an unset SLACK_OWNER_USER_ID
        # would otherwise accept an empty owner_user_id and let anyone holding
        # the shared secret approve gated assignments.
        self.assertFalse(owner_identity_matches("", ""))
        self.assertFalse(owner_identity_matches("U0B9UJ86D27", ""))

    def test_empty_claim_is_rejected_when_owner_is_configured(self):
        self.assertFalse(owner_identity_matches("", "U0B9UJ86D27"))

    def test_mismatched_owner_is_rejected(self):
        self.assertFalse(owner_identity_matches("U000IMPOSTOR", "U0B9UJ86D27"))

    def test_matching_owner_is_accepted(self):
        self.assertTrue(owner_identity_matches("U0B9UJ86D27", "U0B9UJ86D27"))


if __name__ == "__main__":
    unittest.main()
