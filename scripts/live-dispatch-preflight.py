# [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-13 | 06:06 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-13 | 04:27 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 03:50 UTC | BEGIN]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 02:45 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 03:08 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 06:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:36 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:08 UTC | BEGIN]]
"""Report every unmet live-dispatch prerequisite at once, and change nothing.

The prerequisites for paid, live provider dispatch are enforced by several
validators that each raise on the first problem they find, so an operator
learns them one process death at a time. This script asks those same
validators and readiness functions what they would say, and prints all of
their verdicts together in the order startup reaches them.

Read-only by construction. It sets no environment variable, writes no file,
opens no socket, touches no database, and calls no provider. It removes,
relaxes and defaults nothing: every verdict here is the deterministic
validator's own EXCEPT the paid/live coupling line, which is the preflight's
own paraphrase of the startup constructor's refusal, because a read-only
preflight must not construct the activation (it opens a Postgres control plane).
Enabling live dispatch remains an owner
decision taken elsewhere.

Usage:

    PYTHONPATH=src python -B scripts/live-dispatch-preflight.py

Exit status is 0 when nothing is outstanding and 1 when anything is.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import sys
from dataclasses import replace

try:
    from director_orchestrator.config import Settings
except Exception:  # pragma: no cover - exercised by subprocess, not import
    # config.py evaluates its dataclass field defaults at class-definition
    # time, so a value that fails to convert raises during THIS import, before
    # main() can guard it, and the unhandled traceback prints that value on
    # stderr. A review of #140 set MAX_AGENT_HOPS to a secret and read it back
    # out of the traceback. Wrapping the import is what moves that failure
    # inside the guard; main() reports it without the message.
    Settings = None

try:
    # The exact DSN parser and storage-identity digest startup applies to the
    # entitlement DSN file (antigravity_team_routing.configured_provider_budget_routing).
    # Pure functions: conninfo_to_dict parses text, nothing here opens a
    # connection, and the ReadOnlyTests pin that no connect/pool/cursor name
    # is ever referenced in this file.
    from psycopg.conninfo import conninfo_to_dict
    from director_orchestrator.entitlement_control_plane import (
        _storage_identity as entitlement_storage_identity,
    )
    from director_orchestrator.langgraph_postgres import _parse_connection_target
except Exception:  # pragma: no cover - exercised only where psycopg is absent
    conninfo_to_dict = None
    entitlement_storage_identity = None
    _parse_connection_target = None


# The switches that must be on together for live provider dispatch. The
# preflight evaluates the rest of the configuration as though these were
# already set, which is what makes one pass report every prerequisite.
TARGET_SWITCHES: tuple[tuple[str, str], ...] = (
    ("ALLOW_PAID_RUNS", "allow_paid_runs"),
    ("LANGGRAPH_PRIMARY_ENABLED", "langgraph_primary_enabled"),
    (
        "LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED",
        "langgraph_primary_new_intake_enabled",
    ),
    (
        "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
        "langgraph_primary_live_provider_dispatch_enabled",
    ),
)

# Variables named by a problem whose own text names none. The validators own
# every rule; this table only restores the operator-facing variable names.
IMPLIED_VARIABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "mutually exclusive",
        (
            "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED",
            "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED",
        ),
    ),
    (
        "provider activation bindings",
        ("LANGGRAPH_PRIMARY_PROVIDER_ACTIVATION_BINDINGS_JSON",),
    ),
    ("delegated token file", ("MICROSOFT_365_COPILOT_DELEGATED_TOKEN_FILE",)),
    ("tenant id", ("MICROSOFT_365_COPILOT_TENANT_ID",)),
    ("expected subject", ("MICROSOFT_365_COPILOT_EXPECTED_SUBJECT",)),
    ("expected object id", ("MICROSOFT_365_COPILOT_EXPECTED_OBJECT_ID",)),
    (
        "completion authority evidence secrets",
        (
            "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET",
            "LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET",
            "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET",
        ),
    ),
    (
        "completion evidence actor allowlist",
        ("LANGGRAPH_PRIMARY_COMPLETION_EVIDENCE_ACTOR_ALLOWLIST_JSON",),
    ),
    (
        "host provider bridge endpoint and secret",
        (
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_ENDPOINT",
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET",
        ),
    ),
    (
        "ingress secrets must be pairwise distinct",
        (
            "ORCHESTRATOR_SHARED_SECRET",
            "SLACK_GATEWAY_SECRET",
            "LANGGRAPH_PRIMARY_ADAPTER_CALLBACK_SECRET",
            "LANGGRAPH_PRIMARY_INDEPENDENT_REVIEW_EVIDENCE_SECRET",
            "LANGGRAPH_PRIMARY_CI_EVIDENCE_SECRET",
            "LANGGRAPH_PRIMARY_SECURITY_EVIDENCE_SECRET",
            "LANGGRAPH_PRIMARY_EFFECT_RECONCILIATION_SECRET",
            "LANGGRAPH_PRIMARY_HOST_BRIDGE_SECRET",
        ),
    ),
    (
        "caps must start at 2",
        (
            "LANGGRAPH_PARALLEL_INITIAL_MAX_CONCURRENCY",
            "LANGGRAPH_PARALLEL_AUTO_SCALE_MAX_CONCURRENCY",
            "LANGGRAPH_PARALLEL_CLEAN_BATCHES_TO_SCALE",
            "LANGGRAPH_PARALLEL_RECOVERY_CLEAN_BATCHES",
        ),
    ),
    (
        "must all be distinct files",
        (
            "SQLITE_PATH",
            "LANGGRAPH_CHECKPOINT_PATH",
            "LANGGRAPH_RUNTIME_CHECKPOINT_PATH",
            "LANGGRAPH_RUNTIME_LEDGER_PATH",
        ),
    ),
    (
        "resume claim database",
        ("SQLITE_PATH", "LANGGRAPH_CHECKPOINT_PATH"),
    ),
)

# Booleans that gate live dispatch before any provider call. Reported by
# `/health` as `execution_profile`; an empty allowlist fails closed.
EXECUTION_PROFILE: tuple[tuple[str, str], ...] = (
    ("projects_configured", "PROJECT_ALLOWLIST"),
    ("repositories_configured", "REPOSITORY_ALLOWLIST"),
    ("starting_refs_configured", "ALLOWED_STARTING_REFS"),
    ("claude_callback_configured", "CLAUDE_CALLBACK_SECRET"),
)

VARIABLE_NAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")


def _declared_environment_variables() -> frozenset[str]:
    """Every name config.py actually reads from the environment.

    The shape of an environment variable is not evidence that a token IS one.
    An independent review of #140 set a secret to an uppercase,
    underscore-delimited value; `redact` removed it from the prose and this
    module then reprinted it under "variables", because the regex matched its
    shape. Names are now intersected with what the configuration declares, so
    a token that merely looks like a variable is dropped rather than echoed.

    Read from the source rather than restated here: a list maintained by hand
    drifts, and drifting open is exactly the failure being closed.

    Matching only `os.getenv("X")` was too narrow and silently dropped real
    names: config.py also reads through helpers such as
    `_csv("PROJECT_ALLOWLIST", "")`, and that variable stopped being reported
    at all. Every uppercase string literal in the module is taken instead. That
    is a superset of the environment names, which is the safe direction to be
    wrong in -- it can name a constant that is not a variable, but it cannot
    admit a value that never appears in the source, and a secret does not.
    """

    source = pathlib.Path(__file__).resolve().parents[1] / "src" / "director_orchestrator" / "config.py"
    text = source.read_text(encoding="utf-8")
    return frozenset(re.findall(r'"([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)"', text))


DECLARED_VARIABLES = _declared_environment_variables()

# Startup requirements mirrored read-only. Sources of truth:
# antigravity_team_routing.configured_provider_budget_routing (the control
# plane must be enabled; the DSN file must exist, be at most 65536 bytes and,
# stripped, be non-empty with no interior line break before psycopg parses it),
# entitlement_control_plane._read_secret_file / _validate_secret_material /
# _SCHEMA_PATTERN (each secret file must exist and, stripped, hold at least 32
# UTF-8 bytes with at least 16 distinct byte values; the schema must match
# [a-z0-9_]+_v<N>), and oracle_merge.GitHubOracleGateway.from_settings (numeric
# App and installation ids, strictly base64 private key). File contents are
# read only to apply exactly those rules and never leave the function; the
# full DSN parse (psycopg conninfo) happens at startup, not here.
ENTITLEMENT_SCHEMA = re.compile(r"^[a-z0-9_]+_v[0-9]+$")
ENTITLEMENT_DSN_FILE_LIMIT = 65_536
ENTITLEMENT_SECRET_MINIMUM_BYTES = 32
ENTITLEMENT_SECRET_MINIMUM_DISTINCT = 16
ENTITLEMENT_SECRET_FILES: tuple[tuple[str, str], ...] = (
    ("ENTITLEMENT_TRUSTED_VERIFIER_SECRET_FILE", "entitlement_trusted_verifier_secret_file"),
    ("ENTITLEMENT_ROUTE_VERIFIER_SECRET_FILE", "entitlement_route_verifier_secret_file"),
    ("ENTITLEMENT_OWNER_AUTHORITY_SECRET_FILE", "entitlement_owner_authority_secret_file"),
)
# What base64.b64decode(..., validate=True) accepts: alphabet only, correct padding.
STRICT_BASE64 = re.compile(r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$")


def _regular_file(path: str, *, expand: bool) -> pathlib.Path | None:
    """The configured path when it names an existing regular file, else None.

    `expand` mirrors the caller startup uses: _read_secret_file expands `~`,
    the DSN-file check in configured_provider_budget_routing does not. Any
    failure (permission, a race with removal, an unresolvable `~user`) reads
    as unavailable rather than escaping: a traceback would print the path.
    """

    if not path:
        return None
    try:
        resolved = pathlib.Path(path)
        if expand:
            resolved = resolved.expanduser()
        return resolved if resolved.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _file_size(path: str, *, expand: bool) -> int | None:
    """Byte size of an existing regular file, else None. Metadata only."""

    resolved = _regular_file(path, expand=expand)
    if resolved is None:
        return None
    try:
        return resolved.stat().st_size
    except OSError:
        return None


def _dsn_file_problems(settings: Settings) -> list[str]:
    """Why startup would refuse the entitlement DSN file, as problem texts.

    Mirrors configured_provider_budget_routing step by step: an existing file
    at the literal path, at most 65536 bytes; its stripped contents parsed by
    the same _parse_connection_target startup uses (explicit dbname, port,
    user and a verifying sslmode, no delegated identity); and the same
    storage-identity digest as LANGGRAPH_PRIMARY_POSTGRES_DSN under the
    entitlement schema. The contents never leave this function and no
    connection is opened; the one check left to startup is the database-side
    accepted identity, which needs the live handle.
    """

    size = _file_size(settings.entitlement_postgres_dsn_file, expand=False)
    if size is None or size > ENTITLEMENT_DSN_FILE_LIMIT:
        return [f"ENTITLEMENT_POSTGRES_DSN_FILE must name an existing file of at most {ENTITLEMENT_DSN_FILE_LIMIT} bytes"]
    try:
        text = _regular_file(settings.entitlement_postgres_dsn_file, expand=False).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError, AttributeError):
        return ["ENTITLEMENT_POSTGRES_DSN_FILE must be readable as UTF-8 connection info"]
    if not text:
        return ["ENTITLEMENT_POSTGRES_DSN_FILE must not be empty"]
    if "\n" in text or "\r" in text:
        return ["ENTITLEMENT_POSTGRES_DSN_FILE must be one line of connection info"]
    if conninfo_to_dict is None or _parse_connection_target is None:
        return ["ENTITLEMENT_POSTGRES_DSN_FILE could not be validated: psycopg is not importable here"]
    try:
        entitlement = _parse_connection_target(
            text, conninfo_to_dict=conninfo_to_dict,
            allow_insecure_local_transport=settings.entitlement_allow_insecure_local_postgres,
        )
    except Exception:
        return ["ENTITLEMENT_POSTGRES_DSN_FILE must hold connection info startup accepts: explicit dbname, port, user and a verifying sslmode, no delegated identity"]
    try:
        primary = _parse_connection_target(
            settings.langgraph_primary_postgres_dsn, conninfo_to_dict=conninfo_to_dict,
            allow_insecure_local_transport=settings.langgraph_primary_allow_insecure_local_postgres,
        )
    except Exception:
        return ["LANGGRAPH_PRIMARY_POSTGRES_DSN must parse as connection info startup accepts before the entitlement identity can be compared"]
    schema = settings.entitlement_postgres_schema or ""
    if entitlement_storage_identity(entitlement, schema=schema) != entitlement_storage_identity(primary, schema=schema):
        return ["ENTITLEMENT_POSTGRES_DSN_FILE and LANGGRAPH_PRIMARY_POSTGRES_DSN must share database and role"]
    return []


def _secret_file_problem(path: str) -> str | None:
    """Why startup would refuse an entitlement secret file, or None.

    Mirrors _read_secret_file and _validate_secret_material exactly: an
    existing file (after `~` expansion) whose stripped UTF-8 text is at least
    32 bytes with at least 16 distinct byte values. The material is read only
    to apply that rule and is never returned or logged.
    """

    resolved = _regular_file(path, expand=True)
    if resolved is None:
        return "must name an existing file"
    try:
        material = resolved.read_text(encoding="utf-8").strip().encode("utf-8")
    except (OSError, UnicodeDecodeError):
        return "must be readable as UTF-8 text"
    if not material:
        return "must not be empty"
    if len(material) < ENTITLEMENT_SECRET_MINIMUM_BYTES or len(set(material)) < ENTITLEMENT_SECRET_MINIMUM_DISTINCT:
        return f"must hold at least {ENTITLEMENT_SECRET_MINIMUM_BYTES} bytes of nontrivial entropy"
    return None


def _github_app_credential_malformed(settings: Settings) -> bool:
    """True when startup would construct the GitHub App gateway and refuse it.

    The effect registry builds the GitHub gateway only when
    oracle_github_credential_configured() is true (all three App values and
    no static token, or the token alone); an incomplete App configuration is
    simply skipped there, so it is not a problem here either.
    """

    if not settings.oracle_github_credential_configured():
        return False
    app_id = settings.oracle_automerge_github_app_id
    installation_id = settings.oracle_automerge_github_installation_id
    private_key = settings.oracle_automerge_github_app_private_key_b64
    if not any((app_id, installation_id, private_key)):
        return False
    return not (
        app_id.isdigit()
        and installation_id.isdigit()
        and STRICT_BASE64.fullmatch(private_key) is not None
    )

# Field names whose value must never reach the output under any drift: no
# secret, token, endpoint, path, allowlist entry, or owner identifier.
SENSITIVE_FIELD = re.compile(
    r"secret|token|key|password|dsn|url|endpoint|path|allowlist"
    r"|owner|tenant|subject|object_id|actor"
)


def redact(text: str, settings: Settings) -> str:
    """Replace any configured sensitive value that reached `text`.

    Nothing should ever match: the validators raise static prose. This is the
    standing guarantee that a future message interpolating a value cannot turn
    the preflight into a secret printer.
    """

    for spec in dataclasses.fields(settings):
        if not SENSITIVE_FIELD.search(spec.name):
            continue
        value = getattr(settings, spec.name)
        items = value if isinstance(value, tuple) else (value,)
        for item in items:
            if not isinstance(item, str) or not item or item not in text:
                continue
            if len(item) < 3:
                # A one- or two-character value cannot be redacted by
                # substitution: replacing every "Z" would shred the message and
                # still leak the length. A review of #140 read back secrets of
                # "Z" and "Z9" that the length floor skipped entirely. Withhold
                # the whole line instead -- it is pathological configuration,
                # and a lost diagnostic beats a printed credential.
                return (
                    "message withheld: a configured value of one or two "
                    "characters appears in it and cannot be redacted in place"
                )
            text = text.replace(item, "<redacted>")
    return text


def variables(problem: str) -> tuple[str, ...]:
    """The exact environment variable names a problem is about."""

    named = tuple(
        name for name in dict.fromkeys(VARIABLE_NAME.findall(problem))
        if name in DECLARED_VARIABLES
    )
    if named:
        return named
    for fragment, names in IMPLIED_VARIABLES:
        if fragment in problem:
            return names
    return ()


def validator_problems(validate) -> list[str]:
    """Run one validator and return its problems, one per entry."""

    try:
        validate()
    except ValueError as exc:
        _, joined, tail = str(exc).partition(" configuration: ")
        body = tail if joined else str(exc)
        return [item.strip() for item in body.split("; ") if item.strip()]
    return []


# [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | BEGIN]]
# A provider whose lane is enabled and whose credential is configured, but which
# the roster overrides leave out, can never receive an assignment or a usage
# check: the key sits unused and nothing reports why. Owner instruction
# 2026-09-16: no configured key may go unused. Each entry is
# (lane, enable attribute, credential attribute, enable variable, credential
# variable); the roster variables are PRIMARY_TEAM and USAGE_CHECK_PROVIDERS.
ROSTERED_PROVIDERS: tuple[tuple[str, str, str, str, str], ...] = (
    ("grok", "grok_enabled", "grok_api_key", "GROK_ENABLED", "XAI_API_KEY"),
    ("perplexity", "perplexity_enabled", "perplexity_api_key", "PERPLEXITY_ENABLED", "PERPLEXITY_API_KEY"),
)


def roster_problems(settings: Settings) -> list[str]:
    """Enabled, credentialed lanes that the roster overrides exclude."""

    problems: list[str] = []
    for lane, enabled, key, enable_name, key_name in ROSTERED_PROVIDERS:
        if not getattr(settings, enabled) or not getattr(settings, key):
            continue
        missing = [
            name
            for name, roster in (
                ("PRIMARY_TEAM", settings.primary_team),
                ("USAGE_CHECK_PROVIDERS", settings.usage_check_providers),
            )
            if lane not in roster
        ]
        if missing:
            problems.append(
                f"{enable_name} is enabled and {key_name} is configured but "
                f"'{lane}' is absent from {' and '.join(missing)}: the credential "
                "can never be exercised until the roster names the lane"
            )
    return problems
# [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | END]]


def unmet(settings: Settings) -> list[tuple[str, str]]:
    """Every unmet prerequisite as (stage, problem), in startup order."""

    problems: list[tuple[str, str]] = []
    for name, attribute in TARGET_SWITCHES:
        if not getattr(settings, attribute):
            problems.append(("live dispatch switches", f"{name} must be enabled"))
    target = replace(
        settings, **{attribute: True for _, attribute in TARGET_SWITCHES}
    )
    runtime_target = replace(
        target,
        langgraph_parallel_external_dispatch_enabled=True,
        langgraph_runtime_enabled=True,
    )
    # main.py runs these three before it constructs anything.
    for stage, validate in (
        ("workload policy", target.validate_workload_policy_configuration),
        ("langgraph shadow lane", target.validate_langgraph_configuration),
        ("langgraph primary", target.validate_langgraph_primary_configuration),
    ):
        problems.extend((stage, item) for item in validator_problems(validate))
    # main.py then builds DefaultPrimaryActivation, which refuses paid runs
    # without live dispatch requested. Evaluated on the configured settings,
    # not the target: this one is about the deployment as it stands now.
    if (
        settings.langgraph_primary_enabled
        and settings.allow_paid_runs
        and not settings.langgraph_primary_live_provider_dispatch_enabled
    ):
        problems.append(
            (
                "paid/live coupling",
                "ALLOW_PAID_RUNS is enabled but "
                "LANGGRAPH_PRIMARY_LIVE_PROVIDER_DISPATCH_ENABLED is disabled: "
                "startup refuses this pair, so the deployment is down until "
                "both move together",
            )
        )
    for stage, validate in (
        ("parallel dispatch", runtime_target.validate_parallel_dispatch_configuration),
        ("langgraph runtime", runtime_target.validate_langgraph_runtime_configuration),
    ):
        problems.extend((stage, item) for item in validator_problems(validate))
    # [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | BEGIN]]
    problems.extend(("provider roster", item) for item in roster_problems(settings))
    # [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | END]]
    profile = settings.execution_profile_readiness()
    for key, name in EXECUTION_PROFILE:
        if not profile[key]:
            problems.append(
                (
                    "execution profile",
                    f"{name} must be configured: an empty execution profile "
                    "fails closed before any provider call",
                )
            )

    # The dependent checks run whether or not the plane is enabled: this script
    # exists to report every prerequisite in one pass, not one enable later.
    stage = "entitlement control plane"
    if not settings.entitlement_control_plane_enabled:
        problems.append((stage, "ENTITLEMENT_CONTROL_PLANE_ENABLED must be enabled: live primary requires enrolled daily provider budget authority"))
    problems.extend((stage, text) for text in _dsn_file_problems(settings))
    for name, attribute in ENTITLEMENT_SECRET_FILES:
        secret_problem = _secret_file_problem(getattr(settings, attribute))
        if secret_problem is not None:
            problems.append((stage, f"{name} {secret_problem}"))
    if not ENTITLEMENT_SCHEMA.fullmatch(settings.entitlement_postgres_schema or ""):
        problems.append((stage, "ENTITLEMENT_POSTGRES_SCHEMA must match [a-z0-9_]+_v<N>"))

    slack_configured = bool(settings.slack_bot_token)
    github_configured = bool(settings.oracle_github_credential_configured())
    if not (slack_configured or github_configured):
        problems.append(("effect gateways", "SLACK_BOT_TOKEN or ORACLE_AUTOMERGE_GITHUB_TOKEN (or the ORACLE_AUTOMERGE_GITHUB_APP_ID app credential set) must configure at least one authenticated effect gateway"))
    if _github_app_credential_malformed(settings):
        problems.append(("effect gateways", "ORACLE_AUTOMERGE_GITHUB_APP_ID and ORACLE_AUTOMERGE_GITHUB_INSTALLATION_ID must be numeric and ORACLE_AUTOMERGE_GITHUB_APP_PRIVATE_KEY_B64 strict base64: startup's GitHub App gateway refuses otherwise"))

    return problems


def report(settings: Settings, problems: list[tuple[str, str]]) -> list[str]:
    """The whole preflight as output lines. Names and booleans only."""

    lines = [
        "Live dispatch preflight: reports only, enables nothing.",
        "",
        "Configured switches:",
    ]
    for name, attribute in TARGET_SWITCHES + (
        (
            "LANGGRAPH_PRIMARY_OFFLINE_SIMULATION_ENABLED",
            "langgraph_primary_offline_simulation_enabled",
        ),
        (
            "LANGGRAPH_PARALLEL_EXTERNAL_DISPATCH_ENABLED",
            "langgraph_parallel_external_dispatch_enabled",
        ),
    ):
        lines.append(f"  {name}: {str(getattr(settings, attribute)).lower()}")
    lines.append("")

    # *_FILE_PRESENT means an existing regular file at the configured path
    # (metadata only); size and material rules are reported through unmet().
    lines.append("Subsystem readiness:")
    lines.append(f"  ENTITLEMENT_CONTROL_PLANE_ENABLED: {str(bool(settings.entitlement_control_plane_enabled)).lower()}")
    lines.append(f"  ENTITLEMENT_POSTGRES_DSN_FILE_PRESENT: {str(_file_size(settings.entitlement_postgres_dsn_file, expand=False) is not None).lower()}")
    for name, attribute in ENTITLEMENT_SECRET_FILES:
        lines.append(f"  {name}_PRESENT: {str(_file_size(getattr(settings, attribute), expand=True) is not None).lower()}")
    lines.append(f"  ENTITLEMENT_SCHEMA_CONFIGURED: {str(ENTITLEMENT_SCHEMA.fullmatch(settings.entitlement_postgres_schema or '') is not None).lower()}")

    slack_configured = bool(settings.slack_bot_token)
    github_configured = bool(settings.oracle_github_credential_configured())
    lines.append(f"  EFFECT_GATEWAY_SLACK_CONFIGURED: {str(slack_configured).lower()}")
    lines.append(f"  EFFECT_GATEWAY_GITHUB_CONFIGURED: {str(github_configured).lower()}")
    lines.append(f"  EFFECT_GATEWAY_GITHUB_APP_SYNTAX_OK: {str(not _github_app_credential_malformed(settings)).lower()}")
    lines.append(f"  EFFECT_GATEWAY_ANY_CONFIGURED: {str(slack_configured or github_configured).lower()}")

    bridge_endpoint = bool(settings.langgraph_primary_host_bridge_endpoint)
    bridge_secret = bool(settings.langgraph_primary_host_bridge_secret)
    lines.append(f"  HOST_BRIDGE_ENDPOINT_CONFIGURED: {str(bridge_endpoint).lower()}")
    lines.append(f"  HOST_BRIDGE_SECRET_CONFIGURED: {str(bridge_secret).lower()}")
    lines.append("")

    if not problems:
        lines.append("No unmet prerequisite. Configuration is not authority:")
        lines.append("acceptance, evidence, and owner approval are separate gates.")
        return lines
    lines.append(f"Unmet prerequisites ({len(problems)}), in startup order:")
    for index, (stage, problem) in enumerate(problems, start=1):
        safe = redact(problem, settings)
        lines.append(f"  {index:>2}. [{stage}] {safe}")
        # Extract from the REDACTED text. Extracting from the raw problem meant
        # a configured value that happens to equal a declared variable name --
        # a secret literally set to "PROJECT_ALLOWLIST" -- was scrubbed from the
        # prose and then reprinted intact under "variables". Found by a review
        # of #140. A real name in a real message survives redaction, so nothing
        # legitimate is lost.
        named = variables(safe)
        if named:
            lines.append(f"      variables: {', '.join(named)}")
    return lines


def main() -> int:
    if Settings is None:
        # The import above already failed, for the same reason and with the
        # same disclosure risk as a failed construction.
        print("Settings could not be imported: configuration is unreadable.")
        print("The message is withheld because it can quote a configured value.")
        print("Re-run the import directly to see it.")
        return 1
    try:
        settings = Settings()
    except Exception as exc:
        # The exception text is never printed. Settings interpolates values
        # into construction errors -- an independent review of #140 put a
        # secret in LANGGRAPH_PRIMARY_SNAPSHOT_MAX_FILES and the integer
        # conversion echoed it verbatim -- and redaction is not available here
        # because it needs the Settings instance that just failed to build.
        # The type and the field are enough to act on; the value is the
        # operator's to look at.
        print(f"Settings could not be constructed: {type(exc).__name__}.")
        print("The message is withheld because it can quote a configured value.")
        print("Re-run the failing construction directly to see it.")
        return 1
    problems = unmet(settings)
    print("\n".join(report(settings, problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:08 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 05:36 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 06:56 UTC | END]]
# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 03:08 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 02:45 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-13 | 03:50 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-13 | 04:27 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-13 | 06:06 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-16 | 18:40 UTC | END]]
