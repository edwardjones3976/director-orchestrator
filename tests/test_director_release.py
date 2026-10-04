"""Preparation contracts using the real A2 reader in isolated temporary owner roots."""
import copy
import base64
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import unittest
from unittest.mock import patch

from director_orchestrator.provider_activation import ProviderActivationBinding
from director_orchestrator.primary_activation_evidence import ActivationIdentity
from director_orchestrator.primary_generation import resolved_deployment_digest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("director_release_under_test", ROOT / "scripts/director-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
REAL_COMMAND = release.command
PREFLIGHT_SPEC = importlib.util.spec_from_file_location("preflight_contract_fixture", ROOT / "tests/test_live_dispatch_preflight.py")
PREFLIGHT_FIXTURE = importlib.util.module_from_spec(PREFLIGHT_SPEC)
PREFLIGHT_SPEC.loader.exec_module(PREFLIGHT_FIXTURE)


class ReleasePreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source, self.output, self.snapshot = [self.root / name for name in ("source", "output", "snapshot")]
        for path in (self.source, self.output, self.snapshot):
            path.mkdir()
        (self.snapshot / "README.md").write_text("fixture snapshot", encoding="utf-8")
        root_patch = patch.object(release, "ROOT", self.source)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.candidate, self.tree, self.image = "a" * 40, "b" * 40, "sha256:" + "8" * 64
        self.activation = asdict(ActivationIdentity(
            candidate_commit=self.candidate, candidate_tree=self.tree, profile_id="fixture",
            compose_project="fixture-director", database_identity="postgres:sha256:" + "c" * 64,
            database_instance_id="11111111-1111-4111-8111-111111111111",
            postgres_schema="director_primary_v1", checkpoint_namespace="fixture-primary-v1"))
        self.compose = {"name": "fixture-director", "services": {
            "orchestrator": {"image": self.image, "pull_policy": "never",
                "command": ["uvicorn", "director_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"],
                "environment": {"DIRECTOR_RUNTIME_IMAGE": self.image, "DIRECTOR_RUNTIME_IMAGE_ID": self.image,
                    "DIRECTOR_ASGI_APP": "director_orchestrator.main:app", "ALLOW_PAID_RUNS": "false",
                    "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": "0" * 64,
                    "ORCHESTRATOR_SHARED_SECRET": "fixture-protected-value"},
                "volumes": [{"type": "bind", "source": str(self.snapshot), "target": "/repository", "read_only": True}]},
            "slackbot": {"image": self.image, "pull_policy": "never",
                "command": ["python", "-m", "director_orchestrator.slack_bot"],
                "environment": {"ALLOW_PAID_RUNS": "false", "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED": "false",
                                "LANGGRAPH_PARALLEL_KILL_SWITCH": "true"}},
            "n8n": {"image": "docker.n8n.io/n8nio/n8n:latest@sha256:" + "9" * 64, "pull_policy": "never"}}}
        self.manifest = {"version": "director-primary-generation/v1", "activation": self.activation,
            "image_id": self.image, "resolved_compose_sha256": resolved_deployment_digest(self.compose, self.image),
            "journal_path": "/data/fresh.sqlite3", "registry_path": "/data/registry.json",
            "registry_sha256": "d" * 64, "provider_bindings_sha256": "e" * 64,
            "deployment_configuration_sha256": "f" * 64}
        manifest_pin = self.pin("generation.json", release.encoded(self.manifest))
        self.generation = manifest_pin["sha256"]
        self.compose["services"]["orchestrator"]["environment"]["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"] = self.generation
        packet = json.loads((ROOT / "docs/schemas/model-work-packet-v1.example.json").read_text())
        packet.update(task_id="VALIDATION-canary", assignment_id="VALIDATION-canary", starting_ref=self.candidate,
                      allowed_tools=[], allowed_external_effects=[])
        packet["limits"].update(max_hops=1, max_revisions=0)
        self.validation = {"generation": self.generation, "candidate": self.candidate, "tree": self.tree,
            "expires_at": int(time.time()) + 600, "budget_units": 2, "lanes": {"grok": {
                "binding": asdict(ProviderActivationBinding("grok", "workspace", "v1", "v1", "v1", "v1", "v1")),
                "configuration_digest": "e" * 64, "cost_units": 2, "work_packet": packet}}}
        self.runtime = {"LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA": self.candidate,
            "REPOSITORY_ALLOWLIST": "fixture/project", "ALLOWED_STARTING_REFS": "main",
            "LANGGRAPH_PRIMARY_ACCEPTANCE_MODE": "provider-v3-two-party",
            "LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_PATH": "/data/registry.json",
            "LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256": "d" * 64,
            "LANGGRAPH_PRIMARY_ACCEPTANCE_JOURNAL_PATH": "/data/acceptance.sqlite3",
            "LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_DIRECTORY": "/data",
            "LANGGRAPH_PRIMARY_PINNED_TREE_SHA": self.tree,
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": self.generation,
            "DIRECTOR_PROVIDER_VALIDATION_GENERATION": self.generation,
            "DIRECTOR_RUNTIME_IMAGE": self.image, "REPOSITORY_SNAPSHOT_HOST_PATH": str(self.snapshot),
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT": "https://127.0.0.1:18767/v1/provider/execute",
            "LANGGRAPH_PRIMARY_ACTIVATION_INDEPENDENT_REVIEWER_ALLOWLIST_JSON": '["fixture-reviewer"]',
            "LANGGRAPH_PRIMARY_ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON": '["fixture-security"]'}
        self.bridge = {"DIRECTOR_PROVIDER_VALIDATION_CANDIDATE": self.candidate,
            "DIRECTOR_PROVIDER_VALIDATION_TREE": self.tree,
            "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": self.generation,
            "DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN": "fixture-owner-secret-" + "x" * 32,
            "DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE": str(self.root / "process.env.json"),
            "DIRECTOR_HOST_PROVIDER_POSTGRES_DSN": "fixture-only-no-database",
            "DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN": "fixture-only-no-validation-database"}
        self.spec = {"schema_version": "director-release-descriptor/v1", "release_id": "fixture-r1",
            "source": str(self.source), "candidate": {"commit": self.candidate, "tree": self.tree},
            "compose_project": "fixture-director", "image_id": self.image,
            "runtime_env": self.pin_env("runtime.env", self.runtime),
            "bridge_env": self.pin_env("bridge.env", self.bridge), "generation_manifest": manifest_pin,
            "validation_manifest": self.pin("validation.json", release.encoded(self.validation)),
            "compose_files": [self.pin("compose.yml", b"services: {}\n")],
            "bridge_url": "https://127.0.0.1:18767/v1/provider/configuration-digest",
            "bridge_ca": self.pin("ca.pem", b"fixture-ca-not-used-by-mocked-io"),
            "snapshot": {"path": str(self.snapshot), "repository": "fixture/project", "starting_ref": "main",
                         "remote": "origin", "commit": self.candidate, "tree": self.tree},
            "reviewers": {"independent": "fixture-reviewer", "security": "fixture-security"},
            # Linux venv launchers are commonly symlinks; pin the real executable.
            # The production consumer must still refuse unresolved linked paths.
            "executables": {"python": str(Path(sys.executable).resolve()),
                            "powershell": str(Path(sys.executable).resolve())},
            "output_directory": str(self.output), "timeout_seconds": 30}
        self.descriptor = self.root / "descriptor.json"
        self.owner_root = self.root / "owner" / ".director-scorecard"
        self.owner_root.mkdir(parents=True)
        self.installed = self.owner_root / "launcher.py"
        self.owner_descriptor = self.owner_root / "release-descriptor.json"
        (self.source / "scripts").mkdir()
        self.locator = self.source / "scripts" / "scorecard-launcher.py"
        # Copy the production source unchanged. Only the OS account lookup is
        # replaced after loading; root selection and descriptor reads stay real.
        launcher = (ROOT / "scripts" / "scorecard-launcher.py").read_bytes()
        self.locator.write_bytes(launcher)
        self.installed.write_bytes(launcher)
        load = release.launcher_module
        def fixture_account(path, *, raw=None):
            module, raw = load(path, raw=raw)
            if callable(getattr(module, "account_home", None)):
                module.account_home = lambda: self.owner_root.parent
            return module, raw
        account_patch = patch.object(release, "launcher_module", side_effect=fixture_account)
        account_patch.start()
        self.addCleanup(account_patch.stop)
        anchor_patch = patch.object(release, "account_home", return_value=self.owner_root.parent)
        anchor_patch.start()
        self.addCleanup(anchor_patch.stop)
        self.owner_spec = {"version": "director-cli-release-descriptor/v1", "evaluator_sha": "c" * 40,
                           "security_base_sha": "d" * 40, "release_preparations": {}}
        self.save()
        self.calls = []
        self.source_calls = []
        self.reply_changes = {}
        command_patch = patch.object(release, "command", side_effect=self.platform)
        command_patch.start()
        self.addCleanup(command_patch.stop)

    def pin(self, name, raw):
        path = self.root / name
        path.write_bytes(raw)
        return {"path": str(path), "sha256": release.sha(raw)}

    def pin_env(self, name, values):
        return self.pin(name, "".join(f"{key}={value}\n" for key, value in values.items()).encode())

    def save(self):
        self.descriptor.write_bytes(release.encoded(self.spec))
        self.owner_spec["release_preparations"][self.spec["release_id"]] = release.sha(self.descriptor.read_bytes())
        if "rehearsal" in self.spec:
            sibling = Path(self.spec["rehearsal"]["descriptor"]["path"]).read_bytes()
            self.owner_spec["release_preparations"][json.loads(sibling)["release_id"]] = release.sha(sibling)
        self.owner_descriptor.write_bytes(release.encoded(self.owner_spec))

    def owner_digest(self):
        return release.sha(self.owner_descriptor.read_bytes())

    def launcher_digest(self):
        return release.sha(self.installed.read_bytes())

    def platform(self, argv, **kwargs):
        if argv[0] == "git":
            self.source_calls.append((argv, kwargs))
        else:
            self.calls.append((argv, kwargs))
        if argv[:2] == ["git", "config"]:
            return b""
        if argv[:2] == ["docker", "context"]:
            return release.encoded([{"Endpoints": {"docker": {"Host": "npipe:////./pipe/docker_engine"}}}])
        if argv[:2] == ["docker", "info"]:
            return b"fixture-daemon"
        if argv[:2] in (["docker", "volume"], ["docker", "network"]):
            return b""
        if argv[:2] == ["docker", "ps"]:
            return b""
        if argv[:2] == ["git", "rev-parse"]:
            return self.reply_changes.get("commit" if argv[-1] == "HEAD^{commit}" else "tree",
                (self.candidate if argv[-1] == "HEAD^{commit}" else self.tree).encode())
        if argv[:2] == ["git", "status"]:
            return self.reply_changes.get("status", b"")
        if argv[:3] == ["docker", "image", "inspect"]:
            return release.encoded([{"Id": self.reply_changes.get("image", self.image), "Config": {
                "Labels": {"org.director.candidate.commit": self.candidate, "org.director.candidate.tree": self.tree},
                "Entrypoint": None}}])
        if argv[:2] == ["docker", "compose"]:
            self.assertEqual(argv[-3:], ["config", "--format", "json"])
            self.assertIn(str(self.root / "compose.yml"), argv)
            graph = self.sibling_compose if argv[3] == "fixture-sibling" else self.compose
            return release.encoded(graph)
        if "-File" in argv:
            self.assertIn(argv[argv.index("-Action") + 1], ("Verify", "Preflight"))
            return b"read-only fixture proof"
        if argv[-1] == release.BRIDGE_PROBE:
            from director_orchestrator.host_provider_bridge_server import _configured_selection
            # Each endpoint spy represents the serving process selected by that
            # profile, rather than echoing the consumer's request or expectations.
            bridge_call = next(call for call, _ in reversed(self.calls)
                               if any(str(arg).endswith("windows-host-provider-bridge.ps1") for arg in call))
            current = release.dotenv(Path(bridge_call[bridge_call.index("-EnvironmentFile") + 1]).read_bytes())
            selection = _configured_selection(candidate_commit=current["DIRECTOR_PROVIDER_VALIDATION_CANDIDATE"],
                candidate_tree=current["DIRECTOR_PROVIDER_VALIDATION_TREE"],
                generation_sha256=current["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"],
                workspace=self.snapshot, validation_dsn=current["DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN"])
            return release.encoded({"ok": True, "pinned_environment": True,
                "configured_selection": self.reply_changes.get("bridge_selection", selection),
                "configuration_digests": self.reply_changes.get("bridge", {"grok": "e" * 64})})
        if argv[-1] == release.DATABASE_PROBE:
            request = json.loads(kwargs["input_bytes"])
            activation = self.sibling_activation if request["generation"] != self.generation else self.activation
            observed = self.reply_changes.get("database", {}).get(request["generation"], activation)
            return release.encoded({"database_identity": observed["database_identity"],
                "database_instance_id": observed["database_instance_id"],
                "occupied_slots": self.reply_changes.get("slots", [])})
        if Path(argv[-1]).name == "live-dispatch-preflight.py":
            # Exercise the actual validator contract, including disabled-switch
            # failures. External process I/O is the mocked boundary, not its verdict.
            settings = self.preflight_settings(kwargs["env"])
            if PREFLIGHT_FIXTURE.PREFLIGHT.unmet(settings):
                raise release.ReleaseError("platform_check_failed")
            return b"real read-only configuration validators passed"
        self.fail(f"Unexpected platform operation: {argv[:3]}")

    def preflight_settings(self, environment):
        values = {attribute: environment.get(name, "false").lower() == "true"
                  for name, attribute in PREFLIGHT_FIXTURE.PREFLIGHT.TARGET_SWITCHES}
        values["langgraph_primary_offline_simulation_enabled"] = environment.get(
            "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED", "false") == "true"
        values.update(self.reply_changes.get("preflight_settings", {}))
        return PREFLIGHT_FIXTURE._complete(**values)

    def repin_graph(self, spec, graph):
        """Change actual pinned fixture bytes when a Compose override is selected."""
        manifest = json.loads(Path(spec["generation_manifest"]["path"]).read_bytes())
        manifest["resolved_compose_sha256"] = resolved_deployment_digest(graph, self.image)
        spec["generation_manifest"] = self.pin(Path(spec["generation_manifest"]["path"]).name, release.encoded(manifest))
        generation = spec["generation_manifest"]["sha256"]
        for name in ("runtime_env", "bridge_env"):
            values = release.dotenv(Path(spec[name]["path"]).read_bytes())
            values["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"] = generation
            if name == "runtime_env":
                values["DIRECTOR_PROVIDER_VALIDATION_GENERATION"] = generation
            spec[name] = self.pin_env(Path(spec[name]["path"]).name, values)
        validation = json.loads(Path(spec["validation_manifest"]["path"]).read_bytes())
        validation["generation"] = generation
        spec["validation_manifest"] = self.pin(Path(spec["validation_manifest"]["path"]).name, release.encoded(validation))
        graph["services"]["orchestrator"]["environment"]["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"] = generation
        if spec is self.spec:
            self.generation = generation
        else:
            self.spec["rehearsal"]["descriptor"] = self.pin("sibling.json", release.encoded(spec))
        self.save()

    def test_doctor_reuses_real_contracts_but_never_grants_release_authority(self):
        report = release.Release(self.descriptor).doctor()
        self.assertTrue(report["preparation_ready"], report)
        self.assertFalse(report["release_ready"])
        self.assertEqual(report["provider_calls"], 0)
        self.assertIn("fresh_generation_authority", report["dependencies"])
        self.assertIn("paid_admission", report["dependencies"])
        self.assertEqual(list(self.output.iterdir()), [])
        with redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(release.main(["doctor", "--descriptor", str(self.descriptor)]), 2)
        self.assertFalse(json.loads(stream.getvalue())["release_ready"])

    def test_prepare_receipt_and_resume_verify_bytes_without_replaying_platform_work(self):
        result = release.Release(self.descriptor).prepare()
        self.assertEqual(result["status"], "PREPARED")
        self.assertFalse(result["release_ready"])
        output = self.output / "fixture-r1.activation.compose.json"
        self.assertEqual(json.loads(output.read_bytes()), self.compose)
        journal = self.output / "fixture-r1.jsonl"
        records = release.read_journal(journal, release.sha(self.descriptor.read_bytes()), self.owner_digest(), self.launcher_digest())
        self.assertEqual(len(records), 2)
        self.assertTrue(all(row["owner_descriptor_sha256"] == self.owner_digest() for row in records))
        self.assertTrue(all(row["owner_launcher_sha256"] == self.launcher_digest() for row in records))
        self.assertNotIn("fixture-protected-value", journal.read_text())
        self.assertNotIn(self.bridge["DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN"], journal.read_text())
        calls = len(self.calls)
        self.assertTrue(release.Release(self.descriptor).prepare(resume=True)["resumed"])
        self.assertEqual(len(self.calls), calls)
        with self.assertRaisesRegex(release.ReleaseError, "use_resume"):
            release.Release(self.descriptor).prepare()

    def test_resume_rejects_changed_input_and_output_without_replay(self):
        release.Release(self.descriptor).prepare()
        calls = len(self.calls)
        output = self.output / "fixture-r1.activation.compose.json"
        original = output.read_bytes()
        output.write_bytes(original + b" ")
        with self.assertRaisesRegex(release.ReleaseError, "completed_output_changed"):
            release.Release(self.descriptor).prepare(resume=True)
        output.write_bytes(original)
        Path(self.spec["bridge_env"]["path"]).write_text("changed")
        with self.assertRaisesRegex(release.ReleaseError, "input_digest_changed"):
            release.Release(self.descriptor).prepare(resume=True)
        self.assertEqual(len(self.calls), calls)

    def test_interrupted_receipt_recovers_proven_local_write_without_replay(self):
        append = release.append_record
        def fail_receipt(path, value):
            if value["event"] == "receipt":
                raise OSError("fixture-only-write-failure")
            append(path, value)
        with patch.object(release, "append_record", side_effect=fail_receipt), self.assertRaisesRegex(
                release.ReleaseError, "reconciliation_required_no_replay"):
            release.Release(self.descriptor).prepare()
        calls = len(self.calls)
        original = (self.output / "fixture-r1.activation.compose.json").read_bytes()
        result = release.Release(self.descriptor).prepare(resume=True)
        self.assertTrue(result["recovered_receipt"])
        self.assertEqual(len(self.calls), calls)
        self.assertEqual((self.output / "fixture-r1.activation.compose.json").read_bytes(), original)

    def test_missing_or_partial_output_after_intent_is_never_replayed(self):
        journal = self.output / "fixture-r1.jsonl"
        release.append_record(journal, {"event": "intent", "descriptor_sha256": release.sha(self.descriptor.read_bytes()),
            "owner_descriptor_sha256": self.owner_digest(),
            "owner_launcher_sha256": self.launcher_digest(),
            "step": "render_compose", "sequence": 1, "output_sha256": "a" * 64})
        for raw in (None, b"partial"):
            if raw is not None:
                (self.output / "fixture-r1.activation.compose.json").write_bytes(raw)
            with self.assertRaisesRegex(release.ReleaseError, "reconciliation_required_no_replay"):
                release.Release(self.descriptor).prepare(resume=True)
        self.assertEqual(self.calls, [])

    def test_truncated_tampered_and_other_descriptor_journals_fail_closed(self):
        tool = release.Release(self.descriptor)
        journal = self.output / "fixture-r1.jsonl"
        journal.write_bytes(b'{"event":"intent"}')
        with self.assertRaisesRegex(release.ReleaseError, "incomplete_journal"):
            tool.prepare(resume=True)
        journal.write_bytes(b"")
        release.append_record(journal, {"event": "intent", "descriptor_sha256": "0" * 64,
            "owner_descriptor_sha256": self.owner_digest(),
            "owner_launcher_sha256": self.launcher_digest(),
            "step": "render_compose", "sequence": 1, "output_sha256": "a" * 64})
        with self.assertRaisesRegex(release.ReleaseError, "journal_binding_mismatch"):
            tool.prepare(resume=True)
        entry = json.loads(journal.read_bytes())
        entry["descriptor_sha256"] = tool.descriptor_digest
        journal.write_bytes(release.encoded(entry) + b"\n")
        with self.assertRaisesRegex(release.ReleaseError, "journal_digest_mismatch"):
            tool.prepare(resume=True)
        self.assertEqual(self.calls, [])

    def test_occupied_slots_stale_bridge_and_dirty_source_prevent_preparation(self):
        for name, value in (("slots", ["grok"]), ("bridge", {"grok": "0" * 64}),
                            ("status", b"1 .M dirty-file"), ("image", "sha256:" + "0" * 64)):
            with self.subTest(name=name):
                self.reply_changes = {name: value}
                reason = "source_not_clean" if name == "status" else "doctor_refused_preparation"
                with self.assertRaisesRegex(release.ReleaseError, reason):
                    release.Release(self.descriptor).prepare()
                self.assertFalse((self.output / "fixture-r1.jsonl").exists())

    def test_invalid_selector_expiry_and_reviewer_are_not_readiness(self):
        for target, key, value in (("runtime", "DIRECTOR_PROVIDER_VALIDATION_GENERATION", "0" * 64),
            ("validation", "expires_at", 1), ("validation", "expires_at", int(time.time()) + 90000),
            ("runtime", "LANGGRAPH_PRIMARY_ACTIVATION_SECURITY_REVIEWER_ALLOWLIST_JSON", '["other"]')):
            with self.subTest(key=key, value=value):
                tool = release.Release(self.descriptor)
                getattr(tool, target)[key] = value
                self.assertFalse(tool.doctor()["preparation_ready"])

    def test_gateway_image_drift_and_writeable_snapshot_fail_real_compose_contract(self):
        tool = release.Release(self.descriptor)
        self.compose["services"]["slackbot"]["image"] = "mutable:latest"
        self.assertFalse(tool.doctor()["preparation_ready"])
        self.compose["services"]["slackbot"]["image"] = self.image
        self.compose["services"]["orchestrator"]["volumes"][0]["read_only"] = False
        self.assertFalse(release.Release(self.descriptor).doctor()["preparation_ready"])

    def test_input_change_during_doctor_is_caught_before_intent(self):
        tool = release.Release(self.descriptor)
        with patch.object(tool, "live_config_check", side_effect=lambda: Path(self.spec["bridge_ca"]["path"]).write_bytes(b"changed")):
            with self.assertRaisesRegex(release.ReleaseError, "doctor_refused_preparation"):
                tool.prepare()
        self.assertFalse((self.output / "fixture-r1.jsonl").exists())

    def test_descriptor_mutation_is_refused_before_write_or_resume_receipt(self):
        tool = release.Release(self.descriptor)
        self.descriptor.write_bytes(self.descriptor.read_bytes() + b" ")
        with self.assertRaisesRegex(release.ReleaseError, "descriptor_changed"):
            tool.verify_inputs()
        self.assertFalse((self.output / "fixture-r1.jsonl").exists())
        self.save()
        tool.prepare()
        self.spec["timeout_seconds"] += 1
        self.save()
        with self.assertRaisesRegex(release.ReleaseError, "journal_binding_mismatch"):
            release.Release(self.descriptor).prepare(resume=True)

    def test_missing_installation_fails_before_profiles_source_probes_and_writes(self):
        self.installed.unlink()
        with patch.object(release, "pinned_file") as profiles, self.assertRaisesRegex(
                release.ReleaseError, "trusted_descriptor_consumer_unavailable"):
            release.Release(self.descriptor)
        profiles.assert_not_called()
        self.assertEqual(self.source_calls, [])
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_caller_home_digest_and_nonce_claims_never_select_authority(self):
        fake = self.root / "caller-home"
        (fake / ".director-scorecard").mkdir(parents=True)
        (fake / ".director-scorecard" / "launcher.py").write_text("raise AssertionError('caller launcher ran')\n")
        (fake / ".director-scorecard" / "release-descriptor.json").write_bytes(self.owner_descriptor.read_bytes())
        for digest in (None, "0" * 64):
            with self.subTest(digest=digest), patch.dict(os.environ, {
                    "HOME": str(fake), "USERPROFILE": str(fake), "DIRECTOR_SCORECARD_OWNER_HOME": str(fake),
                    "DIRECTOR_SCORECARD_NONCE": "e" * 64}):
                os.environ.pop("DIRECTOR_SCORECARD_DESCRIPTOR_SHA256", None)
                if digest is not None:
                    os.environ["DIRECTOR_SCORECARD_DESCRIPTOR_SHA256"] = digest
                tool = release.Release(self.descriptor)
                self.assertEqual(tool.owner_descriptor_digest, self.owner_digest())
                tool.verify_inputs()
        self.owner_descriptor.unlink()  # The matching caller copy cannot replace missing owner evidence.
        with patch.dict(os.environ, {"HOME": str(fake), "USERPROFILE": str(fake),
                                    "DIRECTOR_SCORECARD_OWNER_HOME": str(fake)}):
            with self.assertRaisesRegex(release.ReleaseError, "owner_descriptor_unavailable"):
                release.Release(self.descriptor)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_missing_wrong_or_malformed_owner_pins_refuse_before_profiles(self):
        for pins in (None, {}, {"fixture-r1": "0" * 64}, {"fixture-r1": True}, {"fixture-r1": "bad"}):
            with self.subTest(pins=pins):
                self.owner_spec["release_preparations"] = pins
                self.owner_descriptor.write_bytes(release.encoded(self.owner_spec))
                with patch.object(release, "pinned_file") as profiles, self.assertRaises(release.ReleaseError):
                    release.Release(self.descriptor)
                profiles.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_actual_A2_reader_binds_exact_preparation_and_owner_bytes(self):
        # Equal JSON values with different bytes require a new explicit pin.
        changed = self.descriptor.read_bytes() + b"\n"
        self.descriptor.write_bytes(changed)
        with self.assertRaisesRegex(release.ReleaseError, "preparation_not_owner_pinned"):
            release.Release(self.descriptor)
        self.owner_spec["release_preparations"][self.spec["release_id"]] = release.sha(changed)
        owner_bytes = json.dumps(self.owner_spec, indent=2).encode() + b"\n"
        self.owner_descriptor.write_bytes(owner_bytes)
        tool = release.Release(self.descriptor)
        self.assertEqual(tool.owner_launcher.read_descriptor(self.owner_descriptor),
                         (release.sha(owner_bytes), self.owner_spec))
        self.assertEqual(tool.owner_descriptor_digest, release.sha(owner_bytes))
        self.assertEqual(tool.owner_launcher.read_descriptor.__code__.co_filename, str(self.installed))
        tool.verify_inputs()
        self.assertEqual(self.calls, [])

    def test_owner_descriptor_mutation_with_same_evaluator_pin_blocks_before_intent(self):
        tool = release.Release(self.descriptor)
        self.owner_spec["security_base_sha"] = "e" * 40
        self.owner_descriptor.write_bytes(release.encoded(self.owner_spec))
        with self.assertRaisesRegex(release.ReleaseError, "owner_descriptor_changed"):
            tool.prepare()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_owner_rotation_after_intent_holds_until_exact_authority_restored_without_replay(self):
        original_owner = self.owner_descriptor.read_bytes()
        append = release.append_record
        def rotate_after_intent(path, value):
            append(path, value)
            if value["event"] == "intent":
                self.owner_descriptor.write_bytes(original_owner + b" ")
        with patch.object(release, "append_record", side_effect=rotate_after_intent), self.assertRaisesRegex(
                release.ReleaseError, "reconciliation_required_no_replay"):
            release.Release(self.descriptor).prepare()
        calls = len(self.calls)
        journal = self.output / "fixture-r1.jsonl"
        original_journal = journal.read_bytes()
        target = self.output / "fixture-r1.activation.compose.json"
        original_output = target.read_bytes()
        with self.assertRaisesRegex(release.ReleaseError, "journal_binding_mismatch"):
            release.Release(self.descriptor).prepare(resume=True)
        self.assertEqual(journal.read_bytes(), original_journal)
        self.owner_descriptor.write_bytes(original_owner)
        self.assertTrue(release.Release(self.descriptor).prepare(resume=True)["recovered_receipt"])
        self.assertEqual(target.read_bytes(), original_output)
        self.assertEqual(len(self.calls), calls)

    def test_completed_resume_refuses_rotated_authority_and_legacy_unbound_journal(self):
        release.Release(self.descriptor).prepare()
        calls = len(self.calls)
        original_owner = self.owner_descriptor.read_bytes()
        self.owner_descriptor.write_bytes(original_owner + b"\n")
        with self.assertRaisesRegex(release.ReleaseError, "journal_binding_mismatch"):
            release.Release(self.descriptor).prepare(resume=True)
        self.owner_descriptor.write_bytes(original_owner)
        journal = self.output / "fixture-r1.jsonl"
        rows = [json.loads(line) for line in journal.read_bytes().splitlines()]
        journal.write_bytes(b"")
        for row in rows:
            del row["owner_descriptor_sha256"]
            del row["record_sha256"]
            release.append_record(journal, row)
        with self.assertRaisesRegex(release.ReleaseError, "invalid_journal_record"):
            release.Release(self.descriptor).prepare(resume=True)
        self.assertEqual(len(self.calls), calls)

    def test_installed_launcher_rotation_is_not_silently_reloaded(self):
        tool = release.Release(self.descriptor)
        self.installed.write_bytes(self.installed.read_bytes() + b"\n")
        with self.assertRaisesRegex(release.ReleaseError, "owner_launcher_changed"):
            tool.prepare()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_bootstrap_does_not_import_candidate_modules_before_owner_authentication(self):
        import builtins
        original_import = builtins.__import__
        def refuse_candidate(name, *args, **kwargs):
            if name == "director_orchestrator" or name.startswith("director_orchestrator."):
                self.fail("candidate module imported before authentication")
            return original_import(name, *args, **kwargs)
        fresh = importlib.util.module_from_spec(SPEC)
        with patch.object(builtins, "__import__", side_effect=refuse_candidate):
            SPEC.loader.exec_module(fresh)
            with patch.object(fresh, "account_home", return_value=self.root / "missing"), self.assertRaisesRegex(
                    fresh.ReleaseError, "trusted_descriptor_consumer_unavailable"):
                fresh.Release(self.descriptor)

    def test_checkout_locator_never_executes_and_source_rejects_before_helpers_or_profiles(self):
        marker = self.root / "untrusted-locator-ran"
        self.locator.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError('untrusted')\n")
        # Even a clean source observation cannot turn this checkout locator into
        # the owner authority; only the installed A2 reader is executed.
        release.Release(self.descriptor)
        self.assertFalse(marker.exists())
        for changed in ({"status": b"1 .M scripts/scorecard-launcher.py"}, {"commit": b"f" * 40}):
            with self.subTest(changed=changed), patch.object(release, "pinned_file") as profiles:
                self.reply_changes = changed
                with self.assertRaisesRegex(release.ReleaseError, "source_(not_clean|identity_changed)"):
                    release.Release(self.descriptor)
                profiles.assert_not_called()
                self.assertFalse(marker.exists())
        self.assertEqual(self.calls, [])

    def test_installed_loader_executes_captured_bytes_if_path_changes_before_exec(self):
        load = release.launcher_module
        marker = self.root / "changed-install-ran"
        def mutate_path(path, *, raw=None):
            self.assertIsNotNone(raw)
            self.installed.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
            return load(path, raw=raw)
        with patch.object(release, "launcher_module", side_effect=mutate_path), self.assertRaisesRegex(
                release.ReleaseError, "owner_launcher_changed"):
            release.Release(self.descriptor)
        self.assertFalse(marker.exists())
        self.assertEqual(self.source_calls, [])

    def test_doctor_stops_on_source_failure_before_any_checkout_child(self):
        tool = release.Release(self.descriptor)
        self.reply_changes["status"] = b"1 .M scripts/live-dispatch-preflight.py"
        report = tool.doctor()
        self.assertEqual([item["check"] for item in report["checks"]], ["owner", "source"])
        self.assertFalse(report["preparation_ready"])
        self.assertEqual(self.calls, [])

    def test_new_instance_resume_and_recovery_require_original_installed_launcher(self):
        append = release.append_record
        def interrupt_receipt(path, value):
            if value["event"] == "receipt":
                raise OSError("fixture interruption")
            append(path, value)
        with patch.object(release, "append_record", side_effect=interrupt_receipt), self.assertRaisesRegex(
                release.ReleaseError, "reconciliation_required_no_replay"):
            release.Release(self.descriptor).prepare()
        original_launcher = self.installed.read_bytes()
        target = self.output / "fixture-r1.activation.compose.json"
        journal = self.output / "fixture-r1.jsonl"
        for complete in (False, True):
            with self.subTest(complete=complete):
                if complete:
                    release.Release(self.descriptor).prepare(resume=True)
                before_journal, before_target, calls = journal.read_bytes(), target.read_bytes(), len(self.calls)
                self.installed.write_bytes(original_launcher + b"\n")
                with self.assertRaisesRegex(release.ReleaseError, "journal_binding_mismatch"):
                    release.Release(self.descriptor).prepare(resume=True)
                self.assertEqual(journal.read_bytes(), before_journal)
                self.assertEqual(target.read_bytes(), before_target)
                self.assertEqual(len(self.calls), calls)
                self.installed.write_bytes(original_launcher)

    def test_legacy_journal_without_installed_launcher_binding_is_not_migrated(self):
        release.Release(self.descriptor).prepare()
        journal = self.output / "fixture-r1.jsonl"
        records = [json.loads(line) for line in journal.read_bytes().splitlines()]
        journal.write_bytes(b"")
        for entry in records:
            del entry["owner_launcher_sha256"]
            del entry["record_sha256"]
            release.append_record(journal, entry)
        before, calls = journal.read_bytes(), len(self.calls)
        with self.assertRaisesRegex(release.ReleaseError, "invalid_journal_record"):
            release.Release(self.descriptor).prepare(resume=True)
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual(len(self.calls), calls)

    def test_output_snapshot_overlap_refuses_before_lock_or_artifact(self):
        below = self.snapshot / "output"
        below.mkdir()
        for output, snapshot in ((self.snapshot, self.snapshot), (below, self.snapshot),
                                 (self.output, self.output / "snapshot")):
            with self.subTest(output=output, snapshot=snapshot):
                self.spec["output_directory"] = str(output)
                self.spec["snapshot"]["path"] = str(snapshot)
                self.save()
                with patch.object(release, "locked") as lock, self.assertRaisesRegex(
                        release.ReleaseError, "output_overlaps_snapshot"):
                    release.Release(self.descriptor).prepare()
                lock.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_running_bridge_selection_rejects_every_mismatch_and_legacy_reply(self):
        from director_orchestrator.host_provider_bridge_server import _configured_selection
        expected = _configured_selection(candidate_commit=self.candidate, candidate_tree=self.tree,
            generation_sha256=self.generation, workspace=self.snapshot,
            validation_dsn=self.bridge["DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN"])
        for selection in (None, {}, *[dict(expected, **{key: "0" * 64}) for key in expected]):
            with self.subTest(selection=selection):
                self.reply_changes["bridge_selection"] = selection
                report = release.Release(self.descriptor).doctor()
                self.assertFalse(report["preparation_ready"])
                self.assertIn({"check": "bridge", "status": "FAIL", "reason": "running_bridge_selection_mismatch"},
                              report["checks"])

    def test_real_child_preflight_checks_hypothetical_target_without_changing_unpaid_profile(self):
        tool = release.Release(self.descriptor)
        complete = PREFLIGHT_FIXTURE._complete()
        names = set(PREFLIGHT_FIXTURE.SECRETS) | {
            "allow_paid_runs", "langgraph_primary_enabled", "langgraph_primary_new_intake_enabled",
            "langgraph_primary_live_provider_dispatch_enabled", "entitlement_control_plane_enabled",
            "trusted_provider_cost_ranks_json", "langgraph_primary_provider_activation_bindings_json",
            "langgraph_primary_completion_evidence_actor_allowlist_json", "project_allowlist",
            "repository_allowlist", "allowed_starting_refs"}
        aliases = {"shared_secret": "ORCHESTRATOR_SHARED_SECRET", "trusted_provider_cost_ranks_json": "TRUSTED_PROVIDER_COST_RANKS"}
        for name in names:
            value = getattr(complete, name)
            tool.runtime[aliases.get(name, name.upper())] = (
                str(value).lower() if isinstance(value, bool) else ",".join(value) if isinstance(value, tuple) else value)
        tool.runtime.update(ALLOW_PAID_RUNS="false", LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED="false",
            LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED="true", LANGGRAPH_PARALLEL_KILL_SWITCH="true",
            LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET="fixture-bridge-secret-" + "h" * 32)
        # A canonical Linux venv executable resolves outside its venv. Keep that
        # exact non-symlink executable and explicitly pin the existing dependency
        # location in this fixture; do not swap interpreters or bypass unlinked().
        tool.runtime["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), sysconfig.get_paths()["purelib"]))
        # Relocated Linux tool-cache Python also needs its matching libpython;
        # child_environment deliberately excludes the parent's loader override.
        # Bind the existing library through the same protected fixture profile.
        library = Path(sys.base_prefix) / "lib" / str(sysconfig.get_config_var("LDLIBRARY"))
        if sys.platform == "linux" and library.is_file():
            tool.runtime["LD_LIBRARY_PATH"] = str(library.parent.resolve(strict=True))
        self.spec["runtime_env"] = self.pin_env("runtime.env", tool.runtime)
        self.save()
        tool = release.Release(self.descriptor)
        real_script = self.source / "scripts/live-dispatch-preflight.py"
        real_script.write_bytes((ROOT / "scripts/live-dispatch-preflight.py").read_bytes())
        config = self.source / "src/director_orchestrator/config.py"
        config.parent.mkdir(parents=True)
        config.write_bytes((ROOT / "src/director_orchestrator/config.py").read_bytes())
        release.unlinked(Path(self.spec["executables"]["python"]))
        if Path(sys.executable).is_symlink():
            with self.assertRaisesRegex(release.ReleaseError, "linked_path_refused"):
                release.unlinked(Path(sys.executable))
        before_runtime = dict(tool.runtime)
        before_profile = Path(self.spec["runtime_env"]["path"]).read_bytes()
        env = release.child_environment()
        self.assertNotIn("LD_LIBRARY_PATH", env)
        env.update(tool.runtime)
        expected_argv = [self.spec["executables"]["python"], "-B", str(real_script)]
        unpaid = subprocess.run(expected_argv, env=env, cwd=self.source,
                                capture_output=True, timeout=20)
        self.assertEqual(unpaid.returncode, 1)
        self.assertIn(b"ALLOW_PAID_RUNS must be enabled", unpaid.stdout)
        def actual_child(argv, **kwargs):
            if argv[0] == "git":
                return self.platform(argv, **kwargs)
            self.assertEqual(argv, expected_argv)
            self.assertEqual(kwargs["cwd"], self.source)
            self.assertEqual(kwargs["env"]["PYTHONPATH"], tool.runtime["PYTHONPATH"])
            self.assertEqual(kwargs["env"].get("LD_LIBRARY_PATH"), tool.runtime.get("LD_LIBRARY_PATH"))
            result = subprocess.run(argv, env=kwargs["env"], cwd=kwargs["cwd"],
                                    capture_output=True, timeout=20)
            if result.returncode:
                raise release.ReleaseError("platform_check_failed")
            return result.stdout
        with patch.object(release, "command", side_effect=actual_child):
            tool.live_config_check()
            self.assertEqual(tool.runtime, before_runtime)
            tool.runtime["PROJECT_ALLOWLIST"] = ""
            with self.assertRaisesRegex(release.ReleaseError, "platform_check_failed"):
                tool.live_config_check()
        self.assertEqual(Path(self.spec["runtime_env"]["path"]).read_bytes(), before_profile)
        self.assertEqual(tool.runtime["ALLOW_PAID_RUNS"], "false")
        self.assertEqual(tool.runtime["LANGGRAPH_PARALLEL_KILL_SWITCH"], "true")

    def test_installed_launcher_cannot_relocate_the_OS_anchor(self):
        self.installed.write_text(f"from pathlib import Path\ndef owner_root(): return Path({str(self.root)!r})\n"
                                  "def release_descriptor(): return owner_root() / 'release-descriptor.json'\n"
                                  "def read_descriptor(path): raise AssertionError('must not read')\n")
        with self.assertRaisesRegex(release.ReleaseError, "owner_root_changed"):
            release.Release(self.descriptor)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_owner_path_reparse_points_refuse_before_loading_or_reading(self):
        original_lstat = Path.lstat
        from types import SimpleNamespace
        for linked in (self.owner_root.parent, self.installed, self.owner_descriptor):
            with self.subTest(linked=linked):
                def lstat(path, *args, **kwargs):
                    if path == linked:
                        return SimpleNamespace(st_mode=0, st_file_attributes=0x400)
                    return original_lstat(path, *args, **kwargs)
                with patch.object(Path, "lstat", lstat), self.assertRaisesRegex(release.ReleaseError, "linked_path_refused"):
                    release.Release(self.descriptor)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_owner_failure_after_construction_refuses_doctor_probes_and_lock(self):
        tool = release.Release(self.descriptor)
        self.owner_descriptor.unlink()
        report = tool.doctor()
        self.assertFalse(report["preparation_ready"])
        self.assertEqual(report["checks"][0]["reason"], "owner_descriptor_unavailable")
        with self.assertRaisesRegex(release.ReleaseError, "owner_descriptor_unavailable"):
            tool.prepare()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.output.iterdir()), [])

    def sibling(self):
        spec = copy.deepcopy(self.spec)
        spec.update(release_id="fixture-sibling", compose_project="fixture-sibling",
                    output_directory=str(self.root / "sibling-output"))
        Path(spec["output_directory"]).mkdir()
        self.sibling_compose = copy.deepcopy(self.compose)
        self.compose["networks"] = {"default": {"name": "fixture-director_default"}}
        self.compose["services"]["orchestrator"]["ports"] = [{"host_ip": "127.0.0.1", "published": "8080", "target": 8080}]
        self.sibling_compose.update(name="fixture-sibling", networks={"default": {"name": "fixture-sibling_default"}})
        self.sibling_compose["services"]["orchestrator"]["environment"].update(
            ALLOW_PAID_RUNS="false", LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED="false",
            LANGGRAPH_PARALLEL_KILL_SWITCH="true")
        self.sibling_compose["services"]["orchestrator"]["ports"] = [{"host_ip": "127.0.0.1", "published": "18080", "target": 8080}]
        self.sibling_compose["volumes"] = {"data": {"name": "fixture-sibling_data"}}
        manifest = copy.deepcopy(self.manifest)
        manifest["activation"].update(compose_project="fixture-sibling", database_identity="postgres:sha256:" + "d" * 64,
                                       database_instance_id="22222222-2222-4222-8222-222222222222")
        self.sibling_activation = manifest["activation"]
        for graph, item in ((self.compose, self.manifest), (self.sibling_compose, manifest)):
            item["resolved_compose_sha256"] = resolved_deployment_digest(graph, self.image)
        # Re-pin the parent after adding actual resolved topology to its fixture.
        self.spec["generation_manifest"] = self.pin("generation.json", release.encoded(self.manifest))
        self.generation = self.spec["generation_manifest"]["sha256"]
        self.runtime.update(LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256=self.generation,
                            DIRECTOR_PROVIDER_VALIDATION_GENERATION=self.generation)
        self.bridge["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"] = self.generation
        self.validation["generation"] = self.generation
        self.compose["services"]["orchestrator"]["environment"]["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"] = self.generation
        self.spec["runtime_env"] = self.pin_env("runtime.env", self.runtime)
        self.spec["bridge_env"] = self.pin_env("bridge.env", self.bridge)
        self.spec["validation_manifest"] = self.pin("validation.json", release.encoded(self.validation))
        spec["generation_manifest"] = self.pin("sibling-generation.json", release.encoded(manifest))
        generation = spec["generation_manifest"]["sha256"]
        runtime = dict(self.runtime, LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256=generation,
                       DIRECTOR_PROVIDER_VALIDATION_GENERATION=generation, ALLOW_PAID_RUNS="false",
                       DIRECTOR_PROVIDER_VALIDATION_ENABLED="false",
                       LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED="false", LANGGRAPH_PARALLEL_KILL_SWITCH="true",
                       DIRECTOR_HOST_PORT="18080")
        spec["runtime_env"] = self.pin_env("sibling-runtime.env", runtime)
        spec["bridge_env"] = self.pin_env("sibling-bridge.env", dict(self.bridge, LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256=generation))
        spec["validation_manifest"] = self.pin("sibling-validation.json", release.encoded(dict(self.validation, generation=generation)))
        self.sibling_compose["services"]["orchestrator"]["environment"]["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"] = generation
        self.spec["rehearsal"] = {"descriptor": self.pin("sibling.json", release.encoded(spec)),
            "acceptance_artifact": self.pin("sibling-acceptance.json", b'{"fixture":"never accepted by runner"}'),
            "backup_path": str(Path(spec["output_directory"]) / "rehearsal.dump"), "host_port": 18080}
        self.save()
        return spec

    def test_rehearsal_emits_existing_control_contract_only_for_sibling_without_execution(self):
        sibling = self.sibling()
        with patch.dict(os.environ, {"DIRECTOR_HOST_PORT": "8080", "ALLOW_PAID_RUNS": "true"}):
            report = release.Release(self.descriptor).rehearsal()
        self.assertEqual(report["status"], "PLANNED")
        self.assertFalse(report["executed"])
        self.assertLess(report["timeout_seconds"], 1500)
        actions = [row["action"] for row in report["steps"]]
        self.assertEqual(actions, ["Preflight", "Accept", "Migrate", "Backup", "RestoreDrill", "StageRuntime",
                                   "PrepareOfflineHandoff", "ActivateOffline", "Health", "Rollback", "Health"])
        for step in report["steps"]:
            argv = step["control_argv"]
            self.assertEqual(argv[argv.index("-ComposeProject") + 1], "fixture-sibling")
            self.assertEqual(argv[argv.index("-EnvironmentFile") + 1], sibling["runtime_env"]["path"])
        self.assertEqual(report["environment"]["COMPOSE_FILE"], self.spec["compose_files"][0]["path"])
        self.assertEqual(report["environment"]["DIRECTOR_HOST_PORT"], "18080")
        self.assertEqual(report["environment"]["ALLOW_PAID_RUNS"], "false")
        self.assertEqual(report["environment"]["LANGGRAPH_PARALLEL_KILL_SWITCH"], "true")
        self.assertFalse(any("windows-langgraph-primary-control.ps1" in str(arg) for call, _ in self.calls for arg in call))
        self.assertEqual(list(Path(sibling["output_directory"]).iterdir()), [])

    def test_actual_launcher_replaces_environment_and_rechecks_daemon_and_images(self):
        self.sibling()
        ambient = {"DOCKER_HOST": "tcp://remote:2376", "DOCKER_CONTEXT": "remote",
                   "DOCKER_CONFIG": "/untrusted", "DOCKER_TLS_VERIFY": "1", "DOCKER_CERT_PATH": "/untrusted",
                   "COMPOSE_FILE": "wrong.yml", "OPENAI_API_KEY": "fixture-never-forward"}
        with patch.dict(os.environ, ambient):
            plan = release.Release(self.descriptor).rehearsal()
            step = plan["steps"][0]
            payload = json.loads(base64.b64decode(step["argv"][-1]))
            payload["control_argv"] = [sys.executable, "-I", "-c",
                "import json,os,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(os.environ)))",
                str(self.root / "observed-environment.json")]
            observed_calls = []
            def read(argv, **kwargs):
                observed_calls.append((argv, kwargs))
                if argv[0] == "git":
                    return self.platform(argv, **kwargs)
                if argv[1] == "info":
                    return payload["daemon_id"].encode()
                if argv[1] in ("ps", "volume", "network"):
                    return b""
                return release.encoded([{"Id": payload["images"][argv[-1]]}])
            # Only Docker observation is mocked. The emitted launcher invokes a
            # real harmless child, which records its actual replacement env.
            with patch.object(sys, "argv", ["launcher", base64.b64encode(release.encoded(payload)).decode()]), \
                    patch.object(subprocess, "check_output", side_effect=read):
                exec(compile(step["argv"][-2], "emitted-launcher", "exec"), {})
            observed = json.loads((self.root / "observed-environment.json").read_text())
            self.assertEqual(observed["DOCKER_HOST"], "npipe:////./pipe/docker_engine")
            for key in ambient.keys() - {"DOCKER_HOST", "COMPOSE_FILE"}:
                self.assertNotIn(key, observed)
            self.assertEqual(observed["COMPOSE_FILE"], payload["environment"]["COMPOSE_FILE"])
            self.assertTrue(all(item[1]["env"] == payload["environment"] for item in observed_calls))
            for change in ("daemon", "image", "rollback"):
                with self.subTest(change=change):
                    def drift(argv, **kwargs):
                        if argv[0] == "git":
                            return self.platform(argv, **kwargs)
                        if argv[1] == "info":
                            return b"different-daemon" if change == "daemon" else payload["daemon_id"].encode()
                        if argv[1] in ("ps", "volume", "network"):
                            return b""
                        return release.encoded([{"Id": "sha256:" + "0" * 64 if change == "image" else payload["images"][argv[-1]]}])
                    if change == "rollback":
                        Path(payload["prior_images"]).write_text('{"orchestrator_image_id":"unverified"}')
                    with patch.object(sys, "argv", ["launcher", base64.b64encode(release.encoded(payload)).decode()]), \
                            patch.object(subprocess, "check_output", side_effect=drift), \
                            patch.object(subprocess, "run") as child:
                        with self.assertRaises(SystemExit):
                            exec(compile(step["argv"][-2], "emitted-launcher", "exec"), {})
                        child.assert_not_called()

    def test_real_git_fsmonitor_cannot_execute_before_dirty_gate(self):
        marker, hook = self.root / "hook-ran", self.root / "fsmonitor-hook"
        hook.write_text('#!/bin/sh\nprintf invoked > "' + marker.as_posix() + '"\nprintf "token\\0"\n', encoding="utf-8")
        hook.chmod(0o700)
        def git(*args, env=None):
            return subprocess.check_output(["git", *args], cwd=self.source,
                                           env=env or release.child_environment(), stderr=subprocess.DEVNULL)
        git("init", "-q")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "fixture")
        git("config", "core.fsmonitor", hook.as_posix())
        unsafe_env = {key: value for key, value in release.child_environment().items() if not key.startswith("GIT_CONFIG_")}
        git("status", "--porcelain=v2", env=unsafe_env)
        self.assertTrue(marker.exists(), "fixture must demonstrate executable fsmonitor before testing its suppression")
        marker.unlink()
        tool = release.Release.__new__(release.Release)
        tool.source, tool.deadline = self.source, time.monotonic() + 30
        tool.spec = {"candidate": {"commit": git("rev-parse", "HEAD").decode().strip(),
                                    "tree": git("rev-parse", "HEAD^{tree}").decode().strip()}}
        with patch.object(release, "command", REAL_COMMAND):
            with self.assertRaisesRegex(release.ReleaseError, "source_not_clean"):
                tool.source_check()
        self.assertFalse(marker.exists())
        git("config", "filter.fixture.clean", hook.as_posix())
        with patch.object(release, "command", REAL_COMMAND):
            with self.assertRaisesRegex(release.ReleaseError, "executable_git_filter_refused"):
                tool.source_check()
        self.assertFalse(marker.exists())

    def test_emitted_launcher_rechecks_real_git_before_every_control_step(self):
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        real_read = subprocess.check_output
        for defect in (None, "dirty", "dirty_after_probe", "commit", "tree", "filter", "submodule", "network"):
            for first_step in (True, False):
                with self.subTest(defect=defect, first_step=first_step), tempfile.TemporaryDirectory() as directory:
                    checkout = Path(directory)
                    def git(*args):
                        return real_read(["git", *args], cwd=checkout, env=release.child_environment(),
                                         stderr=subprocess.DEVNULL)
                    git("init", "-q")
                    control = checkout / "control.ps1"
                    control.write_text("# original control\n")
                    git("add", "control.ps1")
                    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "fixture")
                    payload = json.loads(base64.b64decode(plan["steps"][0]["argv"][-1]))
                    payload.update(cwd=str(checkout), first_step=first_step,
                        candidate={kind: git("rev-parse", "HEAD^{" + kind + "}").decode().strip()
                                   for kind in ("commit", "tree")})
                    if defect == "dirty":
                        control.write_text("# changed control\n")
                    elif defect in ("commit", "tree"):
                        payload["candidate"][defect] = "0" * 40
                    elif defect == "filter":
                        git("config", "filter.fixture.clean", "never-execute-this")
                    elif defect == "submodule":
                        (checkout / ".gitmodules").write_text("# unsupported\n")
                    def read(argv, **kwargs):
                        if argv[0] == "git":
                            return REAL_COMMAND(argv, cwd=kwargs["cwd"], env=kwargs["env"],
                                                deadline=time.monotonic() + 30)
                        if argv[1] == "info":
                            if defect == "dirty_after_probe":
                                control.write_text("# changed during Docker observations\n")
                            return payload["daemon_id"].encode()
                        if argv[1] == "image":
                            return release.encoded([{"Id": payload["images"][argv[-1]]}])
                        if argv[1] == "network" and defect == "network":
                            return payload["fresh_networks"][0].encode()
                        return b""
                    with patch.object(sys, "argv", ["launcher", base64.b64encode(release.encoded(payload)).decode()]), \
                            patch.object(subprocess, "check_output", side_effect=read), \
                            patch.object(subprocess, "run") as child:
                        if defect and not (defect == "network" and not first_step):
                            with self.assertRaises(SystemExit):
                                exec(compile(release.REHEARSAL_LAUNCH, "launcher", "exec"), {})
                            child.assert_not_called()
                        else:
                            exec(compile(release.REHEARSAL_LAUNCH, "launcher", "exec"), {})
                            child.assert_called_once()

    def test_control_timeout_preserves_quiescence_and_full_health_wait(self):
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        for step in plan["steps"]:
            payload = json.loads(base64.b64decode(step["argv"][-1]))
            health = int(step["control_argv"][step["control_argv"].index("-TimeoutSeconds") + 1])
            quiescence = {"Rollback": 65, "ActivateOffline": 75}.get(step["action"], 0)
            self.assertGreater(payload["control_timeout_seconds"], health + quiescence)
            self.assertLessEqual(payload["control_timeout_seconds"], 260)
            if quiescence:
                def read(argv, **kwargs):
                    return self.platform(argv, **kwargs)
                with patch.object(sys, "argv", ["launcher", step["argv"][-1]]), \
                        patch.object(subprocess, "check_output", side_effect=read), \
                        patch.object(subprocess, "run") as child:
                    exec(compile(step["argv"][-2], "launcher", "exec"), {})
                    self.assertGreater(child.call_args.kwargs["timeout"], health + quiescence)

    def test_sibling_rejects_duplicate_host_bindings_but_allows_distinct_protocols(self):
        self.sibling()
        graph = copy.deepcopy(self.sibling_compose)
        port = copy.deepcopy(graph["services"]["orchestrator"]["ports"][0])
        graph["services"]["n8n"]["ports"] = [dict(port, target=5678, protocol="tcp")]
        with self.assertRaisesRegex(release.ReleaseError, "sibling_port_collision"):
            release.isolated_sibling(self.compose, graph, 18080)
        graph["services"]["n8n"]["ports"][0]["protocol"] = "udp"
        release.isolated_sibling(self.compose, graph, 18080)
        graph["services"]["orchestrator"]["ports"].append(copy.deepcopy(port))
        with self.assertRaisesRegex(release.ReleaseError, "sibling_port_collision"):
            release.isolated_sibling(self.compose, graph, 18080)

    def test_supported_graph_covers_transitive_services_and_rejects_unsafe_variants(self):
        self.sibling()
        graph = copy.deepcopy(self.sibling_compose)
        graph["services"].update({name: {"image": "postgres:16@sha256:" + "1" * 64, "pull_policy": "never"}
                                   for name in ("n8n-db", "langgraph-primary-db")})
        graph["services"]["slackbot"]["depends_on"] = {"orchestrator": {}, "n8n": {}}
        graph["services"]["orchestrator"]["depends_on"] = {"langgraph-primary-db": {}}
        graph["services"]["n8n"].update(depends_on={"n8n-db": {}},
            environment={"DB_TYPE": "postgresdb", "DB_POSTGRESDB_HOST": "n8n-db"})
        release.isolated_sibling(self.compose, graph, 18080)
        changes = [
            ("n8n", "image", "n8n:latest"), ("n8n-db", "pull_policy", "always"),
            ("n8n", "build", {"context": "."}), ("n8n", "command", ["sh", "-c", "unapproved"]),
            ("slackbot", "depends_on", {"rogue": {}}),
            ("slackbot", "command", ["python", "-c", "unapproved"]),
            ("slackbot", "environment", {"ALLOW_PAID_RUNS": "true"}),
            ("orchestrator", "environment", dict(graph["services"]["orchestrator"]["environment"], CHATGPT_BRIDGE_ALLOW_PAID_RUNS="true")),
            ("slackbot", "environment", dict(graph["services"]["slackbot"]["environment"], ORCHESTRATOR_BASE_URL="https://production")),
            ("n8n", "environment", {"NODE_OPTIONS": "--require malicious"}),
            ("n8n", "environment", {"DB_TYPE": "postgresdb", "DB_POSTGRESDB_HOST": "production"}),
            ("n8n-db", "environment", {"POSTGRES_INITDB_ARGS": "unapproved"}),
            ("n8n", "use_api_socket", True),
            ("orchestrator", "use_api_socket", True),
            ("slackbot", "use_api_socket", True),
            ("n8n-db", "use_api_socket", True),
            ("langgraph-primary-db", "use_api_socket", True),
        ]
        for name, key, value in changes:
            with self.subTest(service=name, key=key):
                changed = copy.deepcopy(graph)
                changed["services"][name][key] = value
                with self.assertRaises(release.ReleaseError):
                    release.isolated_sibling(self.compose, changed, 18080)
        graph["services"]["rogue"] = copy.deepcopy(graph["services"]["n8n"])
        with self.assertRaisesRegex(release.ReleaseError, "sibling_service_graph_unsupported"):
            release.isolated_sibling(self.compose, graph, 18080)

    def test_health_mapping_requires_tcp(self):
        self.sibling()
        port = self.sibling_compose["services"]["orchestrator"]["ports"][0]
        for protocol in (None, "tcp", "udp"):
            with self.subTest(protocol=protocol):
                if protocol is None:
                    port.pop("protocol", None)
                else:
                    port["protocol"] = protocol
                if protocol == "udp":
                    with self.assertRaisesRegex(release.ReleaseError, "sibling_host_port_mismatch"):
                        release.isolated_sibling(self.compose, self.sibling_compose, 18080)
                else:
                    release.isolated_sibling(self.compose, self.sibling_compose, 18080)

    def test_acceptance_modes_and_required_inputs_match_control(self):
        for mode in (None, "github-v1", "unknown", "provider-v2", "provider-v3-two-party"):
            with self.subTest(mode=mode):
                runtime = dict(self.runtime)
                runtime.pop("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE")
                if mode is not None:
                    runtime["LANGGRAPH_PRIMARY_ACCEPTANCE_MODE"] = mode
                if mode in ("provider-v2", "provider-v3-two-party"):
                    release.acceptance_inputs(runtime)
                    for key in ("PATH", "SHA256", "DIRECTORY"):
                        changed = dict(runtime)
                        changed.pop("LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_" + key)
                        with self.assertRaisesRegex(release.ReleaseError, "generation_acceptance_inputs_missing"):
                            release.acceptance_inputs(changed)
                else:
                    with self.assertRaisesRegex(release.ReleaseError, "generation_acceptance_mode_unsupported"):
                        release.acceptance_inputs(runtime)
        invalid = dict(self.runtime, LANGGRAPH_PRIMARY_ACCEPTANCE_REGISTRY_SHA256="wrong")
        with self.assertRaisesRegex(release.ReleaseError, "generation_acceptance_digest_invalid"):
            release.acceptance_inputs(invalid)

    def test_offline_plan_rejects_owner_local_mode_and_enabled_validation(self):
        sibling = self.sibling()
        original = release.dotenv(Path(sibling["runtime_env"]["path"]).read_bytes())
        for key, value, error in (
                ("LANGGRAPH_PRIMARY_ACCEPTANCE_MODE", "owner-local-evidence-v1", "sibling_offline_acceptance_mode_unsupported"),
                ("DIRECTOR_PROVIDER_VALIDATION_ENABLED", "true", "sibling_offline_handoff_profile_invalid")):
            with self.subTest(key=key):
                sibling["runtime_env"] = self.pin_env("sibling-runtime.env", dict(original, **{key: value}))
                self.spec["rehearsal"]["descriptor"] = self.pin("sibling.json", release.encoded(sibling))
                self.save()
                with self.assertRaisesRegex(release.ReleaseError, error):
                    release.Release(self.descriptor).rehearsal()

    def test_remote_docker_context_is_refused_before_image_lookup(self):
        tool = release.Release(self.descriptor)
        def platform(argv, **kwargs):
            if argv[:2] == ["docker", "context"]:
                return release.encoded([{"Endpoints": {"docker": {"Host": "tcp://remote:2376"}}}])
            return self.platform(argv, **kwargs)
        with patch.object(release, "command", side_effect=platform):
            with self.assertRaisesRegex(release.ReleaseError, "docker_target_not_local"):
                tool.image_check()
        self.assertFalse(any(argv[:3] == ["docker", "image", "inspect"] for argv, _ in self.calls))

    def test_snapshot_must_match_effective_runtime_authority(self):
        tool = release.Release(self.descriptor)
        original = copy.deepcopy(tool.spec["snapshot"])
        for key in ("repository", "starting_ref", "commit", "tree"):
            with self.subTest(key=key):
                tool.spec["snapshot"] = dict(original, **{key: "wrong"})
                with self.assertRaisesRegex(release.ReleaseError, "runtime_snapshot_authority_mismatch"):
                    tool.selectors_check()
        tool.spec["snapshot"] = original
        tool.runtime["LANGGRAPH_PRIMARY_PROJECT_EXECUTION_PINNED_COMMIT_SHA"] = "1" * 40
        with self.assertRaisesRegex(release.ReleaseError, "runtime_snapshot_authority_invalid"):
            tool.selectors_check()
        tool.runtime["LANGGRAPH_PRIMARY_PROJECT_EXECUTION_PINNED_TREE_SHA"] = "2" * 40
        with self.assertRaisesRegex(release.ReleaseError, "runtime_snapshot_authority_mismatch"):
            tool.selectors_check()
        tool.spec["snapshot"].update(commit="1" * 40, tree="2" * 40)
        tool.selectors_check()

    def test_cross_release_output_snapshot_overlap_is_refused_before_probes(self):
        sibling = self.sibling()
        sibling_snapshot = self.root / "separate-snapshot"
        sibling_snapshot.mkdir()
        sibling["snapshot"]["path"] = str(sibling_snapshot)
        self.spec["output_directory"] = str(sibling_snapshot)
        self.spec["rehearsal"]["descriptor"] = self.pin("sibling.json", release.encoded(sibling))
        self.save()
        with self.assertRaisesRegex(release.ReleaseError, "cross_release_output_overlap"):
            release.Release(self.descriptor).rehearsal()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(sibling_snapshot.iterdir()), [])

    def test_rehearsal_requires_cached_images_fresh_state_and_no_prior_rollback(self):
        sibling = self.sibling()
        tool = release.Release(self.root / "sibling.json")
        graph = self.sibling_compose
        graph["services"]["n8n"]["volumes"] = [{"type": "volume", "source": "data", "target": "/home/node/.n8n"}]
        tool.rehearsal_images(graph)
        for defect in ("container", "image", "volume", "network", "prior"):
            with self.subTest(defect=defect):
                def platform(argv, **kwargs):
                    if defect == "container" and argv[:2] == ["docker", "ps"]:
                        return b"existing-container"
                    if defect == "image" and argv[:3] == ["docker", "image", "inspect"] and "n8n" in argv[-1]:
                        raise release.ReleaseError("platform_check_failed")
                    if defect == "volume" and argv[:2] == ["docker", "volume"]:
                        return b"fixture-sibling_data"
                    if defect == "network" and argv[:2] == ["docker", "network"]:
                        return b"fixture-sibling_default"
                    return self.platform(argv, **kwargs)
                if defect == "prior":
                    Path(tool.prior_images_path()).write_text("{}")
                with patch.object(release, "command", side_effect=platform):
                    with self.assertRaises(release.ReleaseError):
                        tool.rehearsal_images(graph)

    def test_rehearsal_rejects_resolved_slack_overrides_even_when_profiles_differ(self):
        sibling = self.sibling()
        for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_DIRECTOR_CHANNEL_ID"):
            with self.subTest(key=key):
                self.compose["services"]["slackbot"]["environment"] = {key: "shared-fixture-identity"}
                self.sibling_compose["services"]["slackbot"]["environment"] = {key: "shared-fixture-identity"}
                self.repin_graph(self.spec, self.compose)
                self.repin_graph(sibling, self.sibling_compose)
                with self.assertRaisesRegex(release.ReleaseError, "sibling_slack_identity_not_isolated"):
                    release.Release(self.descriptor).rehearsal()

    def test_rehearsal_rejects_effective_paid_override_of_unpaid_profile(self):
        sibling = self.sibling()
        self.sibling_compose["services"]["orchestrator"]["environment"]["ALLOW_PAID_RUNS"] = "true"
        self.repin_graph(sibling, self.sibling_compose)
        with self.assertRaisesRegex(release.ReleaseError, "sibling_must_be_unpaid"):
            release.Release(self.descriptor).rehearsal()

    def test_rehearsal_rejects_writable_binds_to_either_release_protected_paths(self):
        sibling = self.sibling()
        final_tool, sibling_tool = release.Release(self.descriptor), release.Release(self.root / "sibling.json")
        protected = final_tool.protected_paths() + sibling_tool.protected_paths()
        for path in protected:
            with self.subTest(path=path):
                graph = copy.deepcopy(self.sibling_compose)
                graph["services"]["orchestrator"]["volumes"].append(
                    {"type": "bind", "source": str(path), "target": "/writable-escape", "read_only": False})
                self.sibling_compose = graph
                self.repin_graph(sibling, graph)
                with self.assertRaisesRegex(release.ReleaseError, "sibling_shared_write_mount"):
                    release.Release(self.descriptor).rehearsal()
                graph["services"]["orchestrator"]["volumes"].pop()
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(list(sibling_tool.output.iterdir()), [])

    def test_rehearsal_checks_final_observed_database_before_comparing_isolation(self):
        self.sibling()
        self.reply_changes["database"] = {self.generation: self.sibling_activation}
        with self.assertRaisesRegex(release.ReleaseError, "database_binding_mismatch"):
            release.Release(self.descriptor).rehearsal()
        requests = [json.loads(args["input_bytes"]) for argv, args in self.calls if argv[-1] == release.DATABASE_PROBE]
        self.assertEqual([item["generation"] for item in requests], [self.generation])

    def test_rehearsal_compares_both_observed_database_bindings(self):
        sibling = self.sibling()
        manifest = json.loads(Path(sibling["generation_manifest"]["path"]).read_bytes())
        for key in ("database_identity", "database_instance_id"):
            manifest["activation"][key] = self.activation[key]
        self.sibling_activation = manifest["activation"]
        Path(sibling["generation_manifest"]["path"]).write_bytes(release.encoded(manifest))
        self.repin_graph(sibling, self.sibling_compose)
        with self.assertRaisesRegex(release.ReleaseError, "sibling_database_not_isolated"):
            release.Release(self.descriptor).rehearsal()
        requests = [json.loads(args["input_bytes"]) for argv, args in self.calls if argv[-1] == release.DATABASE_PROBE]
        self.assertEqual([item["generation"] for item in requests], [self.generation, sibling["generation_manifest"]["sha256"]])

    def test_sibling_also_requires_an_explicit_owner_pin(self):
        self.sibling()
        del self.owner_spec["release_preparations"]["fixture-sibling"]
        self.owner_descriptor.write_bytes(release.encoded(self.owner_spec))
        with self.assertRaisesRegex(release.ReleaseError, "preparation_not_owner_pinned"):
            release.Release(self.descriptor).rehearsal()
        self.assertEqual(self.calls, [])

    def test_rehearsal_refuses_reusing_final_identity_or_paths(self):
        sibling = self.sibling()
        for field, value in (("compose_project", self.spec["compose_project"]),
                             ("output_directory", self.spec["output_directory"]),
                             ("runtime_env", self.spec["runtime_env"]),
                             ("generation_manifest", self.spec["generation_manifest"])):
            with self.subTest(field=field):
                altered = dict(sibling, **{field: value})
                self.spec["rehearsal"]["descriptor"] = self.pin("sibling.json", release.encoded(altered))
                self.save()
                with self.assertRaises(release.ReleaseError):
                    release.Release(self.descriptor).rehearsal()
        self.assertEqual(self.calls, [])

    def test_sibling_topology_rejects_shared_volume_network_port_or_writable_bind(self):
        self.sibling()
        for field in ("port", "network", "volume", "bind", "host"):
            with self.subTest(field=field):
                graph = copy.deepcopy(self.sibling_compose)
                service = graph["services"]["orchestrator"]
                if field == "port":
                    service["ports"][0]["published"] = "8080"
                elif field == "network":
                    graph["networks"]["default"]["name"] = "fixture-director_default"
                elif field == "volume":
                    graph["volumes"]["data"]["driver_opts"] = {"device": "/production"}
                elif field == "bind":
                    service["volumes"][0]["read_only"] = False
                else:
                    service["network_mode"] = "host"
                with self.assertRaises(release.ReleaseError):
                    release.isolated_sibling(self.compose, graph, 18080)

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI required; configuration-only, no daemon operation")
    def test_sibling_guard_consumes_actual_compose_rendering(self):
        self.sibling()
        graphs = []
        for value in (self.compose, self.sibling_compose):
            path = self.root / (value["name"] + ".json")
            path.write_bytes(release.encoded(value))
            result = subprocess.run(["docker", "compose", "--project-name", value["name"], "--env-file",
                self.spec["runtime_env"]["path"], "-f", str(path), "config", "--format", "json"],
                cwd=self.root, env=release.child_environment(), capture_output=True, timeout=180)
            self.assertEqual(result.returncode, 0, "configuration-only Compose producer failed")
            graphs.append(json.loads(result.stdout))
            self.assertRegex(resolved_deployment_digest(graphs[-1], self.image), r"^[0-9a-f]{64}$")
        release.isolated_sibling(*graphs, 18080)
        graphs[1]["services"]["orchestrator"]["ports"][0]["published"] = "8080"
        with self.assertRaisesRegex(release.ReleaseError, "sibling_port_not_isolated"):
            release.isolated_sibling(*graphs, 18080)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell required for actual control parameter binding")
    def test_rehearsal_argv_binds_to_real_control_parameters_without_running_its_body(self):
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        source = (ROOT / "scripts/windows-langgraph-primary-control.ps1").read_text(encoding="utf-8-sig")
        # Reuse the real consumer's parameter block and PowerShell binder. Its
        # runtime body is intentionally absent: no Docker, profile write or gate runs.
        parameters = source[source.index("[CmdletBinding()]"):source.index('$ErrorActionPreference = "Stop"')]
        harness = self.root / "control-parameters-only.ps1"
        harness.write_text(parameters + '\n$PSBoundParameters | ConvertTo-Json -Compress\n', encoding="utf-8")
        for step in plan["steps"]:
            arguments = step["control_argv"][step["control_argv"].index("-File") + 2:]
            result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(harness), *arguments],
                                    cwd=self.root, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, "generated arguments rejected by actual control parameter contract")
            observed = json.loads(result.stdout)
            self.assertEqual(observed["Action"], step["action"])
            self.assertEqual(observed["ComposeProject"], "fixture-sibling")
            self.assertEqual(observed["HostPort"], 18080)

    def test_descriptor_rejects_bypass_fields_and_output_inside_checkout(self):
        self.spec["allow_unverified_release"] = True
        self.save()
        with self.assertRaisesRegex(release.ReleaseError, "invalid_release_descriptor"):
            release.Release(self.descriptor)
        del self.spec["allow_unverified_release"]
        self.spec["output_directory"] = str(self.source)
        self.save()
        with self.assertRaisesRegex(release.ReleaseError, "output_must_be_outside_checkout"):
            release.Release(self.descriptor)
        self.assertEqual(self.calls, [])

    def test_lock_excludes_second_writer_and_unrecorded_output_is_preserved(self):
        with release.locked(self.output / "fixture-r1.lock"):
            with self.assertRaisesRegex(release.ReleaseError, "another_release_writer"):
                release.Release(self.descriptor).prepare()
        target = self.output / "fixture-r1.activation.compose.json"
        target.write_bytes(b"already exists")
        with self.assertRaisesRegex(release.ReleaseError, "unrecorded_output_requires_reconciliation"):
            release.Release(self.descriptor).prepare()
        self.assertEqual(target.read_bytes(), b"already exists")
        self.assertEqual(self.calls, [])

    def test_unsupported_execution_options_are_not_exposed(self):
        for args in (["run"], ["prepare", "--rehearsal"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                release.main([*args, "--descriptor", str(self.descriptor)])
            self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.calls, [])

    def test_local_database_probe_is_explicitly_read_only_without_bootstrap(self):
        self.assertIn("allow_insecure_local_transport=False", release.DATABASE_PROBE)
        self.assertIn("SET TRANSACTION READ ONLY", release.DATABASE_PROBE)
        self.assertNotIn("bootstrap=True", release.DATABASE_PROBE)
        self.assertNotIn("initialize", release.DATABASE_PROBE)


class ReleaseInputAndProcessTests(unittest.TestCase):
    def test_json_duplicate_and_nonfinite_values_are_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(release.ReleaseError):
                release.document(raw)

    def test_inherited_credentials_and_platform_overrides_are_not_forwarded(self):
        with patch.dict(os.environ, {"PROVIDER_KEY": "fixture", "COMPOSE_FILE": "wrong", "DOCKER_HOST": "wrong",
                                     "PYTHONPATH": "wrong", "GIT_DIR": "wrong"}):
            environment = release.child_environment()
        for key in ("PROVIDER_KEY", "COMPOSE_FILE", "DOCKER_HOST", "GIT_DIR"):
            self.assertNotIn(key, environment)
        self.assertEqual(environment["PYTHONPATH"], str(release.ROOT / "src"))

    def test_subprocess_deadline_is_enforced_and_nonzero_output_is_sanitized(self):
        with self.assertRaisesRegex(release.ReleaseError, "doctor_deadline_exhausted"):
            release.command([sys.executable, "-c", "import time; time.sleep(20)"], cwd=ROOT,
                            env=release.child_environment(), deadline=time.monotonic() + 0.2)
        with self.assertRaisesRegex(release.ReleaseError, "^platform_check_failed$"):
            release.command([sys.executable, "-c", "import sys; sys.stderr.write('fixture-secret'); sys.exit(1)"],
                            cwd=ROOT, env=release.child_environment(), deadline=time.monotonic() + 5)


if __name__ == "__main__":
    unittest.main()
