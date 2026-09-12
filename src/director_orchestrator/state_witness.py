# [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | BEGIN]]
"""No receipt, no state: the Witness Ledger.

An agent cannot make ``merged``, ``green``, ``reviewed``, ``authorized``,
``the document says`` or ``provider down`` true by typing it. Each of those is a
claim class, and a claim class is true only as a row in the insert-only
``state_witness`` table, written by an oracle wrapper that observed the world
(``gh``, the Slack API, the filesystem) rather than by any model output.

Four rules, each enforced here rather than by asking models to be careful:

* **Only oracles write rows.** The wrappers below take the observation
  callable as an argument so tests can substitute it, but a row always records
  which oracle ran, what it observed, and when.
* **Gates consume rows and re-execute the query when they act.** ``witnessed()``
  answers from the ledger; ``verify()`` runs the oracle again and writes a
  fresh row before answering. A gate that acts calls ``verify()``.
* **Unobservable means deny.** A claim class with no oracle, a probe that raised
  or timed out, or a row older than its ttl is ``UNOBSERVABLE`` and every gate
  refuses on it. ``MATCH`` is never the default.
* **The loop closes inside the task.** A refusal carries the ``(claimed,
  observed)`` pair so the caller can inject it into the claiming agent's next
  turn, and ``strikes()`` counts mismatches per agent and class so chronic
  offenders can be put behind automatic verification.

Unwitnessed sentences still reach the owner: ``stamp_unwitnessed()`` marks
state claims in outbound text that cite no witness row. The stamp is cosmetic;
the gate is the rule.

First version, as scoped by the 2026-09-12 panel: one table, four wrappers,
one predicate, the merge and authorization-write gates, the outbound stamp,
and the packet-refusal test. ``provider_reachable`` is a known claim class with
no oracle yet, so it is ``UNOBSERVABLE`` by construction.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping


class ClaimKind(StrEnum):
    GH_PR_MERGED = "gh_pr_merged"
    GH_CHECKS_GREEN = "gh_checks_green"
    OWNER_AUTHORIZATION = "owner_authorization"
    DOC_READ = "doc_read"
    # Named by the standard; no oracle in this version, so always UNOBSERVABLE.
    PROVIDER_REACHABLE = "provider_reachable"


class WitnessStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNOBSERVABLE = "UNOBSERVABLE"


# A row older than this answers UNOBSERVABLE. Short on purpose: the row is an
# audit cache, the re-execution is the safety property.
DEFAULT_TTL = timedelta(minutes=10)

_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_MAX_OBSERVED_BYTES = 16_384


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class WitnessRow:
    witness_id: int
    kind: ClaimKind
    subject: str
    agent: str
    claimed: Any
    observed: Any
    status: WitnessStatus
    oracle: str
    observed_at: datetime

    @property
    def matched(self) -> bool:
        return self.status is WitnessStatus.MATCH


@dataclass(frozen=True)
class Refusal:
    """What a gate hands back when a claim does not survive re-execution."""

    kind: ClaimKind
    subject: str
    claimed: Any
    observed: Any
    status: WitnessStatus

    def feedback(self) -> dict[str, Any]:
        """The pair injected into the claiming agent's next turn."""

        return {
            "witness_refusal": {
                "claim": f"{self.kind}({self.subject})",
                "claimed": self.claimed,
                "observed": self.observed,
                "status": str(self.status),
                "rule": "no receipt, no state: cite a witness row, do not assert",
            }
        }


class WitnessRefused(RuntimeError):
    def __init__(self, refusal: Refusal):
        super().__init__(
            f"{refusal.status}: {refusal.kind}({refusal.subject}) claimed "
            f"{_canonical(refusal.claimed)} observed {_canonical(refusal.observed)}"
        )
        self.refusal = refusal


class StateWitnessLedger:
    """Insert-only table of oracle observations. Same engine-level triggers as the
    delegation audit ledger: a mutation fails even from outside this class."""

    def __init__(self, path: str, *, ttl: timedelta = DEFAULT_TTL):
        self.path = path
        self.ttl = ttl
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS state_witness (
                    witness_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    claimed_json TEXT NOT NULL,
                    observed_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    oracle TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                )
                """
            )
            for verb in ("UPDATE", "DELETE"):
                connection.execute(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS state_witness_no_{verb.lower()}
                    BEFORE {verb} ON state_witness
                    BEGIN SELECT RAISE(ABORT, 'state witness rows are append-only'); END
                    """
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS state_witness_lookup"
                " ON state_witness (kind, subject, observed_at)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def record(
        self,
        kind: ClaimKind,
        subject: str,
        *,
        agent: str,
        claimed: Any,
        observed: Any,
        status: WitnessStatus,
        oracle: str,
        now: datetime | None = None,
    ) -> WitnessRow:
        observed_json = _canonical(observed)
        if len(observed_json.encode("utf-8")) > _MAX_OBSERVED_BYTES:
            raise ValueError("observed payload exceeds its bound")
        at = now or _utcnow()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO state_witness (kind, subject, agent, claimed_json,"
                " observed_json, status, oracle, observed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(kind),
                    subject,
                    agent,
                    _canonical(claimed),
                    observed_json,
                    str(status),
                    oracle,
                    at.isoformat(),
                ),
            )
            witness_id = int(cursor.lastrowid)
        return WitnessRow(
            witness_id, kind, subject, agent, claimed, observed, status, oracle, at
        )

    def latest(self, kind: ClaimKind, subject: str) -> WitnessRow | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM state_witness WHERE kind = ? AND subject = ?"
                " ORDER BY witness_id DESC LIMIT 1",
                (str(kind), subject),
            ).fetchone()
        if row is None:
            return None
        return WitnessRow(
            int(row["witness_id"]),
            ClaimKind(row["kind"]),
            str(row["subject"]),
            str(row["agent"]),
            json.loads(row["claimed_json"]),
            json.loads(row["observed_json"]),
            WitnessStatus(row["status"]),
            str(row["oracle"]),
            datetime.fromisoformat(row["observed_at"]),
        )

    def witnessed(
        self, kind: ClaimKind, subject: str, *, now: datetime | None = None
    ) -> WitnessStatus:
        """The predicate. Answers only from rows; never runs an oracle."""

        row = self.latest(kind, subject)
        if row is None:
            return WitnessStatus.UNOBSERVABLE
        if (now or _utcnow()) - row.observed_at > self.ttl:
            return WitnessStatus.UNOBSERVABLE
        return row.status

    def strikes(self, agent: str, kind: ClaimKind) -> int:
        """Mismatches this agent has recorded for this class. Only a MATCH row
        that gated a real effect clears them, and clearing is a new row, so the
        count is "mismatches since the last effect-gating match"."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT status, oracle FROM state_witness WHERE agent = ? AND kind = ?"
                " ORDER BY witness_id",
                (agent, str(kind)),
            ).fetchall()
        count = 0
        for row in rows:
            if row["status"] == WitnessStatus.MISMATCH:
                count += 1
            elif row["status"] == WitnessStatus.MATCH and row["oracle"].endswith("#gate"):
                count = 0
        return count


# --- oracle wrappers ---------------------------------------------------------
#
# Each wrapper observes, compares to the claim, writes exactly one row, and
# returns it. The observation callable is a parameter so a test can hand in a
# fake; the default is the real thing. An exception inside the observation is
# recorded as UNOBSERVABLE, never as a match.

ObservationError = (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError)


def _gh_json(args: list[str]) -> Any:
    completed = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True, timeout=60
    )
    return json.loads(completed.stdout)


def _row(
    ledger: StateWitnessLedger,
    kind: ClaimKind,
    subject: str,
    *,
    agent: str,
    claimed: Any,
    oracle: str,
    observe: Callable[[], Any],
    matches: Callable[[Any], bool],
) -> WitnessRow:
    try:
        observed = observe()
    except ObservationError as exc:
        return ledger.record(
            kind, subject, agent=agent, claimed=claimed,
            observed={"error": type(exc).__name__}, status=WitnessStatus.UNOBSERVABLE,
            oracle=oracle,
        )
    status = WitnessStatus.MATCH if matches(observed) else WitnessStatus.MISMATCH
    return ledger.record(
        kind, subject, agent=agent, claimed=claimed, observed=observed,
        status=status, oracle=oracle,
    )


def gh_pr_merged(
    ledger: StateWitnessLedger,
    repository: str,
    pull_number: int,
    sha: str,
    *,
    agent: str,
    claimed: bool = True,
    gh_json: Callable[[list[str]], Any] = _gh_json,
) -> WitnessRow:
    """Is pull ``pull_number`` merged, with ``sha`` as the head that merged?"""

    if not _SHA1.match(sha):
        raise ValueError("sha must be a full 40-hex commit")
    subject = f"{repository}#{pull_number}@{sha}"

    def observe() -> Any:
        pr = gh_json(["api", f"repos/{repository}/pulls/{pull_number}"])
        return {
            "merged": bool(pr["merged"]),
            "state": pr["state"],
            "head_sha": pr["head"]["sha"],
            "merge_commit_sha": pr.get("merge_commit_sha"),
        }

    return _row(
        ledger, ClaimKind.GH_PR_MERGED, subject, agent=agent, claimed=claimed,
        oracle="gh api pulls", observe=observe,
        matches=lambda o: o["merged"] is claimed and o["head_sha"] == sha,
    )


def gh_checks_green(
    ledger: StateWitnessLedger,
    repository: str,
    sha: str,
    *,
    agent: str,
    required: Iterable[str] = (),
    gh_json: Callable[[list[str]], Any] = _gh_json,
) -> WitnessRow:
    """Every job at ``sha`` concluded ``success`` and executed at least one step.
    A zero-step job is a billing stub and can never be green."""

    if not _SHA1.match(sha):
        raise ValueError("sha must be a full 40-hex commit")
    subject = f"{repository}@{sha}"
    wanted = set(required)

    def observe() -> Any:
        runs = gh_json(
            ["api", f"repos/{repository}/actions/runs?head_sha={sha}&per_page=20"]
        )["workflow_runs"]
        jobs: dict[str, dict[str, Any]] = {}
        for run in runs:
            for job in gh_json(
                ["api", f"repos/{repository}/actions/runs/{run['id']}/jobs?per_page=50"]
            )["jobs"]:
                steps = len(job.get("steps") or [])
                # Latest run per job name wins; runs come newest first.
                jobs.setdefault(
                    job["name"],
                    {"conclusion": job.get("conclusion"), "steps": steps, "run_id": run["id"]},
                )
        return {"jobs": jobs}

    def matches(observed: Any) -> bool:
        jobs = observed["jobs"]
        if not jobs or (wanted and not wanted <= set(jobs)):
            return False
        return all(j["conclusion"] == "success" and j["steps"] > 0 for j in jobs.values())

    return _row(
        ledger, ClaimKind.GH_CHECKS_GREEN, subject, agent=agent, claimed=True,
        oracle="gh api actions/runs+jobs", observe=observe, matches=matches,
    )


def owner_authorization(
    ledger: StateWitnessLedger,
    channel: str,
    ts: str,
    *,
    agent: str,
    owner_user_id: str,
    authorization_digest: str,
    fetch_message: Callable[[str, str], Mapping[str, Any]],
) -> WitnessRow:
    """The Slack message at ``channel``/``ts`` exists and its author is the owner.
    ``authorization_digest`` is the subject so the authorization-write gate can
    look the row up by the digest it is about to persist."""

    def observe() -> Any:
        message = fetch_message(channel, ts)
        text = str(message.get("text", ""))
        return {
            "channel": channel,
            "ts": ts,
            "user": message.get("user"),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    return _row(
        ledger, ClaimKind.OWNER_AUTHORIZATION, authorization_digest, agent=agent,
        claimed={"channel": channel, "ts": ts, "author_is_owner": True},
        oracle="slack conversations.history", observe=observe,
        matches=lambda o: o["user"] == owner_user_id,
    )


def doc_read(
    ledger: StateWitnessLedger,
    path: str,
    sha256: str,
    *,
    agent: str,
    read_bytes: Callable[[str], bytes] = lambda p: Path(p).read_bytes(),
) -> WitnessRow:
    """The document at ``path`` was actually read, and its bytes hash to
    ``sha256``. "The document says" without this row is unwitnessed."""

    def observe() -> Any:
        data = read_bytes(path)
        return {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    return _row(
        ledger, ClaimKind.DOC_READ, path, agent=agent, claimed={"sha256": sha256},
        oracle="filesystem read", observe=observe,
        matches=lambda o: o["sha256"] == sha256,
    )


# --- the gate -----------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    kind: ClaimKind
    subject: str
    claimed: Any


def verify(
    ledger: StateWitnessLedger,
    claim: Claim,
    *,
    agent: str,
    oracle: Callable[[], WitnessRow] | None,
) -> WitnessRow | Refusal:
    """Re-execute the oracle for one claim and decide. ``oracle`` is the bound
    wrapper call for this claim; ``None`` means the class has no oracle."""

    if oracle is None:
        ledger.record(
            claim.kind, claim.subject, agent=agent, claimed=claim.claimed,
            observed={"error": "no oracle for claim class"},
            status=WitnessStatus.UNOBSERVABLE, oracle="none#gate",
        )
        return Refusal(claim.kind, claim.subject, claim.claimed, None, WitnessStatus.UNOBSERVABLE)
    row = oracle()
    if row.matched:
        return row
    return Refusal(claim.kind, claim.subject, claim.claimed, row.observed, row.status)


def verify_packet(
    ledger: StateWitnessLedger,
    claims: Iterable[Claim],
    *,
    agent: str,
    oracles: Mapping[ClaimKind, Callable[[Claim], WitnessRow]],
) -> list[Refusal]:
    """Every state claim a packet makes, re-executed. Empty list means all
    witnessed; otherwise the caller refuses and returns the feedback."""

    refusals: list[Refusal] = []
    for claim in claims:
        bound = oracles.get(claim.kind)
        result = verify(
            ledger, claim, agent=agent,
            oracle=None if bound is None else (lambda c=claim, b=bound: b(c)),
        )
        if isinstance(result, Refusal):
            refusals.append(result)
    return refusals


def require(
    ledger: StateWitnessLedger, kind: ClaimKind, subject: str, *, now: datetime | None = None
) -> None:
    """Consume a row at a gate: raise unless a fresh MATCH row exists."""

    status = ledger.witnessed(kind, subject, now=now)
    if status is not WitnessStatus.MATCH:
        row = ledger.latest(kind, subject)
        raise WitnessRefused(
            Refusal(kind, subject, True, None if row is None else row.observed, status)
        )


# --- the outbound stamp ---------------------------------------------------------

_STATE_CLAIM = re.compile(
    r"\b(merged|green|reviewed|authori[sz]ed|the (?:doc|document) says|provider (?:is )?down)\b",
    re.IGNORECASE,
)
_WITNESS_CITE = re.compile(r"\bwitness:\d+\b")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def stamp_unwitnessed(text: str) -> str:
    """Prefix every sentence that asserts a state claim without citing a
    ``witness:<id>`` row. Cosmetic by design: nothing transitions on prose."""

    def stamp(match: re.Match[str]) -> str:
        sentence = match.group(0)
        if _STATE_CLAIM.search(sentence) and not _WITNESS_CITE.search(sentence):
            stripped = sentence.lstrip()
            return sentence[: len(sentence) - len(stripped)] + "UNWITNESSED: " + stripped
        return sentence

    return _SENTENCE.sub(stamp, text)
# [[Claude Code | Claude Opus 5 | 2026-09-12 | 03:05 UTC | END]]
