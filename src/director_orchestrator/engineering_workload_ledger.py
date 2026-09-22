# [[Codex Director | GPT-6 Astra | 2026-09-22 | 00:52 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-22 | 00:38 UTC | BEGIN]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | BEGIN]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:04 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | BEGIN]]
"""Local admission journal for external engineering work; never a dispatcher.

The operator supplies policy, route enrollment and history evidence. This module
checks consistency and allocation, not the authenticity of those attestations.
Use one protected journal per team; runtime TaskStore is a different population.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .allocation_profiles import (
    allocation_profile_digest, allocation_targets,
    historical_allocation_profile_digest, PRE_RETIREMENT_V3,
)
from .workload_policy import (
    DirectProductionProvider, FailureKind, RetryRequest, Transport,
    WorkloadPolicy, direct_assignment_capacity_violation, evaluate_retry,
)

VERSION = "external-engineering-workload/v1"
WINDOW = 20
PRODUCT_BUCKETS = {
    "openai-codex": "openai", "claude-code": "claude", "cursor": "cursor",
    "xai-grok": "grok", "gemini-antigravity": "antigravity",
    "grok-bot": None,
}
KINDS = {"implementation", "review", "validation", "research", "coordination"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
TERMINAL = {"succeeded", "failed", "rejected", "cancelled", "ambiguous"}


class LedgerError(ValueError):
    """A request cannot safely be admitted or recorded."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise LedgerError(f"{field}_required")
    return value


def _positive(value: Any, field: str, maximum: int = 2**31 - 1) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise LedgerError(f"{field}_invalid")
    return value


def _evidence(value: Any, field: str) -> None:
    if not isinstance(value, dict):
        raise LedgerError(f"{field}_required")
    _text(value.get("reference"), field)
    if not SHA256.fullmatch(str(value.get("sha256", ""))):
        raise LedgerError(f"{field}_digest_required")


def _time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError()
        return parsed
    except (ValueError, TypeError, AttributeError) as exc:
        raise LedgerError("utc_timestamp_required") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def policy_preview(policy: dict) -> dict:
    """Validate a proposal without enrolling a route or changing any journal."""
    if not isinstance(policy, dict):
        raise LedgerError("policy_required")
    kind = policy.get("kind")
    if kind == "canonical":
        try:
            targets = dict(allocation_targets(policy.get("profile", "engineering")))
        except ValueError as exc:
            raise LedgerError(str(exc)) from exc
        bindings = dict(PRODUCT_BUCKETS)
        for product, bucket in policy.get("product_bindings", {}).items():
            if product != "grok-bot" or bucket not in targets:
                raise LedgerError("unsupported_product_binding")
            bindings[product] = bucket
    elif kind == "product":
        targets = policy.get("targets")
        if (not isinstance(targets, dict) or not targets
                or any(k not in PRODUCT_BUCKETS or type(v) is not int or not 0 <= v <= 100
                       for k, v in targets.items()) or sum(targets.values()) != 100):
            raise LedgerError("product_targets_must_total_100")
        bindings = {product: product if product in targets else None for product in PRODUCT_BUCKETS}
    else:
        raise LedgerError("policy_kind_invalid")
    return {
        "policy_version": VERSION, "policy_digest": _digest(policy), "kind": kind,
        "targets": targets, "product_bindings": bindings, "window": WINDOW,
        "normal_tolerance_points": 3, "hard_tolerance_points": 5,
        "historical_maximum_slots": {key: min(WINDOW, (target + 5) * WINDOW // 100)
                          if target else 0 for key, target in targets.items()},
        "dispatch_authorized": False,
    }


@dataclass(frozen=True)
class _CapacityRow:
    # A capacity projection only. It is not an accepted-production credit.
    assignment_id: str
    provider: DirectProductionProvider


def _provider(bucket: str) -> DirectProductionProvider:
    return DirectProductionProvider({"openai": "external_openai", "antigravity": "gemini"}.get(bucket, bucket))


class EngineeringWorkloadLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    @contextmanager
    def _connection(self, *, create: bool = False, write: bool = False):
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        elif not self.path.exists():
            raise LedgerError("history_unknown_bootstrap_required")
        uri = self.path.as_uri() + ("?mode=rwc" if create else "?mode=rw" if write else "?mode=ro")
        db = sqlite3.connect(uri, uri=True, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            # One atomic read/check/append across processes; no provider I/O in this lock.
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _meta(db) -> dict:
        try:
            row = db.execute("SELECT payload FROM metadata WHERE id=1").fetchone()
        except sqlite3.Error as exc:
            raise LedgerError("not_an_engineering_ledger") from exc
        if row is None:
            raise LedgerError("history_unknown_bootstrap_required")
        meta = json.loads(row[0])
        if meta.get("schema_version") != VERSION:
            raise LedgerError("ledger_version_unsupported")
        if meta.get("policy_digest") != _digest(meta["policy"]):
            raise LedgerError("stored_policy_digest_mismatch")
        if meta["policy"]["kind"] == "canonical":
            profile = meta["policy"].get("profile", "engineering")
            # Bind the current prospective identity for new admissions while still
            # authenticating rows recorded under the retired hard-tolerance identity
            # or an earlier pre-retirement profile revision, without rewriting them.
            valid = {allocation_profile_digest(profile), historical_allocation_profile_digest(profile)}
            if profile in PRE_RETIREMENT_V3:
                valid.add(PRE_RETIREMENT_V3[profile][0])
            if meta["canonical_digest"] not in valid:
                raise LedgerError("canonical_policy_changed_reconciliation_required")
        return meta

    @staticmethod
    def _routes(db) -> dict:
        return {row["route_id"]: json.loads(row["payload"]) for row in db.execute("SELECT * FROM routes")}

    @staticmethod
    def _validate_route(route: dict, policy: dict) -> None:
        _text(route.get("route_id"), "route_id")
        if route.get("product_id") not in PRODUCT_BUCKETS:
            raise LedgerError("product_unknown")
        if policy_preview(policy)["product_bindings"][route["product_id"]] is None:
            raise LedgerError("allocation_binding_missing")
        _text(route.get("actor_id"), "actor_id")
        # Product/model identity is optional unless strict native acceptance needs it.
        for key in ("provider_identity", "model_family"):
            if route.get(key) is not None:
                _text(route[key], key)
        if (type(route.get("eligible")) is not bool or not isinstance(route.get("roles"), list)
                or not route["roles"] or any(role not in KINDS for role in route["roles"])):
            raise LedgerError("route_roles_or_eligibility_invalid")
        _evidence(route.get("enrollment_evidence"), "route_enrollment")

    def bootstrap(self, manifest: dict) -> dict:
        """Import an attested last20, or explicitly start future-only accounting.

        The caller must be the authorized operator. Evidence references are
        preserved, not interpreted as signatures or silently turned into proof.
        """
        manifest = json.loads(_json(manifest))
        _evidence(manifest.get("authorization"), "bootstrap_authorization")
        _text(manifest.get("operator"), "operator")
        _time(manifest.get("cutover_at"))
        if _time(manifest["cutover_at"]) > _time(_now()):
            raise LedgerError("cutover_must_not_be_future")
        preview = policy_preview(manifest.get("policy"))
        if manifest["policy"]["kind"] == "product" or manifest["policy"].get("product_bindings"):
            _evidence(manifest.get("policy_authorization"), "policy_authorization")
        mode = manifest.get("history_mode")
        history = manifest.get("history", [])
        if not isinstance(history, list):
            raise LedgerError("history_invalid")
        _evidence(manifest.get("history_evidence"), "history_evidence")
        if mode == "prospective":
            if (history or manifest.get("prior_history_unknown") is not True
                    or manifest.get("quiescent_cutover_verified") is not True
                    or manifest.get("all_future_assignments_required") is not True):
                raise LedgerError("prospective_cutover_attestation_required")
        elif mode == "verified_last20":
            if len(history) != WINDOW or manifest.get("complete_last20_attested") is not True:
                raise LedgerError("verified_history_requires_exactly_20_and_attestation")
        else:
            raise LedgerError("history_mode_required")
        routes = manifest.get("routes", [])
        if not isinstance(routes, list) or len({r["route_id"] for r in routes}) != len(routes):
            raise LedgerError("route_ids_must_be_unique")
        for route in routes:
            self._validate_route(route, manifest["policy"])
        meta = {**manifest, "history": [], "routes": [], "schema_version": VERSION,
                "policy_digest": preview["policy_digest"], "recorded_at": _now(),
                "canonical_digest": allocation_profile_digest(manifest["policy"].get("profile", "engineering"))
                if manifest["policy"]["kind"] == "canonical" else None}
        with self._connection(create=True, write=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
                raise LedgerError("existing_database_must_not_be_reset")
            schema = (
                "CREATE TABLE metadata(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)",
                "CREATE TABLE routes(route_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, actor_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)",
                "CREATE TABLE assignments(seq INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id TEXT UNIQUE NOT NULL, route_id TEXT NOT NULL REFERENCES routes(route_id), product_id TEXT NOT NULL, bucket TEXT NOT NULL, kind TEXT NOT NULL, packet_sha TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(product_id,kind,packet_sha))",
                "CREATE TABLE attempts(assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id), number INTEGER NOT NULL CHECK(number BETWEEN 1 AND 3), payload TEXT NOT NULL, PRIMARY KEY(assignment_id,number))",
                "CREATE TABLE outcomes(assignment_id TEXT NOT NULL, number INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(assignment_id,number), FOREIGN KEY(assignment_id,number) REFERENCES attempts(assignment_id,number))",
                "CREATE TABLE completions(assignment_id TEXT PRIMARY KEY REFERENCES assignments(assignment_id), payload TEXT NOT NULL)",
                "CREATE INDEX assignment_window ON assignments(kind,seq)",
            )
            for statement in schema:
                db.execute(statement)
            for table in ("metadata", "routes", "assignments", "attempts", "outcomes", "completions"):
                for verb in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER immutable_{table}_{verb} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT, 'ledger_is_append_only'); END")
            db.execute("INSERT INTO metadata VALUES (1,?)", (_json(meta),))
            for route in routes:
                db.execute("INSERT INTO routes VALUES (?,?,?,?)", (route["route_id"], route["product_id"], route["actor_id"].strip().casefold(), _json(route)))
            previous = None
            for item in history:
                _evidence(item.get("evidence"), "historical_assignment_evidence")
                timestamp = _time(item.get("admitted_at"))
                if timestamp > _time(meta["cutover_at"]) or previous is not None and timestamp < previous:
                    raise LedgerError("historical_order_invalid")
                previous = timestamp
                request, route, bucket = self._request(db, meta, item.get("request"), historical=True)
                if request["kind"] == "coordination" or item.get("outcome") not in TERMINAL:
                    raise LedgerError("historical_assignment_invalid")
                self._insert(db, request, bucket, item["admitted_at"])
                db.execute("INSERT INTO completions VALUES (?,?)", (request["assignment_id"], _json({
                    "outcome": item["outcome"], "evidence": item["evidence"], "imported": True,
                    "recorded_at": _now(), "productive_credit_issued": False})))
        return self.status()

    def enroll_route(self, route: dict) -> dict:
        with self._connection(write=True) as db:
            meta = self._meta(db)
            self._validate_route(route, meta["policy"])
            if db.execute("SELECT 1 FROM routes WHERE route_id=?", (route["route_id"],)).fetchone():
                raise LedgerError("existing_route_binding_immutable")
            if db.execute("SELECT 1 FROM routes WHERE actor_id=?", (route["actor_id"].strip().casefold(),)).fetchone():
                raise LedgerError("actor_already_enrolled_no_route_alias_reset")
            db.execute("INSERT INTO routes VALUES (?,?,?,?)", (route["route_id"], route["product_id"], route["actor_id"].strip().casefold(), _json(route)))
        return self.status()

    @staticmethod
    def _bounds(bounds: dict) -> None:
        if not isinstance(bounds, dict):
            raise LedgerError("bounds_required")
        for key in ("timeout_seconds", "turn_limit", "output_bytes_limit"):
            _positive(bounds.get(key), key)
        _positive(bounds.get("call_limit"), "call_limit", 3)
        if type(bounds.get("hard_usd_cap_verified")) is not bool:
            raise LedgerError("usd_enforcement_declaration_required")
        cap = bounds.get("usd_limit")
        if cap is not None and (isinstance(cap, bool) or not isinstance(cap, (int, float)) or not 0 < cap < 1_000_000):
            raise LedgerError("usd_limit_invalid")
        if bounds["hard_usd_cap_verified"]:
            if cap is None:
                raise LedgerError("verified_usd_cap_requires_limit")
            _evidence(bounds.get("usd_cap_evidence"), "usd_cap_evidence")
        else:
            _evidence(bounds.get("unverified_billing_authorization"), "unverified_billing_authorization")

    def _request(self, db, meta: dict, raw: dict, *, historical=False) -> tuple:
        if not isinstance(raw, dict):
            raise LedgerError("assignment_request_required")
        request = json.loads(_json(raw))
        for key in ("assignment_id", "task_id", "scope"):
            _text(request.get(key), key)
        if request.get("parent_assignment_id") is not None:
            _text(request["parent_assignment_id"], "parent_assignment_id")
            if request["parent_assignment_id"] == request["assignment_id"]:
                raise LedgerError("assignment_cannot_parent_itself")
        _evidence(request.get("packet_evidence"), "packet_evidence")
        if request.get("kind") not in KINDS:
            raise LedgerError("work_kind_invalid")
        self._bounds(request.get("bounds"))
        try:
            Transport(request.get("transport"))
        except ValueError as exc:
            raise LedgerError("transport_invalid") from exc
        _text(request.get("route_id"), "route_id")
        route = self._routes(db).get(request["route_id"])
        if route is None:
            raise LedgerError("route_not_enrolled")
        if route["product_id"] != request.get("product_id"):
            raise LedgerError("route_product_mismatch")
        if not historical and (not route["eligible"] or request["kind"] not in route["roles"]):
            raise LedgerError("route_not_eligible_for_role")
        authors = request.get("material_authors", [])
        if not isinstance(authors, list) or any(not isinstance(a, dict) for a in authors):
            raise LedgerError("material_authors_invalid")
        if request["kind"] == "review" and not historical:
            if not SHA1.fullmatch(str(request.get("candidate_sha", ""))) or not authors:
                raise LedgerError("review_candidate_and_authors_required")
            _evidence(request.get("authorship_evidence"), "authorship_evidence")
            review_policy = request.get("review_policy")
            if review_policy not in {"ordinary_pr", "native_acceptance"}:
                raise LedgerError("explicit_review_policy_required")
            if review_policy == "native_acceptance" and (not route.get("provider_identity") or not route.get("model_family")):
                raise LedgerError("reviewer_independence_unverified")
            for author in authors:
                keys = ("actor_id",) if review_policy == "ordinary_pr" else ("actor_id", "product_id", "provider_identity", "model_family")
                for key in keys:
                    _text(author.get(key), "material_author_" + key)
                    if str(route.get(key, "")).strip().casefold() == author[key].strip().casefold():
                        raise LedgerError("reviewer_not_independent_of_material_author")
        bucket = policy_preview(meta["policy"])["product_bindings"][request["product_id"]]
        return request, route, bucket

    @staticmethod
    def _window(db, limit=WINDOW) -> list:
        return list(reversed(db.execute(
            "SELECT * FROM assignments WHERE kind!='coordination' ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()))

    @staticmethod
    def _capacity(meta: dict, bucket: str, history: list) -> str | None:
        preview = policy_preview(meta["policy"])
        if bucket is None:
            return "allocation_binding_missing"
        if meta["policy"]["kind"] == "canonical":
            rows = [_CapacityRow(row["assignment_id"], _provider(row["bucket"])) for row in history]
            return direct_assignment_capacity_violation(_provider(bucket), rows,
                policy=WorkloadPolicy.for_profile(meta["policy"].get("profile", "engineering")))
        target = preview["targets"].get(bucket, 0)
        if not target:
            return "allocation_policy_product_ineligible"
        return None

    @staticmethod
    def _require_current_authority(meta: dict) -> None:
        """A recognized historical or pre-retirement canonical identity remains
        readable and supports idempotent replay and late reporting of prior
        work, but it never mints new admission or new dispatch authority. Only
        the current canonical identity may authorize a new assignment row or a
        new attempt permit.
        """
        if meta["policy"]["kind"] == "canonical":
            profile = meta["policy"].get("profile", "engineering")
            if meta["canonical_digest"] != allocation_profile_digest(profile):
                raise LedgerError("canonical_policy_superseded_new_authority_required")

    @staticmethod
    def _insert(db, request: dict, bucket: str, created_at: str) -> None:
        db.execute("INSERT INTO assignments(assignment_id,route_id,product_id,bucket,kind,packet_sha,created_at,payload) VALUES (?,?,?,?,?,?,?,?)",
                   (request["assignment_id"], request["route_id"], request["product_id"], bucket, request["kind"], request["packet_evidence"]["sha256"], created_at, _json(request)))

    def reserve(self, request: dict) -> dict:
        with self._connection(write=True) as db:
            meta = self._meta(db)
            request, route, bucket = self._request(db, meta, request)
            old = db.execute("SELECT payload FROM assignments WHERE assignment_id=?", (request["assignment_id"],)).fetchone()
            if old:
                if old[0] != _json(request):
                    raise LedgerError("assignment_id_reused_with_different_request")
                return {"assignment_id": request["assignment_id"], "already_reserved": True, "dispatch_permitted": False}
            if db.execute("SELECT 1 FROM assignments WHERE product_id=? AND kind=? AND packet_sha=?", (request["product_id"], request["kind"], request["packet_evidence"]["sha256"])).fetchone():
                raise LedgerError("same_packet_cannot_create_another_assignment")
            if request["kind"] != "coordination":
                if self._route_failure_count(db, request["route_id"]) >= 3:
                    raise LedgerError("route_unavailable_after_three_failures")
                violation = self._capacity(meta, bucket, self._window(db, 19))
                if violation:
                    raise LedgerError(violation)
            self._require_current_authority(meta)
            self._insert(db, request, bucket, _now())
        return {"assignment_id": request["assignment_id"], "already_reserved": False,
                "dispatch_permitted": False,
                "next": "settle" if request["kind"] == "coordination" else "begin-attempt"}

    @staticmethod
    def _assignment(db, assignment_id: str):
        row = db.execute("SELECT * FROM assignments WHERE assignment_id=?", (assignment_id,)).fetchone()
        if row is None:
            raise LedgerError("assignment_unknown")
        return row, json.loads(row["payload"])

    def begin_attempt(self, assignment_id: str, *, transport: str, new_information_evidence=None) -> dict:
        with self._connection(write=True) as db:
            meta = self._meta(db)
            self._require_current_authority(meta)
            row, request = self._assignment(db, assignment_id)
            if request["kind"] == "coordination":
                raise LedgerError("coordination_is_record_only_no_provider_dispatch")
            if db.execute("SELECT 1 FROM completions WHERE assignment_id=?", (assignment_id,)).fetchone():
                raise LedgerError("assignment_already_terminal")
            if self._route_failure_count(db, row["route_id"]) >= 3:
                raise LedgerError("route_unavailable_after_three_failures")
            attempts = db.execute("SELECT * FROM attempts WHERE assignment_id=? ORDER BY number", (assignment_id,)).fetchall()
            outcomes = [json.loads(r[0]) for r in db.execute("SELECT payload FROM outcomes WHERE assignment_id=? ORDER BY number", (assignment_id,))]
            if len(outcomes) != len(attempts):
                raise LedgerError("attempt_outcome_unknown_no_duplicate_dispatch")
            if outcomes and outcomes[-1]["outcome"] == "succeeded":
                raise LedgerError("successful_attempt_must_not_be_repeated")
            bounds = request["bounds"]
            if len(attempts) >= bounds["call_limit"]:
                raise LedgerError("call_budget_exhausted_route_unavailable")
            if new_information_evidence is not None:
                _evidence(new_information_evidence, "new_information")
            cost_known = all(o["usage"]["usd"] is not None for o in outcomes)
            usage_known = all(all(o["usage"][key] is not None for key in ("turns", "output_bytes")) for o in outcomes)
            if outcomes and (not usage_known or bounds["usd_limit"] is not None and not cost_known):
                raise LedgerError("retry_usage_unknown_reconciliation_required")
            consumed = lambda key: sum(o["usage"][key] or 0 for o in outcomes)
            failure = {"transient": FailureKind.TRANSIENT, "failed": FailureKind.TERMINAL,
                       "ambiguous": FailureKind.AMBIGUOUS_PAID_DISPATCH}
            previous = outcomes[-1] if outcomes else None
            decision = evaluate_retry(RetryRequest(
                attempt_number=len(attempts) + 1, retry_limit=3,
                previous_failure=failure[previous["outcome"]] if previous else FailureKind.NONE,
                transport=Transport(transport), previous_transport=Transport(json.loads(attempts[-1]["payload"])["transport"]) if attempts else None,
                materially_new_information=new_information_evidence is not None,
                token_budget_remaining=consumed("turns") < bounds["turn_limit"] and consumed("output_bytes") < bounds["output_bytes_limit"],
                cost_budget_remaining=bounds["usd_limit"] is None or consumed("usd") < bounds["usd_limit"],
                timeout_budget_remaining=(_time(_now()) - _time(row["created_at"])).total_seconds() < bounds["timeout_seconds"],
            ))
            if not decision.allowed:
                raise LedgerError(";".join(decision.violations))
            if not attempts and transport != request["transport"]:
                raise LedgerError("initial_transport_differs_from_reserved_packet")
            payload = {"assignment_id": assignment_id, "attempt_number": len(attempts) + 1,
                       "transport": transport, "started_at": _now(),
                       "deadline_at": (_time(row["created_at"]) + timedelta(seconds=bounds["timeout_seconds"])).isoformat(),
                       "new_information_evidence": new_information_evidence,
                       "remaining_bounds": {"calls": 1,
                           "assignment_calls_remaining": bounds["call_limit"] - len(attempts),
                           "timeout_seconds": max(0, bounds["timeout_seconds"] - (_time(_now()) - _time(row["created_at"])).total_seconds()),
                           "turns": bounds["turn_limit"] - consumed("turns"),
                           "output_bytes": bounds["output_bytes_limit"] - consumed("output_bytes"),
                           "usd": bounds["usd_limit"] - consumed("usd") if bounds["usd_limit"] is not None else None},
                       "dispatch_permitted": True,
                       "permission_scope": "workload_and_retry_only_other_gates_still_required"}
            db.execute("INSERT INTO attempts VALUES (?,?,?)", (assignment_id, len(attempts) + 1, _json(payload)))
        return payload

    def record_outcome(self, assignment_id: str, number: int, outcome: dict) -> dict:
        outcome = json.loads(_json(outcome))
        if outcome.get("outcome") not in {"succeeded", "transient", "failed", "ambiguous"}:
            raise LedgerError("attempt_outcome_invalid")
        _evidence(outcome.get("evidence"), "attempt_outcome_evidence")
        usage = outcome.get("usage")
        if not isinstance(usage, dict) or set(usage) != {"usd", "turns", "output_bytes"}:
            raise LedgerError("usage_fields_required_unknown_is_null")
        for key, value in usage.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
                                      or key != "usd" and type(value) is not int):
                raise LedgerError("usage_invalid")
        with self._connection(write=True) as db:
            self._meta(db)
            if not db.execute("SELECT 1 FROM attempts WHERE assignment_id=? AND number=?", (assignment_id, number)).fetchone():
                raise LedgerError("attempt_unknown")
            existing = db.execute("SELECT payload FROM outcomes WHERE assignment_id=? AND number=?", (assignment_id, number)).fetchone()
            if existing:
                if existing[0] != _json(outcome):
                    raise LedgerError("attempt_outcome_immutable")
                return {"already_recorded": True}
            if db.execute("SELECT 1 FROM completions WHERE assignment_id=?", (assignment_id,)).fetchone():
                raise LedgerError("terminal_assignment_requires_separate_reconciliation")
            db.execute("INSERT INTO outcomes VALUES (?,?,?)", (assignment_id, number, _json(outcome)))
        return {"already_recorded": False}

    def settle(self, assignment_id: str, outcome: str, evidence: dict) -> dict:
        if outcome not in TERMINAL:
            raise LedgerError("assignment_outcome_invalid")
        _evidence(evidence, "completion_evidence")
        with self._connection(write=True) as db:
            self._meta(db)
            _, request = self._assignment(db, assignment_id)
            previous = db.execute("SELECT payload FROM completions WHERE assignment_id=?", (assignment_id,)).fetchone()
            if previous:
                payload = json.loads(previous[0])
                if payload["outcome"] != outcome or payload["evidence"] != evidence:
                    raise LedgerError("completion_immutable")
                return payload
            attempts = db.execute("SELECT number FROM attempts WHERE assignment_id=? ORDER BY number DESC", (assignment_id,)).fetchall()
            last = db.execute("SELECT payload FROM outcomes WHERE assignment_id=? ORDER BY number DESC LIMIT 1", (assignment_id,)).fetchone()
            known = db.execute("SELECT COUNT(*) FROM outcomes WHERE assignment_id=?", (assignment_id,)).fetchone()[0]
            if len(attempts) != known and outcome != "ambiguous":
                raise LedgerError("pending_attempt_requires_ambiguous_settlement")
            if last and json.loads(last[0])["outcome"] == "ambiguous" and outcome != "ambiguous":
                raise LedgerError("ambiguous_attempt_cannot_be_relabelled")
            if request["kind"] != "coordination" and outcome == "succeeded" and (not last or json.loads(last[0])["outcome"] != "succeeded"):
                raise LedgerError("successful_native_outcome_required")
            payload = {"outcome": outcome, "evidence": evidence, "recorded_at": _now(),
                       "productive_credit_issued": False}
            db.execute("INSERT INTO completions VALUES (?,?)", (assignment_id, _json(payload)))
        return payload

    @staticmethod
    def _route_failure_count(db, route_id: str) -> int:
        # Arrival order records what the operator knew before the next admission.
        rows = db.execute("SELECT o.payload FROM outcomes o JOIN assignments a USING(assignment_id) WHERE a.route_id=? AND a.kind!='coordination' ORDER BY o.rowid DESC LIMIT 3", (route_id,)).fetchall()
        count = 0
        for row in rows:
            if json.loads(row[0])["outcome"] == "succeeded":
                break
            count += 1
        return count

    def status(self) -> dict:
        if not self.path.exists():
            return {"schema_version": VERSION, "history_mode": "unknown", "holds": ["history_unknown_bootstrap_required"],
                    "dispatch_authorized": False, "records": [], "next_eligible_routes": []}
        with self._connection() as db:
            meta = self._meta(db)
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                raise LedgerError("sqlite_integrity_check_failed")
            preview = policy_preview(meta["policy"])
            window = self._window(db)
            completions = {r["assignment_id"]: json.loads(r["payload"]) for r in db.execute("SELECT * FROM completions WHERE assignment_id IN (SELECT assignment_id FROM assignments WHERE kind!='coordination' ORDER BY seq DESC LIMIT 20)")}
            records = [{"sequence": r["seq"], "assignment_id": r["assignment_id"], "product_id": r["product_id"],
                        "route_id": r["route_id"],
                        "allocation_bucket": r["bucket"], "kind": r["kind"], "admitted_at": r["created_at"],
                        "state": completions.get(r["assignment_id"], {}).get("outcome", "reserved_or_in_flight"),
                        "productive_credit_issued": False} for r in window]
            for record, row in zip(records, window, strict=True):
                outcomes = [json.loads(r[0]) for r in db.execute("SELECT payload FROM outcomes WHERE assignment_id=? ORDER BY number", (row["assignment_id"],))]
                attempts = db.execute("SELECT COUNT(*) FROM attempts WHERE assignment_id=?", (row["assignment_id"],)).fetchone()[0]
                bounds = json.loads(row["payload"])["bounds"]
                usage = {key: sum(o["usage"][key] for o in outcomes) if outcomes and all(o["usage"][key] is not None for o in outcomes) else None for key in ("usd", "turns", "output_bytes")}
                violations = [key for key, limit in (("usd", bounds["usd_limit"]), ("turns", bounds["turn_limit"]), ("output_bytes", bounds["output_bytes_limit"])) if limit is not None and usage[key] is not None and usage[key] > limit]
                record.update(attempt_count=attempts, settled_attempts=len(outcomes), reported_usage=usage,
                              bounds=bounds, reported_bound_violations=violations)
            utilization = []
            for bucket, target in preview["targets"].items():
                count = sum(r["bucket"] == bucket for r in window)
                delta = count * 100 / WINDOW - target
                utilization.append({"bucket": bucket, "target_percent": target, "admitted": count,
                    "share_percent": count * 100 / WINDOW, "delta_points": delta,
                    "normal_band_state": "below" if delta < -3 else "above" if delta > 3 else "within",
                    "historical_hard_limit_slots": preview["historical_maximum_slots"][bucket],
                    "reserved_or_in_flight": sum(r["bucket"] == bucket and r["assignment_id"] not in completions for r in window)})

            routes = self._routes(db)
            eligibility = []
            products_enrolled = {r["product_id"] for r in routes.values()}
            route_options = list(routes.values()) + [{"product_id": p, "route_id": None} for p in PRODUCT_BUCKETS if p not in products_enrolled]
            for route in route_options:
                product = route["product_id"]
                bucket = preview["product_bindings"][product]
                reason = self._capacity(meta, bucket, window[-19:])
                if route["route_id"] is None:
                    reason = reason or "route_not_enrolled"
                elif not route["eligible"]:
                    reason = "route_not_eligible"
                if self._route_failure_count(db, route["route_id"]) >= 3:
                    reason = "route_unavailable_after_three_failures"
                eligibility.append({"product_id": product, "route_id": route["route_id"],
                    "actor_id": route.get("actor_id"), "allocation_bucket": bucket,
                    "workload_eligible": reason is None, "hold": reason,
                    "prospective_share_percent": (1 + sum(r["bucket"] == bucket for r in window[-19:])) * 100 / WINDOW if bucket else None,
                    "ordinary_review_actor_attested": bool(route.get("actor_id")),
                    "native_review_identity_attested": bool(route.get("provider_identity") and route.get("model_family")),
                    "role_candidate_budget_and_other_gates_still_required": True})
            # Prefer the most under-target eligible lane; labels never supply eligibility.
            eligible = sorted((r for r in eligibility if r["workload_eligible"]),
                              key=lambda r: (r["prospective_share_percent"] - preview["targets"][r["allocation_bucket"]], r["route_id"]))
            return {"schema_version": VERSION, "ledger_path": str(self.path), "policy": preview,
                "history_mode": meta["history_mode"], "history_evidence": meta["history_evidence"],
                "bootstrap_authorization": meta["authorization"], "cutover_at": meta["cutover_at"],
                "pre_cutover_history_verified": meta["history_mode"] == "verified_last20",
                "history_attestations_authenticated_by_this_tool": False,
                "journal_consistency": {"sqlite_integrity_ok": True, "policy_digest_matches": True,
                    "operator_attestations_are_not_cryptographic_proof": True},
                "current_window_checks": {
                    "historical_hard_caps_ok": all(r["admitted"] <= r["historical_hard_limit_slots"] for r in utilization),
                    "normal_bands_ok": all(r["normal_band_state"] == "within" for r in utilization),
                    "reported_resource_bounds_ok": False if any(r["reported_bound_violations"] for r in records)
                        else True if records and all(r["attempt_count"] == r["settled_attempts"]
                            and r["reported_usage"]["turns"] is not None
                            and r["reported_usage"]["output_bytes"] is not None
                            and (r["bounds"]["usd_limit"] is None or r["reported_usage"]["usd"] is not None)
                            for r in records) else None,
                    "unsettled_assignments": sum(r["state"] == "reserved_or_in_flight" for r in records)},
                "rolling_window_complete": len(window) == WINDOW, "denominator": WINDOW,
                "real_assignments_in_window": len(window), "unfilled_slots": WINDOW - len(window),
                "total_admitted_assignments": db.execute("SELECT COUNT(*) FROM assignments WHERE kind!='coordination'").fetchone()[0],
                "coordination_records": db.execute("SELECT COUNT(*) FROM assignments WHERE kind='coordination'").fetchone()[0],
                "records": records, "utilization": utilization, "routes": eligibility,
                "next_eligible_routes": [r["route_id"] for r in eligible],
                "next_eligible_products": list(dict.fromkeys(r["product_id"] for r in eligible)),
                "holds": [] if eligible else ["no_allocation_eligible_enrolled_route"],
                "dispatch_authorized": False, "production_credit_issued": False}


def human_status(status: dict) -> str:
    lines = [f"External engineering ledger: {status['history_mode']}",
             "This report does not dispatch providers or authenticate imported evidence."]
    if "policy" in status:
        lines += [f"Policy: {status['policy']['kind']} / {status['policy']['policy_digest']}",
                  f"Window: {status['real_assignments_in_window']}/20 real assignments; denominator 20",
                  "Product allocation bucket | assignments | share | target | delta | normal band"]
        for row in status["utilization"]:
            lines.append(f"{row['bucket']} | {row['admitted']} | {row['share_percent']:g}% | {row['target_percent']}% | {row['delta_points']:+g}pp | {row['normal_band_state']}")
        lines.append("Next allocation-eligible routes: " + (", ".join(status["next_eligible_routes"]) or "none"))
        lines.extend(f"{r['sequence']}: {r['assignment_id']} / {r['product_id']} / {r['state']}" for r in status["records"])
    lines.extend("Hold: " + hold for hold in status["holds"])
    return "\n".join(lines)
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:38 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 09:04 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 20:49 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-21 | 21:30 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 21:56 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-21 | 23:26 UTC | END]]
# [[Codex Director | GPT-6 Astra | 2026-09-21 | 23:56 UTC | END]]
# [[Claude Code | claude-sonnet-5 | 2026-09-22 | 00:38 UTC | END]]

# [[Codex Director | GPT-6 Astra | 2026-09-22 | 00:52 UTC | END]]
