"""Preparation contracts using the real A2 reader in isolated temporary owner roots."""
import copy
import base64
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
import importlib.util
import io
import json
import os
import re
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
from director_orchestrator import process_isolation


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("director_release_under_test", ROOT / "scripts/director-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
REAL_COMMAND = release.command
REAL_CONTAINED_LAUNCH = process_isolation.launch_contained_process
REAL_CONTAINED_STOP = process_isolation.stop_contained_process
REAL_POPEN = subprocess.Popen
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
        tools = self.root / "trusted-tools"
        tools.mkdir()
        for name in ("git", "docker", "gh"):
            (tools / name).write_bytes(b"fixture command; platform calls are mocked")
        # Most tests fake the native boundary while still executing emitted
        # launcher logic. The real containment regression restores both helpers.
        def launch(argv, **kwargs):
            if kwargs.get("stdout") is not None:
                return subprocess.Popen(argv, **kwargs), None
            class Process:
                def wait(inner, timeout):
                    result = subprocess.run(argv, **kwargs, check=True, timeout=timeout)
                    code = getattr(result, "returncode", 0)
                    return code if isinstance(code, int) else 0
            return Process(), object()
        for name, replacement in (("launch_contained_process", launch), ("stop_contained_process", lambda *a, **kw: None)):
            helper = patch.object(process_isolation, name, replacement)
            helper.start()
            self.addCleanup(helper.stop)
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
                "volumes": [{"type": "bind", "source": str(self.snapshot), "target": "/repository", "read_only": True,
                             "bind": {"create_host_path": False}}]},
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
                            "powershell": str(Path(sys.executable).resolve()),
                            **{name: str(tools / name) for name in ("git", "docker", "gh")}},
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
        self.probe_rows = {}
        self.intake_rows = {}
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
        if argv[0] == self.spec["executables"]["docker"]:
            argv = ["docker", *argv[1:]]
        if argv[0] == "git":
            self.source_calls.append((argv, kwargs))
        else:
            self.calls.append((argv, kwargs))
        if argv[:2] == ["git", "config"]:
            return b""
        if argv[:2] in (["git", "ls-files"], ["git", "ls-tree"], ["git", "check-attr"], ["git", "hash-object"]):
            root = Path(kwargs.get("cwd", self.source))
            files = sorted(path for path in root.rglob("*") if path.is_file())
            def blob(path):
                raw = path.read_bytes()
                return release.hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if argv[1] == "ls-files":
                return b"".join(b"H " + path.relative_to(root).as_posix().encode() + b"\0" for path in files)
            if argv[1] == "ls-tree":
                return b"".join(("100644 blob " + blob(path) + "\t" + path.relative_to(root).as_posix() + "\0").encode() for path in files)
            raw = kwargs.get("input_bytes", kwargs.get("input"))
            if argv[1] == "check-attr":
                return b"".join(name + b"\0" + key.encode() + b"\0unspecified\0" for name in raw.split(b"\0") if name
                                for key in ("text", "eol", "ident", "working-tree-encoding", "filter"))
            return "\n".join(blob(root / json.loads(name)) for name in raw.decode().splitlines()).encode()
        if argv[:2] == ["docker", "context"]:
            return release.encoded([{"Endpoints": {"docker": {"Host": "npipe:////./pipe/docker_engine"}}}])
        if argv[:2] == ["docker", "info"]:
            return b"fixture-daemon"
        if argv[:3] == ["docker", "volume", "inspect"]:
            if argv[-1] in self.intake_rows:
                return release.encoded([self.intake_rows[argv[-1]]])
            key = argv[-1].removeprefix("fixture-sibling_")
            return release.encoded([{"Name": argv[-1], "Driver": "local", "Options": None,
                "Labels": {"com.docker.compose.project": "fixture-sibling", "com.docker.compose.volume": key},
                "Mountpoint": "/fixture/" + key, "CreatedAt": "2026-01-01T00:00:00Z", "Scope": "local"}])
        if argv[:3] == ["docker", "volume", "ls"]:
            return b"fixture-sibling_director-data\nfixture-sibling_langgraph-primary-db-data\n" + "\n".join(self.intake_rows).encode()
        if argv[:3] == ["docker", "volume", "create"]:
            name = argv[-1]
            if name not in self.intake_rows:
                row = json.loads(self.platform(["docker", "volume", "inspect", name]))[0]
                row["Labels"].update(dict(argv[i + 1].split("=", 1) for i, value in enumerate(argv) if value == "--label"))
                self.intake_rows[name] = row
            return name.encode()
        if argv[:2] == ["docker", "network"]:
            return b""
        if argv[:2] == ["docker", "ps"] and any(str(value).startswith("name=^/") for value in argv):
            name = next(value[len("name=^/"):-1] for value in argv if str(value).startswith("name=^/"))
            return "\n".join(key for key, row in self.probe_rows.items() if row["Name"] == "/" + name).encode()
        if argv[:2] == ["docker", "inspect"] and argv[-1] in self.probe_rows:
            return release.encoded([self.probe_rows[argv[-1]]])
        if argv[:2] == ["docker", "wait"] and argv[-1] in self.probe_rows:
            return b"0\n"
        if argv[:2] == ["docker", "logs"] and argv[-1] in self.probe_rows:
            return release.encoded(self.sibling_activation)
        if argv[:2] == ["docker", "rm"] and argv[-1] in self.probe_rows:
            del self.probe_rows[argv[-1]]
            return argv[-1].encode()
        if argv[:2] == ["docker", "ps"]:
            if "label=com.docker.compose.service=slackbot" in argv:
                return b"fixture-gateway"
            return b""
        if argv[:2] == ["docker", "inspect"] and argv[-1] == "fixture-gateway":
            return release.encoded([{"Id": "fixture-gateway", "Image": self.image, "Config": {"Labels": {
                "com.docker.compose.project": "fixture-sibling", "com.docker.compose.service": "slackbot",
                "com.docker.compose.oneoff": "False"}}, "State": {"Running": True, "Paused": False, "Restarting": False}}])
        if argv[:2] == ["git", "rev-parse"]:
            return self.reply_changes.get("commit" if argv[-1] == "HEAD^{commit}" else "tree",
                (self.candidate if argv[-1] == "HEAD^{commit}" else self.tree).encode())
        if argv[:2] == ["git", "status"]:
            return self.reply_changes.get("status", b"")
        if argv[:3] == ["docker", "image", "inspect"]:
            return release.encoded([{"Id": self.reply_changes.get("images", {}).get(
                argv[-1], self.reply_changes.get("image", self.image)), "Config": {
                "Labels": {"org.director.candidate.commit": self.candidate, "org.director.candidate.tree": self.tree},
                "WorkingDir": "/app", "Env": ["PATH=/usr/local/bin:/usr/bin:/bin", "PYTHONPATH=/app/src",
                    "PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1"],
                "Entrypoint": None}}])
        if argv[:2] == ["docker", "compose"]:
            if argv[-1] == release.LOCAL_DATABASE_PROBE:
                if "--detach" in argv:
                    name = argv[argv.index("--name") + 1]
                    container = release.sha(name.encode())
                    self.probe_rows[container] = {"Id": container, "Name": "/" + name, "Image": self.image,
                        "HostConfig": {"RestartPolicy": {"Name": "no"}},
                        "Config": {"Labels": {"org.director.release.probe": argv[argv.index("--label") + 1].partition("=")[2],
                            "com.docker.compose.project": "fixture-sibling", "com.docker.compose.service": "orchestrator",
                            "com.docker.compose.oneoff": "True"}}}
                    return container.encode()
                return release.encoded(self.sibling_activation)
            self.assertEqual(argv[-3:], ["config", "--format", "json"])
            self.assertIn(str(self.root / "compose.yml"), argv)
            graph = copy.deepcopy(self.sibling_compose if argv[3] == "fixture-sibling" else self.compose)
            # Model the stock Compose interpolation for the three intentional
            # future profile modes without changing the pinned on-disk profile.
            environment = kwargs.get("env", {})
            if argv[3] == "fixture-sibling" and "DIRECTOR_ASGI_APP" in environment:
                service = graph["services"]["orchestrator"]
                service["environment"].update({key: environment[key] for key in release.RUNTIME_CHANGES["StageRuntime"]})
                service["command"][1] = environment["DIRECTOR_ASGI_APP"]
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
        if argv[-1] == release.VALIDATION_PROBE:
            return release.encoded({"occupied_slots": self.reply_changes.get("slots", [])})
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
        # exact non-symlink executable and provide the existing dependency paths
        # through this test's trusted host adapter, never its runtime profile.
        # Do not swap interpreters or bypass unlinked().
        fixture_dependencies = {"PYTHONPATH": os.pathsep.join((str(ROOT / "src"), sysconfig.get_paths()["purelib"]))}
        # Relocated Linux tool-cache Python also needs its matching libpython;
        # child_environment deliberately excludes the parent's loader override.
        # The same explicit host adapter supplies that existing library.
        library = Path(sys.base_prefix) / "lib" / str(sysconfig.get_config_var("LDLIBRARY"))
        if sys.platform == "linux" and library.is_file():
            fixture_dependencies["LD_LIBRARY_PATH"] = str(library.parent.resolve(strict=True))
        self.spec["runtime_env"] = self.pin_env("runtime.env", tool.runtime)
        self.save()
        tool = release.Release(self.descriptor)
        tool.host_environment.update(fixture_dependencies)
        self.assertTrue(set(fixture_dependencies).isdisjoint(tool.runtime))
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
        self.assertNotIn("LD_LIBRARY_PATH", release.child_environment())
        env = dict(tool.host_environment)
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
            self.assertEqual(kwargs["env"]["PYTHONPATH"], fixture_dependencies["PYTHONPATH"])
            self.assertEqual(kwargs["env"].get("LD_LIBRARY_PATH"), fixture_dependencies.get("LD_LIBRARY_PATH"))
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
            LANGGRAPH_PARALLEL_KILL_SWITCH="true", LANGGRAPH_PRIMARY_ENABLED="false",
            LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED="false", LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED="false",
            LANGGRAPH_PRIMARY_POSTGRES_DSN="postgresql://director_primary:fixture-only@langgraph-primary-db:5432/director_primary?sslmode=disable",
            LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES="true")
        self.sibling_compose["services"]["orchestrator"]["ports"] = [{"host_ip": "127.0.0.1", "published": "18080", "target": 8080}]
        self.sibling_compose["volumes"] = {key: {"name": "fixture-sibling_" + key} for key in
            ("director-data", "langgraph-primary-db-data", "n8n-data")}
        self.sibling_compose["services"]["orchestrator"]["volumes"].append(
            {"type": "volume", "source": "director-data", "target": "/data"})
        self.sibling_compose["services"]["n8n"]["volumes"] = [
            {"type": "volume", "source": "n8n-data", "target": "/home/node/.n8n"}]
        self.sibling_compose["services"]["n8n"]["environment"] = {
            "N8N_HOST": "localhost", "N8N_PORT": "5678", "N8N_PROTOCOL": "http", "N8N_SECURE_COOKIE": "false",
            "N8N_DIAGNOSTICS_ENABLED": "false", "N8N_PERSONALIZATION_ENABLED": "false", "N8N_BLOCK_ENV_ACCESS_IN_NODE": "true"}
        self.sibling_compose["services"]["langgraph-primary-db"] = {
            "image": "postgres:17-alpine@sha256:" + "1" * 64, "pull_policy": "never",
            "environment": {"POSTGRES_USER": "director_primary", "POSTGRES_DB": "director_primary",
                            "POSTGRES_PASSWORD": "fixture-only"},
            "volumes": [{"type": "volume", "source": "langgraph-primary-db-data", "target": "/var/lib/postgresql/data"}],
            "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U director_primary -d director_primary"]}}
        self.sibling_compose["services"]["orchestrator"].update(
            depends_on={"langgraph-primary-db": {"condition": "service_healthy"}})
        for service in self.sibling_compose["services"].values():
            service["logging"] = {"driver": "local", "options": {}}
        self.sibling_compose["services"]["slackbot"]["environment"].update(
            SLACK_APP_TOKEN="fixture-app-token", SLACK_BOT_TOKEN="fixture-bot-token", SLACK_DIRECTOR_CHANNEL_ID="fixture-channel")
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
        self.assertEqual(actions, ["InitializeIntakeVolumes", "StartDatabase", "Preflight", "Accept", "Migrate", "Backup", "RestoreDrill", "StageRuntime",
                                   "PrepareOfflineHandoff", "ActivateOffline", "Health", "Rollback", "Health", "Stop"])
        for step in report["steps"]:
            if step["action"] in ("InitializeIntakeVolumes", "StartDatabase", "Stop"):
                continue
            argv = step["control_argv"]
            self.assertEqual(argv[argv.index("-ComposeProject") + 1], "fixture-sibling")
            self.assertEqual(argv[argv.index("-EnvironmentFile") + 1], sibling["runtime_env"]["path"])
        self.assertEqual(report["environment"]["COMPOSE_FILE"], self.spec["compose_files"][0]["path"])
        self.assertEqual(report["environment"]["DIRECTOR_HOST_PORT"], "18080")
        self.assertEqual(report["environment"]["ALLOW_PAID_RUNS"], "false")
        self.assertEqual(report["environment"]["LANGGRAPH_PARALLEL_KILL_SWITCH"], "true")
        self.assertFalse(any("windows-langgraph-primary-control.ps1" in str(arg) for call, _ in self.calls for arg in call))
        self.assertEqual(list(Path(sibling["output_directory"]).iterdir()), [])

    def final_dependency_graph(self):
        """Model final-only image aliases and both actual activation dependencies."""
        self.sibling()
        references = {}
        for name, character in (("n8n", "5"), ("n8n-db", "6"), ("langgraph-primary-db", "7")):
            if name != "n8n":
                self.compose["services"][name] = copy.deepcopy(self.sibling_compose["services"]["langgraph-primary-db"])
                self.compose["services"][name].pop("volumes")
            reference = ("docker.n8n.io/n8nio/n8n:latest" if name == "n8n" else "postgres:17-alpine") + "@sha256:" + character * 64
            self.compose["services"][name].update(image=reference, pull_policy="never")
            references[name] = reference
        self.compose["services"]["orchestrator"]["depends_on"] = {"langgraph-primary-db": {"condition": "service_healthy"}}
        self.compose["services"]["n8n"]["depends_on"] = {"n8n-db": {"condition": "service_healthy"}}
        self.reply_changes["images"] = {reference: "sha256:" + character * 64
                                         for reference, character in zip(references.values(), ("2", "3", "4"))}
        self.repin_graph(self.spec, self.compose)
        return references

    def test_final_doctor_and_rehearsal_require_every_dependency_image_offline_and_cached(self):
        references = self.final_dependency_graph()
        tool = release.Release(self.descriptor)
        self.assertTrue(tool.doctor()["preparation_ready"])
        self.assertEqual(tool.rehearsal()["status"], "PLANNED")
        for name, reference in references.items():
            service = self.compose["services"][name]
            original = copy.deepcopy(service)
            for defect in ("mutable", "always", "missing_policy", "uncached", "raw_id_mismatch"):
                with self.subTest(service=name, defect=defect):
                    service.clear()
                    service.update(copy.deepcopy(original))
                    if defect == "mutable":
                        service["image"] = reference.partition("@")[0]
                    elif defect == "always":
                        service["pull_policy"] = "always"
                    elif defect == "missing_policy":
                        service.pop("pull_policy")
                    elif defect == "raw_id_mismatch":
                        service["image"] = "sha256:" + "0" * 64
                    self.repin_graph(self.spec, self.compose)
                    def observe(argv, **kwargs):
                        if defect == "uncached" and argv[:3] == ["docker", "image", "inspect"] and argv[-1] == reference:
                            return b"[]"
                        return self.platform(argv, **kwargs)
                    with patch.object(release, "command", side_effect=observe):
                        tool = release.Release(self.descriptor)
                        report = tool.doctor()
                        try:
                            tool.rehearsal()
                            refused = False
                        except release.ReleaseError:
                            refused = True
                    self.assertEqual((report["preparation_ready"], refused), (False, True), report)
                    self.assertTrue(any(row["check"] == "compose" and row["status"] == "FAIL" for row in report["checks"]))
                    self.assertFalse(report["release_ready"])
            service.clear()
            service.update(original)
            self.repin_graph(self.spec, self.compose)

    def test_emitted_launcher_rechecks_final_dependency_images_before_effects(self):
        references = self.final_dependency_graph()
        plan = release.Release(self.descriptor).rehearsal()
        expected = self.reply_changes["images"]
        for step in plan["steps"]:
            payload = json.loads(base64.b64decode(step["argv"][-1]))
            self.assertEqual({reference: payload["images"].get(reference) for reference in expected}, expected)
        # Execute the real first-step producer and the following database action.
        self.initialize_intake(plan)
        start = next(step for step in plan["steps"] if step["action"] == "StartDatabase")
        self.launch_fixture(start)
        self.assertEqual(len(self.launched), 1)
        for reference in references.values():
            for step in plan["steps"][:3]:
                for missing in (False, True):
                    def changed(argv, **kwargs):
                        if argv[:3] == ["docker", "image", "inspect"] and argv[-1] == reference:
                            return b"[]" if missing else release.encoded([{"Id": "sha256:" + "0" * 64}])
                        return None
                    with self.subTest(reference=reference, action=step["action"], missing=missing), \
                            self.assertRaisesRegex(SystemExit, "verified cached image changed"):
                        self.launch_fixture(step, observe=changed)
                    self.assertEqual(self.launched, [])

    def test_actual_launcher_replaces_environment_and_rechecks_daemon_and_images(self):
        self.sibling()
        ambient = {"DOCKER_HOST": "tcp://remote:2376", "DOCKER_CONTEXT": "remote",
                   "DOCKER_CONFIG": "/untrusted", "DOCKER_TLS_VERIFY": "1", "DOCKER_CERT_PATH": "/untrusted",
                   "COMPOSE_FILE": "wrong.yml", "OPENAI_API_KEY": "fixture-never-forward"}
        with patch.dict(os.environ, ambient):
            plan = release.Release(self.descriptor).rehearsal()
            self.initialize_intake(plan)
            step = next(item for item in plan["steps"] if item["action"] == "StartDatabase")
            payload = json.loads(base64.b64decode(step["argv"][-1]))
            payload["control_argv"] = [sys.executable, "-I", "-c",
                "import json,os,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(os.environ)))",
                str(self.root / "observed-environment.json")]
            observed_calls = []
            def read(argv, **kwargs):
                observed_calls.append((argv, kwargs))
                if argv[0] == "docker" and (
                        (argv[1] in ("inspect", "wait", "logs", "rm") and argv[-1] in self.probe_rows) or
                        (argv[1] == "ps" and any(str(arg).startswith("name=^/fixture-sibling-probe-") and str(arg).endswith("$") for arg in argv))):
                    return self.platform(argv, **kwargs)
                if argv[0] == "git" or argv[:2] == ["docker", "compose"] or argv[:3] == ["docker", "volume", "inspect"] or argv[-1] == release.VALIDATION_PROBE:
                    return self.platform(argv, **kwargs)
                if argv[1] == "info":
                    return payload["daemon_id"].encode()
                if argv[1] in ("ps", "volume", "network"):
                    return b""
                return release.encoded([{"Id": payload["images"][argv[-1]]}])
            # Only Docker observation is mocked. The emitted launcher invokes a
            # real harmless child, which records its actual replacement env.
            with patch.object(sys, "argv", ["launcher", base64.b64encode(release.encoded(payload)).decode()]), \
                    self.native_reads(read):
                exec(compile(step["argv"][-2], "emitted-launcher", "exec"), {})
            observed = json.loads((self.root / "observed-environment.json").read_text())
            self.assertEqual(observed["DOCKER_HOST"], "npipe:////./pipe/docker_engine")
            for key in ambient.keys() - {"DOCKER_HOST", "COMPOSE_FILE"}:
                self.assertNotIn(key, observed)
            self.assertEqual(observed["COMPOSE_FILE"], payload["environment"]["COMPOSE_FILE"])
            # Derive the expected additions/removals from the independent
            # unchanged PowerShell consumer and the pinned synthetic file.
            control = (ROOT / "scripts/windows-langgraph-primary-control.ps1").read_text()
            block = control[control.index("$env:DIRECTOR_ENV_FILE = $environmentPath"):control.index("Invoke-Compose config --quiet")]
            selectors = re.findall(r'"([A-Z][A-Z0-9_]*)"', block)
            runtime = dict(line.split("=", 1) for line in Path(payload["runtime_pin"]["path"]).read_text().splitlines()
                           if line and not line.startswith("#"))
            expected = dict(payload["environment"])
            for key in selectors:
                expected.pop(key, None)
                if runtime.get(key):
                    expected[key] = runtime[key]
            self.assertTrue(all(item[1]["env"] == expected for item in observed_calls))
            self.assertEqual({key: observed[key] for key in expected}, expected)
            for change in ("daemon", "image", "rollback"):
                with self.subTest(change=change):
                    def drift(argv, **kwargs):
                        if argv[0] == "docker" and (
                                (argv[1] in ("inspect", "wait", "logs", "rm") and argv[-1] in self.probe_rows) or
                                (argv[1] == "ps" and any(str(arg).startswith("name=^/fixture-sibling-probe-") and str(arg).endswith("$") for arg in argv))):
                            return self.platform(argv, **kwargs)
                        if argv[0] == "git" or argv[:2] == ["docker", "compose"] or argv[:3] == ["docker", "volume", "inspect"] or argv[-1] == release.VALIDATION_PROBE:
                            return self.platform(argv, **kwargs)
                        if argv[1] == "info":
                            return b"different-daemon" if change == "daemon" else payload["daemon_id"].encode()
                        if argv[1] in ("ps", "volume", "network"):
                            return b""
                        return release.encoded([{"Id": "sha256:" + "0" * 64 if change == "image" else payload["images"][argv[-1]]}])
                    if change == "rollback":
                        Path(payload["prior_images"]).write_text('{"orchestrator_image_id":"unverified"}')
                    with patch.object(sys, "argv", ["launcher", base64.b64encode(release.encoded(payload)).decode()]), \
                            self.native_reads(drift), \
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
        tool.host_environment = release.child_environment()
        tool.executables = {"git": str(Path(shutil.which("git")).resolve())}
        tool.source, tool.deadline = self.source, time.monotonic() + 30
        tool.spec = {"candidate": {"commit": git("rev-parse", "HEAD").decode().strip(),
                                    "tree": git("rev-parse", "HEAD^{tree}").decode().strip()}}
        with patch.object(release, "command", REAL_COMMAND):
            with self.assertRaisesRegex(release.ReleaseError, "source_(not_clean|import_artifact_refused)"):
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
                    self.intake_rows.clear()
                    Path(plan["steps"][0]["receipt_path"]).unlink(missing_ok=True)
                    if not first_step:
                        self.initialize_intake(plan)
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
                    step = plan["steps"][0 if first_step else 1]
                    payload = json.loads(base64.b64decode(step["argv"][-1]))
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
                                                deadline=time.monotonic() + 30, input_bytes=kwargs.get("input"),
                                                executable=str(Path(shutil.which("git")).resolve()))
                        if argv[0] == "docker" and (
                                (argv[1] in ("inspect", "wait", "logs", "rm") and argv[-1] in self.probe_rows) or
                                (argv[1] == "ps" and any(str(arg).startswith("name=^/fixture-sibling-probe-") and str(arg).endswith("$") for arg in argv))):
                            return self.platform(argv, **kwargs)
                        if argv[:2] in (["docker", "compose"], ["docker", "volume"]) or argv[0] == self.spec["executables"]["docker"] or argv[-1] == release.VALIDATION_PROBE:
                            return self.platform(argv, **kwargs)
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
                            self.native_reads(read), \
                            patch.object(subprocess, "run") as child:
                        if defect and not (defect == "network" and not first_step):
                            with self.assertRaises(SystemExit):
                                exec(compile(release.REHEARSAL_LAUNCH, "launcher", "exec"), {})
                            child.assert_not_called()
                        else:
                            exec(compile(release.REHEARSAL_LAUNCH, "launcher", "exec"), {})
                            if first_step:
                                child.assert_not_called()
                                self.assertEqual(len(Path(payload["intake"]["path"]).read_bytes().splitlines()), 2)
                            else:
                                child.assert_called_once()

    def test_control_timeout_preserves_quiescence_and_full_health_wait(self):
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        self.initialize_intake(plan)
        for step in plan["steps"]:
            if step["action"] in ("InitializeIntakeVolumes", "StartDatabase", "Stop"):
                continue
            payload = json.loads(base64.b64decode(step["argv"][-1]))
            health = int(step["control_argv"][step["control_argv"].index("-TimeoutSeconds") + 1])
            quiescence = {"Rollback": 65, "ActivateOffline": 75}.get(step["action"], 0)
            self.assertGreater(payload["control_timeout_seconds"], health + quiescence)
            self.assertLessEqual(payload["control_timeout_seconds"], 260)
            if quiescence:
                runtime_path = Path(payload["runtime_pin"]["path"])
                runtime = release.dotenv(runtime_path.read_bytes())
                runtime.update(release.RUNTIME_CHANGES["StageRuntime"])
                if step["action"] == "Rollback":
                    runtime.update(release.RUNTIME_CHANGES["ActivateOffline"])
                runtime_path.write_bytes("".join(f"{key}={value}\n" for key, value in runtime.items()).encode())
                def read(argv, **kwargs):
                    return self.platform(argv, **kwargs)
                with patch.object(sys, "argv", ["launcher", step["argv"][-1]]), \
                        self.native_reads(read), \
                        patch.object(subprocess, "run") as child:
                    exec(compile(step["argv"][-2], "launcher", "exec"), {})
                    self.assertGreater(child.call_args.kwargs["timeout"], health + quiescence)

    def test_sibling_rejects_duplicate_and_nonstock_host_bindings(self):
        self.sibling()
        graph = copy.deepcopy(self.sibling_compose)
        port = copy.deepcopy(graph["services"]["orchestrator"]["ports"][0])
        graph["services"]["n8n"]["ports"] = [dict(port, target=5678, protocol="tcp")]
        with self.assertRaisesRegex(release.ReleaseError, "sibling_port_collision"):
            release.isolated_sibling(self.compose, graph, 18080)
        graph["services"]["n8n"]["ports"][0]["protocol"] = "udp"
        with self.assertRaisesRegex(release.ReleaseError, "sibling_port_not_stock"):
            release.isolated_sibling(self.compose, graph, 18080)
        graph["services"]["n8n"]["ports"][0].update(protocol="tcp", published="5678")
        release.isolated_sibling(self.compose, graph, 18080)
        graph["services"]["orchestrator"]["ports"].append(copy.deepcopy(port))
        with self.assertRaisesRegex(release.ReleaseError, "sibling_port_not_stock"):
            release.isolated_sibling(self.compose, graph, 18080)

    def test_supported_graph_covers_transitive_services_and_rejects_unsafe_variants(self):
        self.sibling()
        graph = copy.deepcopy(self.sibling_compose)
        graph["services"].update({name: {"image": "postgres:16@sha256:" + "1" * 64, "pull_policy": "never",
                                       "logging": {"driver": "local"}}
                                   for name in ("n8n-db", "langgraph-primary-db")})
        for name, key in (("n8n-db", "n8n-db-data"), ("langgraph-primary-db", "langgraph-primary-db-data")):
            graph["volumes"][key] = {"name": "fixture-sibling_" + key}
            graph["services"][name].update(environment={"POSTGRES_DB": "n8n", "POSTGRES_USER": "n8n", "POSTGRES_PASSWORD": "fixture"},
                volumes=[{"type": "volume", "source": key, "target": "/var/lib/postgresql/data"}])
        graph["services"]["slackbot"]["depends_on"] = {"orchestrator": {}, "n8n": {}}
        graph["services"]["orchestrator"]["depends_on"] = {"langgraph-primary-db": {}}
        graph["services"]["n8n-db"]["healthcheck"] = {"test": ["CMD-SHELL", "pg_isready -U n8n -d n8n"]}
        graph["services"]["n8n"].update(depends_on={"n8n-db": {"condition": "service_healthy"}},
            environment={**graph["services"]["n8n"]["environment"], "DB_TYPE": "postgresdb", "DB_POSTGRESDB_HOST": "n8n-db", "DB_POSTGRESDB_DATABASE": "n8n",
                         "DB_POSTGRESDB_USER": "n8n", "DB_POSTGRESDB_PASSWORD": "fixture", "DB_POSTGRESDB_PORT": "5432"})
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
                    with self.assertRaisesRegex(release.ReleaseError, "sibling_port_not_stock"):
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
        graph["services"]["n8n"]["volumes"] = [{"type": "volume", "source": "n8n-data", "target": "/home/node/.n8n"}]
        tool.rehearsal_images(graph)
        for defect in ("container", "image", "volume", "network", "prior"):
            with self.subTest(defect=defect):
                def platform(argv, **kwargs):
                    if defect == "container" and argv[:2] == ["docker", "ps"]:
                        return b"existing-container"
                    if defect == "image" and argv[:3] == ["docker", "image", "inspect"] and "n8n" in argv[-1]:
                        raise release.ReleaseError("platform_check_failed")
                    if defect == "volume" and argv[:2] == ["docker", "volume"]:
                        return b"fixture-sibling_n8n-data"
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

    def test_rehearsal_refuses_same_database_claim_before_deferred_start(self):
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
        self.assertEqual([item["generation"] for item in requests], [self.generation])

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
                    graph["volumes"]["director-data"]["driver_opts"] = {"device": "/production"}
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
            if step["action"] in ("InitializeIntakeVolumes", "StartDatabase", "Stop"):
                continue
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

    @contextmanager
    def native_reads(self, read, *, real_controls=False):
        """Fake native observations at Popen; execute the actual bounded loops."""
        control_launch = REAL_CONTAINED_LAUNCH if real_controls else process_isolation.launch_contained_process
        control_stop = REAL_CONTAINED_STOP if real_controls else process_isolation.stop_contained_process
        def popen(argv, **kwargs):
            if not hasattr(kwargs.get("stdout"), "write"):
                return REAL_POPEN(argv, **kwargs)
            selected = {key: kwargs[key] for key in ("env", "cwd", "executable") if key in kwargs}
            if hasattr(kwargs.get("stdin"), "read"):
                selected["input"] = kwargs["stdin"].read()
            # A custom router may deliberately use real Git. Its inner native
            # launch must not recursively re-enter this observation fixture.
            with patch.object(subprocess, "Popen", REAL_POPEN):
                raw = read(argv, **selected)
            kwargs["stdout"].write(raw)
            class ReadProcess:
                returncode = 0
                def poll(inner): return 0
            return ReadProcess()
        def contained(argv, **kwargs):
            if hasattr(kwargs.get("stdout"), "write"):
                return popen(argv, **kwargs), None
            return control_launch(argv, **kwargs)
        def stop(process, guard, **kwargs):
            if guard is not None:
                return control_stop(process, guard, **kwargs)
        with patch.object(subprocess, "Popen", side_effect=popen), \
                patch.object(process_isolation, "launch_contained_process", side_effect=contained), \
                patch.object(process_isolation, "stop_contained_process", side_effect=stop):
            yield

    def launch_fixture(self, step, *, observe=None, effect=None):
        """Run the emitted launcher; only native command boundaries are faked."""
        payload = json.loads(base64.b64decode(step["argv"][-1]))
        def read(argv, **kwargs):
            if observe:
                reply = observe(argv, **kwargs)
                if reply is not None:
                    return reply
            return self.platform(argv, **kwargs)
        with patch.object(sys, "argv", ["launcher", step["argv"][-1]]), \
                self.native_reads(read), \
                patch.object(subprocess, "run", side_effect=effect) as child:
            try:
                exec(compile(step["argv"][-2], "emitted-launcher", "exec"), {})
            finally:
                self.launched = child.call_args_list
        return payload

    def initialize_intake(self, plan):
        """Execute the real first-step launcher against the modeled daemon."""
        self.assertEqual(plan["steps"][0]["action"], "InitializeIntakeVolumes")
        self.launch_fixture(plan["steps"][0])
        self.assertEqual(self.launched, [])  # Only the declared native volume commands ran.

    def test_rehearsal_rechecks_every_pinned_input_before_every_action(self):
        sibling = self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        paths = {self.descriptor, self.owner_descriptor, self.installed,
                 Path(self.spec["rehearsal"]["descriptor"]["path"]),
                 Path(self.spec["rehearsal"]["acceptance_artifact"]["path"])}
        for spec in (self.spec, sibling):
            paths.update(Path(spec[key]["path"]) for key in
                         ("runtime_env", "bridge_env", "generation_manifest", "validation_manifest", "bridge_ca"))
            paths.update(Path(pin["path"]) for pin in spec["compose_files"])
        for step in plan["steps"][:5]:  # All steps before intentional runtime edits.
            for path in paths:
                with self.subTest(action=step["action"], path=path.name):
                    raw = path.read_bytes()
                    path.write_bytes(raw + b"\n")
                    try:
                        with self.assertRaises(SystemExit):
                            self.launch_fixture(step)
                        self.assertEqual(self.launched, [])
                    finally:
                        path.write_bytes(raw)
        step = plan["steps"][0]
        changed = Path(sibling["bridge_env"]["path"])
        raw = changed.read_bytes()
        def during_observation(argv, **kwargs):
            if argv[:2] == ["docker", "info"]:
                changed.write_bytes(raw + b"\n")
            return None
        with self.assertRaises(SystemExit):
            self.launch_fixture(step, observe=during_observation)
        self.assertEqual(self.launched, [])

    def test_rehearsal_rejects_readonly_engine_socket_and_named_pipe_mounts(self):
        self.sibling()
        mounts = [{"type": "bind", "source": "/var/run/docker.sock", "target": "/run/engine", "read_only": True},
                  {"type": "bind", "source": "/var/run", "target": "/run/host", "read_only": True},
                  {"type": "npipe", "source": "//./pipe/docker_engine", "target": "//./pipe/docker_engine", "read_only": True}]
        for mount in mounts:
            with self.subTest(mount=mount):
                graph = copy.deepcopy(self.sibling_compose)
                graph["services"]["slackbot"]["volumes"] = [mount]
                with self.assertRaisesRegex(release.ReleaseError, "sibling_engine_socket_refused"):
                    release.isolated_sibling(self.compose, graph, 18080)

    def test_rehearsal_rejects_unreviewed_healthcheck_commands(self):
        self.sibling()
        for name in self.sibling_compose["services"]:
            with self.subTest(service=name):
                graph = copy.deepcopy(self.sibling_compose)
                graph["services"][name]["healthcheck"] = {"test": ["CMD-SHELL", "unreviewed-provider-command"]}
                with self.assertRaisesRegex(release.ReleaseError, "sibling_healthcheck_unsupported"):
                    release.isolated_sibling(self.compose, graph, 18080)

    def test_rehearsal_requires_singleton_services(self):
        self.sibling()
        for name in self.sibling_compose["services"]:
            for change in ({"scale": 0}, {"scale": 2}, {"deploy": {"replicas": 2}},
                           {"deploy": {"mode": "global"}}, {"scale": True}):
                with self.subTest(service=name, change=change):
                    graph = copy.deepcopy(self.sibling_compose)
                    graph["services"][name].update(change)
                    with self.assertRaisesRegex(release.ReleaseError, "sibling_service_not_singleton"):
                        release.isolated_sibling(self.compose, graph, 18080)
        for service in self.sibling_compose["services"].values():
            service.update(scale=1, deploy={"mode": "replicated", "replicas": 1})
        release.isolated_sibling(self.compose, self.sibling_compose, 18080)

    def test_rehearsal_inspects_running_and_stopped_final_container_resources(self):
        sibling = self.sibling()
        actual = self.root / "actual-final-data"
        actual.mkdir()
        self.sibling_compose["services"]["orchestrator"]["volumes"].append(
            {"type": "bind", "source": str(actual / "nested"), "target": "/data"})
        self.repin_graph(sibling, self.sibling_compose)
        for running in (True, False):
            with self.subTest(running=running):
                def observe(argv, **kwargs):
                    if argv[:2] == ["docker", "ps"] and "label=com.docker.compose.project=fixture-director" in argv:
                        self.assertIn("-a", argv)
                        return b"final-container"
                    if argv[:2] == ["docker", "inspect"]:
                        return release.encoded([{"Id": "final-container", "State": {"Running": running},
                            "Config": {"Env": [], "Labels": {"com.docker.compose.project": "fixture-director"}},
                            "Mounts": [{"Type": "bind", "Source": str(actual), "Destination": "/old-data"}],
                            "NetworkSettings": {"Networks": {}}, "HostConfig": {"PortBindings": {}}}])
                    return self.platform(argv, **kwargs)
                with patch.object(release, "command", side_effect=observe), self.assertRaisesRegex(
                        release.ReleaseError, "sibling_shared_write_mount"):
                    release.Release(self.descriptor).rehearsal()

    def test_rehearsal_refuses_windows_command_line_overflow(self):
        self.sibling()
        env = dict(release.child_environment(), PATH="C:/fixture/" + "x" * 26000)
        with patch.object(release, "child_environment", return_value=env), self.assertRaisesRegex(
                release.ReleaseError, "rehearsal_command_line_too_long"):
            release.Release(self.descriptor).rehearsal()

    def test_complete_rehearsal_requires_local_backup_database_and_route(self):
        sibling = self.sibling()
        graph = self.sibling_compose
        original = copy.deepcopy(graph)
        for defect in ("missing", "external", "wrong_user"):
            with self.subTest(defect=defect):
                graph.clear()
                graph.update(copy.deepcopy(original))
                if defect == "missing":
                    del graph["services"]["langgraph-primary-db"]
                    graph["services"]["orchestrator"].pop("depends_on")
                elif defect == "external":
                    graph["services"]["orchestrator"]["environment"]["LANGGRAPH_PRIMARY_POSTGRES_DSN"] = "postgresql://director_primary:fixture@external:5432/director_primary"
                else:
                    graph["services"]["langgraph-primary-db"]["environment"]["POSTGRES_USER"] = "other"
                self.repin_graph(sibling, graph)
                with self.assertRaisesRegex(release.ReleaseError, "sibling_backup_database_"):
                    release.Release(self.descriptor).rehearsal()

    def test_inherited_image_healthcheck_cannot_hide_behind_timing_override(self):
        sibling = self.sibling()
        self.sibling_compose["services"]["slackbot"]["healthcheck"] = {"interval": "30s"}
        self.repin_graph(sibling, self.sibling_compose)
        tool = release.Release(self.spec["rehearsal"]["descriptor"]["path"])
        def observe(argv, **kwargs):
            if argv[:3] == ["docker", "image", "inspect"]:
                return release.encoded([{"Id": self.image, "Config": {
                    "Healthcheck": {"Test": ["CMD-SHELL", "unreviewed-provider-command"]}}}])
            return self.platform(argv, **kwargs)
        with patch.object(release, "command", side_effect=observe), self.assertRaisesRegex(
                release.ReleaseError, "sibling_healthcheck_unsupported"):
            tool.rehearsal_images(self.sibling_compose)

    def test_launcher_rechecks_final_resource_inventory_before_effects(self):
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        def changed(argv, **kwargs):
            if argv[:2] == ["docker", "ps"] and "label=com.docker.compose.project=fixture-director" in argv:
                return b"new-final-container"
            if argv[:2] == ["docker", "inspect"]:
                return release.encoded([{"Id": "new-final-container", "Mounts": [],
                    "Config": {"Env": [], "Labels": {"com.docker.compose.project": "fixture-director"}},
                    "NetworkSettings": {"Networks": {}}, "HostConfig": {"PortBindings": {}}}])
            return None
        for step in plan["steps"][:7]:
            with self.subTest(action=step["action"]), self.assertRaisesRegex(SystemExit, "final project resources changed"):
                self.launch_fixture(step, observe=changed)
            self.assertEqual(self.launched, [])

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell required for actual profile transitions")
    def test_launcher_accepts_only_actual_control_runtime_transitions(self):
        sibling = self.sibling()
        runtime = release.dotenv(Path(sibling["runtime_env"]["path"]).read_bytes())
        runtime["FIXTURE_PRIVATE_VALUE"] = "synthetic-secret-only-in-bound-file"
        sibling["runtime_env"] = self.pin_env("sibling-runtime.env", runtime)
        self.spec["rehearsal"]["descriptor"] = self.pin("sibling.json", release.encoded(sibling))
        self.save()
        plan = release.Release(self.descriptor).rehearsal()
        path = Path(sibling["runtime_env"]["path"])
        source = (ROOT / "scripts/windows-langgraph-primary-control.ps1").read_text()
        # Execute only the real profile writer and its successful-action switch
        # literals. No application, Docker or control-script body is executed.
        writer = source[source.index("function Set-DotEnvValue"):source.index("function Invoke-Compose")]
        switch_blocks = {}
        for action, marker, count in (
            ("StageRuntime", 'if ($Action -eq "StageRuntime")', 1),
            ("ActivateOffline", 'if ($Action -in @("Activate", "ActivateOffline"))', 1),
            ("Rollback", 'if ($Action -eq "Rollback")', 2)):
            tail = source[source.index(marker):]
            switch_blocks[action] = re.findall(r"Write-Switches @\{.*?\}", tail, re.S)[:count]
        for step in plan["steps"]:
            self.assertNotIn(b"synthetic-secret-only-in-bound-file", base64.b64decode(step["argv"][-1]))
            def effect(argv, **kwargs):
                current = release.dotenv(path.read_bytes())
                self.assertEqual(kwargs["env"], release.compose_environment(plan["environment"], current))
                self.assertNotIn("FIXTURE_PRIVATE_VALUE", kwargs["env"])
                self.assertEqual(kwargs["env"]["DIRECTOR_RUNTIME_IMAGE"], self.image)
                if step["action"] in switch_blocks:
                    harness = self.root / "profile-writer-only.ps1"
                    harness.write_text(writer + '\n$script:environmentPath=$args[0]; $script:values=@{}\n' +
                                       "\n".join(switch_blocks[step["action"]]))
                    # subprocess.run is mocked by launch_fixture; use Popen for
                    # this explicit, harmless production-writer harness.
                    process = subprocess.Popen(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(harness), str(path)],
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    _, error = process.communicate(timeout=20)
                    self.assertEqual(process.returncode, 0, error)
            self.launch_fixture(step, effect=effect)
            self.assertEqual(len(self.launched), 0 if step["action"] == "InitializeIntakeVolumes" else 1)
            if step["action"] in ("StageRuntime", "ActivateOffline", "Rollback"):
                following = plan["steps"][plan["steps"].index(step) + 1]
                raw = path.read_bytes()
                for key in ("ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_POSTGRES_DSN", "DIRECTOR_RUNTIME_IMAGE"):
                    values = release.dotenv(raw)
                    values[key] = "unreviewed"
                    path.write_bytes("".join(f"{name}={value}\n" for name, value in values.items()).encode())
                    with self.subTest(after=step["action"], key=key), self.assertRaises(SystemExit):
                        self.launch_fixture(following)
                    self.assertEqual(self.launched, [])
                    path.write_bytes(raw)

    def test_local_database_start_is_planned_before_control_and_evidence_is_deferred(self):
        sibling = self.sibling()
        graph = self.sibling_compose
        # Use the checked-in stock service shape; only pinning/fresh isolation is overlaid.
        import yaml
        stock = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        graph["services"]["langgraph-primary-db"] = copy.deepcopy(stock["services"]["langgraph-primary-db"])
        db = graph["services"]["langgraph-primary-db"]
        db.update(pull_policy="never", environment={"POSTGRES_USER": "director_primary",
            "POSTGRES_DB": "director_primary", "POSTGRES_PASSWORD": "fixture-only"},
            logging={"driver": "local", "options": {}},
            volumes=[{"type": "volume", "source": "langgraph-primary-db-data", "target": "/var/lib/postgresql/data"}])
        graph["services"]["orchestrator"]["depends_on"] = {"langgraph-primary-db": {"condition": "service_healthy"}}
        graph["services"]["orchestrator"]["environment"].update(
            LANGGRAPH_PRIMARY_POSTGRES_DSN="postgresql://director_primary:fixture-only@langgraph-primary-db:5432/director_primary?sslmode=disable",
            LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES="true")
        self.repin_graph(sibling, graph)
        plan = release.Release(self.descriptor).rehearsal()
        self.initialize_intake(plan)
        self.assertEqual([row["action"] for row in plan["steps"][:3]], ["InitializeIntakeVolumes", "StartDatabase", "Preflight"])
        self.assertFalse(plan["preparation_ready"])
        self.assertIn({"check": "database", "status": "DEFERRED", "reason": "local_database_start_required"},
                      plan["checks"])
        probes = [kwargs for argv, kwargs in self.calls if argv[-1] == release.DATABASE_PROBE]
        self.assertEqual(len(probes), 1, "only the already available final DB may be probed during planning")
        start = next(item for item in plan["steps"] if item["action"] == "StartDatabase")
        self.assertEqual(start["control_argv"][-1], "langgraph-primary-db")
        for option in ("--no-deps", "--no-build", "--wait", "--wait-timeout"):
            self.assertIn(option, start["control_argv"])
        payload = json.loads(base64.b64decode(start["argv"][-1]))
        self.assertEqual(start["control_argv"][start["control_argv"].index("--wait-timeout") + 1], "120")
        for defect in (None, "readiness", "identity", "occupied"):
            events = []
            def effect(argv, **kwargs):
                events.append("start")
                if defect == "readiness":
                    raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            def observe(argv, **kwargs):
                if argv[:2] == ["docker", "logs"]:
                    events.append("identity")
                    return release.encoded(dict(self.sibling_activation,
                        database_instance_id="wrong" if defect == "identity" else self.sibling_activation["database_instance_id"]))
                if argv[-1] == release.VALIDATION_PROBE:
                    events.append("slots")
                    return release.encoded({"occupied_slots": ["grok"] if defect == "occupied" else []})
                return None
            with self.subTest(defect=defect):
                if defect:
                    with self.assertRaises((SystemExit, subprocess.TimeoutExpired)):
                        self.launch_fixture(start, observe=observe, effect=effect)
                else:
                    self.launch_fixture(start, observe=observe, effect=effect)
                    self.assertEqual(events, ["start", "identity", "slots"])
                self.assertEqual(len(self.launched), 1)
                self.assertNotIn("-File", self.launched[0].args[0])
                if defect == "readiness":
                    self.assertEqual(events, ["start"])

    def test_orchestrator_rejects_unreviewed_command_and_effective_image_entrypoint(self):
        self.sibling()
        for field, value in (("command", ["python", "-c", "unreviewed"]),
                             ("entrypoint", ["sh", "-c", "unreviewed"])):
            graph = copy.deepcopy(self.sibling_compose)
            graph["services"]["orchestrator"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(release.ReleaseError, "sibling_orchestrator_command_unsupported"):
                release.isolated_sibling(self.compose, graph, 18080)
        tool = release.Release(self.spec["rehearsal"]["descriptor"]["path"])
        for field in ("Entrypoint", "Cmd"):
            graph = copy.deepcopy(self.sibling_compose)
            graph["services"]["orchestrator"]["command"] = None
            def observe(argv, **kwargs):
                if argv[:3] == ["docker", "image", "inspect"]:
                    config = json.loads(self.platform(argv, **kwargs))[0]["Config"]
                    config["Cmd"] = ["uvicorn", "director_orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"]
                    config[field] = ["unreviewed"]
                    return release.encoded([{"Id": self.image, "Config": config}])
                return self.platform(argv, **kwargs)
            with self.subTest(inherited=field), patch.object(release, "command", side_effect=observe), \
                    self.assertRaisesRegex(release.ReleaseError, "sibling_orchestrator_command_unsupported"):
                tool.rehearsal_images(graph)

    def test_sibling_only_allows_stock_mount_targets_and_unaliased_state(self):
        self.sibling()
        for name, mount in (
            ("orchestrator", {"type": "bind", "source": str(self.snapshot), "target": "/app", "read_only": True}),
            ("orchestrator", {"type": "bind", "source": str(self.snapshot), "target": "/", "read_only": True}),
            ("orchestrator", {"type": "volume", "source": "data", "target": "/app"}),
            ("orchestrator", {"type": "volume", "source": "primary", "target": "/data"}),
            ("orchestrator", {"type": "volume", "source": "data", "target": "/data", "volume": {"subpath": "other"}}),
            ("slackbot", {"type": "volume", "source": "data", "target": "/app/src"}),
            ("n8n", {"type": "volume", "source": "data", "target": "/usr/local/bin"}),
            ("langgraph-primary-db", {"type": "volume", "source": "data", "target": "/docker-entrypoint-initdb.d"})):
            graph = copy.deepcopy(self.sibling_compose)
            graph["services"][name].setdefault("volumes", []).append(mount)
            with self.subTest(service=name, mount=mount), self.assertRaisesRegex(release.ReleaseError, "sibling_mount_unsupported"):
                release.isolated_sibling(self.compose, graph, 18080)

    def test_future_runtime_modes_cannot_change_the_reviewed_command_or_mounts(self):
        self.sibling()
        for mode, switches in release.RUNTIME_CHANGES.items():
            for defect in ("command", "entrypoint", "mount"):
                with self.subTest(mode=mode, defect=defect):
                    def future(argv, **kwargs):
                        raw = self.platform(argv, **kwargs)
                        env = kwargs.get("env", {})
                        if argv[:4] == ["docker", "compose", "--project-name", "fixture-sibling"] and \
                                all(env.get(key) == value for key, value in switches.items()):
                            graph = json.loads(raw)
                            service = graph["services"]["orchestrator"]
                            if defect == "command":
                                service["command"] = ["python", "-c", "unreviewed"]
                            elif defect == "entrypoint":
                                service["entrypoint"] = ["unreviewed"]
                            else:
                                service["volumes"].append({"type": "bind", "source": str(self.snapshot),
                                    "target": "/app", "read_only": True})
                            raw = release.encoded(graph)
                        return raw
                    with patch.object(release, "command", side_effect=future), self.assertRaisesRegex(
                            release.ReleaseError, "sibling_runtime_graph_changed"):
                        release.Release(self.descriptor).rehearsal()

    def test_future_stock_modes_are_rendered_without_mutating_the_runtime_profile(self):
        spec = self.sibling()
        profile = Path(spec["runtime_env"]["path"])
        original = profile.read_bytes()
        plan = release.Release(self.descriptor).rehearsal()
        rendered = [kwargs["env"] for argv, kwargs in self.calls
            if argv[:4] == ["docker", "compose", "--project-name", "fixture-sibling"]
            and "DIRECTOR_ASGI_APP" in kwargs.get("env", {})]
        self.assertEqual([{key: env[key] for key in release.RUNTIME_CHANGES["StageRuntime"]}
                          for env in rendered], list(release.RUNTIME_CHANGES.values()))
        self.assertEqual(profile.read_bytes(), original)
        self.assertEqual(plan["timeout_seconds"], 1499)
        self.assertEqual(plan["steps"][-1]["action"], "Stop")

    def test_state_volume_identity_and_freshness_are_pinned_until_execution(self):
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        step = plan["steps"][0]
        def changed(argv, **kwargs):
            if argv[:3] == ["docker", "volume", "inspect"]:
                rows = json.loads(self.platform(argv, **kwargs))
                rows[0]["CreatedAt"] = "replacement-volume"
                return release.encoded(rows)
            return None
        with self.assertRaisesRegex(SystemExit, "state volume changed"):
            self.launch_fixture(step, observe=changed)
        self.assertEqual(self.launched, [])
        tool = release.Release(self.spec["rehearsal"]["descriptor"]["path"])
        def foreign(argv, **kwargs):
            if argv[:3] == ["docker", "volume", "inspect"]:
                rows = json.loads(self.platform(argv, **kwargs))
                rows[0]["Labels"]["com.docker.compose.project"] = "other-project"
                return release.encoded(rows)
            return self.platform(argv, **kwargs)
        with patch.object(release, "command", side_effect=foreign), self.assertRaisesRegex(
                release.ReleaseError, "state_volume_identity_mismatch"):
            tool.rehearsal_images(self.sibling_compose)

    def test_fresh_rehearsal_refuses_stale_offline_attempt_before_activation(self):
        sibling = self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        self.initialize_intake(plan)
        tool = release.Release(self.spec["rehearsal"]["descriptor"]["path"])
        marker = Path(tool.prior_images_path().replace(".prior-images.", ".offline-attempt."))
        marker.write_text("director-offline-attempt/v1")
        with self.subTest(phase="planning"), self.assertRaises(release.ReleaseError):
            release.Release(self.descriptor).rehearsal()
        runtime_path = Path(sibling["runtime_env"]["path"])
        runtime = release.dotenv(runtime_path.read_bytes())
        runtime.update(release.RUNTIME_CHANGES["StageRuntime"])
        runtime_path.write_bytes("".join(f"{key}={value}\n" for key, value in runtime.items()).encode())
        step = next(item for item in plan["steps"] if item["action"] == "ActivateOffline")
        for late in (False, True):
            if late:
                marker.unlink()
            def observe(argv, **kwargs):
                if late and argv[:2] == ["docker", "logs"]:
                    marker.write_text("director-offline-attempt/v1")
                return None
            with self.subTest(phase="before_control" if late else "activation"):
                with self.assertRaisesRegex(SystemExit, "offline attempt"):
                    self.launch_fixture(step, observe=observe)
                self.assertEqual(self.launched, [])

    def test_n8n_state_appearing_after_first_step_is_not_silently_adopted(self):
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        self.initialize_intake(plan)
        for action in ("Preflight", "StageRuntime"):
            step = next(item for item in plan["steps"] if item["action"] == action)
            self.assertFalse(json.loads(base64.b64decode(step["argv"][-1]))["first_step"])
            def foreign(argv, **kwargs):
                if argv[:3] == ["docker", "volume", "ls"]:
                    return self.platform(argv, **kwargs) + b"fixture-sibling_n8n-data\n"
                if argv[:3] == ["docker", "volume", "inspect"] and argv[-1] == "fixture-sibling_n8n-data":
                    rows = json.loads(self.platform(argv, **kwargs))
                    rows[0]["Labels"]["com.docker.compose.project"] = "unrelated-project"
                    return release.encoded(rows)
                return None
            with self.subTest(action=action):
                with self.assertRaises(SystemExit):
                    self.launch_fixture(step, observe=foreign)
                self.assertEqual(self.launched, [])

    def test_primary_database_requires_stock_enabled_readiness_before_plan(self):
        sibling = self.sibling()
        for health in ({}, {"disable": True}, {"test": ["NONE"]}, {"test": []}):
            self.sibling_compose["services"]["langgraph-primary-db"]["healthcheck"] = health
            self.repin_graph(sibling, self.sibling_compose)
            with self.subTest(health=health), self.assertRaises(release.ReleaseError):
                release.Release(self.descriptor).rehearsal()

    def test_primary_database_requires_healthy_dependency_and_explicit_local_transport(self):
        spec = self.sibling()
        service = self.sibling_compose["services"]["orchestrator"]
        for dependency in (None, {"condition": "service_completed_successfully"}, {"condition": "service_started"},
                           {"condition": "service_healthy", "required": False},
                           {"condition": "service_healthy", "restart": True}):
            with self.subTest(dependency=dependency):
                service["depends_on"] = {} if dependency is None else {"langgraph-primary-db": dependency}
                self.repin_graph(spec, self.sibling_compose)
                with self.assertRaisesRegex(release.ReleaseError, "sibling_primary_database_dependency_required"):
                    release.Release(self.descriptor).rehearsal()
        service["depends_on"] = {"langgraph-primary-db": {"condition": "service_healthy", "required": True, "restart": False}}
        for value in (None, "false"):
            with self.subTest(allow_insecure=value):
                if value is None:
                    service["environment"].pop("LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES")
                else:
                    service["environment"]["LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES"] = value
                self.repin_graph(spec, self.sibling_compose)
                with self.assertRaisesRegex(release.ReleaseError, "sibling_primary_database_transport_unsupported"):
                    release.Release(self.descriptor).rehearsal()
        service["environment"]["LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES"] = "true"
        self.repin_graph(spec, self.sibling_compose)
        self.assertEqual(release.Release(self.descriptor).rehearsal()["status"], "PLANNED")

    def test_rehearsal_refuses_effective_proxy_environment_including_image_probe(self):
        spec = self.sibling()
        for origin, key in (("compose", "HTTP_PROXY"), ("compose", "http_proxy"),
                            ("image", "ALL_PROXY"), ("masked-image", "HTTPS_PROXY")):
            with self.subTest(origin=origin, key=key):
                service = self.sibling_compose["services"]["slackbot"]
                if origin == "compose":
                    service["environment"][key] = "http://synthetic.invalid:18777"
                elif origin == "masked-image":
                    for name in ("orchestrator", "slackbot"):
                        self.sibling_compose["services"][name]["environment"][key] = ""
                self.repin_graph(spec, self.sibling_compose)
                def observe(argv, **kwargs):
                    raw = self.platform(argv, **kwargs)
                    if origin != "compose" and argv[:3] == ["docker", "image", "inspect"]:
                        image = json.loads(raw)
                        image[0]["Config"]["Env"].append(key + "=http://synthetic.invalid:18777")
                        return release.encoded(image)
                    return raw
                with patch.object(release, "command", side_effect=observe), self.assertRaisesRegex(
                        release.ReleaseError, "sibling_proxy_environment_unsupported"):
                    release.Release(self.descriptor).rehearsal()
                for name in ("orchestrator", "slackbot"):
                    self.sibling_compose["services"][name]["environment"].pop(key, None)
        self.sibling_compose["services"]["slackbot"]["environment"].update(HTTP_PROXY="", https_proxy="", ALL_PROXY="")
        self.repin_graph(spec, self.sibling_compose)
        self.assertEqual(release.Release(self.descriptor).rehearsal()["status"], "PLANNED")

    def test_live_final_slack_identity_is_compared_with_effective_sibling_and_keyed(self):
        spec = self.sibling()
        row = {"Id": "final-container", "State": {"Running": True}, "Mounts": [],
            "Config": {"Labels": {"com.docker.compose.project": "fixture-director"}, "Env": []},
            "NetworkSettings": {"Networks": {}}, "HostConfig": {"PortBindings": {}}}
        image_field = None
        def observe(argv, **kwargs):
            if argv[:2] == ["docker", "ps"] and "label=com.docker.compose.project=fixture-director" in argv:
                self.assertIn("-a", argv)
                self.assertIn("--no-trunc", argv)
                return b"final-container"
            if argv[:2] == ["docker", "inspect"] and argv[-1] == "final-container":
                return release.encoded([row])
            raw = self.platform(argv, **kwargs)
            if image_field and argv[:3] == ["docker", "image", "inspect"]:
                image = json.loads(raw)
                image[0]["Config"]["Env"].append(image_field + "=synthetic-live-identity")
                return release.encoded(image)
            return raw
        with patch.object(release, "command", side_effect=observe):
            for field in release.SLACK_IDENTITY_KEYS:
                for running in (True, False):
                    with self.subTest(field=field, running=running):
                        row["State"]["Running"] = running
                        row["Config"]["Env"] = [field + "=" + self.sibling_compose["services"]["slackbot"]["environment"][field]]
                        with self.assertRaisesRegex(release.ReleaseError, "sibling_slack_identity_not_isolated"):
                            release.Release(self.descriptor).rehearsal()
                # The orchestrator does not override these fields in the stock
                # fixture. Its effective image fallback must also be isolated.
                image_field = field
                row["Config"]["Env"] = [field + "=synthetic-live-identity"]
                with self.assertRaisesRegex(release.ReleaseError, "sibling_slack_identity_not_isolated"):
                    release.Release(self.descriptor).rehearsal()
                self.sibling_compose["services"]["orchestrator"]["environment"][field] = "synthetic-isolated-override"
                self.repin_graph(spec, self.sibling_compose)
                plan = release.Release(self.descriptor).rehearsal()
                for step in plan["steps"]:
                    self.assertNotIn(b"synthetic-live-identity", base64.b64decode(step["argv"][-1]))
                    self.assertNotIn(b"synthetic-isolated-override", base64.b64decode(step["argv"][-1]))
                self.sibling_compose["services"]["orchestrator"]["environment"].pop(field)
                self.repin_graph(spec, self.sibling_compose)
                image_field = None
            inventory = release.resource_inventory(observe, "fixture-director", "ab" * 32)
            other = release.resource_inventory(observe, "fixture-director", "cd" * 32)
            self.assertNotIn("synthetic-live-identity", json.dumps(inventory))
            self.assertNotEqual(inventory["slack_identities"], other["slack_identities"])
            for malformed in (None, {}, ["SLACK_APP_TOKEN"], ["SLACK_APP_TOKEN=a", "SLACK_APP_TOKEN=b"]):
                row["Config"]["Env"] = malformed
                with self.subTest(malformed=malformed), self.assertRaisesRegex(
                        release.ReleaseError, "final_resource_inventory_unavailable"):
                    release.Release(self.descriptor).rehearsal()
            row["Config"].pop("Env")
            with self.assertRaisesRegex(release.ReleaseError, "final_resource_inventory_unavailable"):
                release.Release(self.descriptor).rehearsal()

    def test_launcher_rechecks_only_keyed_final_slack_identity_before_effects(self):
        self.sibling()
        row = {"Id": "final-container", "Mounts": [],
            "Config": {"Labels": {"com.docker.compose.project": "fixture-director"}, "Env": ["SLACK_APP_TOKEN=synthetic-live-final"]},
            "NetworkSettings": {"Networks": {}}, "HostConfig": {"PortBindings": {}}}
        def observe(argv, **kwargs):
            if argv[:2] == ["docker", "ps"] and "label=com.docker.compose.project=fixture-director" in argv:
                return b"final-container"
            if argv[:2] == ["docker", "inspect"] and argv[-1] == "final-container":
                return release.encoded([row])
            return self.platform(argv, **kwargs)
        with patch.object(release, "command", side_effect=observe):
            plan = release.Release(self.descriptor).rehearsal()
        self.launch_fixture(plan["steps"][0], observe=observe)
        row["Config"]["Env"] = ["SLACK_APP_TOKEN=synthetic-changed-final"]
        for action in ("StartDatabase", "Preflight"):
            step = next(step for step in plan["steps"] if step["action"] == action)
            with self.subTest(action=action), self.assertRaisesRegex(SystemExit, "final project resources changed"):
                self.launch_fixture(step, observe=observe)
            self.assertEqual(self.launched, [])

    def sibling_with_postgres_n8n(self):
        sibling = self.sibling()
        graph = self.sibling_compose
        database = graph["services"]["n8n-db"] = copy.deepcopy(graph["services"]["langgraph-primary-db"])
        database["environment"] = {"POSTGRES_DB": "n8n", "POSTGRES_USER": "n8n", "POSTGRES_PASSWORD": "fixture"}
        database["healthcheck"]["test"] = ["CMD-SHELL", "pg_isready -U n8n -d n8n"]
        database["volumes"][0]["source"] = "n8n-db-data"
        graph["volumes"]["n8n-db-data"] = {"name": "fixture-sibling_n8n-db-data"}
        graph["services"]["n8n"]["environment"].update(DB_TYPE="postgresdb", DB_POSTGRESDB_HOST="n8n-db",
            DB_POSTGRESDB_DATABASE="n8n", DB_POSTGRESDB_USER="n8n", DB_POSTGRESDB_PASSWORD="fixture")
        graph["services"]["n8n"]["depends_on"] = {"n8n-db": {"condition": "service_healthy"}}
        self.repin_graph(sibling, graph)
        return sibling

    def test_intake_initialization_binds_both_stock_volumes_before_all_consumers(self):
        sibling = self.sibling_with_postgres_n8n()
        plan = release.Release(self.descriptor).rehearsal()
        first = plan["steps"][0]
        self.assertEqual(first["control_argv"], [])
        self.assertEqual(first["timeout_seconds"], 35)
        self.assertEqual(plan["timeout_seconds"], 1499)
        self.assertEqual(len(first["initialize_argvs"]), 2)
        self.assertTrue(all(argv[0] == sibling["executables"]["docker"] for argv in first["initialize_argvs"]))
        self.assertLessEqual(max(len(subprocess.list2cmdline(step["argv"]).encode("utf-16-le")) // 2 + 1
                                 for step in plan["steps"]), 32767)
        before = len(self.calls)
        self.initialize_intake(plan)
        self.assertFalse(any(argv[-1] == release.LOCAL_DATABASE_PROBE for argv, _ in self.calls[before:]))
        receipt = Path(first["receipt_path"])
        rows = [json.loads(line) for line in receipt.read_bytes().splitlines()]
        payload = json.loads(base64.b64decode(first["argv"][-1]))
        self.assertEqual(rows[0], {"event": "intent", "binding": payload["intake"]["binding"]})
        self.assertEqual(set(rows[1]["volumes"]), {"fixture-sibling_n8n-data", "fixture-sibling_n8n-db-data"})
        for name, row in self.intake_rows.items():
            self.assertEqual(rows[1]["volumes"][name], release.sha(release.encoded(release.volume_identity(row))))
        original = receipt.read_bytes()
        # Run every actual emitted consumer in order; native control effects are
        # limited to the already-reviewed runtime-map transitions in this fixture.
        profile = Path(sibling["runtime_env"]["path"])
        for step in plan["steps"][1:]:
            def effect(argv, **kwargs):
                if step["action"] in release.RUNTIME_CHANGES:
                    values = release.dotenv(profile.read_bytes())
                    values.update(release.RUNTIME_CHANGES[step["action"]])
                    profile.write_bytes("".join(f"{key}={value}\n" for key, value in values.items()).encode())
            with self.subTest(action=step["action"]):
                self.launch_fixture(step, effect=effect)
                self.assertEqual(len(self.launched), 1)
                self.assertEqual(receipt.read_bytes(), original)

    def test_intake_initialization_leaves_nonreplayable_intent_on_uncertain_create(self):
        self.sibling_with_postgres_n8n()
        plan = release.Release(self.descriptor).rehearsal()
        first = plan["steps"][0]
        payload = json.loads(base64.b64decode(first["argv"][-1]))
        receipt = Path(payload["intake"]["path"])
        name = next(iter(payload["intake"]["binding"]["names"]))
        for mode in ("occupied", "timeout", "partial", "nonzero", "wrong-name", "foreign", "wrong-driver", "driver-options"):
            self.intake_rows.clear()
            receipt.unlink(missing_ok=True)
            creates = []
            def observe(argv, **kwargs):
                if argv[:3] == ["docker", "volume", "ls"] and mode == "occupied" and receipt.exists():
                    return (name + "\n").encode()
                if argv[1:3] == ["volume", "create"]:
                    creates.append(argv)
                    self.assertEqual(len(receipt.read_bytes().splitlines()), 1, "intent must precede every create")
                    result = self.platform(argv, **kwargs)
                    if mode == "timeout" or mode == "partial" and len(creates) == 2: raise subprocess.TimeoutExpired(argv, 1)
                    if mode == "nonzero": raise subprocess.CalledProcessError(1, argv)
                    if mode == "wrong-name": return b"unrelated-name"
                    if mode == "foreign": self.intake_rows[name]["Labels"]["org.director.release.intake"] = "foreign"
                    if mode == "wrong-driver": self.intake_rows[name]["Driver"] = "remote"
                    if mode == "driver-options": self.intake_rows[name]["Options"] = {"device": "unrelated"}
                    return result
                return None
            with self.subTest(mode=mode), self.assertRaisesRegex(SystemExit, "initialization uncertain") as error:
                self.launch_fixture(first, observe=observe)
            self.assertIn(payload["intake"]["binding"]["nonce"], str(error.exception))
            self.assertIn(creates[-1][-1] if mode == "partial" else name, str(error.exception))
            self.assertEqual(len(receipt.read_bytes().splitlines()), 1)
            self.assertEqual(len(creates), 0 if mode == "occupied" else 2 if mode == "partial" else 1)
            if mode != "occupied":
                self.assertIn(name, self.intake_rows, "uncertain resources must not be deleted")
            with self.assertRaises(SystemExit):
                self.launch_fixture(first)
            with self.assertRaises(release.ReleaseError):
                release.Release(self.descriptor).rehearsal()
            with self.assertRaisesRegex(SystemExit, "intake volume receipt"):
                self.launch_fixture(plan["steps"][1])
            self.assertEqual(self.launched, [])

    def test_intake_receipt_and_identity_drift_block_before_probe_and_before_control(self):
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        self.initialize_intake(plan)
        step = next(item for item in plan["steps"] if item["action"] == "Preflight")
        payload = json.loads(base64.b64decode(step["argv"][-1]))
        receipt = Path(payload["intake"]["path"])
        original, volume_rows = receipt.read_bytes(), copy.deepcopy(self.intake_rows)
        name = next(iter(self.intake_rows))
        for mode in ("missing", "incomplete", "oversized", "directory", "nonce", "candidate", "digest", "identity", "missing-volume", "late-identity", "late-receipt"):
            self.intake_rows = copy.deepcopy(volume_rows)
            if receipt.is_dir(): receipt.rmdir()
            receipt.write_bytes(original)
            rows = [json.loads(line) for line in original.splitlines()]
            if mode == "missing": receipt.unlink()
            elif mode == "incomplete": receipt.write_bytes(original.splitlines(keepends=True)[0])
            elif mode == "oversized": receipt.write_bytes(b" " * (release.MAX_BYTES + 1))
            elif mode == "directory":
                receipt.unlink()
                receipt.mkdir()
            elif mode in ("nonce", "candidate"):
                rows[0]["binding"][mode] = "unrelated"
                receipt.write_bytes(b"".join(release.encoded(row) + b"\n" for row in rows))
            elif mode == "digest":
                rows[1]["volumes"][name] = "0" * 64
                receipt.write_bytes(b"".join(release.encoded(row) + b"\n" for row in rows))
            elif mode == "identity": self.intake_rows[name]["CreatedAt"] = "replaced"
            probes = []
            def observe(argv, **kwargs):
                if argv[:3] == ["docker", "volume", "inspect"] and argv[-1] == name and mode == "missing-volume":
                    raise subprocess.CalledProcessError(1, argv)
                if argv[:2] == ["docker", "logs"]:
                    probes.append(argv)
                    if mode == "late-identity": self.intake_rows[name]["CreatedAt"] = "replaced"
                    elif mode == "late-receipt": receipt.write_bytes(original + b"\n")
                return None
            with self.subTest(mode=mode), self.assertRaises((SystemExit, FileNotFoundError)):
                self.launch_fixture(step, observe=observe)
            self.assertEqual(self.launched, [])
            self.assertEqual(bool(probes), mode.startswith("late-"))

    def test_n8n_database_configuration_must_match_its_stock_postgres_service(self):
        self.sibling()
        graph = copy.deepcopy(self.sibling_compose)
        graph["services"]["n8n-db"] = {"image": "postgres:17-alpine@sha256:" + "1" * 64,
            "pull_policy": "never", "logging": {"driver": "local"},
            "environment": {"POSTGRES_DB": "n8n", "POSTGRES_USER": "n8n", "POSTGRES_PASSWORD": "fixture-password"}}
        graph["services"]["n8n"]["environment"] = {**graph["services"]["n8n"]["environment"], "DB_TYPE": "postgresdb", "DB_POSTGRESDB_HOST": "n8n-db",
            "DB_POSTGRESDB_PORT": "5432", "DB_POSTGRESDB_DATABASE": "n8n", "DB_POSTGRESDB_USER": "n8n",
            "DB_POSTGRESDB_PASSWORD": "fixture-password"}
        for key in ("DB_POSTGRESDB_PORT", "DB_POSTGRESDB_DATABASE", "DB_POSTGRESDB_USER", "DB_POSTGRESDB_PASSWORD"):
            changed = copy.deepcopy(graph)
            changed["services"]["n8n"]["environment"][key] = "unmatched-sensitive-fixture"
            with self.subTest(key=key), self.assertRaisesRegex(release.ReleaseError, "^sibling_n8n_database_mismatch$"):
                release.isolated_sibling(self.compose, changed, 18080)

    def test_successful_rehearsal_ends_with_bounded_stop_and_proof_for_all_intake(self):
        sibling = self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        self.initialize_intake(plan)
        self.assertEqual([step["action"] for step in plan["steps"][-3:]], ["Rollback", "Health", "Stop"])
        self.assertEqual(plan["timeout_seconds"], 1499)
        stop = plan["steps"][-1]
        self.assertEqual(stop["control_argv"][-3:], ["slackbot", "n8n", "orchestrator"])
        self.assertIn("--timeout", stop["control_argv"])
        runtime = release.dotenv(Path(sibling["runtime_env"]["path"]).read_bytes())
        runtime.update(release.RUNTIME_CHANGES["Rollback"])
        Path(sibling["runtime_env"]["path"]).write_bytes("".join(f"{key}={value}\n" for key, value in runtime.items()).encode())
        for active in (None, "slackbot", "n8n", "orchestrator", "orchestrator-oneoff"):
            calls = []
            def observe(argv, **kwargs):
                calls.append(argv)
                if argv[:2] == ["docker", "ps"] and "label=com.docker.compose.project=fixture-sibling" in argv:
                    return b"sibling-container"
                if argv[:2] == ["docker", "inspect"]:
                    return release.encoded([{"Id": "sibling-container", "Config": {"Labels": {
                        "com.docker.compose.project": "fixture-sibling",
                        "com.docker.compose.service": (active or "orchestrator").replace("-oneoff", "")}},
                        "State": {"Running": active is not None, "Paused": False, "Restarting": False, "Status": "running" if active else "exited"}}])
                return None
            with self.subTest(active=active):
                if active:
                    with self.assertRaisesRegex(SystemExit, "intake.*stopped"):
                        self.launch_fixture(stop, observe=observe)
                else:
                    self.launch_fixture(stop, observe=observe)
                self.assertEqual(len(self.launched), 1)
                self.assertIn("fixture-sibling", self.launched[0].args[0])
                self.assertNotIn("fixture-director", self.launched[0].args[0])
                self.assertLessEqual(self.launched[0].kwargs["timeout"], 140)

    def test_real_git_rejects_hidden_index_flags_and_non_eol_clean_transformations(self):
        for defect in ("assume-unchanged", "skip-worktree", "ident"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                checkout = Path(directory)
                def git(*args, input=None):
                    return subprocess.check_output(["git", *args], cwd=checkout, env=release.child_environment(),
                                                   input=input, stderr=subprocess.DEVNULL)
                git("init", "-q")
                (checkout / "tracked.py").write_bytes(b"VALUE = '$Id$'\n")
                (checkout / ".gitattributes").write_text("tracked.py ident\n" if defect == "ident" else "*.py text\n")
                git("add", ".")
                git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
                if defect != "ident":
                    git("update-index", "--" + defect, "tracked.py")
                else:
                    git("checkout-index", "-f", "-u", "--", "tracked.py")
                (checkout / "tracked.py").write_bytes(b"VALUE = '$Id: " + b"a" * 40 + b" $'\n" if defect == "ident" else b"VALUE = 'unreviewed'\n")
                if defect == "ident":
                    self.assertEqual(git("hash-object", "tracked.py"), git("rev-parse", "HEAD:tracked.py"))
                    self.assertNotEqual(git("hash-object", "--no-filters", "tracked.py"), git("rev-parse", "HEAD:tracked.py"))
                else:
                    self.assertEqual(git("status", "--porcelain=v2"), b"", "real Git must demonstrate the hidden change")
                tool = release.Release.__new__(release.Release)
                tool.host_environment = release.child_environment()
                tool.executables = {"git": str(Path(shutil.which("git")).resolve())}
                tool.source, tool.deadline = checkout, time.monotonic() + 30
                tool.spec = {"candidate": {kind: git("rev-parse", "HEAD^{" + kind + "}").decode().strip() for kind in ("commit", "tree")}}
                with patch.object(release, "command", REAL_COMMAND), self.assertRaises(release.ReleaseError):
                    tool.source_check()

    def test_isolated_child_refuses_ignored_unchecked_hash_cache_and_keeps_dependencies(self):
        import py_compile
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            package = checkout / "src" / "fixture_cache"
            package.mkdir(parents=True)
            source = package / "__init__.py"
            source.write_text("SAFE = True\n")
            (checkout / ".gitignore").write_text("__pycache__/\n")
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=checkout, env=release.child_environment(), stderr=subprocess.DEVNULL)
            git("init", "-q")
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
            candidate = {kind: git("rev-parse", "HEAD^{" + kind + "}").decode().strip() for kind in ("commit", "tree")}
            marker = checkout / "sentinel"
            source.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\nSAFE = False\n")
            cache = py_compile.compile(str(source), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
            source.write_text("SAFE = True\n")
            self.assertEqual(git("status", "--porcelain=v2", "--untracked-files=all"), b"")
            unsafe = "import sys; sys.path.insert(0,sys.argv[1]); import fixture_cache"
            subprocess.run([sys.executable, "-I", "-B", "-c", unsafe, str(checkout / "src")], check=True, timeout=20)
            self.assertTrue(marker.exists(), "-I/-B alone must demonstrate stale unchecked bytecode execution")
            marker.unlink()
            child = """import pathlib,runpy,sys,time
m=runpy.run_path(sys.argv[1],run_name='verified_runner')
t=m['Release'].__new__(m['Release']); t.source=pathlib.Path(sys.argv[2]); t.deadline=time.monotonic()+30
t.host_environment=m['child_environment']()
t.executables={'git':sys.argv[5]}
t.spec={'candidate':{'commit':sys.argv[3],'tree':sys.argv[4]}}
t.source_check()
sys.path.insert(0,str(t.source/'src'))
import fixture_cache,jsonschema
assert fixture_cache.SAFE and callable(jsonschema.validate)
"""
            argv = [sys.executable, "-I", "-B", "-c", child, str(ROOT / "scripts/director-release.py"), str(checkout), candidate["commit"], candidate["tree"], str(Path(shutil.which("git")).resolve())]
            guarded = subprocess.run(argv, capture_output=True, timeout=40)
            self.assertFalse(marker.exists(), "unchecked cache executed after the claimed source gate")
            self.assertNotEqual(guarded.returncode, 0)
            Path(cache).unlink()
            # Ordinary Windows CRLF source and installed dependencies stay usable.
            git("config", "core.autocrlf", "true")
            source.unlink()
            git("checkout-index", "--force", "-u", "--", "src/fixture_cache/__init__.py")
            self.assertEqual(source.read_bytes(), b"SAFE = True\r\n")
            self.assertEqual(git("status", "--porcelain=v2", "--untracked-files=all"), b"")
            clean = subprocess.run(argv, capture_output=True, timeout=40)
            self.assertEqual(clean.returncode, 0, clean.stderr)

    def test_rehearsal_refuses_unreviewed_execution_settings(self):
        self.sibling()
        cases = [("environment", {"PYTHONPATH": "/data"}), ("working_dir", "/data"),
                 ("restart", "always"), ("logging", {"driver": "syslog"}),
                 ("logging", None), ("cap_add", ["SYS_ADMIN"])]
        for field, value in cases:
            graph = copy.deepcopy(self.sibling_compose)
            service = graph["services"]["orchestrator"]
            if field == "environment":
                service[field].update(value)
            elif value is None:
                service.pop(field, None)
            else:
                service[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(release.ReleaseError):
                release.isolated_sibling(self.compose, graph, 18080)

    def test_rehearsal_checks_image_loader_environment_and_workdir(self):
        self.sibling()
        tool = release.Release(self.root / "sibling.json")
        for field, value in (("Env", ["PYTHONPATH=/data"]), ("Env", ["LD_PRELOAD=/data/loader.so"]),
                             ("Env", ["PATH=/data:/usr/local/bin"]), ("WorkingDir", "/data")):
            def observe(argv, **kwargs):
                raw = self.platform(argv, **kwargs)
                if argv[:3] == ["docker", "image", "inspect"]:
                    rows = json.loads(raw)
                    rows[0]["Config"][field] = value
                    return release.encoded(rows)
                return raw
            with self.subTest(field=field, value=value), patch.object(release, "command", side_effect=observe), \
                    self.assertRaises(release.ReleaseError):
                tool.rehearsal_images(self.sibling_compose)

    def test_rehearsal_refuses_image_defaults_masked_by_compose_for_direct_probe(self):
        self.sibling()
        tool = release.Release(self.root / "sibling.json")
        tool.rehearsal_images(self.sibling_compose)  # Reviewed stock image is usable.
        stock = json.loads(self.platform(["docker", "image", "inspect", self.image]))[0]["Config"]
        for key, unsafe, override in (("PYTHONPATH", "/data", "/app/src"),
                                      ("PATH", "/data:/usr/local/bin", "/usr/local/bin:/usr/bin:/bin"),
                                      ("PYTHONHOME", "/data", None), ("LD_PRELOAD", "/data/loader.so", None),
                                      ("WorkingDir", "/data", "/app")):
            graph = copy.deepcopy(self.sibling_compose)
            config = copy.deepcopy(stock)
            # Both services resolve to this same pinned image. Compose masks its
            # unsafe default, but the control's direct digest probe has no such
            # override and must be rejected before any candidate launch.
            for name in ("orchestrator", "slackbot"):
                service = graph["services"][name]
                if key == "WorkingDir":
                    config[key] = unsafe
                    service["working_dir"] = override
                else:
                    config["Env"] = [value for value in config["Env"] if not value.startswith(key + "=")]
                    config["Env"].append(key + "=" + unsafe)
                    service["environment"][key] = override
                release.reviewed_application_environment(service, config)
            def observe(argv, **kwargs):
                raw = self.platform(argv, **kwargs)
                if argv[:3] == ["docker", "image", "inspect"] and argv[-1] == self.image:
                    rows = json.loads(raw)
                    rows[0]["Config"] = config
                    return release.encoded(rows)
                return raw
            with self.subTest(masked=key), patch.object(release, "command", side_effect=observe), \
                    self.assertRaisesRegex(release.ReleaseError, "sibling_(loader_environment|working_directory)_unsupported"):
                tool.rehearsal_images(graph)

    def test_rehearsal_refuses_git_environment_even_when_compose_masks_image(self):
        self.sibling()
        tool = release.Release(self.root / "sibling.json")
        tool.rehearsal_images(self.sibling_compose)
        for key, value in (("GIT_CONFIG_COUNT", "1"), ("HOME", "/repository/tracked-home"),
                           ("XDG_CONFIG_HOME", "/repository/tracked-xdg")):
            graph = copy.deepcopy(self.sibling_compose)
            for name in ("orchestrator", "slackbot"):
                graph["services"][name]["environment"][key] = None
            def observe(argv, **kwargs):
                raw = self.platform(argv, **kwargs)
                if argv[:3] == ["docker", "image", "inspect"] and argv[-1] == self.image:
                    rows = json.loads(raw)
                    rows[0]["Config"]["Env"].append(key + "=" + value)
                    # Compose removes the key, but the direct image-only probe
                    # still inherits it and must be refused by its own check.
                    release.reviewed_application_environment(graph["services"]["orchestrator"], rows[0]["Config"])
                    return release.encoded(rows)
                return raw
            with self.subTest(key=key), patch.object(release, "command", side_effect=observe), \
                    self.assertRaisesRegex(release.ReleaseError, "^sibling_git_environment_unsupported$"):
                tool.rehearsal_images(graph)

    @unittest.skipUnless(os.name == "nt", "Windows executable search regression")
    def test_trusted_native_path_prevents_candidate_executable_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "candidate-executed"
            # A harmless OS command processor stands in for candidate git.exe.
            shutil.copyfile(Path(os.environ["SYSTEMROOT"]) / "System32" / "cmd.exe", root / "git.exe")
            child = """import pathlib,runpy,sys,time
m=runpy.run_path(sys.argv[1],run_name='verified_runner')
t=m['Release'].__new__(m['Release']); t.source=pathlib.Path.cwd(); t.deadline=time.monotonic()+5
t.host_environment=m['child_environment']()
t.executables={'git':sys.argv[2]}
try: t.run(['git','/c','echo harmless>candidate-executed'])
except m['ReleaseError']: pass
"""
            result = subprocess.run([sys.executable, "-I", "-B", "-c", child,
                str(ROOT / "scripts/director-release.py"), str(Path(shutil.which("git")).resolve())],
                cwd=root, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists(), "bare executable lookup ran candidate git.exe")

    def test_emitted_control_timeout_ends_the_child_and_grandchild(self):
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        self.initialize_intake(plan)
        step = next(item for item in plan["steps"] if item["action"] == "StartDatabase")
        payload = json.loads(base64.b64decode(step["argv"][-1]))
        marker = self.root / "escaped-grandchild"
        ready = self.root / "grandchild-started"
        grandchild = "import pathlib,sys,time; time.sleep(1); pathlib.Path(sys.argv[1]).write_text('escaped')"
        detach = "creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS" if os.name == "nt" else "close_fds=True"
        parent = ("import pathlib,subprocess,sys,time; subprocess.Popen([sys.executable,'-I','-c',sys.argv[3],sys.argv[1]], " +
                  detach + "); pathlib.Path(sys.argv[2]).write_text('ready'); time.sleep(20)")
        payload.update(control_argv=[sys.executable, "-I", "-c", parent, str(marker), str(ready), grandchild],
                       control_timeout_seconds=0.5)
        with patch.object(sys, "argv", ["launcher", base64.b64encode(release.encoded(payload)).decode()]), \
                self.native_reads(self.platform, real_controls=True):
            with self.assertRaises(subprocess.TimeoutExpired):
                exec(compile(release.REHEARSAL_LAUNCH, "launcher", "exec"), {})
        self.assertTrue(ready.exists(), "fixture child must start before the deadline")
        time.sleep(1.1)
        self.assertFalse(marker.exists(), "grandchild survived the emitted control timeout")

    def test_rehearsal_refuses_missing_gateway_identity_and_implicit_bind_creation(self):
        self.sibling()
        graph = copy.deepcopy(self.sibling_compose)
        graph["services"]["slackbot"]["environment"].pop("SLACK_APP_TOKEN", None)
        with self.assertRaises(release.ReleaseError):
            release.isolated_sibling(self.compose, graph, 18080)
        graph = copy.deepcopy(self.sibling_compose)
        graph["services"]["orchestrator"]["volumes"].append({"type": "bind", "source": str(self.root / "missing-registry"),
            "target": "/run/director-acceptance-registry", "read_only": True, "bind": {"create_host_path": True}})
        with self.assertRaises(release.ReleaseError):
            release.isolated_sibling(self.compose, graph, 18080)

    def test_first_mutation_refuses_an_occupied_declared_port(self):
        import socket
        self.sibling()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            step = release.Release(self.descriptor).rehearsal()["steps"][0]
            payload = json.loads(base64.b64decode(step["argv"][-1]))
            payload["ports"] = [{"host_ip": "127.0.0.1", "published": listener.getsockname()[1], "protocol": "tcp"}]
            step["argv"][-1] = base64.b64encode(release.encoded(payload)).decode()
            with self.assertRaisesRegex(SystemExit, "port"):
                self.launch_fixture(step)
            self.assertEqual(self.launched, [])

    def test_database_probe_owns_cleanup_and_never_accepts_unknown_outcomes(self):
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        self.initialize_intake(plan)
        step = next(item for item in plan["steps"] if item["action"] == "Preflight")
        for mode in ("success", "restart-empty", "restart-always", "restart-missing", "wait-timeout", "create-timeout", "create-absent", "cleanup-failure", "absence-unavailable",
                     "identity-name", "identity-owner", "identity-project", "identity-service", "identity-oneoff", "identity-id", "identity-image"):
            rows, removed = {}, []
            selected = {}
            container_id = "e" * 64
            def observe(argv, **kwargs):
                if argv[:2] == ["docker", "compose"] and argv[-1] == release.LOCAL_DATABASE_PROBE:
                    name = argv[argv.index("--name") + 1] if "--name" in argv else "anonymous-probe"
                    owner = argv[argv.index("--label") + 1].partition("=")[2] if "--label" in argv else ""
                    selected.update(name=name, owner=owner)
                    rows[container_id] = {"Id": container_id, "Name": "/" + name,
                        "Image": self.image,
                        "HostConfig": {"RestartPolicy": {"Name": "" if mode == "restart-empty" else "no"}},
                        "Config": {"Labels": {"org.director.release.probe": owner, "com.docker.compose.project": "fixture-sibling",
                            "com.docker.compose.service": "orchestrator", "com.docker.compose.oneoff": "True"}}}
                    if mode.startswith("identity-"):
                        field = mode.partition("-")[2]
                        if field in ("name", "id", "image"):
                            rows[container_id][{"name": "Name", "id": "Id", "image": "Image"}[field]] = "unrelated"
                        else:
                            key = "org.director.release.probe" if field == "owner" else "com.docker.compose." + field
                            rows[container_id]["Config"]["Labels"][key] = "unrelated"
                    if mode == "restart-always": rows[container_id]["HostConfig"]["RestartPolicy"]["Name"] = "always"
                    if mode == "restart-missing": rows[container_id].pop("HostConfig")
                    if mode == "create-absent": rows.clear()
                    if mode in ("create-timeout", "create-absent") or (mode == "wait-timeout" and "--detach" not in argv):
                        raise subprocess.TimeoutExpired(argv, 1)
                    return container_id.encode() if "--detach" in argv else release.encoded(self.sibling_activation)
                if argv[:2] == ["docker", "ps"] and any(str(value).startswith("name=^/") for value in argv):
                    if mode == "absence-unavailable" and removed:
                        raise subprocess.CalledProcessError(1, argv)
                    return (container_id if rows else "").encode()
                if argv[:2] == ["docker", "inspect"] and argv[-1] == container_id:
                    return release.encoded(list(rows.values()))
                if argv[:2] == ["docker", "wait"]:
                    if mode == "wait-timeout": raise subprocess.TimeoutExpired(argv, 1)
                    return b"0\n"
                if argv[:2] == ["docker", "logs"]:
                    return release.encoded(self.sibling_activation)
                if argv[:2] == ["docker", "rm"]:
                    removed.append(argv[-1])
                    if mode == "cleanup-failure": raise subprocess.CalledProcessError(1, argv)
                    rows.clear()
                    return container_id.encode()
                return None
            with self.subTest(mode=mode):
                if mode in ("success", "restart-empty"):
                    self.launch_fixture(step, observe=observe)
                    self.assertEqual(len(self.launched), 1)
                else:
                    with self.assertRaises((SystemExit, subprocess.TimeoutExpired)) as error:
                        self.launch_fixture(step, observe=observe)
                    self.assertEqual(self.launched, [])
                    self.assertIn(selected["name"], str(error.exception))
                    self.assertIn(selected["owner"], str(error.exception))
                if mode.startswith("identity-"):
                    self.assertEqual(removed, [], "never remove an object outside the authenticated probe identity")
                    self.assertTrue(rows)
                elif mode == "cleanup-failure":
                    self.assertEqual(removed, [container_id])
                    self.assertTrue(rows)
                elif mode == "create-absent":
                    self.assertEqual(removed, [])
                    self.assertFalse(rows)
                else:
                    self.assertEqual(removed, [container_id])
                    self.assertFalse(rows, "client completion/timeout must not leave its daemon-owned probe behind")

    def test_owned_probe_client_bounds_output_and_reaps_real_descendants(self):
        import ast
        import tempfile as tempfiles
        node = next(item for item in ast.parse(release.REHEARSAL_LAUNCH).body
                    if isinstance(item, ast.FunctionDef) and item.name == "probe_read")
        namespace = {"time": time, "tempfile": tempfiles, "os": os, "subprocess": subprocess,
            "launch_contained_process": REAL_CONTAINED_LAUNCH, "stop_contained_process": REAL_CONTAINED_STOP,
            "env": release.child_environment(), "v": {"executables": {}, "cwd": str(self.root)}}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "actual-emitted-probe-client", "exec"), namespace)
        read = namespace["probe_read"]
        self.assertEqual(read([sys.executable, "-I", "-B", "-c", "print('fixture')"], time.monotonic() + 10), b"fixture\r\n" if os.name == "nt" else b"fixture\n")
        with self.assertRaisesRegex(RuntimeError, "output limit|client failed"):
            read([sys.executable, "-I", "-B", "-c", "import sys; sys.stdout.write('x' * (4*1024*1024+1))"], time.monotonic() + 10)
        # Establish real child readiness before arming the short timeout proof.
        grandchild = ("import pathlib,sys,time\n"
                      "ready,arm,marker=map(pathlib.Path,sys.argv[1:4])\n"
                      "ready.write_text('ready')\n"
                      "while not arm.exists(): time.sleep(.02)\n"
                      "time.sleep(6.5)\nmarker.write_text('escaped')\n")
        detach = "creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS" if os.name == "nt" else "close_fds=True"
        parent = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-I','-B','-c',sys.argv[4],"
                  "sys.argv[1],sys.argv[2],sys.argv[3]], " + detach + "); time.sleep(20)")
        for contained in (False, True):
            with self.subTest(contained=contained):
                ready = self.root / f"probe-ready-{contained}"
                arm = self.root / f"probe-arm-{contained}"
                marker = self.root / f"probe-survived-{contained}"
                argv = [sys.executable, "-I", "-B", "-c", parent, str(ready), str(arm), str(marker), grandchild]
                process, guard = REAL_CONTAINED_LAUNCH(argv, env=release.child_environment(), cwd=self.root,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                stopped = False
                try:
                    startup_deadline = time.monotonic() + 10
                    while not ready.exists() and process.poll() is None and time.monotonic() < startup_deadline:
                        time.sleep(.02)
                    self.assertTrue(ready.exists(), "real grandchild did not signal readiness")
                    self.assertFalse(marker.exists())
                    armed_at = time.monotonic()
                    arm.write_text("armed")
                    if contained:
                        launched = False
                        def owned_launcher(args, **kwargs):
                            nonlocal launched
                            self.assertEqual(args, argv)
                            self.assertFalse(launched, "probe launcher must run once")
                            launched = True
                            return process, guard
                        def owned_stop(child, token, **kwargs):
                            nonlocal stopped
                            self.assertIs(child, process)
                            self.assertIs(token, guard)
                            result = REAL_CONTAINED_STOP(child, token, **kwargs)
                            stopped = True
                            return result
                        with patch.dict(namespace, launch_contained_process=owned_launcher, stop_contained_process=owned_stop):
                            start = time.monotonic()
                            with self.assertRaisesRegex(RuntimeError, "probe deadline"):
                                read(argv, start + 5.5)
                            self.assertLess(time.monotonic() - start, 5.5)
                        self.assertTrue(launched)
                        self.assertTrue(stopped, "actual emitted helper did not verify cleanup")
                        self.assertIsNotNone(process.poll())
                    # Observe past the armed marker window in both controls.
                    while time.monotonic() < armed_at + 7.5 and (contained or not marker.exists()):
                        time.sleep(.02)
                    self.assertEqual(marker.exists(), not contained,
                        "positive survival control or real descendant containment failed")
                finally:
                    if not stopped:
                        REAL_CONTAINED_STOP(process, guard)

    def test_compose_raw_inputs_refuse_transitive_files_before_render(self):
        external = self.root / "unbound.env"
        external.write_text("FIXTURE_UNBOUND=original\n")
        for raw in (
                b"include: [hidden.yml]\nservices: {}\n",
                b"services: {orchestrator: {extends: {file: hidden.yml, service: app}}}\n",
                release.encoded({"services": {"orchestrator": {"env_file": str(external)}}}),
                b"services: {orchestrator: {env_file: [unbound.env]}}\n",
                b"services: {orchestrator: {env_file: [{path: unbound.env, required: false}]}}\n",
                b"services: {orchestrator: {env_file: '${UNBOUND_FILE:-unbound.env}'}}\n",
                b"services: {orchestrator: {label_file: unbound.env}}\n",
                b"configs: {hidden: {file: hidden.yml}}\nservices: {}\n",
                b"services: {orchestrator: {env_file: unbound.env}}\nservices: {}\n",
                b"defaults: &defaults {env_file: unbound.env}\nservices: {orchestrator: {<<: *defaults}}\n"):
            self.spec["compose_files"] = [self.pin("compose.yml", raw)]
            self.save()
            self.calls.clear()
            with self.subTest(raw=raw):
                with self.assertRaises(release.ReleaseError):
                    tool = release.Release(self.descriptor)
                    tool.compose_document(tool.runtime)
                self.assertFalse(any(argv[:2] == ["docker", "compose"] for argv, _ in self.calls))

    def test_compose_refuses_startup_interpolation_and_ambiguous_control_selectors(self):
        original = copy.deepcopy(self.spec)
        for value in ("$PSModulePath", "${PSModulePath:-safe}", "$${PSModulePath}", "${psmodulepath:+other}"):
            with self.subTest(reference=value):
                self.spec["compose_files"] = [self.pin("compose.yml", release.encoded({"services": {
                    "orchestrator": {"image": self.image, "environment": {"FIXTURE_REFERENCE": value}}}}))]
                self.save()
                before = len(self.calls)
                with self.assertRaisesRegex(release.ReleaseError, "compose_startup_interpolation_unsupported"):
                    release.Release(self.descriptor)
                self.assertFalse(any(argv[:2] == ["docker", "compose"] for argv, _ in self.calls[before:]))
        self.spec = original
        self.spec["compose_files"] = [self.pin("compose.yml", b"services: {}\n")]
        for key, value, reason in (("FIXTURE_REFERENCE", "${PSModulePath}", "compose_startup_interpolation_unsupported"),
                                  ("PSMODULEPATH", "synthetic", "compose_startup_interpolation_unsupported"),
                                  ("DIRECTOR_ASGI_APP", "'director_orchestrator.main:app'", "compose_control_selector_unsupported"),
                                  ("DIRECTOR_RUNTIME_IMAGE", "${FIXTURE_IMAGE}", "compose_control_selector_unsupported"),
                                  ("ALLOW_PAID_RUNS", "false # inline comment", "compose_control_selector_unsupported")):
            with self.subTest(key=key, value=value):
                self.spec["runtime_env"] = self.pin_env("runtime.env", dict(self.runtime, **{key: value}))
                self.save()
                with self.assertRaisesRegex(release.ReleaseError, reason):
                    release.Release(self.descriptor)

    def test_host_preflight_refuses_runtime_loader_tool_and_host_overrides_before_child(self):
        for key in ("PYTHONPATH", "LD_PRELOAD", "DOCKER_HOST", "GIT_CONFIG_GLOBAL", "PATH", "TEMP", "HOME"):
            with self.subTest(key=key):
                self.spec["runtime_env"] = self.pin_env("runtime.env", dict(self.runtime, **{key: "synthetic-override"}))
                self.save()
                tool = release.Release(self.descriptor)
                self.calls.clear()
                with self.assertRaisesRegex(release.ReleaseError, "runtime_host_environment_unsupported"):
                    tool.live_config_check()
                self.assertFalse(any(Path(argv[-1]).name == "live-dispatch-preflight.py" for argv, _ in self.calls))
        token = "synthetic-intended-app-token"
        self.spec["runtime_env"] = self.pin_env("runtime.env", dict(self.runtime, GITHUB_TOKEN=token,
            ORACLE_AUTOMERGE_GITHUB_TOKEN=token))
        self.save()
        release.Release(self.descriptor).live_config_check()
        self.assertEqual(sum(Path(argv[-1]).name == "live-dispatch-preflight.py" for argv, _ in self.calls), 1)
        environment = next(kwargs["env"] for argv, kwargs in self.calls if Path(argv[-1]).name == "live-dispatch-preflight.py")
        self.assertTrue(environment["GITHUB_TOKEN"] == environment["ORACLE_AUTOMERGE_GITHUB_TOKEN"] == token)

    def test_compose_environment_uses_control_selectors_and_frozen_host_values(self):
        self.runtime.update(TEMP="profile-temp", HOME="profile-home", PATH="profile-path",
                            FIXTURE_QUOTED="'synthetic-secret'", FIXTURE_EXPANDED="${FIXTURE_QUOTED}")
        self.spec["runtime_env"] = self.pin_env("runtime.env", self.runtime)
        self.save()
        with patch.dict(os.environ, {"TEMP": "frozen-temp", "HOME": "frozen-home", "PATH": "frozen-path"}):
            tool = release.Release(self.descriptor)
        with patch.dict(os.environ, {"TEMP": "later-temp", "HOME": "later-home", "PATH": "later-path"}):
            tool.compose_document(tool.runtime)
        environment = next(kwargs["env"] for argv, kwargs in reversed(self.calls) if argv[:2] == ["docker", "compose"])
        self.assertEqual(environment["TEMP"], "frozen-temp")
        self.assertEqual(environment["HOME"], "frozen-home")
        self.assertEqual(environment["PATH"], str(Path(self.spec["executables"]["powershell"]).parent) + os.pathsep + "frozen-path")
        self.assertNotIn("FIXTURE_QUOTED", environment)
        self.assertNotIn("FIXTURE_EXPANDED", environment)
        self.assertEqual(environment["DIRECTOR_RUNTIME_IMAGE"], self.image)
        self.assertEqual(environment["DIRECTOR_ENV_FILE"], self.spec["runtime_env"]["path"])
        control = (ROOT / "scripts/windows-langgraph-primary-control.ps1").read_text()
        block = control[control.index("$env:DIRECTOR_ENV_FILE = $environmentPath"):control.index("Invoke-Compose config --quiet")]
        self.assertEqual(tuple(re.findall(r'"([A-Z][A-Z0-9_]*)"', block)), release.CONTROL_SELECTORS)
        for changes in release.RUNTIME_CHANGES.values():
            self.assertLessEqual(set(changes), set(release.CONTROL_SELECTORS))
            self.assertEqual({key: release.compose_environment(tool.compose_base_environment(),
                dict(tool.runtime, **changes))[key] for key in changes}, changes)

    def test_n8n_postgres_requires_healthy_required_database_dependency(self):
        self.sibling()
        graph = copy.deepcopy(self.sibling_compose)
        db = graph["services"]["n8n-db"] = copy.deepcopy(graph["services"]["langgraph-primary-db"])
        db["environment"] = {"POSTGRES_DB": "n8n", "POSTGRES_USER": "n8n", "POSTGRES_PASSWORD": "fixture"}
        db["healthcheck"]["test"] = ["CMD-SHELL", "pg_isready -U n8n -d n8n"]
        db["volumes"][0]["source"] = "n8n-db-data"
        graph["volumes"]["n8n-db-data"] = {"name": "fixture-sibling_n8n-db-data"}
        graph["services"]["n8n"]["environment"].update(DB_TYPE="postgresdb", DB_POSTGRESDB_HOST="n8n-db",
            DB_POSTGRESDB_DATABASE="n8n", DB_POSTGRESDB_USER="n8n", DB_POSTGRESDB_PASSWORD="fixture")
        graph["services"]["n8n"]["depends_on"] = {"n8n-db": {"condition": "service_healthy", "required": True}}
        release.isolated_sibling(self.compose, graph, 18080)
        for dependency in (None, {}, {"condition": "service_started"},
                           {"condition": "service_healthy", "required": False},
                           {"condition": "service_healthy", "restart": True}):
            altered = copy.deepcopy(graph)
            altered["services"]["n8n"]["depends_on"] = {} if dependency is None else {"n8n-db": dependency}
            with self.subTest(dependency=dependency), self.assertRaises(release.ReleaseError):
                release.isolated_sibling(self.compose, altered, 18080)
        for healthcheck in ({}, {"disable": True}, {"test": ["NONE"]}):
            altered = copy.deepcopy(graph)
            altered["services"]["n8n-db"]["healthcheck"] = healthcheck
            with self.subTest(healthcheck=healthcheck), self.assertRaises(release.ReleaseError):
                release.isolated_sibling(self.compose, altered, 18080)

    def test_stock_compose_runtime_selector_is_bound_before_start_database(self):
        sibling = self.sibling()
        stock = self.pin("compose.yml", (ROOT / "docker-compose.yml").read_bytes())
        self.spec["compose_files"] = sibling["compose_files"] = [stock]
        self.spec["rehearsal"]["descriptor"] = self.pin("sibling.json", release.encoded(sibling))
        self.save()
        with patch.dict(os.environ, {"DIRECTOR_ENV_FILE": str(self.root / "unbound.env")}):
            plan = release.Release(self.descriptor).rehearsal()
            self.initialize_intake(plan)
            start = next(item for item in plan["steps"] if item["action"] == "StartDatabase")
            self.assertEqual(start["action"], "StartDatabase")
            payload = json.loads(base64.b64decode(start["argv"][-1]))
            self.assertEqual(payload["environment"].get("DIRECTOR_ENV_FILE"), sibling["runtime_env"]["path"])
            self.launch_fixture(start)
        self.assertEqual(self.launched[0].kwargs["env"]["DIRECTOR_ENV_FILE"], sibling["runtime_env"]["path"])
        Path(sibling["runtime_env"]["path"]).write_bytes(b"ALTERED=fixture\n")
        with self.assertRaisesRegex(SystemExit, "runtime profile changed"):
            self.launch_fixture(start)
        self.assertEqual(self.launched, [])

    def test_compose_env_file_supports_only_existing_bound_runtime_input(self):
        runtime = self.spec["runtime_env"]
        for entry in (runtime["path"], Path(runtime["path"]).name, "$DIRECTOR_ENV_FILE", "${DIRECTOR_ENV_FILE}",
                      "${DIRECTOR_ENV_FILE:-.env}", [{"path": runtime["path"], "required": True}]):
            self.spec["compose_files"] = [self.pin("compose.yml", release.encoded({"services": {"orchestrator": {"env_file": entry}}}))]
            self.save()
            tool = release.Release(self.descriptor)
            tool.compose_document(tool.runtime)
        duplicate = self.root / "same-bytes-other-input.env"
        duplicate.write_bytes(Path(runtime["path"]).read_bytes())
        for entry in (str(duplicate), [{"path": runtime["path"], "format": "raw"}],
                      [{"path": runtime["path"], "required": False}]):
            self.spec["compose_files"] = [self.pin("compose.yml", release.encoded({"services": {"orchestrator": {"env_file": entry}}}))]
            self.save()
            self.calls.clear()
            with self.subTest(entry=entry), self.assertRaisesRegex(release.ReleaseError, "compose_env_file_unbound"):
                release.Release(self.descriptor)
            self.assertEqual(self.calls, [])

    def test_stock_compose_source_renders_bound_runtime_and_future_modes_without_daemon(self):
        docker = shutil.which("docker")
        if not docker and os.name == "nt":
            path = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Docker/Docker/resources/bin/docker.exe"
            docker = str(path) if path.is_file() else None
        if not docker:
            self.skipTest("Docker Compose CLI required for configuration-only stock-source proof")
        windows_cli_from_wsl = False
        if os.name != "nt" and os.environ.get("WSL_DISTRO_NAME"):
            with Path(docker).open("rb") as stream:
                shim = stream.read(1024)
            windows_cli_from_wsl = Path(docker).resolve().suffix.lower() == ".exe" or bool(re.fullmatch(
                rb'#!/bin/sh\r?\nexec "[^"\r\n]+docker\.exe" "\$@"\s*', shim))
        self.runtime["FIXTURE_BOUND_INPUT"] = "from-protected-runtime"
        self.spec["runtime_env"] = self.pin_env("runtime.env", self.runtime)
        self.spec["compose_files"] = [self.pin("compose.yml", (ROOT / "docker-compose.yml").read_bytes())]
        self.save()
        calls = []
        def actual_config(argv, **kwargs):
            self.assertEqual(argv[:2], ["docker", "compose"])
            self.assertEqual(argv[-3:], ["config", "--format", "json"])
            self.assertEqual(kwargs["env"]["DIRECTOR_ENV_FILE"], self.spec["runtime_env"]["path"])
            calls.append(argv)
            environment = dict(kwargs["env"])
            if windows_cli_from_wsl:
                # A WSL Docker shim may invoke Windows Docker.exe. Forward the
                # synthetic inputs across that boundary, preserving the bound
                # file identity. Native Linux and Windows paths stay unchanged.
                forwarded = (set(runtime) | {"DIRECTOR_COMPOSE_PROJECT_NAME"}) - {"DIRECTOR_ENV_FILE"}
                environment["WSLENV"] = ":".join(["DIRECTOR_ENV_FILE/pw", *sorted(key + "/w" for key in forwarded)])
            result = subprocess.run([docker, *argv[1:]], cwd=kwargs["cwd"], env=environment,
                                    capture_output=True, timeout=30)
            self.assertEqual(
                result.returncode, 0,
                f"configuration-only stock Compose producer failed: rc={result.returncode}, "
                f"stdout_bytes={len(result.stdout)}, "
                f"stderr_tail={result.stderr.decode('utf-8', errors='replace')[-1500:]!r}",
            )
            return result.stdout
        tool = release.Release(self.descriptor)
        runtime = dict(tool.runtime)
        for changes in ({}, *release.RUNTIME_CHANGES.values()):
            runtime.update(changes)
            with patch.object(release, "command", side_effect=actual_config):
                graph = tool.compose_document(runtime)
            self.assertEqual(graph["services"]["orchestrator"]["environment"]["FIXTURE_BOUND_INPUT"], "from-protected-runtime")
            for key, value in changes.items():
                self.assertEqual(graph["services"]["orchestrator"]["environment"][key], value)
        self.assertEqual(len(calls), 4)

    def test_sibling_network_options_are_closed_to_stock_defaults(self):
        self.sibling()
        for key, value in (("ipam", {"config": [{"subnet": "172.25.0.0/16"}]}),
                           ("ipam", None), ("ipam", []), ("ipam", False), ("ipam", {"config": []}),
                           ("labels", {"unreviewed": "value"}),
                           ("attachable", True), ("internal", True), ("enable_ipv6", True), ("enable_ipv4", False)):
            graph = copy.deepcopy(self.sibling_compose)
            graph["networks"]["default"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(release.ReleaseError):
                release.isolated_sibling(self.compose, graph, 18080)
        graph = copy.deepcopy(self.sibling_compose)
        graph["networks"]["default"].update(external=False, internal=False, attachable=False, enable_ipv4=True, enable_ipv6=False, ipam={})
        release.isolated_sibling(self.compose, graph, 18080)

    def test_sibling_ports_only_publish_stock_application_targets(self):
        self.sibling()
        for name, target, protocol in (("langgraph-primary-db", 5432, "tcp"), ("slackbot", 8080, "tcp"),
                                       ("orchestrator", 5432, "tcp"), ("n8n", 5678, "udp"), ("n8n", 9999, "tcp")):
            graph = copy.deepcopy(self.sibling_compose)
            graph["services"][name].setdefault("ports", []).append(
                {"host_ip": "127.0.0.1", "published": "15678", "target": target, "protocol": protocol})
            with self.subTest(service=name, target=target, protocol=protocol), self.assertRaises(release.ReleaseError):
                release.isolated_sibling(self.compose, graph, 18080)

    def test_sibling_n8n_requires_stock_safe_setting_values(self):
        self.sibling()
        safe = self.sibling_compose["services"]["n8n"]["environment"]
        for key, value in (("N8N_HOST", "remote.invalid"), ("N8N_PORT", "9999"), ("N8N_PROTOCOL", "https"),
                           ("N8N_SECURE_COOKIE", "true"), ("N8N_DIAGNOSTICS_ENABLED", "true"),
                           ("N8N_PERSONALIZATION_ENABLED", "true"), ("N8N_BLOCK_ENV_ACCESS_IN_NODE", "false")):
            for missing in (False, True):
                graph = copy.deepcopy(self.sibling_compose)
                if missing: graph["services"]["n8n"]["environment"].pop(key)
                else: graph["services"]["n8n"]["environment"][key] = value
                with self.subTest(key=key, missing=missing), self.assertRaises(release.ReleaseError):
                    release.isolated_sibling(self.compose, graph, 18080)
        self.assertEqual(safe["N8N_PORT"], "5678")

    def test_n8n_published_port_must_match_the_selected_runtime_profile(self):
        sibling = self.sibling()
        self.sibling_compose["services"]["n8n"]["ports"] = [
            {"host_ip": "127.0.0.1", "published": "15678", "target": 5678, "protocol": "tcp"}]
        self.repin_graph(sibling, self.sibling_compose)
        with self.assertRaisesRegex(release.ReleaseError, "sibling_port_not_stock"):
            release.Release(self.descriptor).rehearsal()
        runtime = release.dotenv(Path(sibling["runtime_env"]["path"]).read_bytes())
        runtime["N8N_HOST_PORT"] = "15678"
        sibling["runtime_env"] = self.pin_env("sibling-runtime.env", runtime)
        self.repin_graph(sibling, self.sibling_compose)
        plan = release.Release(self.descriptor).rehearsal()
        payload = json.loads(base64.b64decode(plan["steps"][0]["argv"][-1]))
        self.assertEqual(next(item for item in payload["ports"] if item["service"] == "n8n")["published"], "15678")

    def test_stage_rechecks_ports_without_a_database_exception(self):
        import socket
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        self.initialize_intake(plan)
        step = next(item for item in plan["steps"] if item["action"] == "StageRuntime")
        for protocol, kind in (("tcp", socket.SOCK_STREAM), ("udp", socket.SOCK_DGRAM)):
            with socket.socket(socket.AF_INET, kind) as listener:
                listener.bind(("127.0.0.1", 0))
                if protocol == "tcp": listener.listen(1)
                payload = json.loads(base64.b64decode(step["argv"][-1]))
                payload["ports"] = [{"host_ip": "127.0.0.1", "published": listener.getsockname()[1],
                    "protocol": protocol, "service": "orchestrator"}]
                step["argv"][-1] = base64.b64encode(release.encoded(payload)).decode()
                with self.subTest(protocol=protocol), self.assertRaisesRegex(SystemExit, "port"):
                    self.launch_fixture(step)
                self.assertEqual(self.launched, [])
                payload["ports"][0]["service"] = "langgraph-primary-db"
                step["argv"][-1] = base64.b64encode(release.encoded(payload)).decode()
                with self.assertRaisesRegex(SystemExit, "port"):
                    self.launch_fixture(step)
                self.assertEqual(self.launched, [])

    def test_registry_bind_requires_existing_exact_owner_directory(self):
        self.sibling()
        directory = self.root / "registry"
        directory.mkdir()
        mount = {"type": "bind", "source": str(directory), "target": "/run/director-acceptance-registry",
                 "read_only": True, "bind": {"create_host_path": False}}
        self.sibling_compose["services"]["orchestrator"]["volumes"].append(mount)
        release.isolated_sibling(self.compose, self.sibling_compose, 18080, registry_path=str(directory))
        with self.assertRaisesRegex(release.ReleaseError, "registry_mount_mismatch"):
            release.isolated_sibling(self.compose, self.sibling_compose, 18080, registry_path=str(self.snapshot))
        mount["source"] = str(directory / "missing")
        with self.assertRaisesRegex(release.ReleaseError, "bind_source_unavailable"):
            release.isolated_sibling(self.compose, self.sibling_compose, 18080, registry_path=mount["source"])
        self.assertFalse(Path(mount["source"]).exists())

    def test_gateway_start_actions_require_running_pinned_sibling_process(self):
        sibling = self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        self.initialize_intake(plan)
        path = Path(sibling["runtime_env"]["path"])
        original = release.dotenv(path.read_bytes())
        for action, preceding in (("ActivateOffline", "StageRuntime"), ("Rollback", "ActivateOffline")):
            runtime = dict(original, **release.RUNTIME_CHANGES[preceding])
            path.write_bytes("".join(f"{key}={value}\n" for key, value in runtime.items()).encode())
            step = next(item for item in plan["steps"] if item["action"] == action)
            for defect in (None, "stopped", "image", "project"):
                def observe(argv, **kwargs):
                    if argv[:2] == ["docker", "inspect"] and argv[-1] == "fixture-gateway":
                        rows = json.loads(self.platform(argv, **kwargs))
                        if defect == "stopped": rows[0]["State"]["Running"] = False
                        if defect == "image": rows[0]["Image"] = "unreviewed"
                        if defect == "project": rows[0]["Config"]["Labels"]["com.docker.compose.project"] = "other"
                        return release.encoded(rows)
                    return None
                with self.subTest(action=action, defect=defect):
                    if defect:
                        with self.assertRaisesRegex(SystemExit, "gateway not proved running"):
                            self.launch_fixture(step, observe=observe)
                    else:
                        self.launch_fixture(step, observe=observe)
                    self.assertEqual(len(self.launched), 1)

    def test_emitted_tools_remain_outside_source_and_snapshot(self):
        self.sibling()
        step = release.Release(self.descriptor).rehearsal()["steps"][0]
        for base in (self.source, self.snapshot):
            path = base / "substituted-git"
            path.write_bytes(b"never execute")
            payload = json.loads(base64.b64decode(step["argv"][-1]))
            payload["executables"]["git"] = str(path)
            changed = copy.deepcopy(step)
            changed["argv"][-1] = base64.b64encode(release.encoded(payload)).decode()
            with self.subTest(base=base.name), self.assertRaisesRegex(SystemExit, "trusted executable changed"):
                self.launch_fixture(changed)
            self.assertEqual(self.launched, [])

    def test_real_emitted_launcher_imports_verified_containment_without_cache_writes(self):
        self.sibling()
        plan = release.Release(self.descriptor).rehearsal()
        self.initialize_intake(plan)
        step = next(item for item in plan["steps"] if item["action"] == "StartDatabase")
        checkout = self.root / "real-source"
        package = checkout / "src" / "director_orchestrator"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "process_isolation.py").write_bytes((ROOT / "src/director_orchestrator/process_isolation.py").read_bytes())
        git_path = str(Path(shutil.which("git")).resolve())
        def git(*args):
            return subprocess.check_output([git_path, *args], cwd=checkout, env=release.child_environment(), stderr=subprocess.DEVNULL)
        git("init", "-q")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        marker = self.root / "contained-child-ran"
        payload = json.loads(base64.b64decode(step["argv"][-1]))
        payload.update(cwd=str(checkout), first_step=False, action="Fixture", state_volumes={}, images={}, ports=[],
            candidate={kind: git("rev-parse", "HEAD^{" + kind + "}").decode().strip() for kind in ("commit", "tree")},
            control_argv=[sys.executable, "-I", "-B", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ran')", str(marker)],
            final_resources_sha256=release.sha(release.encoded(dict(
                {key: [] for key in ("containers", "binds", "volumes", "networks", "ports")},
                slack_identities={key: [] for key in release.SLACK_IDENTITY_KEYS}))))
        payload["executables"]["git"] = git_path
        payload.pop("local_database", None)
        # Docker observations alone are replaced. The isolated process uses real
        # Git twice, imports the actual helper from this pinned tiny checkout,
        # and launches a real harmless child through the production containment.
        harness = self.root / "emitted-fixture.json"
        harness.write_bytes(release.encoded({"code": step["argv"][-2], "payload": payload, "intake_rows": self.intake_rows}))
        response = ("import json,pathlib,sys\n"
            "fixture=json.loads(pathlib.Path(sys.argv[1]).read_bytes()); v=fixture['payload']; args=sys.argv[2:]\n"
            "if args[:2]==['docker','info']: raw=v['daemon_id'].encode()\n"
            "elif args[:2]==['docker','ps']: raw=b''\n"
            "elif args[:3]==['docker','volume','inspect']: raw=json.dumps([fixture['intake_rows'][args[-1]]]).encode()\n"
            "else: raise AssertionError(args)\n"
            "sys.stdout.buffer.write(raw)\n")
        wrapper = """import base64,json,pathlib,subprocess,sys
fixture_path=sys.argv[1]
fixture=json.loads(pathlib.Path(fixture_path).read_bytes()); v=fixture['payload']
assert sys.flags.isolated and sys.flags.dont_write_bytecode
original=subprocess.Popen; checks=[]
response=""" + repr(response) + """
def popen(args,**kwargs):
    args=list(args)
    if args[0]=='git':
        if args[1]=='status': checks.append('source')
    elif args[0]=='docker':
        # A harmless real process supplies synthetic daemon observations. The
        # production bounded I/O and authenticated containment stay real.
        args=[sys.executable,'-I','-B','-c',response,fixture_path,*args]
        kwargs['executable']=sys.executable
    return original(args,**kwargs)
subprocess.Popen=popen
sys.argv=['launcher',base64.b64encode(json.dumps(v).encode()).decode()]
exec(compile(fixture['code'],'emitted-launcher','exec'),{})
assert checks==['source','source'],checks
"""
        argv = [*step["argv"][:-2], wrapper, str(harness)]
        result = subprocess.run(argv, capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.exists())
        self.assertEqual(list(checkout.rglob("*.pyc")), [])
        self.assertEqual(git("status", "--porcelain=v2", "--untracked-files=all"), b"")


class ReleaseInputAndProcessTests(unittest.TestCase):
    def git_authority_fixture(self, root):
        """A real pinned repo with inert tracked configs until the app selects them."""
        git_path = shutil.which("git")
        if not git_path:
            self.skipTest("Git required")
        repo = root / "snapshot"
        repo.mkdir()
        for name in ("empty-template", "empty-home", "empty-xdg"):
            (root / name).mkdir()
        environment = {key: value for key, value in release.child_environment().items()
                       if not key.startswith("GIT_")}
        environment.update(HOME=str(root / "empty-home"), XDG_CONFIG_HOME=str(root / "empty-xdg"),
                           GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull)
        def git(*args):
            return subprocess.check_output([git_path, *args], cwd=repo, env=environment, stderr=subprocess.DEVNULL)
        git("init", "--template=" + str(root / "empty-template"), "-b", "main")
        git("config", "core.autocrlf", "false")
        git("config", "core.filemode", "false")
        files = {"README.md": b"synthetic pinned authority\n",
            "tracked-hook.sh": b'#!/bin/sh\nprintf executed > "$SYNTHETIC_MARKER"\nprintf "token\\0"\n',
            "tracked-home/.gitconfig": b"[core]\n\tfsmonitor = ./tracked-hook.sh\n",
            "tracked-xdg/git/config": b"[core]\n\tfsmonitor = ./tracked-hook.sh\n"}
        for name, raw in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (repo / "tracked-hook.sh").chmod(0o755)
        git("add", ".")
        git("-c", "core.hooksPath=/dev/null", "-c", "user.name=Fixture",
            "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        git("remote", "add", "origin", "https://github.com/example/demo.git")
        return repo, environment, git, git("rev-parse", "HEAD").decode().strip(), git("rev-parse", "HEAD^{tree}").decode().strip()

    def test_application_git_environment_cannot_execute_tracked_hook(self):
        from types import SimpleNamespace
        from director_orchestrator.models import Assignment, ProviderName
        from director_orchestrator.packet_transports import LocalGitAuthoritySnapshotResolver
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, environment, git, commit, tree = self.git_authority_fixture(root)
            assignment = Assignment(sequence=1, agent=ProviderName.CURSOR, task="synthetic inspection",
                acceptance=["report"], repository="example/demo", starting_ref=commit)
            record = SimpleNamespace(plan=SimpleNamespace(prestage=SimpleNamespace(
                worktree=str(repo), base_sha=commit, branch=commit)))
            resolver = LocalGitAuthoritySnapshotResolver("example/demo", str(repo),
                pinned_commit_sha=commit, pinned_tree_sha=tree)
            stock = {"WorkingDir": "/app", "Env": ["PATH=/usr/local/bin:/usr/bin:/bin", "PYTHONPATH=/app/src",
                     "PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1"]}
            release.reviewed_application_environment({}, stock)
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(len(resolver.resolve(record, assignment)), 4)
            cases = {"count": {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
                               "GIT_CONFIG_VALUE_0": "./tracked-hook.sh"},
                     "home": {"HOME": str(repo / "tracked-home")},
                     "xdg": {"XDG_CONFIG_HOME": str(repo / "tracked-xdg")}}
            for origin in ("image", "compose"):
                for mode, changes in cases.items():
                    with self.subTest(origin=origin, mode=mode):
                        marker = root / (origin + "-" + mode + ".marker")
                        image, service = copy.deepcopy(stock), {}
                        changes = dict(changes, SYNTHETIC_MARKER=marker.as_posix())
                        if origin == "image":
                            image["Env"].extend(key + "=" + value for key, value in changes.items())
                        else:
                            service["environment"] = changes
                        refused = False
                        try:
                            effective = release.reviewed_application_environment(service, image)
                        except release.ReleaseError as error:
                            self.assertEqual(str(error), "sibling_git_environment_unsupported")
                            refused = True
                        else:
                            # The real local Git executable remains on the
                            # trusted host PATH; the accepted Git selectors are
                            # carried unchanged into the real pinned resolver.
                            native = dict(environment, **{key: value for key, value in effective.items() if key != "PATH"})
                            with patch.dict(os.environ, native, clear=True):
                                self.assertEqual(len(resolver.resolve(record, assignment)), 4)
                        self.assertFalse(marker.exists(), "accepted application environment executed the tracked hook")
                        self.assertTrue(refused, "unsupported application Git selector was admitted")
            for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "GIT_EXEC_PATH", "HOME", "XDG_CONFIG_HOME"):
                with self.subTest(empty=key), self.assertRaisesRegex(release.ReleaseError, "^sibling_git_environment_unsupported$"):
                    release.reviewed_application_environment({"environment": {key: ""}}, stock)
            self.assertEqual(git("status", "--porcelain=v2"), b"")

    def emitted_read_fixture(self, *, containment=None):
        import ast
        import signal
        nodes = [item for item in ast.parse(release.REHEARSAL_LAUNCH).body
                 if isinstance(item, ast.FunctionDef) and item.name in ("command", "require", "read")]
        namespace = {"subprocess": subprocess, "pathlib": __import__("pathlib"), "Path": Path,
            "signal": signal, "os": os, "time": time, "tempfile": tempfile, "MAX_BYTES": release.MAX_BYTES,
            "native_containment": containment, "env": release.child_environment(),
            "v": {"executables": {}, "cwd": str(ROOT)}}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "actual-emitted-native-reader", "exec"), namespace)
        return namespace

    def test_emitted_read_caps_output_and_preserves_input(self):
        namespace = self.emitted_read_fixture()
        read = namespace["read"]
        echo = [sys.executable, "-I", "-B", "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"]
        self.assertEqual(read(echo, input=b"synthetic stdin\0bytes"), b"synthetic stdin\0bytes")
        self.assertEqual(read(echo), b"")
        for stream in ("stdout", "stderr"):
            writer = [sys.executable, "-I", "-B", "-c",
                      "import sys; sys." + stream + ".buffer.write(b'x'*(4*1024*1024+1))"]
            # On the published implementation stdout already fails this first
            # assertion; it never floods the parent with the later stderr case.
            with self.assertRaisesRegex(SystemExit, "^command_output_exceeded$"):
                read(writer)
        with self.assertRaisesRegex(SystemExit, "^platform_check_failed$"):
            read([sys.executable, "-I", "-B", "-c", "import sys; sys.stderr.write('synthetic-not-for-errors'); sys.exit(1)"])

    def test_emitted_read_deadline_cleans_children_before_and_after_authentication(self):
        from types import SimpleNamespace
        for contained in (False, True):
            with self.subTest(contained=contained), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker, ready, arm = root / "survived", root / "ready", root / "arm"
                descendant = ("import pathlib,sys,time\n"
                              "ready,arm,marker=map(pathlib.Path,sys.argv[1:4])\n"
                              "ready.write_text('ready')\n"
                              "while not arm.exists(): time.sleep(.02)\n"
                              "time.sleep(6.5)\nmarker.write_text('escaped')\n")
                parent = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-I','-B','-c',"
                          "sys.argv[4],*sys.argv[1:4]]); time.sleep(20)")
                argv = [sys.executable, "-I", "-B", "-c", parent, str(ready), str(arm), str(marker), descendant]
                process, guard = REAL_CONTAINED_LAUNCH(argv, env=release.child_environment(), cwd=ROOT,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                stopped = False
                try:
                    startup_deadline = time.monotonic() + 10
                    while not ready.exists() and process.poll() is None and time.monotonic() < startup_deadline:
                        time.sleep(.02)
                    self.assertTrue(ready.exists(), "real grandchild did not signal readiness")
                    launched = False
                    def owned_launcher(args, **kwargs):
                        nonlocal launched
                        self.assertEqual(args, argv)
                        self.assertFalse(launched, "read launcher must run once")
                        launched = True
                        return (process, guard) if contained else process
                    def owned_stop(child, token, **kwargs):
                        nonlocal stopped
                        self.assertIs(child, process)
                        self.assertIs(token, guard)
                        result = REAL_CONTAINED_STOP(child, token, **kwargs)
                        stopped = True
                        return result
                    namespace = self.emitted_read_fixture(containment=(owned_launcher, owned_stop) if contained else None)
                    if not contained:
                        namespace["subprocess"] = SimpleNamespace(**{**vars(subprocess), "Popen": owned_launcher})
                    actual = namespace["command"]
                    def shortened(*args, deadline, **kwargs):
                        self.assertGreater(deadline, time.monotonic() + 29)
                        return actual(*args, deadline=min(deadline, time.monotonic() + 0.5), **kwargs)
                    namespace["command"] = shortened
                    armed_at = time.monotonic()
                    arm.write_text("armed")
                    start = time.monotonic()
                    with self.assertRaisesRegex(SystemExit, "^doctor_deadline_exhausted$"):
                        namespace["read"](argv)
                    self.assertLess(time.monotonic() - start, 6)
                    self.assertTrue(launched)
                    self.assertIsNotNone(process.poll())
                    self.assertEqual(stopped, contained)
                    # Keep the outer guard open until fallback cleanup proves itself.
                    while time.monotonic() < armed_at + 7.5:
                        time.sleep(.02)
                    self.assertFalse(marker.exists(), "read timeout left a native descendant alive")
                finally:
                    if not stopped:
                        REAL_CONTAINED_STOP(process, guard)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell required for the actual snapshot consumers")
    def test_snapshot_remote_alias_preserves_mandatory_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, environment, git, commit, tree = self.git_authority_fixture(root)
            git("remote", "add", "reviewed", "https://github.com/example/demo.git")
            tool = release.Release.__new__(release.Release)
            tool.spec = {"snapshot": {"path": str(repo), "remote": "reviewed", "repository": "example/demo",
                                     "starting_ref": "main", "commit": commit, "tree": tree}}
            def powershell(name, arguments):
                return REAL_COMMAND([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts" / name),
                    *arguments, "-GitExecutable", shutil.which("git")], cwd=ROOT, env=environment, deadline=time.monotonic() + 30)
            tool.powershell = powershell
            control = (ROOT / "scripts/windows-langgraph-primary-control.ps1").read_text()
            start = control.index('$snapshot = [string]$values["REPOSITORY_SNAPSHOT_HOST_PATH"]')
            block = control[start:control.index("$env:DIRECTOR_ENV_FILE =", start)]
            harness = root / "control-snapshot.ps1"
            harness.write_text('param($SnapshotPath,$Commit,$Tree,$SelectedGit,$ScriptRoot)\n'
                '$ErrorActionPreference="Stop";$PSScriptRoot=$ScriptRoot\n'
                '$values=@{REPOSITORY_SNAPSHOT_HOST_PATH=$SnapshotPath;REPOSITORY_ALLOWLIST="example/demo";ALLOWED_STARTING_REFS="main"}\n'
                '$executionCommit=$Commit;$executionTree=$Tree;$GitExecutable=$SelectedGit\n' + block)
            argv = [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-File", str(harness),
                    "-SnapshotPath", str(repo), "-Commit", commit, "-Tree", tree,
                    "-SelectedGit", shutil.which("git"), "-ScriptRoot", str(ROOT / "scripts")]
            tool.snapshot_check()
            REAL_COMMAND(argv, cwd=ROOT, env=environment, deadline=time.monotonic() + 30)
            for missing in (False, True):
                if missing:
                    git("remote", "remove", "origin")
                else:
                    git("remote", "set-url", "origin", "https://github.com/example/other.git")
                with self.subTest(missing=missing), self.assertRaisesRegex(release.ReleaseError, "^platform_check_failed$"):
                    tool.snapshot_check()
                with self.subTest(control_missing=missing), self.assertRaisesRegex(release.ReleaseError, "^platform_check_failed$"):
                    REAL_COMMAND(argv, cwd=ROOT, env=environment, deadline=time.monotonic() + 30)

    def test_cli_bootstrap_refuses_checkout_imports_without_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "director-release.py"
            script.write_bytes((ROOT / "scripts/director-release.py").read_bytes())
            marker = root / "imported"
            # Even __future__ is a filesystem import. The old CLI executes a
            # shadow module for --help before any owner/source check protects it.
            (root / "argparse.py").write_text("open(" + repr(str(marker)) + ", 'w').write('executed')\n")
            (root / "__future__.py").write_text("open(" + repr(str(marker)) + ", 'w').write('future executed')\n")
            normal = subprocess.run([sys.executable, "-B", str(script), "--help"], capture_output=True, timeout=20)
            self.assertFalse(marker.exists(), "checkout import executed before the isolation gate")
            self.assertNotEqual(normal.returncode, 0)
            self.assertIn(b"-I", normal.stderr)
            writes_cache = subprocess.run([sys.executable, "-I", str(script), "--help"], capture_output=True, timeout=20)
            self.assertNotEqual(writes_cache.returncode, 0)
            self.assertIn(b"-B", writes_cache.stderr)
            self.assertEqual(list(root.rglob("*.pyc")), [])
            isolated = subprocess.run([sys.executable, "-I", "-B", str(script), "--help"], capture_output=True, timeout=20)
            self.assertEqual(isolated.returncode, 0, isolated.stderr)
            self.assertFalse(marker.exists())

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
