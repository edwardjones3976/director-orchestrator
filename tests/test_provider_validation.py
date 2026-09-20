# [[Claude Code | Claude Opus 5 | 2026-09-20 | 06:01 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 21:26 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:52 UTC | BEGIN]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:41 UTC | BEGIN]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 18:52 UTC | BEGIN]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 13:05 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:50 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | BEGIN]]
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict,replace
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import secrets
import time
import sys
import tempfile
from unittest.mock import MagicMock,patch
import unittest

from director_orchestrator.packet_contracts import canonical_json_bytes
from director_orchestrator.provider_activation import ProviderActivationBinding,ProviderActivationStage,ProviderActivationSurface
from director_orchestrator.primary_provider_activation import PrimaryProviderActivationService
from director_orchestrator.provider_validation import (
    SCHEMA,CHECKPOINT_NAMESPACE,PostgresValidationStore,ValidationError,ValidationRequest,
    CaptureVerifier,capture_verifier_from_environment,make_capture,digest,normalize_capture,
    ValidationBridgeService,production_configuration_digest,
)

ROOT=Path(__file__).resolve().parents[1]
OWNER="owner-fixture-"+"o"*40
KEY="bridge-fixture-"+"k"*40
CANDIDATE="a"*40
TREE="b"*40


def work_packet():
    work=json.loads((ROOT/"docs/schemas/model-work-packet-v1.example.json").read_text())
    work.update(task_id="VALIDATION-canary",assignment_id="VALIDATION-canary",starting_ref=CANDIDATE,
                allowed_tools=[],allowed_external_effects=[])
    work["limits"].update(max_hops=1,max_revisions=0)
    return work


def binding(provider="claude"):
    return ProviderActivationBinding(provider,"workspace","v1","v1","v1","v1","v1")


def raw_native():
    return canonical_json_bytes({"type":"result","subtype":"success","is_error":False,
        "session_id":"actual-native-fixture-session","structured_output":{"status":"SUCCEEDED","outcome":"Canary analyzed"}})


class NativeCaptureTests(unittest.TestCase):
    def test_installed_runtime_inventory_detects_nested_dependency_change(self):
        from director_orchestrator.provider_validation import pinned_path_manifest
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/"lib").mkdir();dep=root/"lib/adapter.py"
            dep.write_text("before")
            before=pinned_path_manifest(directory)
            dep.write_text("after")
            self.assertNotEqual(before,pinned_path_manifest(directory))
            (root/"lib/new.py").write_text("new")
            self.assertEqual(len(pinned_path_manifest(directory)),2)

    def test_generic_antigravity_capture_cannot_qualify_team_worker(self):
        with self.assertRaisesRegex(ValidationError,"team SSH"):
            normalize_capture("antigravity",work_packet(),raw_native())

    def test_normalized_owner_assertion_does_not_count_as_capture(self):
        result=normalize_capture("claude",work_packet(),raw_native())
        with self.assertRaisesRegex(ValidationError,"raw native"):
            normalize_capture("claude",work_packet(),canonical_json_bytes(result))

    def test_terminal_failure_and_missing_session_fail(self):
        for changes in ({"subtype":"error"},{"is_error":True},{"session_id":None},{"tool_calls":[{}]}):
            raw=json.loads(raw_native());raw.update(changes)
            with self.assertRaises(ValidationError):
                normalize_capture("claude",work_packet(),canonical_json_bytes(raw))

    # Recorded verbatim from ONE authenticated grok canary on 2026-09-08 using
    # the exact pinned invocation, cost USD 0.0065.  Keeping the real shape here
    # means the dialect below is justified by an observation rather than a guess.
    GROK_OBSERVED_ENVELOPE = {
        "text": "OK",
        "stopReason": "end_turn",
        "sessionId": "01a081e6-a616-7fd3-9908-dd63513227ba",
        "requestId": "59fef4a5-1492-4891-899e-fbefeb147dfa",
        "num_turns": 1,
        "total_cost_usd": 0.0064991,
        "usage": {
            "input_tokens": 18268,
            "cache_read_input_tokens": 2944,
            "cache_creation_input_tokens": 0,
            "output_tokens": 37,
            "reasoning_tokens": 32,
            "total_tokens": 21249,
        },
    }

    def test_observed_grok_envelope_now_normalizes(self):
        """The real grok capture must pass end to end, not just the checks."""

        result = normalize_capture(
            "grok", work_packet(), canonical_json_bytes(self.GROK_OBSERVED_ENVELOPE)
        )

        self.assertEqual(result["provider"], "grok")
        self.assertEqual(
            result["run_reference"], self.GROK_OBSERVED_ENVELOPE["sessionId"]
        )
        self.assertEqual(result["outcome"], "OK")
        # usage is projected onto the packet contract: unknown native keys are
        # dropped and total_cost_usd is carried as cost_usd.
        self.assertEqual(
            set(result["usage"]),
            {"input_tokens", "output_tokens", "total_tokens", "cost_usd"},
        )
        self.assertEqual(result["usage"]["cost_usd"], 0.0064991)
        self.assertEqual(result["usage"]["total_tokens"], 21249)

    def test_every_closed_result_field_is_projected_not_passed_through(self):
        """The usage defect was general, not a grok quirk.

        The schema helper exposes every closed object. The live normalizer
        deliberately does not use this projection on side-effect evidence;
        the end-to-end rejection test below protects that distinction.
        """

        from director_orchestrator.packet_contracts import result_contract_properties
        from director_orchestrator.primary_transports import (
            _project_onto_result_contract,
        )

        closed = result_contract_properties()
        self.assertLessEqual(
            {"usage", "artifacts", "checks", "acceptance", "side_effects"},
            set(closed),
        )

        for field in ("artifacts", "checks", "acceptance", "side_effects"):
            with self.subTest(field=field):
                allowed = closed[field]
                item = {name: "value" for name in allowed}
                item["vendor_only_extra"] = "dropped"
                projected = _project_onto_result_contract(field, [item])
                self.assertEqual([{name: "value" for name in allowed}], projected)
                self.assertNotIn("vendor_only_extra", projected[0])

        # A field the contract does not close is returned untouched.
        self.assertEqual(
            [{"anything": 1}], _project_onto_result_contract("risks", [{"anything": 1}])
        )

    def test_native_envelope_with_vendor_extras_still_normalizes(self):
        """End to end: extras no longer reject an otherwise valid capture."""

        envelope = dict(self.GROK_OBSERVED_ENVELOPE)
        envelope["structured_output"] = {
            "status": "SUCCEEDED",
            "outcome": "Canary analyzed",
            "artifacts": [
                {"kind": "log", "location": "run://1", "vendor_trace": "x"}
            ],
            "checks": [
                {"command": "true", "result": "PASS", "vendor_duration_ms": 12}
            ],
        }

        result = normalize_capture(
            "grok", work_packet(), canonical_json_bytes(envelope)
        )

        self.assertEqual(
            [{"kind": "log", "location": "run://1"}], result["artifacts"]
        )
        self.assertEqual([{"command": "true", "result": "PASS"}], result["checks"])

    def test_grok_legacy_success_marker_cannot_bypass_one_turn_bound(self):
        for changes in ({"num_turns": 2, "status": "SUCCESS"},
                        {"stopReason": "max_tokens", "type": "result", "subtype": "success", "is_error": False}):
            envelope = {**self.GROK_OBSERVED_ENVELOPE, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                normalize_capture("grok", work_packet(), canonical_json_bytes(envelope))

    def test_cursor_capture_accepts_supported_shapes_and_rejects_failed_evidence(self):
        for envelope in (json.loads(raw_native()),
                         {"status": "SUCCESS", "conversation_id": "cursor-session",
                          "structured_output": {"status": "SUCCEEDED", "outcome": "done"}}):
            result = normalize_capture("cursor", work_packet(), canonical_json_bytes(envelope))
            self.assertEqual("SUCCEEDED", result["status"])
            for changes in ({"schema_version": "1.0"}, {"tool_calls": [{}]},
                            {"tool_uses": [{}]}, {"side_effects": [{}]}):
                with self.subTest(changes=changes), self.assertRaises(ValidationError):
                    normalize_capture("cursor", work_packet(), canonical_json_bytes({**envelope, **changes}))

    def test_normalization_never_sanitizes_unknown_side_effect_evidence(self):
        from director_orchestrator.primary_transports import normalize_native_provider_envelope
        from director_orchestrator.packet_contracts import validate_packet
        envelope = dict(self.GROK_OBSERVED_ENVELOPE)
        effects = [{"kind": "slack", "target": "channel", "status": "COMPLETED",
                    "idempotency_key": "effect-key", "unrecognized_authority": "unsafe"}]
        envelope["text"] = json.dumps({"status": "SUCCEEDED", "outcome": "done", "side_effects": effects})
        result = normalize_native_provider_envelope("grok", work_packet(), envelope)
        self.assertEqual(effects, result["side_effects"])
        with self.assertRaises(ValueError):
            validate_packet("result", result)
        del result["side_effects"][0]["unrecognized_authority"]
        validate_packet("result", result)

    def test_cursor_host_lane_fails_closed_where_its_sandbox_is_unavailable(self):
        """The lane pins --sandbox enabled, which only macOS and Linux support.

        An authenticated attempt on 2026-09-08 showed the Cursor CLI refuses the
        flag elsewhere, so the pinned invocation cannot execute and previously
        failed at dispatch with an opaque flag error.  The lane must instead be
        withheld on unsupported platforms, and must never be silently downgraded
        to --sandbox disabled, which removes the containment it requires.
        """

        from director_orchestrator.primary_transports import (
            CURSOR_SANDBOX_PLATFORMS,
            _cursor_host_lane_prefix,
            _safe_cursor_agent_prefix,
        )

        self.assertEqual({"darwin", "linux"}, set(CURSOR_SANDBOX_PLATFORMS))

        with tempfile.TemporaryDirectory() as directory:
            shim = Path(directory) / "cursor-agent"
            shim.write_text("#!/bin/sh\n", encoding="utf-8")
            # A suffix-less executable resolves directly, isolating the platform
            # decision from the Windows .cmd shim resolution path.
            self.assertIsNotNone(_safe_cursor_agent_prefix(str(shim)))

            for platform, supported in (
                ("darwin", True),
                ("linux", True),
                ("win32", False),
                ("cygwin", False),
            ):
                with self.subTest(platform=platform):
                    with patch(
                        "director_orchestrator.primary_transports.sys.platform",
                        platform,
                    ):
                        prefix = _cursor_host_lane_prefix(str(shim))
                    self.assertEqual(supported, prefix is not None)

    def test_end_turn_cannot_rehabilitate_another_provider_dialect(self):
        """Raised as a MEDIUM security finding and a P1.

        Left provider-agnostic, ``stopReason: end_turn`` rescued an envelope
        that its own dialect had already failed. Only grok's envelope was
        observed and qualified, and a payload must never select the dialect it
        is judged by.
        """

        failed_claude = {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "session_id": "claude-session",
            "sessionId": "claude-session",
            "stopReason": "end_turn",
            "num_turns": 1,
            "text": "rescued",
        }

        for provider in ("claude", "cursor", "github-copilot"):
            with self.subTest(provider=provider):
                with self.assertRaises(ValidationError):
                    normalize_capture(
                        provider, work_packet(), canonical_json_bytes(failed_claude)
                    )

    def test_grok_capture_requires_exactly_one_turn(self):
        """The canary pins max_hops=1 and the command requests --max-turns 1."""

        for turns in (2, 0, "1", True, None):
            with self.subTest(num_turns=turns):
                envelope = dict(self.GROK_OBSERVED_ENVELOPE)
                envelope["num_turns"] = turns
                with self.assertRaises(ValidationError):
                    normalize_capture(
                        "grok", work_packet(), canonical_json_bytes(envelope)
                    )

    def test_live_grok_path_is_protected_not_only_the_capture_path(self):
        """The live lane bypasses normalize_capture entirely.

        ``NativeCommandPacketTransport.send`` calls
        ``normalize_native_provider_envelope`` directly, so checks living only
        in ``normalize_capture`` never reached production dispatch.
        """

        from director_orchestrator.primary_transports import (
            normalize_native_provider_envelope,
        )

        # The camel-case session must resolve, or every real dispatch raised.
        ok = normalize_native_provider_envelope(
            "grok", work_packet(), dict(self.GROK_OBSERVED_ENVELOPE)
        )
        self.assertEqual(ok["run_reference"], self.GROK_OBSERVED_ENVELOPE["sessionId"])
        self.assertEqual(ok["status"], "SUCCEEDED")

        # A truncated run must not be persisted as a success.
        truncated = dict(self.GROK_OBSERVED_ENVELOPE)
        truncated["stopReason"] = "max_tokens"
        self.assertEqual(
            "FAILED",
            normalize_native_provider_envelope("grok", work_packet(), truncated)[
                "status"
            ],
        )

    def test_structured_result_inside_text_is_decoded_not_stringified(self):
        """A FAILED status carried in text must not become a SUCCEEDED packet."""

        from director_orchestrator.primary_transports import (
            normalize_native_provider_envelope,
        )

        envelope = dict(self.GROK_OBSERVED_ENVELOPE)
        envelope["text"] = json.dumps(
            {"status": "FAILED", "outcome": "Canary could not complete"}
        )

        result = normalize_native_provider_envelope(
            "grok", work_packet(), envelope
        )

        self.assertEqual("FAILED", result["status"])
        self.assertEqual("Canary could not complete", result["outcome"])

    def test_grok_dialect_still_rejects_every_unsuccessful_terminal(self):
        """Only the exact stopReason "end_turn" qualifies.

        A truncated or errored run must not slip through because the envelope
        happens to carry a stopReason at all.
        """

        for changes in (
            {"stopReason": "max_tokens"},
            {"stopReason": "error"},
            {"stopReason": "tool_use"},
            {"stopReason": None},
            {"sessionId": None},
            {"schema_version": "1.0"},
            {"tool_calls": [{}]},
            {"side_effects": [{}]},
        ):
            with self.subTest(changes=changes):
                envelope = dict(self.GROK_OBSERVED_ENVELOPE)
                envelope.update(changes)
                with self.assertRaises(ValidationError):
                    normalize_capture(
                        "grok", work_packet(), canonical_json_bytes(envelope)
                    )

    def test_closed_request_rejects_caller_namespace_commands_and_cost(self):
        for field in ("commands","namespace","cost_units","work_packet"):
            with self.assertRaises(ValueError):
                ValidationRequest.model_validate({"generation":"a"*64,"provider":"claude",field:"arbitrary"})

    def test_owner_only_live_record_rejected(self):
        class Store:
            def load(self,*args):return ()
        service=PrimaryProviderActivationService(store=Store(),bindings={"claude":binding()},required_providers=("claude",))
        now=datetime.now(timezone.utc)
        with self.assertRaisesRegex(ValueError,"native execution capture"):
            service.record(provider_id="claude",surface=ProviderActivationSurface.APP,
                stage=ProviderActivationStage.LIVE_OK,evidence_id="assertion",evidence_digest="a"*64,
                observed_at=now,expires_at=now)


class PostgresValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dsn=os.environ.get("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN")
        if not dsn:
            raise RuntimeError("LANGGRAPH_RUNTIME_POSTGRES_TEST_DSN is required; use sanctioned Windows lane")
        cls.store=PostgresValidationStore(dsn)
        cls.store.bootstrap()
        # Deferred commit-time gate: a settlement must also survive a blocked
        # COMMIT. The gate takes the same advisory lock the QA harness uses, so
        # a commit-time lock wait is real and the shared deadline must bound it.
        # Elsewhere the lock is uncontended and costs nothing.
        with cls.store.connect() as db:
            db.execute(f"""CREATE OR REPLACE FUNCTION {SCHEMA}.qa_settle_commit_gate() RETURNS trigger
                LANGUAGE plpgsql AS $gate$ BEGIN
                IF NEW.sequence = 3 AND NEW.state = 'succeeded' THEN
                    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.reservation, 20260914));
                END IF;
                RETURN NEW;
                END $gate$""")
            db.execute(f"DROP TRIGGER IF EXISTS qa_settle_commit_gate ON {SCHEMA}.checkpoints")
            db.execute(f"""CREATE CONSTRAINT TRIGGER qa_settle_commit_gate
                AFTER INSERT ON {SCHEMA}.checkpoints DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.qa_settle_commit_gate()""")

    def generation(self,*,budget=2,providers=("claude",)):
        generation=secrets.token_hex(32)
        import time
        lanes={provider:{"binding":asdict(binding(provider)),"configuration_digest":"c"*64,
                         "cost_units":2,"work_packet":work_packet()} for provider in providers}
        self.store.create_generation(generation=generation,candidate=CANDIDATE,tree=TREE,
            owner_token=OWNER,expires_at=int(time.time())+600,budget_units=budget,lanes=lanes)
        return generation

    def admit(self,generation,provider="claude"):
        return self.store.admit(ValidationRequest(generation=generation,provider=provider),OWNER)

    def completed(self):
        generation=self.generation()
        admission=self.admit(generation)
        self.store.claim_execution(admission)
        raw=raw_native()
        capture=make_capture(admission,raw,capture_key=KEY)
        evidence=self.store.settle(admission,raw,capture)
        verifier=CaptureVerifier(self.store,capture_key=KEY,candidate=CANDIDATE,tree=TREE,generation=generation)
        return admission,capture,evidence,verifier

    def _admitted_and_claimed(self):
        generation=self.generation()
        admission=self.admit(generation)
        self.store.claim_execution(admission)
        return generation,admission

    def test_claim_execution_reads_back_running_state_and_checkpoint(self):
        """Regression for a dropped fetchone(): claim_execution must actually
        read the locked row, not index the live cursor object, before it can
        decide the attempt is eligible and advance it to 'running'.

        A single running checkpoint at sequence 2 is the correct outcome; it
        is distinct from the admitted checkpoint at sequence 1, not a second
        copy of it.
        """
        generation,admission=self._admitted_and_claimed()
        with self.store.connect() as db:
            state=db.execute(f"SELECT state FROM {SCHEMA}.attempts WHERE reservation=%s",
                (admission["reservation"],)).fetchone()[0]
            checkpoints=db.execute(f"SELECT sequence,state FROM {SCHEMA}.checkpoints "
                "WHERE reservation=%s ORDER BY sequence",(admission["reservation"],)).fetchall()
        self.assertEqual("running",state)
        self.assertEqual([(1,"admitted"),(2,"running")],[tuple(row) for row in checkpoints])
        with self.assertRaises(ValidationError):
            self.store.claim_execution(admission)

    def _hold_lock(self, statement, parameters):
        import threading
        release=threading.Event();held=threading.Event()
        def blocker():
            with self.store.connect() as db:
                db.execute("SELECT set_config('statement_timeout','10000',true),set_config('lock_timeout','10000',true)")
                db.execute(statement,parameters).fetchone()
                held.set()
                release.wait(15)
        thread=threading.Thread(target=blocker,daemon=True)
        thread.start()
        self.assertTrue(held.wait(10))
        return release,thread

    def _bounded_deadline(self,started,budget_seconds=2.0):
        import time
        def remaining_seconds():
            remaining=budget_seconds-(time.monotonic()-started)
            if remaining<=0:
                raise ValidationError("shared validation deadline exhausted")
            return remaining
        return remaining_seconds

    def test_team_canary_uses_signed_worker_capture_and_only_validation_storage(self):
        import time
        from types import SimpleNamespace
        from tests.test_antigravity_team import grant
        from tests import test_entitlement_control_plane as fixtures
        from director_orchestrator.antigravity_team import SourceBlob
        from director_orchestrator.antigravity_team_worker import sign,DENIED
        from director_orchestrator.entitlement_contracts import document_digest
        from director_orchestrator.provider_validation_team import TeamValidationRoute,SYNTHETIC_SOURCE
        work=work_packet()
        work["project"]="uppercut"
        g=grant(task_id=work["task_id"],assignment_id=work["assignment_id"],project_id=work["project"],
            repository=work["repository"],commit=CANDIDATE,tree=TREE,packet_digest=digest(canonical_json_bytes(work)),
            operation="review",review_base=CANDIDATE,writable_paths=(),commands=(),
            binding_digest=digest(canonical_json_bytes(asdict(binding("antigravity")))) ,
            source=tuple(SourceBlob(path=p,sha256=digest(b)) for p,b in SYNTHETIC_SOURCE.items()))
        worker_key=b"w"*32
        calls=[]
        class Worker:
            def prepare(inner,**kw):
                inner.prepared=dict(kw,binding_digest=g.binding_digest,workspace_id=g.workspace_id,
                    containment_evidence_digest="d"*64,expires_at=g.expires_at.isoformat(),
                    denied_access=sorted(DENIED),authentication_mode="native-account")
                return sign(worker_key,inner.prepared)
            def exchange(inner,**kw):
                self.assertEqual(kw["source"],SYNTHETIC_SOURCE)
                kw["before_execute"]();calls.append(True)
                result=normalize_capture("claude",work,raw_native())
                result.update(provider="antigravity",checks=[])
                stream=[{"event":"init","conversation_id":"native-fixture","init":{"cwd":"/isolated/native","model":g.model}},
                    {"event":"result","result":{"conversation_id":"native-fixture","status":"SUCCESS",
                    "structured_output":{"packet":result,"returned_files":[]},"usage":{"total_tokens":1}}}]
                evidence={k:getattr(g,k) for k in ("installation_id","executable_digest","runtime_digest","adapter_digest","binding_digest","native_account_id","workspace_id")}
                evidence.update(grant_digest=g.digest,attempt_digest=inner.prepared["attempt_digest"],packet_digest=g.packet_digest,
                    native_project_id=g.native_project_id,native_identity_observations={"requested_project_id":g.native_project_id,"observed_project_id":None,"observed_account_id":None},
                    authentication_mode="native-account",cwd="/isolated/native",source_manifest_digest=document_digest({"source":[b.model_dump() for b in g.source]}),
                    containment_evidence_digest="d"*64,native_stream="\n".join(json.dumps(e) for e in stream),exit_code=0,
                    challenge=kw["challenge"],changed_files=[],returned_files=[],executed_commands=[],checks=[],
                    completed_at=datetime.now(timezone.utc).isoformat(),native_state_manifest_after={})
                return sign(worker_key,evidence)
            def close(inner):pass
        route=TeamValidationRoute(grant=g,owner_secret=fixtures.OWNER_SECRET,worker_secret=worker_key,
            worker_factory=lambda g:Worker(),configuration_digest=lambda:"c"*64)
        generation=secrets.token_hex(32)
        self.store.create_generation(generation=generation,candidate=CANDIDATE,tree=TREE,owner_token=OWNER,
            expires_at=int(time.time())+600,budget_units=2,lanes={"antigravity":{
            "binding":asdict(binding("antigravity")),"configuration_digest":"c"*64,"cost_units":2,"work_packet":work}})
        service=ValidationBridgeService(store=self.store,production_executor=None,capture_key=KEY,owner_token=OWNER,
            account_files={},team_route=route)
        request=ValidationRequest(generation=generation,provider="antigravity")
        result=service.run(request,OWNER)
        verifier=CaptureVerifier(self.store,capture_key=KEY,candidate=CANDIDATE,tree=TREE,generation=generation,team_route=route)
        self.assertTrue(verifier.verify(binding("antigravity"),result["evidence_digest"]))
        with self.assertRaisesRegex(ValidationError,"already admitted"):service.run(request,OWNER)
        self.assertEqual(len(calls),1)
        stored=self.store.load_capture(result["evidence_digest"])
        with self.assertRaisesRegex(ValidationError,"another reservation"):
            make_capture({"provider":"antigravity","packet":bytes(stored[1]),"reservation":"f"*64},bytes(stored[2]),capture_key=KEY,team_route=route)
        with patch.object(route,"worker_secret",b"bad"*12):
            self.assertFalse(verifier.verify(binding("antigravity"),result["evidence_digest"]))
        # The team route's current fingerprint must still equal the digest the
        # capture was minted under: a rotated worker configuration refuses.
        with patch.object(route,"configuration_digest",lambda:"d"*64):
            self.assertFalse(verifier.verify(binding("antigravity"),result["evidence_digest"]))
        self.assertTrue(verifier.verify(binding("antigravity"),result["evidence_digest"]))

    def test_capture_valid_and_reusable_for_two_surfaces_without_second_execution(self):
        admission,capture,evidence,verifier=self.completed()
        for surface in ProviderActivationSurface:
            self.assertTrue(verifier.verify(binding(),evidence))
        with self.assertRaisesRegex(ValidationError,"already admitted"):
            self.admit(admission["generation"])
        with self.store.connect() as db:
            self.assertEqual(db.execute(f"SELECT count(*) FROM {SCHEMA}.checkpoints WHERE reservation=%s AND namespace=%s",
                (admission["reservation"],CHECKPOINT_NAMESPACE)).fetchone()[0],3)

    def test_concurrent_admission_reserves_only_one_budget(self):
        generation=self.generation()
        def attempt(_):
            try:self.admit(generation);return True
            except ValidationError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt,range(2))),1)
        with self.store.connect() as db:
            self.assertEqual(db.execute(f"SELECT reserved_units FROM {SCHEMA}.generations WHERE generation=%s",
                (generation,)).fetchone()[0],2)

    def test_budget_shared_between_lanes(self):
        generation=self.generation(providers=("claude","grok"))
        self.admit(generation)
        with self.assertRaisesRegex(ValidationError,"budget exhausted"):
            self.admit(generation,"grok")

    def test_bad_owner_claims_nothing(self):
        generation=self.generation()
        with self.assertRaises(ValidationError):
            self.store.admit(ValidationRequest(generation=generation,provider="claude"),"bad-owner")
        self.admit(generation)

    def test_ambiguous_restart_never_redispatches(self):
        generation=self.generation();admission=self.admit(generation)
        self.store.claim_execution(admission)
        self.store.fail(admission["reservation"])
        restarted=PostgresValidationStore(self.store.dsn)
        with self.assertRaises(ValidationError):restarted.claim_execution(admission)
        with self.assertRaises(ValidationError):self.admit(generation)

    def test_wrong_key_binding_candidate_missing_capture_rejected(self):
        admission,capture,evidence,verifier=self.completed()
        self.assertFalse(verifier.verify(replace(binding(),app_version="changed"),evidence))
        self.assertFalse(verifier.verify(binding(),"f"*64))
        for key,candidate in (("wrong"*10,CANDIDATE),(KEY,"c"*40)):
            bad=CaptureVerifier(self.store,capture_key=key,candidate=candidate,tree=TREE,generation=admission["generation"])
            self.assertFalse(bad.verify(binding(),evidence))

    def test_capture_for_another_tree_never_qualifies(self):
        """Finding 1: the expected runtime tree is compared, not merely carried.

        A valid signed capture minted under the same candidate and generation
        for a DIFFERENT runtime tree must not qualify LIVE_OK for this one.
        """
        admission,capture,evidence,verifier=self.completed()
        self.assertEqual(TREE,capture.tree)
        self.assertTrue(verifier.verify(binding(),evidence))
        other=CaptureVerifier(self.store,capture_key=KEY,candidate=CANDIDATE,tree="c"*40,
                              generation=admission["generation"])
        self.assertFalse(other.verify(binding(),evidence))
        for bad in ("","b"*39,"B"*40,None):
            with self.subTest(tree=bad),self.assertRaisesRegex(ValueError,"runtime tree pin"):
                CaptureVerifier(self.store,capture_key=KEY,candidate=CANDIDATE,tree=bad,
                                generation=admission["generation"])

    def test_environment_verifier_pins_the_tree(self):
        import os
        from unittest.mock import patch
        env={"DIRECTOR_PROVIDER_VALIDATION_ENABLED":"true",
             "DIRECTOR_PROVIDER_VALIDATION_CANDIDATE":CANDIDATE,
             "DIRECTOR_PROVIDER_VALIDATION_TREE":TREE,
             "DIRECTOR_PROVIDER_VALIDATION_GENERATION":"0"*64,
             "DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN":self.store.dsn,
             "DIRECTOR_PROVIDER_VALIDATION_CAPTURE_KEY":KEY}
        with patch.dict(os.environ,env,clear=False):
            verifier=capture_verifier_from_environment(candidate=CANDIDATE,tree=TREE)
            self.assertEqual(TREE,verifier.tree)
            # The environment pin is authoritative: a caller tree that differs
            # from it, or a missing caller tree, is refused before any store
            # access rather than silently adopting either side.
            for caller in ("c"*40,""):
                with self.subTest(caller=caller),self.assertRaisesRegex(ValidationError,"tree differs from runtime pin"):
                    capture_verifier_from_environment(candidate=CANDIDATE,tree=caller)
        with patch.dict(os.environ,{**env,"DIRECTOR_PROVIDER_VALIDATION_TREE":"e"*40},clear=False):
            with self.assertRaisesRegex(ValidationError,"tree differs from runtime pin"):
                capture_verifier_from_environment(candidate=CANDIDATE,tree=TREE)
            self.assertEqual("e"*40,capture_verifier_from_environment(candidate=CANDIDATE,tree="e"*40).tree)

    def test_connection_acquisition_is_bounded_by_the_shared_deadline_not_the_driver(self):
        """Finding 3: a sub-second budget is enforced as a real outer deadline.

        libpq honours only whole-second connect timeouts of at least two
        seconds, so a remaining budget below that is not rounded upward for
        the driver to wait out: the acquisition is awaited for exactly the
        remaining budget, a slow connect fails closed at that point, the late
        connection is closed on arrival, and a connect that returns only
        after the deadline is refused on return instead of at the first
        statement. Within budget the driver receives the whole-second floor,
        capped at five seconds.
        """
        import threading
        opened=[];arrived=[]
        gate=threading.Event()
        class Store(PostgresValidationStore):
            slow=False
            def connect(self, connect_timeout=5):
                opened.append(connect_timeout)
                if self.slow:
                    gate.wait(3)
                connection=super().connect(connect_timeout=connect_timeout)
                arrived.append(connection)
                return connection
        store=Store(self.store.dsn)
        with self.assertRaisesRegex(ValidationError,"deadline exhausted before connecting"):
            store._connect_bounded(lambda: 0.0)
        self.assertEqual([],opened)
        # Sub-two-second budgets still connect, under the outer deadline.
        for budget in (0.5,1.0,1.9):
            with self.subTest(budget=budget),store._connect_bounded(lambda: budget) as db:
                self.assertEqual(1,db.execute("SELECT 1").fetchone()[0])
        self.assertEqual([2,2,2],opened)
        # A slow connect is cut off at the remaining budget, not at the
        # driver's rounded-up two seconds, and the late connection is closed.
        store.slow=True;opened.clear();arrived.clear()
        started=time.monotonic()
        with self.assertRaisesRegex(ValidationError,"deadline exhausted while connecting"):
            store._connect_bounded(lambda: 0.4)
        self.assertLess(time.monotonic()-started,1.5)
        self.assertEqual([2],opened)
        gate.set()
        deadline=time.monotonic()+5
        while len(arrived)<1 and time.monotonic()<deadline:
            time.sleep(0.05)
        time.sleep(0.1)
        self.assertEqual(1,len(arrived));self.assertTrue(arrived[0].closed)
        store.slow=False;opened.clear()
        # A connect that returns only after the deadline is refused on return
        # and the connection it opened is closed, not leaked.
        readings=iter([3.0])
        def remaining():
            try:
                return next(readings)
            except StopIteration:
                raise ValidationError("Responses validation timeout is exhausted")
        with self.assertRaisesRegex(ValidationError,"timeout is exhausted"):
            store._connect_bounded(remaining)
        self.assertEqual([3],opened);self.assertTrue(arrived[-1].closed)
        with store._connect_bounded(lambda: 2.6) as db:
            self.assertEqual(1,db.execute("SELECT 1").fetchone()[0])
        with store._connect_bounded(lambda: 90.0) as db:
            self.assertEqual(1,db.execute("SELECT 1").fetchone()[0])
        self.assertEqual([3,2,5],opened)

    def test_underlying_bytes_required_not_just_signature(self):
        admission,capture,evidence,verifier=self.completed()
        with self.store.connect() as db:
            db.execute(f"UPDATE {SCHEMA}.attempts SET output=%s WHERE reservation=%s",(b"{}",admission["reservation"]))
        self.assertFalse(verifier.verify(binding(),evidence))

    def test_revocation_between_admission_dispatch_and_capture_read(self):
        admission,capture,evidence,verifier=self.completed()
        with self.store.connect() as db:
            db.execute(f"UPDATE {SCHEMA}.generations SET revoked=true WHERE generation=%s",(admission["generation"],))
        self.assertFalse(verifier.verify(binding(),evidence))
        generation=self.generation();pending=self.admit(generation)
        with self.store.connect() as db:
            db.execute(f"UPDATE {SCHEMA}.generations SET revoked=true WHERE generation=%s",(generation,))
        with self.assertRaises(ValidationError):self.store.claim_execution(pending)

    def test_constructor_and_readback_do_not_bootstrap(self):
        store=PostgresValidationStore(self.store.dsn)
        self.assertIsNone(store.load_capture("f"*64))

    def test_shared_deadline_settlement_succeeds_without_contention(self):
        import time
        generation,admission=self._admitted_and_claimed()
        raw=raw_native();capture=make_capture(admission,raw,capture_key=KEY)
        started=time.monotonic()
        evidence=self.store.settle(admission,raw,capture,remaining_seconds=lambda:30.0)
        self.assertLess(time.monotonic()-started,10)
        verifier=CaptureVerifier(self.store,capture_key=KEY,candidate=CANDIDATE,tree=TREE,generation=generation)
        self.assertTrue(verifier.verify(binding(),evidence))

    def test_exhausted_deadline_refuses_before_any_store_effect(self):
        import time
        generation=self.generation()
        # A fully valid capture built from a synthetic admission: the settle
        # refusal below must be deadline evidence, never serialization noise.
        synthetic={"reservation":"e"*64,"nonce":"n"*64,"fence":1,
                   "generation":"a"*64,"provider":"claude","candidate":CANDIDATE,"tree":TREE,
                   "expires_at":int(time.time())+600,
                   "binding":asdict(binding()),"configuration_digest":"c"*64,
                   "packet":canonical_json_bytes(work_packet()),"owner_digest":"o"*64}
        valid_capture=make_capture(synthetic,raw_native(),capture_key=KEY)
        for call in (lambda: self.store.admit(ValidationRequest(generation=generation,provider="claude"),OWNER,
                            remaining_seconds=lambda:0.0),
                lambda: self.store.claim_execution({"reservation":"f"*64,"generation":generation,
                    "provider":"claude","nonce":"n"*64,"fence":1,"packet":b"{}"},
                    remaining_seconds=lambda:0.0),
                lambda: self.store.settle(synthetic,raw_native(),valid_capture,remaining_seconds=lambda:0.0)):
            with self.subTest(call=call),self.assertRaisesRegex(ValidationError,"deadline"):
                call()
        with self.store.connect() as db:
            self.assertEqual(0,db.execute(f"SELECT reserved_units FROM {SCHEMA}.generations WHERE generation=%s",
                (generation,)).fetchone()[0])
            self.assertIsNone(db.execute(f"SELECT 1 FROM {SCHEMA}.attempts WHERE generation=%s",
                (generation,)).fetchone())

    def test_expired_settle_deadline_refuses_before_serialization(self):
        import time
        generation,admission=self._admitted_and_claimed()
        capture=make_capture(admission,raw_native(),capture_key=KEY)
        started=time.monotonic()
        with self.assertRaisesRegex(ValidationError,"deadline"):
            self.store.settle(admission,raw_native(),capture,remaining_seconds=lambda:0.0)
        self.assertLess(time.monotonic()-started,10)
        with self.store.connect() as db:
            row=db.execute(f"SELECT state,capture_digest FROM {SCHEMA}.attempts WHERE reservation=%s",
                           (admission["reservation"],)).fetchone()
        self.assertEqual("running",row[0]);self.assertIsNone(row[1])

    def test_malformed_capture_fails_independently_of_the_deadline(self):
        generation,admission=self._admitted_and_claimed()
        # With a live budget the malformed capture fails on its own terms
        # (TypeError), which is exactly why it can never masquerade as
        # deadline evidence in the expired-budget regression above.
        with self.assertRaises(TypeError):
            self.store.settle(admission,b"{}",None)
        with self.store.connect() as db:
            row=db.execute(f"SELECT state,capture_digest FROM {SCHEMA}.attempts WHERE reservation=%s",
                           (admission["reservation"],)).fetchone()
        self.assertEqual("running",row[0]);self.assertIsNone(row[1])

    def test_admission_row_lock_is_bounded_and_reserves_nothing(self):
        import time
        generation=self.generation()
        release,thread=self._hold_lock(
            f"SELECT 1 FROM {SCHEMA}.generations WHERE generation=%s FOR UPDATE",(generation,))
        started=time.monotonic()
        try:
            with self.assertRaises(Exception) as caught:
                self.store.admit(ValidationRequest(generation=generation,provider="claude"),OWNER,
                    remaining_seconds=self._bounded_deadline(started))
            self.assertLess(time.monotonic()-started,10)
            self.assertNotIsInstance(caught.exception,ValidationError)
        finally:
            release.set();thread.join(10)
        with self.store.connect() as db:
            self.assertEqual(0,db.execute(f"SELECT reserved_units FROM {SCHEMA}.generations WHERE generation=%s",
                (generation,)).fetchone()[0])
            self.assertIsNone(db.execute(f"SELECT 1 FROM {SCHEMA}.attempts WHERE generation=%s",
                (generation,)).fetchone())

    def test_settlement_row_lock_is_bounded_and_rolls_back(self):
        import time
        generation,admission=self._admitted_and_claimed()
        raw=raw_native();capture=make_capture(admission,raw,capture_key=KEY)
        release,thread=self._hold_lock(
            f"SELECT 1 FROM {SCHEMA}.attempts WHERE reservation=%s FOR UPDATE",(admission["reservation"],))
        started=time.monotonic()
        try:
            with self.assertRaises(Exception):
                self.store.settle(admission,raw,capture,remaining_seconds=self._bounded_deadline(started))
            self.assertLess(time.monotonic()-started,10)
        finally:
            release.set();thread.join(10)
        with self.store.connect() as db:
            row=db.execute(f"SELECT state,capture_digest,output FROM {SCHEMA}.attempts WHERE reservation=%s",
                (admission["reservation"],)).fetchone()
        self.assertEqual("running",row[0]);self.assertIsNone(row[1]);self.assertIsNone(row[2])

    def test_commit_time_lock_cannot_persist_late_settlement(self):
        import time
        generation,admission=self._admitted_and_claimed()
        raw=raw_native();capture=make_capture(admission,raw,capture_key=KEY)
        release,thread=self._hold_lock(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,20260914))",(admission["reservation"],))
        started=time.monotonic()
        try:
            with self.assertRaises(Exception) as caught:
                self.store.settle(admission,raw,capture,remaining_seconds=self._bounded_deadline(started))
            self.assertLess(time.monotonic()-started,10)
            self.assertNotIsInstance(caught.exception,ValidationError)
        finally:
            release.set();thread.join(10)
        with self.store.connect() as db:
            row=db.execute(f"SELECT state,capture_digest FROM {SCHEMA}.attempts WHERE reservation=%s",
                (admission["reservation"],)).fetchone()
            succeeded=db.execute(f"SELECT count(*) FROM {SCHEMA}.checkpoints WHERE reservation=%s AND state='succeeded'",
                (admission["reservation"],)).fetchone()[0]
        self.assertNotEqual("succeeded",row[0]);self.assertIsNone(row[1]);self.assertEqual(0,succeeded)

    def _responses_service_fixture(self,*,transport_ceiling=60):
        """Real store and service; only the provider transport is synthetic.

        `transport_ceiling` bounds `maximum_timeout_seconds`. It defaults to a
        much wider value than the 2s packet ceiling so the claim/fence/settle
        contention cases below exercise the packet-ceiling tightening path.
        The admission contention case needs it equal to the packet ceiling
        instead: admission itself runs before that packet is even known, so
        it can only ever be bounded by the transport ceiling.
        """
        import time as time_module
        from types import SimpleNamespace
        from director_orchestrator.openai_responses_validation import OpenAIResponsesValidationRoute
        work=work_packet()
        work["limits"]["timeout_seconds"]=2
        packet=canonical_json_bytes(work)
        transport=SimpleNamespace(model="gpt-fixture-responses",
            api_url="https://api.openai.com/v1/responses",_api_key="sk-contention-fixture",
            maximum_timeout_seconds=transport_ceiling,response_meter=SimpleNamespace(enrollments={}))
        authority=MagicMock();authority.reserve.return_value=SimpleNamespace(token_limit=128)
        route=OpenAIResponsesValidationRoute(transport=transport,total_token_limit=128,
            expected_model=transport.model,meter_enrollment_digest="d"*64,
            expected_organization_id="org-fixture",expected_api_project_id="proj-fixture",
            canary_task_id=work["task_id"],canary_assignment_id=work["assignment_id"],
            canary_attempt_digest=work["idempotency_key"],
            canary_work_packet_digest="sha256:"+digest(packet),
            budget_authority=authority,budget_authority_digest="c"*64)
        generation=secrets.token_hex(32)
        self.store.create_generation(generation=generation,candidate=CANDIDATE,tree=TREE,
            owner_token=OWNER,expires_at=int(time_module.time())+600,budget_units=1,
            lanes={"openai":{"binding":asdict(binding("openai")),
                "configuration_digest":route.configuration_digest,"cost_units":1,"work_packet":work}})
        native=dict(object="response",id="resp-contention-fixture",
            created_at=int(time_module.time()),status="completed",error=None,
            incomplete_details=None,model="gpt-fixture-responses",service_tier="default",
            output=[dict(type="message",role="assistant",status="completed",
                content=[dict(type="output_text",text="Canary analyzed")])],
            usage=dict(input_tokens=12,output_tokens=4,total_tokens=16,
                input_tokens_details=dict(cached_tokens=0),
                output_tokens_details=dict(reasoning_tokens=0)))
        capture_bytes=canonical_json_bytes({"response":native,"response_headers":{
            "openai-organization":"org-fixture","openai-project":"proj-fixture",
            "x-request-id":"req-contention-fixture"}})
        sends=[]
        def send(raw,*,context):
            sends.append(raw)
            context.execution_observer("openai",capture_bytes,None)
        transport.send=send
        service=ValidationBridgeService(store=self.store,production_executor=None,
            capture_key=KEY,owner_token=OWNER,account_files={},openai_responses_route=route)
        return {"service":service,"generation":generation,"sends":sends}

    def _run_service_contention_case(self, phase):
        """Service-level cleanup under real SQL contention.

        A real PostgreSQL session holds the conflicting lock from inside the
        wrapped store call and keeps holding it across the primary refusal AND
        the except-path fail cleanup, so the SQL state and elapsed time witness
        real contention, not a mocked timeout. The blocker self-releases after
        3 seconds as a safety-net watchdog only: the 2s packet budget plus the
        1s scheduler grace is the whole boundary, and the cleanup allowance in
        PostgresValidationStore.fail is anchored to that same absolute
        deadline-plus-grace, not a second, fresh window stacked on top of it.
        Completion must land strictly inside 2s+1s.

        `threading.Event.wait` returns False on a timeout WITHOUT setting the
        event, so `release.is_set()` can never observe an automatic watchdog
        release; the blocker instead records whether its own wait returned via
        the watchdog (timeout) or the explicit release into `box["watchdog_fired"]`,
        which is witnessed twice: it must still be unset (None) immediately
        after the service call returns/raises (proving the watchdog had not
        yet fired when the primary assertions already ran), and it must be
        False once we join the thread after explicitly releasing it (proving
        that explicit release, not the watchdog, is what actually unblocked
        it). The joined thread's `is_alive()` is asserted False, and any error
        raised inside the blocker itself is recorded and asserted absent, not
        silently dropped. After draining, no late eligible success, no stored
        capture digest, and no duplicate dispatch may exist. The blocker
        thread/connection handle is recorded before it starts or waits, and
        any blocker setup failure is propagated (not silently swallowed) and
        still joined in a finally so a failed setup cannot leak a thread,
        connection or held lock.
        """
        import threading
        import time as time_module
        fixture=self._responses_service_fixture()
        box={"watchdog_fired":None,"errors":[]}
        def wrapped_factory(original):
            def wrapped(*args,**kwargs):
                if phase in ("claim_execution","settle"):
                    box["reservation"]=args[0]["reservation"]
                elif phase=="verify_fence":
                    box["reservation"]=args[0].task_id.removeprefix("VALIDATION-")
                release,held=threading.Event(),threading.Event()
                def blocker():
                    try:
                        with self.store.connect() as db:
                            db.execute("SELECT set_config('statement_timeout','15000',true),"
                                "set_config('lock_timeout','15000',true)")
                            if phase=="verify_fence":
                                db.execute(f"LOCK TABLE {SCHEMA}.attempts IN ACCESS EXCLUSIVE MODE")
                            else:
                                db.execute(f"SELECT 1 FROM {SCHEMA}.attempts WHERE reservation=%s FOR UPDATE",
                                           (box["reservation"],)).fetchone()
                            held.set()
                            # Self-release at 3s (2s packet budget + 1s
                            # scheduler grace) is a watchdog safety net; the
                            # service is expected to finish, and this test to
                            # call release.set() itself, well before this
                            # fires. Event.wait returns False on a timeout
                            # without setting the event, so the outcome is
                            # recorded explicitly rather than inferred from
                            # release.is_set().
                            released_explicitly=release.wait(3.0)
                            box["watchdog_fired"]=not released_explicitly
                    except BaseException as error:
                        box["errors"].append(error)
                        held.set()
                thread=threading.Thread(target=blocker,daemon=True)
                # Recorded before the thread starts or any wait, so a setup
                # failure below can still be released and joined.
                box["release"]=release;box["thread"]=thread
                thread.start()
                acquired=held.wait(10)
                if box["errors"]:
                    release.set();thread.join(5)
                    raise AssertionError(f"contention blocker setup failed: {box['errors'][0]!r}") from box["errors"][0]
                if not acquired:
                    release.set();thread.join(5)
                    raise AssertionError("contention blocker never acquired its lock")
                return original(*args,**kwargs)
            return wrapped
        try:
            with patch.object(self.store,phase,wrapped_factory(getattr(self.store,phase))):
                started=time_module.monotonic()
                with self.assertRaises(Exception) as caught:
                    fixture["service"].run(ValidationRequest(
                        generation=fixture["generation"],provider="openai"),OWNER)
                elapsed=time_module.monotonic()-started
            error=caught.exception
            sqlstate=getattr(error,"sqlstate",None)
            self.assertTrue(sqlstate in ("55P03","57014")
                            or "timeout" in str(error).lower()
                            or "deadline" in str(error).lower(),
                            f"unexpected refusal: {error!r}")
            # The 2s packet budget plus 1s scheduler grace is the whole
            # boundary; no extra cleanup budget may push completion past it.
            self.assertLess(elapsed,2.0+1.0,
                            f"service overran the 2s budget plus 1s scheduler grace: {elapsed:.2f}s")
            self.assertIsNone(box.get("watchdog_fired"),
                              "blocker watchdog already fired before the service completed")
            expected_sends=1 if phase=="settle" else 0
            self.assertEqual(expected_sends,len(fixture["sends"]),
                             "duplicate or missing dispatch under contention")
            box["release"].set();box["thread"].join(5)
            self.assertFalse(box["thread"].is_alive(),
                             "contention blocker thread still running after join")
            self.assertFalse(box["watchdog_fired"],
                             "blocker self-released via its watchdog, not the explicit release")
            self.assertFalse(box["errors"],f"contention blocker raised: {box['errors']}")
            with self.store.connect() as db:
                row=db.execute(f"SELECT state,capture_digest FROM {SCHEMA}.attempts WHERE reservation=%s",
                               (box["reservation"],)).fetchone()
                succeeded=db.execute(f"SELECT count(*) FROM {SCHEMA}.checkpoints WHERE reservation=%s "
                                     "AND state='succeeded'",(box["reservation"],)).fetchone()[0]
            # claim_execution's own SELECT ... FOR UPDATE blocks before it ever
            # writes 'running', so a claim-phase timeout must leave 'admitted'
            # (or the cleanup marker); later phases already reached 'running'
            # before their own contention, so they never regress to 'admitted'.
            expected_states=("admitted","ambiguous") if phase=="claim_execution" else ("running","ambiguous")
            self.assertIn(row[0],expected_states)
            self.assertIsNone(row[1])
            self.assertEqual(0,succeeded)
            with self.assertRaisesRegex(ValidationError,"already admitted"):
                fixture["service"].run(ValidationRequest(
                    generation=fixture["generation"],provider="openai"),OWNER)
        finally:
            release=box.get("release");thread=box.get("thread")
            if release is not None:
                release.set()
            if thread is not None:
                thread.join(5)

    def test_service_claim_timeout_blocks_dispatch_and_stays_reconcilable(self):
        self._run_service_contention_case("claim_execution")

    def test_service_fence_timeout_blocks_dispatch_within_cleanup_allowance(self):
        self._run_service_contention_case("verify_fence")

    def test_service_settle_timeout_keeps_no_late_eligible_success(self):
        self._run_service_contention_case("settle")

    def test_service_openai_responses_admission_timeout_reserves_nothing(self):
        """Full-service C3 admission case.

        Admission runs OUTSIDE the failure-cleanup try block in
        _run_openai_responses -- its own lock timeout is the only refusal; no
        ambiguity marker is ever written for an admission-phase failure -- so
        a blocked generations row lock during admit() must leave
        reserved_units at zero and create no attempt row at all, distinct
        from the claim/fence/settle cases above where an attempt already
        exists before their contention. The transport ceiling here equals the
        packet ceiling (2s) because admission happens before that packet is
        even read back, so admission can only ever be bounded by the
        transport ceiling, never by a later packet-ceiling tightening.
        """
        import threading
        import time as time_module
        fixture=self._responses_service_fixture(transport_ceiling=2)
        box={"watchdog_fired":None,"errors":[]}
        release,held=threading.Event(),threading.Event()
        def blocker():
            try:
                with self.store.connect() as db:
                    db.execute("SELECT set_config('statement_timeout','15000',true),"
                        "set_config('lock_timeout','15000',true)")
                    db.execute(f"SELECT 1 FROM {SCHEMA}.generations WHERE generation=%s FOR UPDATE",
                               (fixture["generation"],)).fetchone()
                    held.set()
                    released_explicitly=release.wait(3.0)
                    box["watchdog_fired"]=not released_explicitly
            except BaseException as error:
                box["errors"].append(error)
                held.set()
        thread=threading.Thread(target=blocker,daemon=True)
        box["release"]=release;box["thread"]=thread
        try:
            thread.start()
            acquired=held.wait(10)
            if box["errors"]:
                raise AssertionError(f"admission blocker setup failed: {box['errors'][0]!r}") from box["errors"][0]
            self.assertTrue(acquired,"admission contention blocker never acquired its lock")
            started=time_module.monotonic()
            with self.assertRaises(Exception) as caught:
                fixture["service"].run(ValidationRequest(
                    generation=fixture["generation"],provider="openai"),OWNER)
            elapsed=time_module.monotonic()-started
            error=caught.exception
            self.assertNotIsInstance(error,ValidationError)
            sqlstate=getattr(error,"sqlstate",None)
            self.assertTrue(sqlstate in ("55P03","57014") or "timeout" in str(error).lower(),
                            f"unexpected refusal: {error!r}")
            self.assertLess(elapsed,2.0+1.0,
                            f"admission overran the 2s budget plus 1s scheduler grace: {elapsed:.2f}s")
            self.assertIsNone(box.get("watchdog_fired"),
                              "blocker watchdog already fired before the service completed")
            self.assertFalse(fixture["sends"],"transport must never be reached on an admission timeout")
            release.set();thread.join(5)
            self.assertFalse(thread.is_alive(),"admission blocker thread still running after join")
            self.assertFalse(box["watchdog_fired"],
                             "blocker self-released via its watchdog, not the explicit release")
            self.assertFalse(box["errors"],f"admission blocker raised: {box['errors']}")
            with self.store.connect() as db:
                reserved=db.execute(f"SELECT reserved_units FROM {SCHEMA}.generations WHERE generation=%s",
                    (fixture["generation"],)).fetchone()[0]
                attempts=db.execute(f"SELECT count(*) FROM {SCHEMA}.attempts WHERE generation=%s",
                    (fixture["generation"],)).fetchone()[0]
            self.assertEqual(0,reserved)
            self.assertEqual(0,attempts)
        finally:
            release.set()
            thread.join(5)

    def test_service_openai_responses_deferred_commit_cannot_persist_late_settlement(self):
        """Full-service C3 deferred-COMMIT case.

        Reuses this class's qa_settle_commit_gate deferred constraint trigger
        (installed once in setUpClass): it takes the same advisory lock a
        blocker session holds, so settle()'s own SELECT/UPDATE/INSERT succeed
        but its implicit COMMIT blocks on the deferred trigger and is itself
        bounded by the shared deadline's lock_timeout (refreshed immediately
        before the implicit COMMIT), rolling back instead of persisting a
        late 'succeeded' state. The blocker only acquires the advisory lock
        once the reservation is known, from inside the wrapped claim_execution
        call -- the reservation does not exist until admission returns -- and
        holds it, uncontended with claim/fence/settle's own row locks, across
        the rest of the operation.
        """
        import threading
        import time as time_module
        fixture=self._responses_service_fixture()
        box={"watchdog_fired":None,"errors":[]}
        def wrapped_claim(original):
            def wrapped(*args,**kwargs):
                reservation=args[0]["reservation"]
                box["reservation"]=reservation
                release,held=threading.Event(),threading.Event()
                def blocker():
                    try:
                        with self.store.connect() as db:
                            db.execute("SELECT set_config('statement_timeout','15000',true),"
                                "set_config('lock_timeout','15000',true)")
                            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,20260914))",
                                       (reservation,)).fetchone()
                            held.set()
                            released_explicitly=release.wait(3.0)
                            box["watchdog_fired"]=not released_explicitly
                    except BaseException as error:
                        box["errors"].append(error)
                        held.set()
                thread=threading.Thread(target=blocker,daemon=True)
                box["release"]=release;box["thread"]=thread
                thread.start()
                acquired=held.wait(10)
                if box["errors"]:
                    release.set();thread.join(5)
                    raise AssertionError(f"commit blocker setup failed: {box['errors'][0]!r}") from box["errors"][0]
                if not acquired:
                    release.set();thread.join(5)
                    raise AssertionError("commit contention blocker never acquired its advisory lock")
                return original(*args,**kwargs)
            return wrapped
        try:
            with patch.object(self.store,"claim_execution",wrapped_claim(self.store.claim_execution)):
                started=time_module.monotonic()
                with self.assertRaises(Exception) as caught:
                    fixture["service"].run(ValidationRequest(
                        generation=fixture["generation"],provider="openai"),OWNER)
                elapsed=time_module.monotonic()-started
            error=caught.exception
            self.assertNotIsInstance(error,ValidationError)
            sqlstate=getattr(error,"sqlstate",None)
            self.assertTrue(sqlstate in ("55P03","57014") or "timeout" in str(error).lower(),
                            f"unexpected refusal: {error!r}")
            self.assertLess(elapsed,2.0+1.0,
                            f"deferred-COMMIT overran the 2s budget plus 1s scheduler grace: {elapsed:.2f}s")
            self.assertIsNone(box.get("watchdog_fired"),
                              "blocker watchdog already fired before the service completed")
            self.assertEqual(1,len(fixture["sends"]),
                             "COMMIT-time contention must still have dispatched exactly once")
            box["release"].set();box["thread"].join(5)
            self.assertFalse(box["thread"].is_alive(),"commit blocker thread still running after join")
            self.assertFalse(box["watchdog_fired"],
                             "blocker self-released via its watchdog, not the explicit release")
            self.assertFalse(box["errors"],f"commit blocker raised: {box['errors']}")
            with self.store.connect() as db:
                row=db.execute(f"SELECT state,capture_digest FROM {SCHEMA}.attempts WHERE reservation=%s",
                               (box["reservation"],)).fetchone()
                succeeded=db.execute(f"SELECT count(*) FROM {SCHEMA}.checkpoints WHERE reservation=%s "
                                     "AND state='succeeded'",(box["reservation"],)).fetchone()[0]
            self.assertNotEqual("succeeded",row[0])
            self.assertIsNone(row[1])
            self.assertEqual(0,succeeded)
        finally:
            release=box.get("release");thread=box.get("thread")
            if release is not None:
                release.set()
            if thread is not None:
                thread.join(5)

    def test_service_openai_responses_control_uncontended_success(self):
        """The C3 control case: an uncontended run through the real service,
        store and capture verifier reaches 'succeeded' with exactly one
        transport call and a verifiable evidence digest. This is the baseline
        the claim/fence/settle contention cases above are refused against.
        """
        fixture=self._responses_service_fixture()
        result=fixture["service"].run(ValidationRequest(
            generation=fixture["generation"],provider="openai"),OWNER)
        self.assertTrue(result.get("evidence_digest"))
        self.assertEqual(1,len(fixture["sends"]))
        verifier=CaptureVerifier(self.store,capture_key=KEY,candidate=CANDIDATE,tree=TREE,
            generation=fixture["generation"],openai_responses_route=fixture["service"].openai_responses_route)
        binding=asdict(ProviderActivationBinding("openai","workspace","v1","v1","v1","v1","v1"))
        self.assertTrue(verifier.verify(ProviderActivationBinding(**binding),result["evidence_digest"]))
        # Finding 2: the signed capture pins the Responses configuration it
        # was minted under; a route whose credential, endpoint or timeout
        # ceiling has since rotated no longer qualifies it, even though the
        # retained response re-normalises identically.
        route=fixture["service"].openai_responses_route
        for attribute,value in (("_api_key","sk-rotated"),("api_url","https://api.example.test/v1"),
                                ("maximum_timeout_seconds",route.transport.maximum_timeout_seconds+1)):
            with self.subTest(rotated=attribute):
                original=getattr(route.transport,attribute)
                setattr(route.transport,attribute,value)
                try:
                    self.assertFalse(verifier.verify(ProviderActivationBinding(**binding),result["evidence_digest"]))
                finally:
                    setattr(route.transport,attribute,original)
        self.assertTrue(verifier.verify(ProviderActivationBinding(**binding),result["evidence_digest"]))
        with self.store.connect() as db:
            row=db.execute(f"SELECT state FROM {SCHEMA}.attempts WHERE generation=%s",
                (fixture["generation"],)).fetchone()
            checkpoints=[r[0] for r in db.execute(f"SELECT c.state FROM {SCHEMA}.checkpoints c "
                f"JOIN {SCHEMA}.attempts a USING(reservation) WHERE a.generation=%s ORDER BY c.sequence",
                (fixture["generation"],)).fetchall()]
        self.assertEqual("succeeded",row[0])
        self.assertEqual(["admitted","running","succeeded"],checkpoints)
        with self.assertRaisesRegex(ValidationError,"already admitted"):
            fixture["service"].run(ValidationRequest(
                generation=fixture["generation"],provider="openai"),OWNER)

    def test_real_subprocess_capture_uses_dedicated_fence_and_no_production_writer(self):
        from director_orchestrator.host_provider_bridge import WindowsHostProviderExecutor
        import time
        with tempfile.TemporaryDirectory() as directory:
            account=Path(directory)/"synthetic-account-pin"
            account.write_text("synthetic fixture account, not a real credential")
            native=Path(directory)/"native_fixture.py"
            native.write_text("import sys\nsys.stdin.buffer.read()\nprint("+repr(raw_native().decode())+")\n")
            # The configured production executor would reject all live calls:
            # no production writer verifier is configured in this fixture.
            production=WindowsHostProviderExecutor({"claude":(str(Path(sys.executable).resolve(strict=True)),str(native))},cwd=directory)
            config=production_configuration_digest(production,"claude",account_files=(str(account),))
            generation=secrets.token_hex(32)
            self.store.create_generation(generation=generation,candidate=CANDIDATE,tree=TREE,owner_token=OWNER,
                expires_at=int(time.time())+600,budget_units=1,lanes={"claude":{
                    "binding":asdict(binding()),"configuration_digest":config,"cost_units":1,"work_packet":work_packet()}})
            service=ValidationBridgeService(store=self.store,production_executor=production,
                capture_key=KEY,owner_token=OWNER,account_files={"claude":(str(account),)})
            result=service.run(ValidationRequest(generation=generation,provider="claude"),OWNER)
            self.assertEqual(result["surfaces"],["app","runtime_adapter"])
            verifier=CaptureVerifier(self.store,capture_key=KEY,candidate=CANDIDATE,tree=TREE,generation=generation)
            self.assertTrue(verifier.verify(binding(),result["evidence_digest"]))
            self.assertIsNone(production.writer_fence_verifier)
            with self.assertRaises(ValidationError):
                service.run(ValidationRequest(generation=generation,provider="claude"),OWNER)

    def test_configuration_drift_prevents_any_native_io(self):
        from director_orchestrator.host_provider_bridge import WindowsHostProviderExecutor
        generation=self.generation()
        with tempfile.TemporaryDirectory() as directory:
            account=Path(directory)/"synthetic-auth";account.write_text("fixture")
            executor=WindowsHostProviderExecutor({"claude":(str(Path(sys.executable).resolve(strict=True)),"-c","pass")},cwd=directory)
            service=ValidationBridgeService(store=self.store,production_executor=executor,capture_key=KEY,
                owner_token=OWNER,account_files={"claude":(str(account),)})
            with patch.object(WindowsHostProviderExecutor,"execute") as execute:
                with self.assertRaisesRegex(ValidationError,"binding drift"):
                    service.run(ValidationRequest(generation=generation,provider="claude"),OWNER)
                execute.assert_not_called()


if __name__=="__main__":unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | END]]

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:50 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 13:05 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:40 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 08:56 UTC | END]]
# [[Z.ai | GLM-5.3-Flash | 2026-09-14 | 18:52 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:41 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 20:52 UTC | END]]
# [[Claude Code | Claude Sonnet 5 high | 2026-09-14 | 21:26 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-20 | 06:01 UTC | END]]
