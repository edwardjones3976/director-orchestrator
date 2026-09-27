# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | BEGIN]]
"""Z.ai production enrollment: offline tests only; nothing here contacts Z.ai."""
import json
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from director_orchestrator.config import Settings
from director_orchestrator.langgraph_runtime import ProviderAdapterContext
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.primary_transports import (
    NativeCommandPacketTransport,
    discover_local_primary_command_transports,
)
from director_orchestrator.zai_native import (
    TEST_COMMAND_PREFIXES,
    ZaiNativeConfig,
    ZaiNativePacketTransport,
    tool_arguments,
    verify_terminal_envelope,
    zai_usage,
)
from director_orchestrator.zai_trial import ENDPOINT

ROOT = Path(__file__).resolve().parents[1]
SECRET = "zai.test.secret"
MODEL = "glm-5.3"
ACTIVATION = "sha256:" + "a" * 64


def work_packet(**updates) -> dict:
    work = json.loads((ROOT / "docs/schemas/model-work-packet-v1.example.json").read_text(encoding="utf-8"))
    work.update(allowed_tools=["READ_REPOSITORY"], allowed_external_effects=[],
                owned_paths=["src/app"], prohibited_paths=["secrets"])
    work.update(updates)
    return work


def context_for(work: dict, **overrides) -> ProviderAdapterContext:
    fields = dict(
        task_id=work["task_id"], assignment_id=work["assignment_id"], idempotency_key=work["idempotency_key"],
        work_packet_digest=document_digest(work), writer_token_digest="sha256:" + "0" * 64,
        timeout_seconds=600, budget_usd=1.0, max_hops=1, max_revisions=1,
        allowed_tools=tuple(work["allowed_tools"]),
        allowed_external_effects=tuple(work["allowed_external_effects"]),
        fencing_generation=1,
    )
    fields.update(overrides)
    return ProviderAdapterContext(**fields)


def native_envelope(**updates) -> dict:
    envelope = {
        "type": "result", "subtype": "success", "is_error": False, "session_id": "zai-session-1",
        "num_turns": 3, "stop_reason": "end_turn",
        "result": json.dumps({"outcome": "Reviewed the diff; two findings.", "status": "SUCCEEDED"}),
        "usage": {"input_tokens": 120, "output_tokens": 40, "cache_read_input_tokens": 10},
        "modelUsage": {MODEL: {"inputTokens": 120, "outputTokens": 40, "cacheReadInputTokens": 10,
                               "cacheCreationInputTokens": 0, "costUSD": 0.5}},
        # Anthropic-tariff estimate from the host CLI: never Z.ai's price.
        "total_cost_usd": 0.5,
    }
    envelope.update(updates)
    return envelope


class ConfigFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.config = ZaiNativeConfig(executable=Path(sys.executable), api_key=SECRET, model=MODEL,
                                      max_turns=12, timeout_seconds=900)


class ConfigTests(ConfigFixture):
    def test_wrong_endpoint_model_key_executable_or_bounds_are_refused(self):
        self.config.validate()
        for change in (dict(endpoint="https://api.anthropic.com"), dict(endpoint=ENDPOINT + "/v2"),
                       dict(model="claude-opus-5"), dict(model="GLM 5"), dict(api_key=""),
                       dict(api_key="has space"), dict(executable=Path("claude.exe")),
                       dict(executable=self.root / "missing.exe"), dict(max_turns=0),
                       dict(max_turns=101), dict(timeout_seconds=10), dict(timeout_seconds=7200)):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    replace(self.config, **change).validate()
                with self.assertRaises(ValueError):
                    ZaiNativePacketTransport(replace(self.config, **change), cwd=self.workspace)

    def test_generic_native_transport_cannot_serve_the_zai_lane(self):
        # Only the dedicated transport binds Z.ai's endpoint, key and model.
        with self.assertRaises(ValueError):
            NativeCommandPacketTransport("zai", (sys.executable,), cwd=self.workspace)


class ToolBoundaryTests(unittest.TestCase):
    def args(self, work, **context_overrides):
        return tool_arguments(work, context_for(work, **context_overrides), allow_repository_write=False)

    @staticmethod
    def values(arguments, flag):
        start = arguments.index(flag) + 1
        end = next((i for i in range(start, len(arguments)) if arguments[i].startswith("--")), len(arguments))
        return list(arguments[start:end])

    def test_read_only_grant_plans_with_read_tools_and_denies_secrets_and_prohibited_paths(self):
        arguments = self.args(work_packet())
        self.assertEqual(self.values(arguments, "--tools"), ["Read,Grep,Glob"])
        self.assertEqual(self.values(arguments, "--permission-mode"), ["plan"])
        denied = self.values(arguments, "--disallowedTools")
        for rule in ("Read(./.env)", "Read(./**/.env.*)", "Edit(./.git/**)", "Read(./secrets)", "Read(./secrets/**)",
                     "Write(./secrets/**)"):
            self.assertIn(rule, denied)
        self.assertNotIn("Edit", self.values(arguments, "--tools")[0])

    def test_no_grant_means_no_tools(self):
        arguments = self.args(work_packet(allowed_tools=[]))
        self.assertEqual(self.values(arguments, "--tools"), [""])
        self.assertNotIn("--allowedTools", arguments)

    def test_run_tests_allows_only_fixed_test_runners(self):
        arguments = self.args(work_packet(allowed_tools=["READ_REPOSITORY", "RUN_TESTS"]))
        allowed = self.values(arguments, "--allowedTools")
        self.assertEqual([rule for rule in allowed if rule.startswith("Bash")],
                         [f"Bash({prefix}:*)" for prefix in TEST_COMMAND_PREFIXES])
        self.assertNotIn("Bash", allowed)

    def test_public_research_grant_adds_only_web_tools(self):
        arguments = self.args(work_packet(allowed_tools=["PUBLIC_WEB_SEARCH"]))
        self.assertEqual(self.values(arguments, "--tools"), ["WebSearch,WebFetch"])

    def test_writes_need_the_deployment_switch_and_stay_inside_owned_paths(self):
        work = work_packet(allowed_tools=["READ_REPOSITORY", "WRITE_ISOLATED_BRANCH"])
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            self.args(work)
        arguments = tool_arguments(work, context_for(work), allow_repository_write=True)
        allowed = self.values(arguments, "--allowedTools")
        self.assertEqual([rule for rule in allowed if rule.startswith(("Edit", "Write"))],
                         ["Edit(./src/app)", "Edit(./src/app/**)", "Write(./src/app)", "Write(./src/app/**)"])
        # Default mode refuses every unlisted edit in print mode; never a bypass.
        self.assertEqual(self.values(arguments, "--permission-mode"), ["default"])
        no_owned = work_packet(allowed_tools=["WRITE_ISOLATED_BRANCH"], owned_paths=[])
        with self.assertRaisesRegex(RuntimeError, "owned paths"):
            tool_arguments(no_owned, context_for(no_owned), allow_repository_write=True)

    def test_refusals_before_launch(self):
        cases = {
            "unsupported": work_packet(allowed_tools=["OPEN_DRAFT_PULL_REQUEST"]),
            "external effects": work_packet(allowed_external_effects=["WRITE_ISOLATED_BRANCH"]),
            "repository-relative": work_packet(prohibited_paths=["../outside"]),
            "unsupported character": work_packet(owned_paths=["src/has space"]),
        }
        for reason, work in cases.items():
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(RuntimeError, reason):
                    tool_arguments(work, context_for(work), allow_repository_write=True)
        work = work_packet()
        with self.assertRaisesRegex(RuntimeError, "differ"):
            tool_arguments(work, context_for(work, allowed_tools=("READ_REPOSITORY", "RUN_TESTS")),
                           allow_repository_write=False)


class EnvelopeTests(unittest.TestCase):
    def test_only_a_complete_successful_run_by_the_configured_glm_model_passes(self):
        verify_terminal_envelope(native_envelope(), model=MODEL, max_turns=12)
        rejected = {
            "error subtype": native_envelope(subtype="error_max_turns"),
            "is_error": native_envelope(is_error=True),
            "missing is_error": {k: v for k, v in native_envelope().items() if k != "is_error"},
            "not a result": native_envelope(type="assistant"),
            "truncated": native_envelope(stop_reason="max_tokens"),
            "over turns": native_envelope(num_turns=13),
            "no model evidence": {k: v for k, v in native_envelope().items() if k != "modelUsage"},
            "empty model evidence": native_envelope(modelUsage={}),
            "claude served it": native_envelope(modelUsage={"claude-opus-5": {}}),
            "mixed models": native_envelope(modelUsage={MODEL: {}, "glm-5.3-flash": {}}),
        }
        for reason, envelope in rejected.items():
            with self.subTest(reason=reason):
                with self.assertRaises(RuntimeError):
                    verify_terminal_envelope(envelope, model=MODEL, max_turns=12)

    def test_usage_is_native_only_and_cost_is_unknown_never_zero(self):
        usage = zai_usage(native_envelope(), model=MODEL)
        self.assertEqual(usage, {"model": MODEL, "input_tokens": 120, "output_tokens": 40,
                                 "total_tokens": 170, "cost_usd": None})
        for envelope in ({k: v for k, v in native_envelope().items() if k != "usage"},
                         native_envelope(usage={"input_tokens": 1}),
                         native_envelope(usage={"input_tokens": -1, "output_tokens": 1}),
                         native_envelope(usage={"input_tokens": True, "output_tokens": 1})):
            with self.subTest(envelope=envelope.get("usage")):
                self.assertIsNone(zai_usage(envelope, model=MODEL))


class TransportTests(ConfigFixture):
    def setUp(self):
        super().setUp()
        self.transport = ZaiNativePacketTransport(self.config, cwd=self.workspace)
        self.calls = []

    def fake_run(self, stdout: bytes, returncode: int = 0):
        def run(argv, *, cwd, environment, input_bytes, cutoff, what):
            self.calls.append({"argv": list(argv), "cwd": cwd, "env": dict(environment), "input": input_bytes})
            return subprocess.CompletedProcess(argv, returncode, stdout, b"")
        return patch("director_orchestrator.zai_native._run_bounded_command", side_effect=run)

    def send(self, envelope=None, *, work=None, returncode=0, **context_overrides):
        # READ_REPOSITORY alone is the small-task class, which native lanes never serve.
        work = work or work_packet(allowed_tools=["READ_REPOSITORY", "RUN_TESTS"])
        stdout = json.dumps(envelope if envelope is not None else native_envelope()).encode()
        with self.fake_run(stdout, returncode):
            return self.transport.send(json.dumps(work).encode(), context=context_for(work, **context_overrides))

    def test_child_receives_only_zai_credentials_and_an_attempt_scoped_home(self):
        leaked = {"ANTHROPIC_API_KEY": "host-anthropic", "CLAUDE_CODE_OAUTH_TOKEN": "host-claude",
                  "OPENAI_API_KEY": "host-openai", "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
                  "ZAI_API_KEY": "host-zai-key-not-the-configured-one", "CLAUDE_CONFIG_DIR": "C:/host/.claude"}
        with patch.dict(os.environ, leaked):
            result = self.send()
        env = self.calls[0]["env"]
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], SECRET)
        self.assertEqual(env["ANTHROPIC_BASE_URL"], ENDPOINT)
        for name in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                     "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_SMALL_FAST_MODEL"):
            self.assertEqual(env[name], MODEL)
        for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "ZAI_API_KEY"):
            self.assertNotIn(name, env)
        home = env["HOME"]
        self.assertEqual(env["USERPROFILE"], home)
        self.assertTrue(env["CLAUDE_CONFIG_DIR"].startswith(home))
        self.assertIn("director-zai-", home)
        self.assertFalse(Path(home).exists(), "the attempt home is removed after the run")
        self.assertEqual(env["DIRECTOR_PROVIDER_ATTEMPT_BUDGET_USD"], "1.0")
        self.assertEqual(result["provider"], "zai")

    def test_argv_is_finite_isolated_and_never_bypasses_permissions(self):
        self.send()
        argv = self.calls[0]["argv"]
        self.assertEqual(Path(argv[0]), Path(sys.executable).resolve())
        self.assertEqual(argv[argv.index("--max-turns") + 1], "12")
        self.assertEqual(argv[argv.index("--model") + 1], MODEL)
        self.assertEqual(argv[argv.index("--mcp-config") + 1], '{"mcpServers":{}}')
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--no-session-persistence", argv)
        joined = " ".join(argv).lower()
        for forbidden in ("bypasspermissions", "dangerously", "--add-dir", "--resume", "--continue"):
            self.assertNotIn(forbidden, joined)
        self.assertEqual(self.calls[0]["cwd"], self.workspace.resolve())
        self.assertIn(b"untrusted task data", self.calls[0]["input"])

    def test_successful_result_keeps_the_glm_identity_and_unknown_cost(self):
        result = self.send()
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["usage"]["model"], MODEL)
        self.assertIsNone(result["usage"]["cost_usd"])
        self.assertTrue(any("cost is UNKNOWN" in risk for risk in result["risks"]))
        self.assertEqual(result["run_reference"], "zai-session-1")

    def test_missing_usage_is_never_a_success(self):
        result = self.send({k: v for k, v in native_envelope().items() if k != "usage"})
        self.assertEqual(result["status"], "FAILED")
        self.assertIsNone(result["usage"])
        self.assertTrue(any("UNKNOWN" in blocker for blocker in result["blockers"]))

    def test_mismatch_error_truncation_or_nonzero_exit_is_rejected(self):
        for envelope in (native_envelope(modelUsage={"claude-sonnet-5": {}}), native_envelope(is_error=True),
                         native_envelope(stop_reason="max_tokens")):
            with self.subTest(envelope=envelope.get("modelUsage"), stop=envelope.get("stop_reason")):
                with self.assertRaises(RuntimeError):
                    self.send(envelope)
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            self.send(returncode=1)

    def test_reported_external_effects_are_rejected(self):
        effect = {"kind": "WRITE_ISOLATED_BRANCH", "target": "x", "status": "COMPLETED", "external_id": None}
        envelope = native_envelope(result=json.dumps(
            {"outcome": "pushed", "status": "SUCCEEDED", "side_effects": [effect]}))
        with self.assertRaisesRegex(RuntimeError, "external effects"):
            self.send(envelope)

    def test_pre_launch_refusals_never_start_a_process(self):
        with self.assertRaisesRegex(RuntimeError, "fencing"):
            self.send(fencing_generation=0)
        with self.assertRaisesRegex(RuntimeError, "external effects"):
            self.send(work=work_packet(allowed_external_effects=["WRITE_ISOLATED_BRANCH"]))
        with self.assertRaisesRegex(RuntimeError, "unsupported tool"):
            self.send(work=work_packet(allowed_tools=["OPEN_DRAFT_PULL_REQUEST"]))
        self.assertEqual(self.calls, [])

    def test_key_echoed_by_the_cli_is_redacted_before_evidence(self):
        observed = []
        envelope = native_envelope(result=json.dumps({"outcome": f"token {SECRET} seen", "status": "SUCCEEDED"}))
        result = self.send(envelope, execution_observer=lambda *args: observed.append(args))
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn(SECRET.encode(), observed[0][1])
        self.assertEqual(observed[0][0], "zai")


class DiscoveryAndSettingsTests(ConfigFixture):
    def production_settings(self, **changes) -> Settings:
        fields = dict(
            zai_mode="production", zai_enabled=True, zai_production_activation=ACTIVATION,
            zai_api_key=SECRET, zai_api_base=ENDPOINT, zai_model=MODEL, zai_cli_executable=sys.executable,
            zai_max_turns=12, zai_production_timeout_seconds=900, zai_trial_enabled=False,
            zai_trial_ledger_path="", zai_allow_repository_write=False)
        fields.update(changes)
        return replace(Settings(), **fields)

    def test_disabled_enrollment_is_closed_and_discovers_nothing(self):
        closed = replace(Settings(), zai_mode="trial_only", zai_enabled=False, zai_api_key="",
                         zai_production_activation="", zai_cli_executable="")
        self.assertIsNone(closed.zai_native_config())
        self.assertFalse(closed.readiness()["zai"])
        readiness = closed.zai_production_readiness()
        self.assertTrue(all(isinstance(value, bool) for value in readiness.values()))
        self.assertFalse(readiness["enabled"])
        self.assertFalse(readiness["production_mode"])
        # The executable on PATH is the Claude lane; it never becomes Z.ai.
        with patch("director_orchestrator.primary_transports._which", return_value=sys.executable):
            transports, capabilities = discover_local_primary_command_transports(
                cwd=self.workspace, authenticated={"zai": True}, zai=closed.zai_native_config())
        self.assertNotIn("zai", transports)
        self.assertFalse(capabilities["zai"].callable)
        self.assertNotIsInstance(transports.get("claude"), ZaiNativePacketTransport)

    def test_enabled_enrollment_discovers_only_the_dedicated_transport(self):
        settings = self.production_settings()
        self.assertTrue(all(settings.zai_production_readiness().values()))
        self.assertTrue(settings.readiness()["zai"])
        settings.validate_zai_trial_configuration()
        with patch("director_orchestrator.primary_transports._which", return_value=None):
            transports, capabilities = discover_local_primary_command_transports(
                cwd=self.workspace, authenticated={"zai": True}, zai=settings.zai_native_config())
        self.assertIsInstance(transports["zai"], ZaiNativePacketTransport)
        self.assertTrue(capabilities["zai"].callable)
        self.assertEqual(capabilities["zai"].backend_identity, "zai")
        self.assertEqual(capabilities["zai"].transport_kind, "zai-native-cli")
        self.assertNotIn(SECRET, json.dumps(settings.zai_production_readiness()))

    def test_each_production_gate_closes_the_lane(self):
        cases = {
            "enabled": dict(zai_enabled=False),
            "production_mode": dict(zai_mode="trial_only"),
            "activation_evidence_recorded": dict(zai_production_activation="approved"),
            "credential_configured": dict(zai_api_key=""),
            "official_endpoint": dict(zai_api_base="https://api.anthropic.com"),
            "glm_model_configured": dict(zai_model="claude-opus-5"),
            "cli_configured": dict(zai_cli_executable="claude.exe"),
            "max_turns_valid": dict(zai_max_turns=0),
            "timeout_valid": dict(zai_production_timeout_seconds=5),
        }
        for gate, change in cases.items():
            with self.subTest(gate=gate):
                settings = self.production_settings(**change)
                self.assertFalse(settings.zai_production_readiness()[gate])
                self.assertIsNone(settings.zai_native_config())

    def test_production_startup_validation(self):
        with self.assertRaisesRegex(ValueError, "ZAI_PRODUCTION_ACTIVATION"):
            self.production_settings(zai_production_activation="").validate_zai_trial_configuration()
        with self.assertRaisesRegex(ValueError, "ZAI_TRIAL_ENABLED"):
            self.production_settings(zai_trial_enabled=True).validate_zai_trial_configuration()
        with self.assertRaisesRegex(ValueError, "ZAI_API_BASE"):
            self.production_settings(zai_api_base="https://example.invalid").validate_zai_trial_configuration()

    def test_budget_and_cost_rank_names_accept_the_lane(self):
        settings = replace(Settings(), langgraph_primary_provider_attempt_budgets_usd_json='{"zai": 0.5}',
                           trusted_provider_cost_ranks_json='{"zai": 1, "openai": 2}')
        self.assertEqual(str(settings.primary_provider_attempt_budgets_usd()["zai"]), "0.5")
        self.assertEqual(settings.trusted_provider_cost_ranks()["zai"], 1)
        self.assertIn("zai", Settings().langgraph_primary_required_providers)


class RosterAndIndependenceTests(unittest.TestCase):
    RANKS = {"openai": 3, "claude": 2, "cursor": 4, "grok": 5, "zai": 1}

    def select(self, **kwargs):
        from director_orchestrator.primary_roster import select_primary_roster
        args = dict(available=("openai", "claude", "cursor", "grok", "zai"), history=(),
                    trusted_cost_ranks=self.RANKS)
        args.update(kwargs)
        return select_primary_roster(**args)

    def test_available_zai_implements_and_is_reviewed_by_another_family(self):
        from director_orchestrator.contact_families import share_provider_family
        roster = self.select()
        self.assertEqual(roster.producer, "zai")
        self.assertNotEqual(roster.reviewer, "zai")
        self.assertFalse(share_provider_family(roster.producer, roster.reviewer))
        # GLM through the Claude Code executable is still not Claude.
        self.assertFalse(share_provider_family("zai", "claude"))

    def test_zai_independently_reviews_other_providers_work(self):
        # Z.ai stays below its band while openai and cursor sit inside theirs.
        roster = self.select(history=("zai",) + ("openai",) * 5 + ("cursor",),
                             trusted_cost_ranks={**self.RANKS, "claude": 0})
        self.assertEqual(roster.producer, "claude")
        self.assertEqual(roster.reviewer, "zai")

    def test_unavailable_zai_is_never_selected(self):
        roster = self.select(available=("openai", "claude", "cursor", "grok"))
        self.assertNotIn("zai", (roster.producer, roster.reviewer))

    def test_hard_ceiling_removes_zai_after_four_admissions_in_twenty(self):
        roster = self.select(history=("zai",) * 4)
        self.assertNotIn("zai", (roster.producer, roster.reviewer))


class HistoricalAdmissionTests(unittest.TestCase):
    def test_pre_enrollment_rows_stay_valid_and_cannot_name_zai(self):
        from director_orchestrator.allocation_profiles import allocation_profile_digest, pre_zai_allocation_profile_digest
        from director_orchestrator.primary_workload import POLICY_VERSION, _valid_history_row
        old = {"policy_version": POLICY_VERSION, "allocation_profile": "engineering",
               "policy_digest": pre_zai_allocation_profile_digest("engineering")}
        self.assertTrue(_valid_history_row({**old, "provider_lane": "claude"}))
        self.assertFalse(_valid_history_row({**old, "provider_lane": "zai"}))
        current = {**old, "policy_digest": allocation_profile_digest("engineering")}
        self.assertTrue(_valid_history_row({**current, "provider_lane": "zai"}))
        self.assertNotEqual(old["policy_digest"], current["policy_digest"])


class SlackThreadTests(unittest.TestCase):
    def test_zai_outcome_is_relayed_in_the_original_thread_under_its_own_name(self):
        from director_orchestrator.models import (AssignmentOutcome, IntakeAuthentication, ProviderName,
                                                  SlackIntake, TaskRecord, TaskStatus)
        from director_orchestrator.primary_slack_delivery import render_notification
        record = TaskRecord(
            task_id="DIR-ZAI", status=TaskStatus.DONE,
            intake_authentication=IntakeAuthentication.SLACK_GATEWAY_ATTESTED,
            intake=SlackIntake(source_event_id="evt-zai", channel_id="C123", thread_ts="1790000000.000100",
                               user_id="U123", text="DIRECTOR Project: demo Outcome: review"),
            assignment_outcomes=[AssignmentOutcome(assignment_sequence=1, provider=ProviderName.ZAI,
                                                   run_id="zai-session-1", result="Two findings.",
                                                   review_summary="Accepted.", evidence=["exact SHA"])],
        )
        settings = replace(Settings(), slack_channel_id="C123", slack_owner_user_id="U123")
        row = {"projection": {"application": {"task_record": record.model_dump(mode="json"),
                                              "execution_mode": "live"},
                              "terminal_classification": "COMPLETED", "reason_code": "completion_passed"},
               "created_at": datetime(2026, 9, 27, 3, 40, tzinfo=timezone.utc), "projection_digest": "sha256:" + "1" * 64}
        payload = render_notification(row, settings)
        self.assertEqual(payload["thread_ts"], "1790000000.000100")
        self.assertEqual(payload["channel"], "C123")
        self.assertIn("zai: Two findings.", payload["blocks"][0]["text"]["text"])
        self.assertNotIn("claude:", payload["blocks"][0]["text"]["text"])


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 03:40 UTC | END]]
