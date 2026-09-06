# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | BEGIN]]
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict,replace
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from unittest.mock import patch
import unittest

from director_orchestrator.packet_contracts import canonical_json_bytes
from director_orchestrator.provider_activation import ProviderActivationBinding,ProviderActivationStage,ProviderActivationSurface
from director_orchestrator.primary_provider_activation import PrimaryProviderActivationService
from director_orchestrator.provider_validation import (
    SCHEMA,CHECKPOINT_NAMESPACE,PostgresValidationStore,ValidationError,ValidationRequest,
    CaptureVerifier,make_capture,digest,normalize_capture,
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
        verifier=CaptureVerifier(self.store,capture_key=KEY,candidate=CANDIDATE,generation=generation)
        return admission,capture,evidence,verifier

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
            bad=CaptureVerifier(self.store,capture_key=key,candidate=candidate,generation=admission["generation"])
            self.assertFalse(bad.verify(binding(),evidence))

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
            production=WindowsHostProviderExecutor({"claude":(sys.executable,str(native))},cwd=directory)
            config=production_configuration_digest(production,"claude",account_files=(str(account),))
            generation=secrets.token_hex(32)
            self.store.create_generation(generation=generation,candidate=CANDIDATE,tree=TREE,owner_token=OWNER,
                expires_at=int(time.time())+600,budget_units=1,lanes={"claude":{
                    "binding":asdict(binding()),"configuration_digest":config,"cost_units":1,"work_packet":work_packet()}})
            service=ValidationBridgeService(store=self.store,production_executor=production,
                capture_key=KEY,owner_token=OWNER,account_files={"claude":(str(account),)})
            result=service.run(ValidationRequest(generation=generation,provider="claude"),OWNER)
            self.assertEqual(result["surfaces"],["app","runtime_adapter"])
            verifier=CaptureVerifier(self.store,capture_key=KEY,candidate=CANDIDATE,generation=generation)
            self.assertTrue(verifier.verify(binding(),result["evidence_digest"]))
            self.assertIsNone(production.writer_fence_verifier)
            with self.assertRaises(ValidationError):
                service.run(ValidationRequest(generation=generation,provider="claude"),OWNER)

    def test_configuration_drift_prevents_any_native_io(self):
        from director_orchestrator.host_provider_bridge import WindowsHostProviderExecutor
        generation=self.generation()
        with tempfile.TemporaryDirectory() as directory:
            account=Path(directory)/"synthetic-auth";account.write_text("fixture")
            executor=WindowsHostProviderExecutor({"claude":(sys.executable,"-c","pass")},cwd=directory)
            service=ValidationBridgeService(store=self.store,production_executor=executor,capture_key=KEY,
                owner_token=OWNER,account_files={"claude":(str(account),)})
            with patch.object(WindowsHostProviderExecutor,"execute") as execute:
                with self.assertRaisesRegex(ValidationError,"binding drift"):
                    service.run(ValidationRequest(generation=generation,provider="claude"),OWNER)
                execute.assert_not_called()


if __name__=="__main__":unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:25 UTC | END]]
