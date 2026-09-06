# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]]
"""Closed owner-pinned activation-generation identity; no issuer authority."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import re

from .primary_activation_evidence import ActivationEvidenceError

_OPERATIONAL_SETTINGS = frozenset({
    "allow_paid_runs", "langgraph_primary_enabled", "langgraph_primary_new_intake_enabled",
    "langgraph_primary_live_provider_dispatch_enabled", "langgraph_parallel_kill_switch",
    "langgraph_primary_offline_simulation_enabled",
})
GENERATION_DATA_ROOT = Path("/data")
_MODE_ENVIRONMENTS = {
    "ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_ENABLED", "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED",
    "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED", "LANGGRAPH_PARALLEL_KILL_SWITCH",
    "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED",
}
_SELF_REFERENCES = {
    "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH", "LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256",
    "DIRECTOR_RESOLVED_DEPLOYMENT_SHA256",
}


def resolved_deployment_digest(document, image_id):
    """Bind the complete resolved Compose graph, including mounts and networks.

    The host obtains image_id from Docker's content-addressed image inspection,
    selects that ID as the actual Compose image, and passes only the digest to
    the isolated service. No Docker socket is exposed to the runtime.
    """
    try:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("invalid content identity")
        observed = json.loads(json.dumps(document))
        service = observed["services"]["orchestrator"]
        if service["image"] != image_id:
            raise ValueError("Compose image is not the verified immutable ID")
        command = service["command"]
        valid_apps = {"director_orchestrator.main:app", "director_orchestrator.primary_staging:app"}
        if command not in [["uvicorn", app, "--host", "0.0.0.0", "--port", "8080"] for app in valid_apps]:
            raise ValueError("unexpected runtime command")
        if service.get("entrypoint") not in (None, []):
            raise ValueError("unexpected entrypoint override")
        environment = service["environment"]
        if environment.get("DIRECTOR_RUNTIME_IMAGE_ID") != image_id:
            raise ValueError("runtime image metadata mismatch")
        if environment.get("DIRECTOR_RUNTIME_IMAGE") != image_id or environment.get("DIRECTOR_ASGI_APP") != command[1]:
            raise ValueError("runtime image or command selection mismatch")
        environment["DIRECTOR_ASGI_APP"] = "<verified-asgi-mode>"
        staged = command[1] == "director_orchestrator.primary_staging:app"
        for name in _MODE_ENVIRONMENTS:
            if name in environment:
                value = str(environment[name]).lower()
                if value not in {"true", "false"}:
                    raise ValueError("invalid mode switch")
                if staged and value != ("true" if name == "LANGGRAPH_PARALLEL_KILL_SWITCH" else "false"):
                    raise ValueError("staged command has enabled execution switch")
                environment[name] = "<verified-operational-switch>"
        if staged and not {"ALLOW_PAID_RUNS", "LANGGRAPH_PRIMARY_ENABLED", "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED", "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED", "LANGGRAPH_PARALLEL_KILL_SWITCH"} <= set(environment):
            raise ValueError("staging switches are incomplete")
        for name in _SELF_REFERENCES:
            if name in environment:
                environment[name] = "<generation-selection>"
        command[1] = "<verified-asgi-mode>"
        return digest(observed)
    except (TypeError, ValueError, KeyError):
        raise ActivationEvidenceError("resolved deployment is invalid or not pinned") from None


def _normalized(value):
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_normalized(item) for item in value)
    if isinstance(value, (tuple, list)):
        return [_normalized(item) for item in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(_normalized(value), sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def deployment_digest(settings):
    return digest({key: value for key, value in asdict(settings).items()
        if key not in _OPERATIONAL_SETTINGS})


def require_validation_generation(identity):
    """Bind a separately authorized fixed validation run to this staged release."""
    from .provider_acceptance_v2 import _pairs
    try:
        source = Path(os.environ["DIRECTOR_PROVIDER_VALIDATION_MANIFEST"])
        raw = source.read_bytes()
        if len(raw) > 131072:
            raise ValueError("oversized validation manifest")
        document = json.loads(raw, object_pairs_hook=_pairs)
        if (document.get("generation") != os.environ["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"]
            or document.get("candidate") != identity.candidate_commit
            or document.get("tree") != identity.candidate_tree):
            raise ValueError("validation candidate or generation differs")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ActivationEvidenceError("provider validation manifest differs from staged generation") from None


def require_generation(settings, identity, *, reserve=False):
    """Verify every binding before any journal creation or runtime mutation.

    A missing manifest is never treated as an implicit legacy generation. The
    owner selects the manifest's exact bytes via a separately protected digest.
    """
    try:
        if settings.langgraph_primary_acceptance_mode != "provider-v2":
            raise ValueError("generation selection requires provider-v2 acceptance")
        source = Path(os.environ["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_PATH"])
        pin = os.environ["LANGGRAPH_PRIMARY_GENERATION_MANIFEST_SHA256"]
        raw = source.read_bytes()
        if len(raw) > 131072 or len(pin) != 64 or hashlib.sha256(raw).hexdigest() != pin:
            raise ValueError("manifest pin mismatch")
        from .provider_acceptance_v2 import _pairs
        manifest = json.loads(raw, object_pairs_hook=_pairs)
        expected_fields = {"version", "activation", "journal_path", "registry_path",
            "registry_sha256", "provider_bindings_sha256", "deployment_configuration_sha256",
            "image_id", "resolved_compose_sha256"}
        if set(manifest) != expected_fields or manifest["version"] != "director-primary-generation/v1":
            raise ValueError("closed generation contract mismatch")
        registry = Path(settings.langgraph_primary_acceptance_registry_path)
        if (manifest["activation"] != asdict(identity)
            or manifest["journal_path"] != settings.langgraph_primary_acceptance_journal_path
            or manifest["registry_path"] != str(registry)
            or manifest["registry_sha256"] != settings.langgraph_primary_acceptance_registry_sha256
            or hashlib.sha256(registry.read_bytes()).hexdigest() != manifest["registry_sha256"]
            or manifest["provider_bindings_sha256"] != hashlib.sha256(
                os.environ.get("LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON", "").encode()).hexdigest()
            or manifest["deployment_configuration_sha256"] != deployment_digest(settings)):
            raise ValueError("mixed generation inputs")
        if (not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest["image_id"])
            or manifest["image_id"] != os.environ.get("DIRECTOR_RUNTIME_IMAGE_ID")
            or not re.fullmatch(r"[0-9a-f]{64}", manifest["resolved_compose_sha256"])
            or manifest["resolved_compose_sha256"] != os.environ.get("DIRECTOR_RESOLVED_DEPLOYMENT_SHA256")):
            raise ValueError("deployed image or Compose configuration differs")
        journal = Path(manifest["journal_path"])
        # Keep generation storage in the durable data mount and away from every
        # manifest/registry. Selection never overwrites an earlier generation.
        if not journal.is_absolute() or journal.parent != GENERATION_DATA_ROOT or journal.suffix != ".sqlite3":
            raise ValueError("generation journal must be a direct /data SQLite file")
        if reserve:
            # Preserve exact reviewed bytes under a content-addressed name. The
            # external protected environment pin is authority, not file ownership.
            destination = journal.parent / ("generation-" + pin + ".json")
            descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            descriptor = os.open(journal, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            # Selection alone creates this narrow generation marker. Readback
            # detects an old journal copied or substituted under the new path.
            connection = sqlite3.connect(journal)
            try:
                with connection:
                    connection.execute("CREATE TABLE activation_generation (singleton INTEGER PRIMARY KEY CHECK(singleton=1), manifest_sha256 TEXT NOT NULL)")
                    connection.execute("INSERT INTO activation_generation VALUES (1,?)", (pin,))
            finally:
                connection.close()
        elif not journal.is_file():
            raise ValueError("selected generation journal is absent")
        else:
            connection = sqlite3.connect(journal.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                row = connection.execute("SELECT manifest_sha256 FROM activation_generation WHERE singleton=1").fetchone()
                if row != (pin,):
                    raise ValueError("journal belongs to another generation")
            finally:
                connection.close()
        return manifest
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        raise ActivationEvidenceError("generation manifest or bindings unavailable, mixed, or already reserved") from None


def main():
    import argparse
    from .config import Settings
    from .langgraph_postgres import database_binding
    from .primary_activation_evidence import ActivationIdentity
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("verify", "reserve", "compose-digest", "validate-providers"))
    parser.add_argument("--image-id")
    args = parser.parse_args()
    if args.command == "compose-digest":
        import sys
        print(resolved_deployment_digest(json.load(sys.stdin), args.image_id))
        return
    settings = Settings()
    identity, instance = database_binding(settings.langgraph_primary_postgres_dsn,
        schema=settings.langgraph_primary_postgres_schema,
        allow_insecure_local_transport=settings.langgraph_primary_allow_insecure_local_postgres)
    expected = ActivationIdentity(settings.langgraph_primary_pinned_commit_sha,
        settings.langgraph_primary_pinned_tree_sha, settings.app_env,
        os.environ.get("DIRECTOR_COMPOSE_PROJECT_NAME", ""), identity, instance,
        settings.langgraph_primary_postgres_schema, settings.langgraph_primary_checkpoint_namespace)
    require_generation(settings, expected, reserve=args.command == "reserve")
    if args.command == "validate-providers":
        from .primary_activation_evidence import ActivationEvidenceStore
        ActivationEvidenceStore("/data/langgraph-primary-activation-evidence.json", read_only=True).require_staged(expected)
        require_validation_generation(expected)
        # The adopted bounded harness owns authentication, durable admission,
        # budgets, captures and ambiguity handling. Never substitute commands.
        from .provider_validation_cli import main as validate
        import sys
        previous = sys.argv
        try:
            sys.argv = ["provider_validation_cli", "validate"]
            validate()
        finally:
            sys.argv = previous
        return
    print("generation_verified=true")


if __name__ == "__main__":
    main()
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:07 UTC | END]]
