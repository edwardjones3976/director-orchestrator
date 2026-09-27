# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:35 UTC | BEGIN]]
"""Offline preparation contracts; all Docker, bridge and database I/O is mocked."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
import importlib.util
import io
import json
import os
from pathlib import Path
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
            "executables": {"python": sys.executable, "powershell": sys.executable},
            "output_directory": str(self.output), "timeout_seconds": 30}
        self.descriptor = self.root / "descriptor.json"
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
            return release.encoded(self.compose)
        if "-File" in argv:
            self.assertIn(argv[argv.index("-Action") + 1], ("Verify", "Preflight"))
            return b"read-only fixture proof"
        if argv[-1] == release.BRIDGE_PROBE:
            return release.encoded({"ok": True, "pinned_environment": True,
                "configuration_digests": self.reply_changes.get("bridge", {"grok": "e" * 64})})
        if argv[-1] == release.DATABASE_PROBE:
            return release.encoded({"database_identity": self.activation["database_identity"],
                "database_instance_id": self.activation["database_instance_id"],
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
        self.assertEqual(len(release.read_journal(journal, release.sha(self.descriptor.read_bytes()))), 2)
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

    def test_interrupted_receipt_is_ambiguous_and_cannot_replay(self):
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
        with self.assertRaisesRegex(release.ReleaseError, "reconciliation_required_no_replay"):
            release.Release(self.descriptor).prepare(resume=True)
        self.assertEqual(len(self.calls), calls)
        self.assertEqual((self.output / "fixture-r1.activation.compose.json").read_bytes(), original)

    def test_truncated_tampered_and_other_descriptor_journals_fail_closed(self):
        tool = release.Release(self.descriptor)
        journal = self.output / "fixture-r1.jsonl"
        journal.write_bytes(b'{"event":"intent"}')
        with self.assertRaisesRegex(release.ReleaseError, "incomplete_journal"):
            tool.prepare(resume=True)
        journal.write_bytes(b"")
        release.append_record(journal, {"event": "intent", "descriptor_sha256": "0" * 64,
            "step": "render_compose", "sequence": 1})
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
