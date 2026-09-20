# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 07:21 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:26 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:10 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:00 UTC | BEGIN]]
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from director_orchestrator.openai_activity_ledgers import (
    DirectorActivityLedger,
    DirectProductionLedger,
    OpenAIActivityLedgers,
    LedgerError,
    DirectorUtilizationHold,
    OBSERVATION_WINDOW_DAYS,
)

class TestOpenAIActivityLedgers(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name) / "director.sqlite3"
        self.prod_path = Path(self.temp_dir.name) / "production.sqlite3"
        
    def tearDown(self):
        self.temp_dir.cleanup()

    def test_round_trip(self):
        ledgers = OpenAIActivityLedgers(str(self.dir_path), str(self.prod_path))
        ledgers.director.record_activity("id1", "planning", "task1", "purpose1")
        ledgers.production.record_production("id2", "review", "task1", "purpose2")
        
        # Verify events were added
        stats = ledgers.utilization()
        self.assertEqual(stats["director_events"], 1)
        self.assertEqual(stats["production_events"], 1)
        self.assertEqual(stats["duplicate_attempts"], 0)
        
        # Paths or raw text shouldn't be in utilization
        stats_str = str(stats)
        self.assertNotIn("purpose1", stats_str)
        self.assertNotIn(str(self.dir_path), stats_str)
        self.assertNotIn(str(self.prod_path), stats_str)

    def test_same_path_refused(self):
        with self.assertRaisesRegex(LedgerError, "director and production ledgers must not be the same file"):
            OpenAIActivityLedgers(str(self.dir_path), str(self.dir_path))

    def test_wrong_ledger_kind_refused(self):
        # Create director ledger
        DirectorActivityLedger(str(self.dir_path))
        # Try to open it as production ledger
        with self.assertRaisesRegex(LedgerError, "wrong_ledger_kind"):
            DirectProductionLedger(str(self.dir_path))
            
    def test_append_only(self):
        ledgers = OpenAIActivityLedgers(str(self.dir_path), str(self.prod_path))
        ledgers.director.record_activity("id1", "planning", "task1", "purpose1")
        
        conn = sqlite3.connect(self.dir_path)
        try:
            with conn:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    conn.execute("UPDATE openai_director_activity_events SET kind = 'intake'")
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    conn.execute("DELETE FROM openai_director_activity_events")
        finally:
            conn.close()

    def test_check_constraints(self):
        ledgers = OpenAIActivityLedgers(str(self.dir_path), str(self.prod_path))
        
        # Director ledger should reject production_credit != 0
        conn_dir = sqlite3.connect(self.dir_path)
        try:
            with conn_dir:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn_dir.execute(
                        "INSERT INTO openai_director_activity_events (idempotency_key, kind, started_at, task_id, purpose_digest, production_credit) VALUES (?, ?, ?, ?, ?, ?)",
                        ("id1", "planning", "2026-09-20T00:00:00+00:00", "t1", "p1", 1)
                    )
        finally:
            conn_dir.close()

        # Production ledger should reject director_activity != 0
        conn_prod = sqlite3.connect(self.prod_path)
        try:
            with conn_prod:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn_prod.execute(
                        "INSERT INTO openai_direct_production_events (idempotency_key, kind, started_at, task_id, purpose_digest, director_activity) VALUES (?, ?, ?, ?, ?, ?)",
                        ("id2", "review", "2026-09-20T00:00:00+00:00", "t1", "p1", 1)
                    )
        finally:
            conn_prod.close()

    def test_duplicates_refused_and_counted(self):
        ledgers = OpenAIActivityLedgers(str(self.dir_path), str(self.prod_path))
        ledgers.director.record_activity("id1", "planning", "task1", "purpose1")
        with self.assertRaisesRegex(LedgerError, "duplicate_idempotency_key"):
            ledgers.director.record_activity("id1", "intake", "task1", "purpose1")
            
        # Re-open with a new instance to prove durability
        ledgers_new = OpenAIActivityLedgers(str(self.dir_path), str(self.prod_path))
        stats = ledgers_new.utilization()
        self.assertEqual(stats["duplicate_attempts"], 1)
        self.assertEqual(stats["director_events"], 1)

    def test_invalid_director_kind_refused(self):
        ledgers = OpenAIActivityLedgers(str(self.dir_path), str(self.prod_path))
        with self.assertRaisesRegex(LedgerError, "invalid_director_kind"):
            ledgers.director.record_activity("id1", "review", "task1", "purpose1")
        stats = ledgers.utilization()
        self.assertEqual(stats["director_events"], 0)
        self.assertEqual(stats["production_events"], 0)

    def test_utilization_verdicts(self):
        # 19 events (insufficient history)
        ledgers1 = OpenAIActivityLedgers(str(self.dir_path), str(self.prod_path))
        for i in range(19):
            ledgers1.director.record_activity(f"id_d_{i}", "planning", "task1", "purpose")
        self.assertEqual(ledgers1.utilization()["verdict"], "insufficient_history")
        
        # 15 director, 5 production = exactly 15/20 (meets_floor)
        dir_path2 = Path(self.temp_dir.name) / "director2.sqlite3"
        prod_path2 = Path(self.temp_dir.name) / "production2.sqlite3"
        ledgers2 = OpenAIActivityLedgers(str(dir_path2), str(prod_path2))
        
        for i in range(15):
            ledgers2.director.record_activity(f"id_d_{i}", "planning", "task1", "purpose")
        for i in range(5):
            ledgers2.production.record_production(f"id_p_{i}", "review", "task1", "purpose")
            
        stats = ledgers2.utilization()
        self.assertEqual(stats["verdict"], "meets_floor")
        ledgers2.require_floor() # Should not raise
        
        # 14 director, 6 production = exactly 14/20 (below_floor)
        dir_path3 = Path(self.temp_dir.name) / "director3.sqlite3"
        prod_path3 = Path(self.temp_dir.name) / "production3.sqlite3"
        ledgers3 = OpenAIActivityLedgers(str(dir_path3), str(prod_path3))
        
        for i in range(14):
            ledgers3.director.record_activity(f"id_d_{i}", "planning", "task1", "purpose")
        for i in range(6):
            ledgers3.production.record_production(f"id_p_{i}", "review", "task1", "purpose")
            
        self.assertEqual(ledgers3.utilization()["verdict"], "below_floor")
        with self.assertRaises(DirectorUtilizationHold):
            ledgers3.require_floor()

    def test_rolling_window(self):
        ledgers = OpenAIActivityLedgers(str(self.dir_path), str(self.prod_path))
        now = datetime.now(timezone.utc)
        
        # Backdate an event by manually inserting it
        old_time = (now - timedelta(days=OBSERVATION_WINDOW_DAYS + 1)).isoformat(timespec="seconds")
        conn = sqlite3.connect(self.dir_path)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO openai_director_activity_events (idempotency_key, kind, started_at, task_id, purpose_digest) VALUES (?, ?, ?, ?, ?)",
                    ("old_id", "planning", old_time, "task", "purpose")
                )
        finally:
            conn.close()
            
        ledgers.director.record_activity("new_id", "planning", "task1", "purpose")
        
        stats = ledgers.utilization(moment=now)
        # Only the new event should be in the window
        self.assertEqual(stats["director_events"], 1)

    def test_schema_verification_foreign_file(self):
        conn = sqlite3.connect(self.dir_path)
        try:
            with conn:
                conn.execute("CREATE TABLE wrong_table (id INTEGER)")
        finally:
            conn.close()
            
        with self.assertRaisesRegex(LedgerError, "schema_mismatch"):
            DirectorActivityLedger(str(self.dir_path))

if __name__ == "__main__":
    unittest.main()
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:00 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:10 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 03:26 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-20 | 07:21 UTC | END]]
