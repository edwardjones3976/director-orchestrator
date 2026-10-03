# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:35 UTC | BEGIN]]
"""Preparation contracts using the real A2 reader in isolated temporary owner roots."""
import copy
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from director_orchestrator.provider_activation import ProviderActivationBinding


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("director_release_under_test", ROOT / "scripts/director-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


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
        self.activation = asdict(release.ActivationIdentity(
            candidate_commit=self.candidate, candidate_tree=self.tree, profile_id="fixture",
            compose_project="fixture-director", database_identity="postgres:sha256:" + "c" * 64,
            database_instance_id="11111111-1111-4111-8111-111111111111",
            postgres_schema="director_primary_v1", checkpoint_namespace="fixture-primary-v1"))
        self.compose = {"name": "fixture-director", "services": {
            "orchestrator": {"image": self.image,
                "command": ["uvicorn", "director_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"],
                "environment": {"DIRECTOR_RUNTIME_IMAGE": self.image, "DIRECTOR_RUNTIME_IMAGE_ID": self.image,
                    "DIRECTOR_ASGI_APP": "director_orchestrator.main:app", "ALLOW_PAID_RUNS": "false",
                    "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256": "0" * 64,
                    "ORCHESTRATOR_SHARED_SECRET": "fixture-protected-value"},
                "volumes": [{"type": "bind", "source": str(self.snapshot), "target": "/repository", "read_only": True}]},
            "slackbot": {"image": self.image, "pull_policy": "never",
                "command": ["python", "-m", "director_orchestrator.slack_bot"]}}}
        self.manifest = {"version": "director-primary-generation/v1", "activation": self.activation,
            "image_id": self.image, "resolved_compose_sha256": release.resolved_deployment_digest(self.compose, self.image),
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
        def fixture_account(path):
            module, raw = load(path)
            if callable(getattr(module, "account_home", None)):
                module.account_home = lambda: self.owner_root.parent
            return module, raw
        account_patch = patch.object(release, "launcher_module", side_effect=fixture_account)
        account_patch.start()
        self.addCleanup(account_patch.stop)
        self.owner_spec = {"version": "director-cli-release-descriptor/v1", "evaluator_sha": "c" * 40,
                           "security_base_sha": "d" * 40, "release_preparations": {}}
        self.save()
        self.calls = []
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

    def platform(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[:2] == ["git", "rev-parse"]:
            return (self.candidate if argv[-1] == "HEAD^{commit}" else self.tree).encode()
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
            return release.encoded({"ok": True, "pinned_environment": True,
                "configuration_digests": self.reply_changes.get("bridge", {"grok": "e" * 64})})
        if argv[-1] == release.DATABASE_PROBE:
            request = json.loads(kwargs["input_bytes"])
            activation = self.sibling_activation if request["generation"] != self.generation else self.activation
            return release.encoded({"database_identity": activation["database_identity"],
                "database_instance_id": activation["database_instance_id"],
                "occupied_slots": self.reply_changes.get("slots", [])})
        if Path(argv[-1]).name == "live-dispatch-preflight.py":
            return b"configuration-only checks"
        self.fail(f"Unexpected platform operation: {argv[:3]}")

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
        records = release.read_journal(journal, release.sha(self.descriptor.read_bytes()), self.owner_digest())
        self.assertEqual(len(records), 2)
        self.assertTrue(all(row["owner_descriptor_sha256"] == self.owner_digest() for row in records))
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
                with self.assertRaisesRegex(release.ReleaseError, "doctor_refused_preparation"):
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

    def test_missing_installation_or_A2_locator_fails_before_profiles_probes_and_writes(self):
        installed_raw, locator_raw = self.installed.read_bytes(), self.locator.read_bytes()
        for missing in (self.installed, self.locator):
            with self.subTest(missing=missing.name):
                missing.unlink()
                with patch.object(release, "pinned_file") as profiles, self.assertRaisesRegex(
                        release.ReleaseError, "trusted_descriptor_consumer_unavailable"):
                    release.Release(self.descriptor)
                profiles.assert_not_called()
                self.installed.write_bytes(installed_raw)
                self.locator.write_bytes(locator_raw)
        self.locator.write_text("# Pre-A2 locator has no OS-root consumer.\n")
        with self.assertRaisesRegex(release.ReleaseError, "trusted_descriptor_consumer_unavailable"):
            release.Release(self.descriptor)
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
        self.sibling_compose["services"]["orchestrator"]["ports"] = [{"host_ip": "127.0.0.1", "published": "18080", "target": 8080}]
        self.sibling_compose["volumes"] = {"data": {"name": "fixture-sibling_data"}}
        manifest = copy.deepcopy(self.manifest)
        manifest["activation"].update(compose_project="fixture-sibling", database_identity="postgres:sha256:" + "d" * 64,
                                       database_instance_id="22222222-2222-4222-8222-222222222222")
        self.sibling_activation = manifest["activation"]
        for graph, item in ((self.compose, self.manifest), (self.sibling_compose, manifest)):
            item["resolved_compose_sha256"] = release.resolved_deployment_digest(graph, self.image)
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
        report = release.Release(self.descriptor).rehearsal()
        self.assertEqual(report["status"], "PLANNED")
        self.assertFalse(report["executed"])
        self.assertLess(report["timeout_seconds"], 1500)
        actions = [row["action"] for row in report["steps"]]
        self.assertEqual(actions, ["Preflight", "Accept", "Migrate", "Backup", "RestoreDrill", "StageRuntime",
                                   "PrepareOfflineHandoff", "ActivateOffline", "Health", "Rollback", "Health"])
        for step in report["steps"]:
            argv = step["argv"]
            self.assertEqual(argv[argv.index("-ComposeProject") + 1], "fixture-sibling")
            self.assertEqual(argv[argv.index("-EnvironmentFile") + 1], sibling["runtime_env"]["path"])
        self.assertEqual(report["environment"]["COMPOSE_FILE"], self.spec["compose_files"][0]["path"])
        self.assertFalse(any("windows-langgraph-primary-control.ps1" in str(arg) for call, _ in self.calls for arg in call))
        self.assertEqual(list(Path(sibling["output_directory"]).iterdir()), [])

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
            self.assertRegex(release.resolved_deployment_digest(graphs[-1], self.image), r"^[0-9a-f]{64}$")
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
            arguments = step["argv"][step["argv"].index("-File") + 2:]
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
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:35 UTC | END]]
