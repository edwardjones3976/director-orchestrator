# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:34 UTC | BEGIN]]
"""Pinned R1 preparation, read-only doctor and sibling rehearsal command plan.

No command here reserves a generation, dispatches a provider, starts a service,
or manufactures acceptance. The existing control script owns execution and gates.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

from jsonschema import Draft202012Validator

from director_orchestrator.primary_activation_evidence import ActivationIdentity
from director_orchestrator.primary_generation import resolved_deployment_digest


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs/schemas/director-release-descriptor-v1.schema.json"
MAX_BYTES = 4 * 1024 * 1024
DEPENDENCIES = {
    "fresh_generation_authority": "primary_generation.require_generation has no read-only fresh-manifest verification path",
    "paid_admission": "the validation CLI has no per-lane campaign/daily-dollar reservation and settlement contract",
    "trusted_descriptor": "W2 must publish the HOME-independent owner descriptor consumer before preparation can be trusted",
}


class ReleaseError(RuntimeError):
    """Only fixed, non-secret reason codes may be reported to the operator."""


def require(condition, reason):
    if not condition:
        raise ReleaseError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def pairs(items):
    result = {}
    for name, value in items:
        require(name not in result, "duplicate_json_key")
        result[name] = value
    return result


def document(raw):
    require(len(raw) <= MAX_BYTES, "oversized_json")
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ReleaseError("nonfinite_json")))


def bounded_read(path):
    path = Path(path)
    unlinked(path)
    require(path.is_file(), "input_path_invalid")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "input_too_large")
    return raw


def unlinked(path):
    """Reject symlinks and Windows junctions anywhere in an input/output path."""
    path = Path(path)
    require(path.is_absolute(), "input_path_invalid")
    for part in (path, *path.parents):
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(metadata.st_mode) and not (
            getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)),
            "linked_path_refused")


def pinned_file(pin):
    raw = bounded_read(pin["path"])
    require(sha(raw) == pin["sha256"], "input_digest_changed")
    return raw


def dotenv(raw):
    result = {}
    for line in raw.decode("utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, value = line.partition("=")
        require(separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", name), "invalid_env_assignment")
        require(name not in result and "\x00" not in value, "duplicate_or_invalid_env_assignment")
        # Match the existing control scripts: raw values, never shell expansion.
        result[name] = value
    return result


def child_environment():
    # Never let an inherited COMPOSE_FILE, DOCKER_HOST, PYTHONPATH, provider key,
    # or Git override silently redirect a release check.
    keep = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
            "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.update(PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1",
               GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def command(argv, *, cwd, env, deadline, input_bytes=None):
    """One deadline, closed stdin, bounded output, no shell and no raw-output logging."""
    require(time.monotonic() < deadline, "doctor_deadline_exhausted")
    with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        if input_bytes:
            stdin.write(input_bytes)
            stdin.seek(0)
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr, **options)
        try:
            while process.poll() is None:
                require(time.monotonic() < deadline, "doctor_deadline_exhausted")
                require(os.fstat(stdout.fileno()).st_size <= MAX_BYTES and
                        os.fstat(stderr.fileno()).st_size <= MAX_BYTES, "command_output_exceeded")
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            require(process.returncode == 0, "platform_check_failed")
            require(time.monotonic() <= deadline, "doctor_deadline_exhausted")
            require(os.fstat(stderr.fileno()).st_size <= MAX_BYTES, "command_output_exceeded")
            stdout.seek(0)
            raw = stdout.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, "command_output_exceeded")
            return raw
        finally:
            if process.poll() is None:
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)


BRIDGE_PROBE = """
import json,ssl,sys,urllib.request
v=json.load(sys.stdin)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
context=ssl.create_default_context(cafile=v['ca'])
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect,
                                 urllib.request.HTTPSHandler(context=context))
request=urllib.request.Request(v['url'],headers={'Authorization':'Bearer '+v['token']})
with opener.open(request,timeout=v['timeout']) as response:
    raw=response.read(65537)
if len(raw)>65536: raise ValueError('oversized bridge reply')
sys.stdout.buffer.write(raw)
"""

DATABASE_PROBE = """
import json,sys
from director_orchestrator.langgraph_postgres import database_binding
from director_orchestrator.provider_validation import PostgresValidationStore,SCHEMA
v=json.load(sys.stdin)
binding=database_binding(v['dsn'],schema=v['schema'],allow_insecure_local_transport=False)
with PostgresValidationStore(v['validation_dsn']).connect() as db:
    db.execute('SET TRANSACTION READ ONLY')
    db.execute("SET LOCAL statement_timeout = '5s'")
    rows=db.execute(f'SELECT provider,state FROM {SCHEMA}.attempts WHERE generation=%s',
                    (v['generation'],)).fetchall()
print(json.dumps({'database_identity':binding[0],'database_instance_id':binding[1],
                  'occupied_slots':[row[0] for row in rows]}))
"""


class Release:
    def __init__(self, path):
        self.path = Path(path)
        self.raw = bounded_read(self.path)
        self.spec = document(self.raw)
        validator = Draft202012Validator(document(SCHEMA.read_bytes()))
        require(not list(validator.iter_errors(self.spec)), "invalid_release_descriptor")
        self.descriptor_digest = sha(self.raw)
        self.source = Path(self.spec["source"])
        require(self.source.is_absolute() and self.source.resolve() == ROOT.resolve(), "wrong_runner_checkout")
        self.output = Path(self.spec["output_directory"])
        unlinked(self.source)
        unlinked(self.output)
        require(self.output.is_absolute() and self.output.is_dir() and not self.output.is_symlink(), "output_directory_unavailable")
        require(not self.output.resolve().is_relative_to(self.source.resolve()), "output_must_be_outside_checkout")
        require(Path(self.spec["snapshot"]["path"]).is_absolute(), "snapshot_path_invalid")
        unlinked(Path(self.spec["snapshot"]["path"]))
        for name in ("python", "powershell"):
            unlinked(Path(self.spec["executables"][name]))
            require(Path(self.spec["executables"][name]).is_absolute() and
                    Path(self.spec["executables"][name]).is_file(), "executable_unavailable")
        self.deadline = time.monotonic() + self.spec["timeout_seconds"]
        self.runtime = dotenv(pinned_file(self.spec["runtime_env"]))
        self.bridge = dotenv(pinned_file(self.spec["bridge_env"]))
        self.manifest = document(pinned_file(self.spec["generation_manifest"]))
        self.validation = document(pinned_file(self.spec["validation_manifest"]))
        pinned_file(self.spec["bridge_ca"])
        for pin in self.spec["compose_files"]:
            pinned_file(pin)
        self.rendered = None

    def run(self, arguments, *, input_bytes=None, env=None):
        return command(arguments, cwd=self.source, env=env or child_environment(),
                       deadline=self.deadline, input_bytes=input_bytes)

    def powershell(self, name, arguments):
        return self.run([self.spec["executables"]["powershell"], "-NoProfile", "-NonInteractive", "-File",
                         str(self.source / "scripts" / name), *arguments])

    def source_check(self):
        expected = self.spec["candidate"]
        for suffix, wanted in (("commit", expected["commit"]), ("tree", expected["tree"])):
            require(self.run(["git", "rev-parse", "--verify", "HEAD^{" + suffix + "}"]).decode().strip() == wanted,
                    "source_identity_changed")
        require(not self.run(["git", "status", "--porcelain=v2", "--untracked-files=all"]).strip(), "source_not_clean")

    def image_check(self):
        images = document(self.run(["docker", "image", "inspect", self.spec["compose_project"] + "-orchestrator"]))
        require(isinstance(images, list) and len(images) == 1, "image_unavailable")
        image = images[0]
        labels = image["Config"]["Labels"]
        require(image["Id"] == self.spec["image_id"] and
                labels.get("org.director.candidate.commit") == self.spec["candidate"]["commit"] and
                labels.get("org.director.candidate.tree") == self.spec["candidate"]["tree"] and
                image["Config"].get("Entrypoint") in (None, []), "image_identity_mismatch")

    def selectors_check(self):
        activation = ActivationIdentity(**self.manifest["activation"])
        candidate, pin = self.spec["candidate"], self.spec["generation_manifest"]["sha256"]
        require(activation.candidate_commit == candidate["commit"] and activation.candidate_tree == candidate["tree"]
                and activation.compose_project == self.spec["compose_project"], "generation_identity_mismatch")
        require(self.runtime.get("LANGGRAPH_PRIMARY_PINNED_COMMIT_SHA") == candidate["commit"] and
                self.runtime.get("LANGGRAPH_PRIMARY_PINNED_TREE_SHA") == candidate["tree"], "runtime_candidate_mismatch")
        require(self.bridge.get("DIRECTOR_PROVIDER_VALIDATION_CANDIDATE") == candidate["commit"] and
                self.bridge.get("DIRECTOR_PROVIDER_VALIDATION_TREE") == candidate["tree"], "bridge_candidate_mismatch")
        for values in (self.runtime, self.bridge):
            require(values.get("LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256") == pin and
                    values.get("DIRECTOR_PROVIDER_VALIDATION_GENERATION", pin) in ("", pin), "generation_selector_mismatch")
        require(self.runtime.get("DIRECTOR_RUNTIME_IMAGE") == self.spec["image_id"] and
                self.manifest.get("image_id") == self.spec["image_id"], "runtime_image_mismatch")
        v = self.validation
        require(set(v) == {"generation", "candidate", "tree", "expires_at", "budget_units", "lanes"} and
                v["generation"] == pin and v["candidate"] == candidate["commit"] and v["tree"] == candidate["tree"],
                "validation_identity_mismatch")
        require(type(v["expires_at"]) is int and 0 < v["expires_at"] - time.time() <= 86400,
                "validation_expired_or_over_24_hours")
        require(isinstance(v["lanes"], dict) and bool(v["lanes"]), "validation_lanes_missing")
        for lane in v["lanes"].values():
            require(isinstance(lane, dict) and re.fullmatch(r"[0-9a-f]{64}", lane.get("configuration_digest", "")),
                    "validation_configuration_pin_invalid")
        # This is a selector comparison, not acceptance or full manifest verification.

    def compose_check(self):
        env = child_environment()
        env.update(self.runtime)
        env.update(DIRECTOR_ENV_FILE=self.spec["runtime_env"]["path"],
                   DIRECTOR_COMPOSE_PROJECT_NAME=self.spec["compose_project"])
        args = ["docker", "compose", "--project-name", self.spec["compose_project"],
                "--env-file", self.spec["runtime_env"]["path"]]
        for pin in self.spec["compose_files"]:
            args.extend(["-f", pin["path"]])
        raw = self.run([*args, "config", "--format", "json"], env=env)
        composed = document(raw)
        require(composed.get("name") == self.spec["compose_project"], "compose_project_mismatch")
        require(resolved_deployment_digest(composed, self.spec["image_id"]) == self.manifest["resolved_compose_sha256"],
                "resolved_compose_mismatch")
        service = composed["services"]["orchestrator"]
        mounts = [item for item in service.get("volumes", []) if item.get("target") == "/repository"]
        snapshot = self.spec["snapshot"]
        require(len(mounts) == 1 and mounts[0].get("type") == "bind" and mounts[0].get("read_only") is True and
                Path(mounts[0]["source"]).resolve() == Path(snapshot["path"]).resolve() and
                Path(self.runtime.get("REPOSITORY_SNAPSHOT_HOST_PATH", "")).resolve() == Path(snapshot["path"]).resolve(),
                "snapshot_mount_mismatch")
        require(Path(snapshot["path"]).is_dir() and any(Path(snapshot["path"]).iterdir()), "snapshot_empty")
        require(composed["services"]["slackbot"].get("pull_policy") == "never", "gateway_can_pull_unreviewed_image")
        self.rendered = encoded(composed) + b"\n"

    def snapshot_check(self):
        s = self.spec["snapshot"]
        self.powershell("windows-primary-snapshot.ps1", ["-Action", "Verify", "-Source", s["path"],
            "-Target", s["path"], "-SourceRemoteName", s["remote"], "-ExpectedRepository", s["repository"],
            "-StartingRef", s["starting_ref"], "-ExpectedCommit", s["commit"], "-ExpectedTree", s["tree"]])

    def reviewers_check(self):
        for kind in ("independent", "security"):
            key = "LANGGRAPH_PRIMARY_ACTIVATION_" + kind.upper() + "_REVIEWER_ALLOWLIST_JSON"
            actors = document(self.runtime.get(key, "null").encode())
            require(isinstance(actors, list) and actors and len(set(actors)) == len(actors) and
                    all(isinstance(actor, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", actor) for actor in actors),
                    "reviewer_allowlist_invalid")
            require(self.spec["reviewers"][kind] in actors, "planned_reviewer_not_allowlisted")

    def bridge_check(self):
        from urllib.parse import urlsplit
        url = urlsplit(self.spec["bridge_url"])
        require(url.scheme == "https" and url.hostname and not url.username and not url.password and
                url.path == "/v1/provider/configuration-digest" and not url.query and not url.fragment,
                "bridge_url_invalid")
        runtime_url = urlsplit(self.runtime.get("LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT", ""))
        require((url.scheme, url.netloc) == (runtime_url.scheme, runtime_url.netloc), "bridge_origin_mismatch")
        self.powershell("windows-host-provider-bridge.ps1", ["-Action", "Preflight", "-EnvironmentFile",
            self.spec["bridge_env"]["path"], "-Workspace", self.spec["snapshot"]["path"],
            "-Python", self.spec["executables"]["python"]])
        require(self.bridge.get("DIRECTOR_HOST_PROVIDER_PROCESS_ENVIRONMENT_FILE"), "bridge_environment_not_pinned")
        token = self.bridge.get("DIRECTOR_PROVIDER_VALIDATION_OWNER_TOKEN", "")
        require(len(token) >= 32, "bridge_owner_token_missing")
        request = {"url": self.spec["bridge_url"], "ca": self.spec["bridge_ca"]["path"],
                   "token": token, "timeout": max(0.01, self.deadline - time.monotonic())}
        data = document(self.run([self.spec["executables"]["python"], "-B", "-c", BRIDGE_PROBE], input_bytes=encoded(request)))
        require(data.get("ok") is True and data.get("pinned_environment") is True, "bridge_not_ready")
        observed = data.get("configuration_digests", {})
        require(all(observed.get(name) == spec["configuration_digest"] for name, spec in self.validation["lanes"].items()),
                "running_bridge_configuration_mismatch")

    def database_check(self):
        a = self.manifest["activation"]
        request = {"dsn": self.bridge.get("DIRECTOR_HOST_PROVIDER_POSTGRES_DSN", ""),
                   "validation_dsn": self.bridge.get("DIRECTOR_PROVIDER_VALIDATION_POSTGRES_DSN", ""),
                   "schema": a["postgres_schema"], "generation": self.spec["generation_manifest"]["sha256"]}
        data = document(self.run([self.spec["executables"]["python"], "-B", "-c", DATABASE_PROBE], input_bytes=encoded(request)))
        require(data.get("database_identity") == a["database_identity"] and
                data.get("database_instance_id") == a["database_instance_id"], "database_binding_mismatch")
        require(data.get("occupied_slots") == [], "validation_slot_already_occupied")

    def live_config_check(self):
        # Existing read-only validator deliberately tests the paid target configuration
        # without constructing its activation or opening providers/control-plane stores.
        env = child_environment()
        env.update(self.runtime)
        self.run([self.spec["executables"]["python"], "-B", str(self.source / "scripts/live-dispatch-preflight.py")], env=env)

    def verify_inputs(self):
        self.owner_check()
        require(bounded_read(self.path) == self.raw, "descriptor_changed")
        unlinked(self.output)
        for name in ("runtime_env", "bridge_env", "generation_manifest", "validation_manifest", "bridge_ca"):
            pinned_file(self.spec[name])
        for pin in self.spec["compose_files"]:
            pinned_file(pin)
        if "rehearsal" in self.spec:
            for key in ("descriptor", "acceptance_artifact"):
                pinned_file(self.spec["rehearsal"][key])

    def owner_check(self):
        # W2 owns this authority boundary. Fail before probes/writes until its
        # published OS-anchored, whole-descriptor consumer can be reused here.
        raise ReleaseError("trusted_descriptor_consumer_unavailable")

    def doctor(self):
        checks = []
        for name in ("owner", "source", "image", "selectors", "compose", "snapshot", "reviewers", "bridge", "database", "live_config"):
            try:
                getattr(self, name + "_check")()
                checks.append({"check": name, "status": "PASS"})
            except Exception as error:
                checks.append({"check": name, "status": "FAIL", "reason": str(error) if isinstance(error, ReleaseError) else type(error).__name__})
                if name == "owner":
                    break
        if checks[0]["status"] == "PASS":
            try:
                self.verify_inputs()
            except Exception:
                checks.append({"check": "input_readback", "status": "FAIL", "reason": "input_changed_during_doctor"})
        return {"schema_version": "director-release-doctor/v1", "descriptor_sha256": self.descriptor_digest,
                "checks": checks, "preparation_ready": all(item["status"] == "PASS" for item in checks),
                "release_ready": False, "provider_calls": 0, "dependencies": DEPENDENCIES}

    def prepare(self, *, resume=False):
        self.owner_check()  # Refuse before even creating the local lock file.
        journal = self.output / (self.spec["release_id"] + ".jsonl")
        target = self.output / (self.spec["release_id"] + ".activation.compose.json")
        with locked(journal.with_suffix(".lock")):
            records = read_journal(journal, self.descriptor_digest)
            if records:
                require(resume, "use_resume_for_existing_journal")
                self.verify_inputs()
                # Only a proven complete local write can be recovered. A missing,
                # partial or different artifact stays held; no effect is replayed.
                reason = "completed_output_changed" if len(records) == 2 else "reconciliation_required_no_replay"
                require(target.is_file() and sha(bounded_read(target)) == records[0]["output_sha256"], reason)
                if len(records) == 1:
                    self.complete_render(journal, records[0]["output_sha256"])
                return {"status": "PREPARED", "resumed": True, "recovered_receipt": len(records) == 1,
                        "release_ready": False, "provider_calls": 0}
            require(not target.exists(), "unrecorded_output_requires_reconciliation")
            report = self.doctor()
            require(report["preparation_ready"] and self.rendered is not None, "doctor_refused_preparation")
            self.verify_inputs()
            append_record(journal, {"event": "intent", "descriptor_sha256": self.descriptor_digest,
                                   "step": "render_compose", "sequence": 1, "output_sha256": sha(self.rendered)})
            try:
                with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                    stream.write(self.rendered)
                    stream.flush()
                    os.fsync(stream.fileno())
                require(bounded_read(target) == self.rendered, "compose_output_readback_failed")
                self.verify_inputs()
                self.complete_render(journal, sha(self.rendered))
            except BaseException:
                # A pending intent is sufficient to refuse replay even if this append fails.
                raise ReleaseError("reconciliation_required_no_replay") from None
            return {"status": "PREPARED", "resumed": False, "release_ready": False, "provider_calls": 0}

    def complete_render(self, journal, digest):
        append_record(journal, {"event": "receipt", "descriptor_sha256": self.descriptor_digest,
                               "step": "render_compose", "sequence": 2, "status": "COMPLETE", "output_sha256": digest})

    def rehearsal(self):
        """Return an unexecuted plan; existing control actions retain every gate."""
        require("rehearsal" in self.spec, "sibling_descriptor_required")
        self.verify_inputs()
        plan = self.spec["rehearsal"]
        sibling = Release(plan["descriptor"]["path"])
        require("rehearsal" not in sibling.spec, "recursive_rehearsal_refused")
        require(sibling.spec["candidate"] == self.spec["candidate"] and
                sibling.spec["image_id"] == self.spec["image_id"], "sibling_candidate_mismatch")
        require(sibling.spec["compose_project"] != self.spec["compose_project"] and
                sibling.spec["release_id"] != self.spec["release_id"] and
                not paths_overlap(sibling.output, self.output), "sibling_project_not_isolated")
        for key in ("runtime_env", "bridge_env", "generation_manifest", "validation_manifest"):
            require(Path(sibling.spec[key]["path"]).resolve() != Path(self.spec[key]["path"]).resolve(),
                    "sibling_profile_not_isolated")
        for key in ("database_identity", "database_instance_id"):
            require(sibling.manifest["activation"][key] != self.manifest["activation"][key],
                    "sibling_database_not_isolated")
        for key in ("ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED"):
            require(sibling.runtime.get(key) == "false", "sibling_must_be_unpaid")
        require(sibling.runtime.get("LANGGRAPH_PARALLEL_KILL_SWITCH") == "true", "sibling_must_be_unpaid")
        for key in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SLACK_DIRECTOR_CHANNEL_ID"):
            require(not sibling.runtime.get(key) or sibling.runtime[key] != self.runtime.get(key),
                    "sibling_slack_identity_not_isolated")
        require(sibling.runtime.get("DIRECTOR_HOST_PORT") == str(plan["host_port"]), "sibling_host_port_mismatch")
        backup = Path(plan["backup_path"])
        unlinked(backup)
        require(backup.parent.resolve() == sibling.output.resolve() and not backup.exists(), "sibling_backup_path_invalid")
        self.source_check()
        self.selectors_check()
        self.compose_check()
        report = sibling.doctor()
        require(report["preparation_ready"], "sibling_doctor_refused")
        final_graph, sibling_graph = document(self.rendered), document(sibling.rendered)
        isolated_sibling(final_graph, sibling_graph, plan["host_port"])
        base = [sibling.spec["executables"]["powershell"], "-NoProfile", "-NonInteractive", "-File",
                str(sibling.source / "scripts/windows-langgraph-primary-control.ps1")]
        common = ["-EnvironmentFile", sibling.spec["runtime_env"]["path"], "-ComposeProject",
                  sibling.spec["compose_project"], "-HostPort", str(plan["host_port"]), "-TimeoutSeconds", "120"]
        steps = []
        for action in ("Preflight", "Accept", "Migrate", "Backup", "RestoreDrill", "StageRuntime",
                       "PrepareOfflineHandoff", "ActivateOffline", "Health", "Rollback", "Health"):
            arguments = [*base, "-Action", action, *common]
            if action == "Accept":
                arguments += ["-AcceptanceArtifactPath", plan["acceptance_artifact"]["path"]]
            elif action in ("Backup", "RestoreDrill"):
                arguments += ["-BackupPath", str(backup)]
            steps.append({"action": action, "argv": arguments})
        self.verify_inputs()
        sibling.verify_inputs()
        return {"schema_version": "director-release-rehearsal-plan/v1", "status": "PLANNED", "executed": False,
                "descriptor_sha256": self.descriptor_digest, "sibling_descriptor_sha256": sibling.descriptor_digest,
                "compose_project": sibling.spec["compose_project"], "candidate": self.spec["candidate"],
                "image_id": self.spec["image_id"], "cwd": str(sibling.source), "timeout_seconds": 1499,
                "environment": {"COMPOSE_FILE": os.pathsep.join(pin["path"] for pin in sibling.spec["compose_files"]),
                                "COMPOSE_PATH_SEPARATOR": os.pathsep},
                "steps": steps, "release_ready": False, "provider_calls": 0,
                "dependencies": DEPENDENCIES, "on_uncertain_effect": "STOP_RECONCILE_WITHOUT_REPLAY"}


def paths_overlap(left, right):
    left, right = Path(left).resolve(), Path(right).resolve()
    return left.is_relative_to(right) or right.is_relative_to(left)


def isolated_sibling(final, sibling, host_port):
    """Check resolved Compose resources, not just a different project name."""
    project = sibling["name"]
    for kind in ("networks", "volumes"):
        final_names = {item.get("name") for item in final.get(kind, {}).values()}
        for item in sibling.get(kind, {}).values():
            require(not item.get("external") and not item.get("driver_opts") and not item.get("driver") and
                    isinstance(item.get("name"), str) and
                    item["name"].startswith(project + "_") and item["name"] not in final_names,
                    "sibling_shared_" + kind)
    require(bool(sibling.get("networks")), "sibling_network_missing")
    final_ports = {str(port.get("published")) for service in final["services"].values() for port in service.get("ports", [])}
    final_binds = [mount["source"] for service in final["services"].values()
                   for mount in service.get("volumes", []) if mount.get("type") == "bind"]
    for service in sibling["services"].values():
        require(not any(service.get(key) for key in ("network_mode", "pid", "ipc", "privileged", "devices",
                                                    "volumes_from", "container_name")), "sibling_host_resource_refused")
        for port in service.get("ports", []):
            published = str(port.get("published", ""))
            require(port.get("host_ip") == "127.0.0.1" and published.isdigit() and
                    1024 <= int(published) <= 65535 and published not in final_ports, "sibling_port_not_isolated")
        for mount in service.get("volumes", []):
            if mount.get("type") == "bind" and not mount.get("read_only"):
                unlinked(Path(mount["source"]))
                require(all(not paths_overlap(mount["source"], source) for source in final_binds),
                        "sibling_shared_write_mount")
    require(any(str(port.get("published")) == str(host_port) and port.get("target") == 8080
                for port in sibling["services"]["orchestrator"].get("ports", [])), "sibling_host_port_mismatch")


@contextmanager
def locked(path):
    unlinked(path)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ReleaseError("another_release_writer") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def append_record(path, value):
    value = dict(value, record_sha256=sha(encoded(value)))
    unlinked(path)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "ab") as stream:
        stream.write(encoded(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_journal(path, descriptor_digest):
    if not path.exists():
        return []
    raw = bounded_read(path)
    require(not raw or raw.endswith(b"\n"), "incomplete_journal_no_replay")
    result = [document(line) for line in raw.splitlines()]
    require(len(result) <= 2, "unexpected_journal_sequence")
    for number, entry in enumerate(result, 1):
        fields = {"event", "descriptor_sha256", "step", "sequence", "record_sha256", "output_sha256"}
        if number == 2:
            fields |= {"status"}
        require(isinstance(entry, dict) and set(entry) == fields, "invalid_journal_record")
        require(entry["sequence"] == number and entry["descriptor_sha256"] == descriptor_digest and
                entry["step"] == "render_compose" and entry["event"] == ("intent" if number == 1 else "receipt"),
                "journal_binding_mismatch")
        require(sha(encoded({k: v for k, v in entry.items() if k != "record_sha256"})) == entry["record_sha256"],
                "journal_digest_mismatch")
        require(isinstance(entry["output_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", entry["output_sha256"]),
                "journal_output_digest_invalid")
        if number == 2:
            require(entry["status"] == "COMPLETE" and entry["output_sha256"] == result[0]["output_sha256"],
                    "journal_receipt_mismatch")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("doctor", "prepare", "resume", "rehearsal"))
    parser.add_argument("--descriptor", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        release = Release(args.descriptor)
        if args.action == "doctor":
            report = release.doctor()
            print(json.dumps(report, sort_keys=True))
            return 2  # Missing authority is never a PASS, even with every diagnostic green.
        if args.action == "rehearsal":
            print(json.dumps(release.rehearsal(), sort_keys=True))
            return 0
        print(json.dumps(release.prepare(resume=args.action == "resume"), sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error) if isinstance(error, ReleaseError) else type(error).__name__,
                          "release_ready": False, "provider_calls": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 10:34 UTC | END]]
