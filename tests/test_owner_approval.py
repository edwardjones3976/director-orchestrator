from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

# main.py constructs a TaskStore at import time from SQLITE_PATH, whose default
# is /data/director.sqlite3. Redirect it before the import so the suite never
# depends on /data existing or being writable.
_TEMP_DIR = tempfile.mkdtemp()
os.environ["SQLITE_PATH"] = str(Path(_TEMP_DIR) / "tasks.sqlite3")

from director_orchestrator.main import owner_identity_matches  # noqa: E402


def tearDownModule() -> None:
    # ignore_errors: on Windows the SQLite handle may still be open at teardown.
    shutil.rmtree(_TEMP_DIR, ignore_errors=True)


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
