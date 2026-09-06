# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | BEGIN]]
"""Closed owner-pinned activation-generation identity; no issuer authority."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .primary_activation_evidence import ActivationEvidenceError

_OPERATIONAL_SETTINGS = frozenset({
    "allow_paid_runs", "langgraph_primary_enabled", "langgraph_primary_new_intake_enabled",
    "langgraph_primary_live_provider_dispatch_enabled", "langgraph_parallel_kill_switch",
    "langgraph_primary_offline_simulation_enabled",
})
GENERATION_DATA_ROOT = Path("/data")


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
            "registry_sha256", "provider_bindings_sha256", "deployment_configuration_sha256"}
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
    parser.add_argument("command", choices=("verify", "reserve"))
    args = parser.parse_args()
    settings = Settings()
    identity, instance = database_binding(settings.langgraph_primary_postgres_dsn,
        schema=settings.langgraph_primary_postgres_schema,
        allow_insecure_local_transport=settings.langgraph_primary_allow_insecure_local_postgres)
    expected = ActivationIdentity(settings.langgraph_primary_pinned_commit_sha,
        settings.langgraph_primary_pinned_tree_sha, settings.app_env,
        os.environ.get("DIRECTOR_COMPOSE_PROJECT_NAME", ""), identity, instance,
        settings.langgraph_primary_postgres_schema, settings.langgraph_primary_checkpoint_namespace)
    require_generation(settings, expected, reserve=args.command == "reserve")
    print("generation_verified=true")


if __name__ == "__main__":
    main()
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 19:47 UTC | END]]
