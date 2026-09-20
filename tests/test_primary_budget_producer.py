# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 18:50 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 08:50 UTC | BEGIN]]
"""Tests for unsigned primary-provider-budget/v1 preparation and import."""
import hashlib
import hmac
import importlib.util
import io
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError

from director_orchestrator.antigravity_team import (
    PrimaryProviderBudgetAuthority,
    ProviderBudget,
    TeamAuthorizationError,
    team_ddl,
)
from director_orchestrator.entitlement_contracts import (
    canonical_json_bytes,
    document_digest,
)
from director_orchestrator.primary_budget_producer import (
    BudgetProducerStatus,
    ProviderBudgetImportReceipt,
    ProviderBudgetVerification,
    UnsignedProviderBudgetIntent,
    import_signed_budget,
    prepare_unsigned_budget_intent,
    verify_signed_budget,
)
from director_orchestrator.primary_paid_preflight import check_budget_setup
from tests import test_entitlement_control_plane as fixtures

ROOT = Path(__file__).resolve().parents[1]
CLI_SPEC = importlib.util.spec_from_file_location(
    "prepare_primary_provider_budget",
    ROOT / "scripts/prepare_primary_provider_budget.py",
)
cli = importlib.util.module_from_spec(CLI_SPEC)
CLI_SPEC.loader.exec_module(cli)


def _valid_fields(**overrides):
    values = dict(
        grant_digest="a" * 64,
        provider_id="openai",
        project_id="uppercut",
        task_id="task-one",
        day="2026-01-01",
        task_limit_microusd=500_000,
        daily_limit_microusd=1_000_000,
        attempt_reservation_microusd=100_000,
        token_limit=1000,
    )
    values.update(overrides)
    return values


def _signed_provider_budget(**changes):
    values = dict(
        schema_version="primary-provider-budget/v1",
        grant_digest="a" * 64,
        provider_id="openai",
        project_id="uppercut",
        task_id="task-one",
        day=datetime.now(timezone.utc).date().isoformat(),
        task_limit_microusd=500_000,
        daily_limit_microusd=1_000_000,
        attempt_reservation_microusd=100_000,
        token_limit=1000,
        owner_signature="0" * 64,
    )
    values.update(changes)
    budget = ProviderBudget(**values)
    signature = hmac.new(
        fixtures.OWNER_SECRET,
        canonical_json_bytes(budget.signing_document()),
        hashlib.sha256,
    ).hexdigest()
    return budget.model_copy(update={"owner_signature": signature})


def _fake_authority(secret=fixtures.OWNER_SECRET, schema="director_budget_producer_v1"):
    entitlements = SimpleNamespace(_owner_authority_secret=secret)
    return PrimaryProviderBudgetAuthority(entitlements, primary_schema=schema)


class UnsignedProviderBudgetIntentTests(unittest.TestCase):
    def test_prepare_is_deterministic_and_unsigned(self):
        intent = prepare_unsigned_budget_intent(**_valid_fields())
        document = intent.canonical_document()
        self.assertNotIn("owner_signature", document)
        self.assertEqual(document["schema_version"], "primary-provider-budget/v1")
        again = prepare_unsigned_budget_intent(**_valid_fields())
        self.assertEqual(intent.digest, again.digest)
        changed = prepare_unsigned_budget_intent(**_valid_fields(task_id="task-two"))
        self.assertNotEqual(intent.digest, changed.digest)

    def test_digest_matches_document_digest_helper(self):
        intent = prepare_unsigned_budget_intent(**_valid_fields())
        self.assertEqual(intent.digest, document_digest(intent.canonical_document()))

    def test_rejects_calendar_impossible_day_despite_matching_regex(self):
        with self.assertRaises(ValidationError):
            prepare_unsigned_budget_intent(**_valid_fields(day="2026-02-30"))

    def test_rejects_non_positive_amounts(self):
        for field in (
            "task_limit_microusd",
            "daily_limit_microusd",
            "attempt_reservation_microusd",
            "token_limit",
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                prepare_unsigned_budget_intent(**_valid_fields(**{field: 0}))

    def test_rejects_unknown_field(self):
        with self.assertRaises(ValidationError):
            UnsignedProviderBudgetIntent(**_valid_fields(), schema_version="primary-provider-budget/v1", extra_field="nope")

    def test_unsigned_document_cannot_validate_as_provider_budget(self):
        intent = prepare_unsigned_budget_intent(**_valid_fields())
        with self.assertRaises(ValidationError):
            ProviderBudget.model_validate(intent.canonical_document())

    def test_zero_filled_signature_still_fails_verification(self):
        intent = prepare_unsigned_budget_intent(**_valid_fields())
        document = dict(intent.canonical_document(), owner_signature="0" * 64)
        budget = ProviderBudget.model_validate(document)
        with self.assertRaises(TeamAuthorizationError):
            budget.verify(fixtures.OWNER_SECRET)


class VerifySignedBudgetTests(unittest.TestCase):
    def test_verifies_correctly_signed_document(self):
        budget = _signed_provider_budget()
        result = verify_signed_budget(budget, authority=_fake_authority())
        self.assertIsInstance(result, ProviderBudgetVerification)
        self.assertEqual(result.status, BudgetProducerStatus.VERIFIED)
        self.assertEqual(result.authority_digest, budget.grant_digest)
        self.assertEqual(result.provider_id, budget.provider_id)

    def test_rejects_wrong_signing_key(self):
        budget = _signed_provider_budget()
        wrong_authority = _fake_authority(secret=b"z" * 32)
        with self.assertRaises(TeamAuthorizationError):
            verify_signed_budget(budget, authority=wrong_authority)

    def test_rejects_changed_field_after_signing(self):
        budget = _signed_provider_budget()
        tampered = budget.model_copy(
            update={"task_limit_microusd": budget.task_limit_microusd + 1}
        )
        with self.assertRaises(TeamAuthorizationError):
            verify_signed_budget(tampered, authority=_fake_authority())

    def test_requires_typed_authority(self):
        budget = _signed_provider_budget()
        with self.assertRaises(TeamAuthorizationError):
            verify_signed_budget(budget, authority=SimpleNamespace())

    def test_expired_but_structurally_valid_document_still_verifies(self):
        budget = _signed_provider_budget(day="2000-01-01")
        result = verify_signed_budget(budget, authority=_fake_authority())
        self.assertEqual(result.status, BudgetProducerStatus.VERIFIED)

    def test_impossible_calendar_day_fails_verification(self):
        budget = _signed_provider_budget(day="2026-02-30")
        with self.assertRaises(TeamAuthorizationError):
            verify_signed_budget(budget, authority=_fake_authority())


class StrictSignedInputValidationTests(unittest.TestCase):
    """Reproduces and closes the independently reported strict-object gap:
    a ``ProviderBudget.model_construct`` instance bypasses field validation
    entirely, and ``model_validate`` on an already-model instance normally
    just hands it back unrevalidated."""

    def _malformed_cases(self):
        base = dict(
            schema_version="primary-provider-budget/v1",
            grant_digest="a" * 64,
            provider_id="openai",
            project_id="uppercut",
            task_id="task-one",
            day=datetime.now(timezone.utc).date().isoformat(),
            task_limit_microusd=500_000,
            daily_limit_microusd=1_000_000,
            attempt_reservation_microusd=100_000,
            token_limit=1000,
            owner_signature="0" * 64,
        )
        cases = {
            "zero_token_limit": dict(base, token_limit=0),
            "boolean_token_limit": dict(base, token_limit=True),
            "fractional_token_limit": dict(base, token_limit=1.5),
            "negative_task_limit": dict(base, task_limit_microusd=-1),
            "invalid_provider_identifier": dict(base, provider_id="invalid provider"),
            "invalid_schema_literal": dict(base, schema_version="invalid/v9"),
        }
        signed = {}
        for name, fields in cases.items():
            constructed = ProviderBudget.model_construct(**fields)
            signature = hmac.new(
                fixtures.OWNER_SECRET,
                canonical_json_bytes(constructed.signing_document()),
                hashlib.sha256,
            ).hexdigest()
            signed[name] = constructed.model_copy(update={"owner_signature": signature})
        return signed

    def test_malformed_model_construct_objects_fail_verification(self):
        for name, budget in self._malformed_cases().items():
            with self.subTest(case=name), self.assertRaises(TeamAuthorizationError):
                verify_signed_budget(budget, authority=_fake_authority())

    def test_malformed_model_construct_objects_never_reach_import_sql(self):
        for name, budget in self._malformed_cases().items():
            connection = Mock()
            with self.subTest(case=name), self.assertRaises(TeamAuthorizationError):
                import_signed_budget(
                    authority=_fake_authority(), connection=connection, budget=budget
                )
            connection.execute.assert_not_called()


class PrepareCliTests(unittest.TestCase):
    def _run(self, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = cli.main(argv)
        return exit_code, buffer.getvalue()

    def _run_capturing_both(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            exit_code = cli.main(argv)
        return exit_code, out.getvalue(), err.getvalue()

    def _prepare_argv(self, **overrides):
        fields = _valid_fields()
        fields.update(overrides)
        flag_by_field = {attribute: flag for flag, attribute in cli.PREPARE_FIELDS}
        argv = []
        for field, value in fields.items():
            argv += [flag_by_field[field], str(value)]
        return argv

    def test_default_action_prepares_unsigned_document(self):
        exit_code, output = self._run(self._prepare_argv())
        self.assertEqual(exit_code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["status"], "PREPARED")
        self.assertNotIn("owner_signature", payload["document"])
        self.assertTrue(payload["intent_digest"])

    def test_prepare_reports_missing_required_fields(self):
        exit_code, _ = self._run(["--grant-digest", "a" * 64])
        self.assertEqual(exit_code, 1)

    def test_prepare_reports_invalid_calendar_day(self):
        exit_code, _ = self._run(self._prepare_argv(day="2026-02-30"))
        self.assertEqual(exit_code, 1)

    def test_inspect_reports_structural_validity_without_verifying_or_registering(self):
        document = dict(_valid_fields(), schema_version="primary-provider-budget/v1", owner_signature="0" * 64)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            exit_code, output = self._run(["--action", "inspect", "--input", str(path)])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["status"], "STRUCTURALLY_VALID_UNVERIFIED")
        self.assertNotIn("owner_signature", payload)

    def test_inspect_rejects_document_missing_required_fields(self):
        document = _valid_fields()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsigned.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            exit_code, _ = self._run(["--action", "inspect", "--input", str(path)])
        self.assertEqual(exit_code, 1)

    def test_inspect_validation_error_never_echoes_synthetic_input(self):
        sentinel = "SYNTHETIC_PRIVATE_INPUT_SHOULD_NOT_ECHO"
        document = dict(
            _valid_fields(), schema_version="primary-provider-budget/v1",
            owner_signature=sentinel,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            exit_code, out, err = self._run_capturing_both(
                ["--action", "inspect", "--input", str(path)]
            )
        self.assertEqual(exit_code, 1)
        self.assertNotIn(sentinel, out)
        self.assertNotIn(sentinel, err)

    def test_prepare_error_names_field_not_value(self):
        exit_code, out, err = self._run_capturing_both(
            self._prepare_argv(day="2026-02-30")
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(out, "")
        self.assertIn("day", err)
        self.assertNotIn("2026-02-30", err)

    def test_inspect_extra_field_error_never_echoes_key_or_value(self):
        key_sentinel = "SYNTHETIC_PRIVATE_UNKNOWN_FIELD_NAME"
        value_sentinel = "SYNTHETIC_PRIVATE_UNKNOWN_FIELD_VALUE"
        document = dict(
            _valid_fields(), schema_version="primary-provider-budget/v1",
            owner_signature="0" * 64,
        )
        document[key_sentinel] = value_sentinel
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "extra.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            exit_code, out, err = self._run_capturing_both(
                ["--action", "inspect", "--input", str(path)]
            )
        self.assertEqual(exit_code, 1)
        for sentinel in (key_sentinel, value_sentinel):
            self.assertNotIn(sentinel, out)
            self.assertNotIn(sentinel, err)


class ProviderBudgetImportPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.EntitlementControlPlanePostgresTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.EntitlementControlPlanePostgresTests.tearDownClass()

    def setUp(self):
        fixtures.EntitlementControlPlanePostgresTests.setUp(self)
        self.authority = PrimaryProviderBudgetAuthority(self.store, primary_schema=self.schema)
        with self.store._pool.connection() as connection, connection.transaction():
            for statement in team_ddl(self.schema):
                connection.execute(statement)

    def tearDown(self):
        fixtures.EntitlementControlPlanePostgresTests.tearDown(self)

    def _row_count(self):
        with self.store._pool.connection() as connection:
            return connection.execute(
                f"SELECT count(*) AS n FROM {self.schema}.director_primary_provider_budget"
            ).fetchone()["n"]

    def test_import_registers_a_new_signed_budget(self):
        budget = _signed_provider_budget()
        with self.store._pool.connection() as connection, connection.transaction():
            receipt = import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        self.assertIsInstance(receipt, ProviderBudgetImportReceipt)
        self.assertEqual(receipt.status, BudgetProducerStatus.REGISTERED)
        self.assertEqual(receipt.authority_digest, budget.grant_digest)
        self.assertEqual(self._row_count(), 1)

    def test_exact_repeat_import_is_idempotent(self):
        budget = _signed_provider_budget()
        with self.store._pool.connection() as connection, connection.transaction():
            import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        with self.store._pool.connection() as connection, connection.transaction():
            receipt = import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        self.assertEqual(receipt.status, BudgetProducerStatus.ALREADY_REGISTERED)
        self.assertEqual(self._row_count(), 1)

    def test_conflicting_content_under_same_grant_digest_is_rejected(self):
        budget = _signed_provider_budget()
        with self.store._pool.connection() as connection, connection.transaction():
            import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        changed = _signed_provider_budget(task_limit_microusd=budget.task_limit_microusd + 1)
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as connection, connection.transaction():
                import_signed_budget(authority=self.authority, connection=connection, budget=changed)
        self.assertEqual(self._row_count(), 1)

    def test_second_grant_digest_for_same_consumer_key_is_rejected(self):
        budget = _signed_provider_budget()
        with self.store._pool.connection() as connection, connection.transaction():
            import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        other = _signed_provider_budget(grant_digest="b" * 64)
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as connection, connection.transaction():
                import_signed_budget(authority=self.authority, connection=connection, budget=other)
        self.assertEqual(self._row_count(), 1)

    def test_wrong_signature_is_rejected_without_mutation(self):
        budget = _signed_provider_budget()
        forged = budget.model_copy(update={"owner_signature": "1" * 64})
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as connection, connection.transaction():
                import_signed_budget(authority=self.authority, connection=connection, budget=forged)
        self.assertEqual(self._row_count(), 0)

    def test_import_requires_existing_project_registration(self):
        budget = _signed_provider_budget(project_id="unregistered-project")
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as connection, connection.transaction():
                import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        self.assertEqual(self._row_count(), 0)

    def test_import_rejects_budget_exceeding_registered_project_ceiling(self):
        budget = _signed_provider_budget(task_limit_microusd=2_000_000)
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as connection, connection.transaction():
                import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        self.assertEqual(self._row_count(), 0)

    def test_receipt_never_carries_signature_or_secret_material(self):
        budget = _signed_provider_budget()
        with self.store._pool.connection() as connection, connection.transaction():
            receipt = import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        rendered = repr(receipt)
        self.assertNotIn(budget.owner_signature, rendered)
        self.assertNotIn(fixtures.OWNER_SECRET.decode("latin-1"), rendered)

    def test_impossible_calendar_day_is_rejected_without_mutation(self):
        budget = _signed_provider_budget(day="2026-02-30")
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as connection, connection.transaction():
                import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        self.assertEqual(self._row_count(), 0)

    def test_expired_day_is_rejected_without_mutation(self):
        budget = _signed_provider_budget(day="2000-01-01")
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as connection, connection.transaction():
                import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        self.assertEqual(self._row_count(), 0)

    def test_exact_repeat_rejects_when_a_second_consumer_match_exists(self):
        first = _signed_provider_budget()
        second = _signed_provider_budget(grant_digest="b" * 64)
        with self.store._pool.connection() as connection, connection.transaction():
            self.authority.register(connection, first)
            self.authority.register(connection, second)
        self.assertEqual(self._row_count(), 2)
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as connection, connection.transaction():
                import_signed_budget(authority=self.authority, connection=connection, budget=first)
        self.assertEqual(self._row_count(), 2)

    def test_repeat_import_rejects_after_registration_superseded(self):
        budget = _signed_provider_budget()
        with self.store._pool.connection() as connection, connection.transaction():
            import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        with self.store._pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.entitlement_project_registration "
                "SET superseded_at = now() WHERE project_profile_id=%s",
                (budget.project_id,),
            )
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as connection, connection.transaction():
                import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        self.assertEqual(self._row_count(), 1)

    def test_repeat_import_rejects_after_ceiling_tightened(self):
        budget = _signed_provider_budget(task_limit_microusd=900_000)
        with self.store._pool.connection() as connection, connection.transaction():
            import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        with self.store._pool.connection() as connection:
            connection.execute(
                f"UPDATE {self.schema}.entitlement_project_registration "
                "SET document = jsonb_set(document, '{budget_task_usd}', '0.5') "
                "WHERE project_profile_id=%s AND superseded_at IS NULL",
                (budget.project_id,),
            )
        with self.assertRaises(TeamAuthorizationError):
            with self.store._pool.connection() as connection, connection.transaction():
                import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        self.assertEqual(self._row_count(), 1)

    def test_import_creates_no_attempt_spend_or_usage_rows(self):
        budget = _signed_provider_budget()
        with self.store._pool.connection() as connection, connection.transaction():
            import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        with self.store._pool.connection() as connection:
            for table in ("director_primary_provider_spend", "director_primary_provider_usage"):
                count = connection.execute(
                    f"SELECT count(*) AS n FROM {self.schema}.{table}"
                ).fetchone()["n"]
                self.assertEqual(count, 0, table)

    def test_funded_preflight_succeeds_while_meter_team_qualification_stays_separate(self):
        budget = _signed_provider_budget()
        with self.store._pool.connection() as connection, connection.transaction():
            import_signed_budget(authority=self.authority, connection=connection, budget=budget)
        with self.store._pool.connection() as connection, connection.transaction():
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {self.schema}.director_primary_provider_attempt "
                "(attempt_digest text PRIMARY KEY)"
            )
        settings = SimpleNamespace(max_task_budget_usd=1.0, max_daily_provider_budget_usd=5.0)
        routing = SimpleNamespace(authority=self.authority)
        packet = {"task_id": budget.task_id, "project": budget.project_id}
        with self.store._pool.connection() as connection, connection.transaction():
            found = check_budget_setup(
                connection=connection, routing=routing, settings=settings,
                provider=budget.provider_id, packet=packet, attempt_usd=0.05,
            )
        self.assertEqual(found.grant_digest, budget.grant_digest)
        # Budget-setup is funded, but this producer created no attempt/spend/
        # usage row of its own: a still-missing meter/team qualification
        # remains a separate, unaffected gate elsewhere.
        with self.store._pool.connection() as connection:
            for table in ("director_primary_provider_spend", "director_primary_provider_usage"):
                count = connection.execute(
                    f"SELECT count(*) AS n FROM {self.schema}.{table}"
                ).fetchone()["n"]
                self.assertEqual(count, 0, table)

    def test_concurrent_identical_imports_produce_exactly_one_row(self):
        budget = _signed_provider_budget()

        def attempt():
            with self.store._pool.connection() as connection, connection.transaction():
                return import_signed_budget(
                    authority=self.authority, connection=connection, budget=budget
                ).status

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(self._row_count(), 1)
        self.assertEqual(
            sorted(results, key=str),
            sorted([BudgetProducerStatus.REGISTERED, BudgetProducerStatus.ALREADY_REGISTERED], key=str),
        )

    def test_concurrent_conflicting_imports_leave_exactly_one_consumer_match(self):
        first = _signed_provider_budget()
        second = _signed_provider_budget(grant_digest="b" * 64)

        def attempt(budget):
            try:
                with self.store._pool.connection() as connection, connection.transaction():
                    return import_signed_budget(
                        authority=self.authority, connection=connection, budget=budget
                    ).status
            except TeamAuthorizationError:
                return "REJECTED"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (first, second)))
        self.assertEqual(self._row_count(), 1)
        self.assertEqual(sorted(results, key=str), sorted([BudgetProducerStatus.REGISTERED, "REJECTED"], key=str))

    def test_lock_contention_across_midnight_rejects_repeat_once_unblocked(self):
        budget = _signed_provider_budget()
        with self.store._pool.connection() as connection, connection.transaction():
            import_signed_budget(authority=self.authority, connection=connection, budget=budget)

        lock_held = threading.Event()
        release_holder = threading.Event()
        holder_errors = []

        def hold_lock():
            try:
                with self.store._pool.connection() as connection, connection.transaction():
                    connection.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        ("primary-provider-budget:" + budget.project_id,),
                    )
                    lock_held.set()
                    release_holder.wait(timeout=5)
            except Exception as exc:  # pragma: no cover - surfaced via holder_errors
                holder_errors.append(exc)

        holder = threading.Thread(target=hold_lock)
        holder.start()
        self.assertTrue(lock_held.wait(timeout=5))

        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        waiter_errors = []

        def attempt_while_blocked():
            try:
                with patch.object(self.store, "_clock", lambda: tomorrow):
                    with self.store._pool.connection() as connection, connection.transaction():
                        import_signed_budget(
                            authority=self.authority, connection=connection, budget=budget
                        )
            except TeamAuthorizationError as exc:
                waiter_errors.append(exc)

        waiter = threading.Thread(target=attempt_while_blocked)
        waiter.start()
        time.sleep(0.2)
        self.assertTrue(waiter.is_alive())  # still genuinely blocked on the held lock
        release_holder.set()
        waiter.join(timeout=5)
        holder.join(timeout=5)
        self.assertEqual(holder_errors, [])
        self.assertEqual(len(waiter_errors), 1)
        self.assertEqual(self._row_count(), 1)
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 08:50 UTC | END]]
# [[Claude Code | Claude Sonnet 5 | 2026-09-14 | 18:50 UTC | END]]
