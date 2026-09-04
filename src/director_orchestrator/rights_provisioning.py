# [[Claude Code | Claude Opus 5 | 2026-09-04 | 19:08 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 10:56 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 09:13 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | BEGIN]]
"""Deterministic rights provisioning for verified provider and project installs.

The owner's requirement is that a provider lane or a project receives its rights
immediately once it has properly installed and built, without a separate manual
grant for every arrival. The hazard in stating it that way is that "anything
that builds gets rights" would hand authority to a subject nobody has looked at
yet, which is the opposite of how every other gate in this service behaves.

This module serves the requirement without accepting that hazard. A grant is
automatic, but it is not silent and it is not unbounded:

* Rights come from a closed per-kind vocabulary. A right nobody named cannot be
  granted, so an unreviewed arrival cannot invent authority for itself.
* A grant requires an `InstallVerification` carrying an exact build digest.
  Absence of evidence is refusal, never a default-open.
* Every grant records an owner notice. The notice is produced by the same call
  that produces the grant, so a grant that reached the ledger without notifying
  the owner is not a state this module can express.
* Revocation outranks reinstallation. Once the owner revokes a right, a later
  build of the same subject does not resurrect it; only an explicit owner
  re-grant does. This is what keeps "automatic" from meaning "unrevocable in
  practice".

* A held right is not usable on its own. Reading rights back requires the
  caller to supply the digest of the build that is *actually running*, from
  configuration this module cannot write. A grant row records what was built
  when the right was granted, so trusting it as the active digest would make
  every stale grant look current. With no active build configured, every
  subject reads as holding nothing.

Nothing here dispatches, writes to a repository, or decides a verdict. It
records which subject holds which right, on what evidence, and whether the owner
has been told. The deterministic policy gates elsewhere remain the authority on
what a held right permits, and today no gate consults this module at all: it is
mechanism with no runtime sink. See `docs/rights-provisioning.md`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Mapping


class RightsProvisioningError(RuntimeError):
    """A provisioning attempt was refused. The attempt is never partially applied."""


class SubjectKind(StrEnum):
    """What a right can be held by."""

    PROVIDER = "provider"
    PROJECT = "project"


#: The complete set of rights that may ever be granted, per subject kind.
#:
#: This table is the reason an unreviewed arrival cannot escalate itself: a
#: declaration naming anything outside it is refused whole. Adding a member is a
#: reviewed code change, which is the point.
RIGHTS_VOCABULARY: Mapping[SubjectKind, frozenset[str]] = {
    SubjectKind.PROVIDER: frozenset(
        {
            "review_verdict",
            "repository_write",
            "tools",
            "debate_turn",
        }
    ),
    SubjectKind.PROJECT: frozenset(
        {
            "intake",
            "repository_write",
            "paid_dispatch",
        }
    ),
}

#: Rights that carry authority over another contributor's work rather than only
#: over the subject's own output. These are still auto-granted when declared and
#: verified, but the owner notice records them explicitly so an escalation is
#: never something the owner has to go looking for.
AUTHORITY_BEARING_RIGHTS: frozenset[str] = frozenset(
    {"review_verdict", "repository_write", "paid_dispatch"}
)

_SUBJECT_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class InstallVerification:
    """Evidence that one subject installed and built cleanly.

    `build_digest` binds the grant to exactly what was built. A later build with
    different content produces a different digest and therefore a different
    grant, so a right cannot silently outlive the artifact it was granted for.
    """

    subject_kind: SubjectKind
    subject_id: str
    declared_rights: frozenset[str]
    build_digest: str
    verified_at: int

    def __post_init__(self) -> None:
        # Canonicalised before anything is checked, and copied rather than
        # aliased: a caller may hand in a plain set, and validating the
        # caller's own object would leave it free to gain a right after the
        # vocabulary check passed. Freezing first makes validation apply to
        # exactly the value this object will keep.
        object.__setattr__(self, "declared_rights", frozenset(self.declared_rights))
        if not _SUBJECT_ID.fullmatch(self.subject_id):
            raise RightsProvisioningError("subject id is not a safe identifier")
        if not _DIGEST.fullmatch(self.build_digest):
            raise RightsProvisioningError("build digest must be an exact sha256")
        if self.verified_at <= 0:
            raise RightsProvisioningError("verification timestamp is not set")
        allowed = RIGHTS_VOCABULARY[self.subject_kind]
        unknown = sorted(set(self.declared_rights) - allowed)
        if unknown:
            # Refused whole rather than filtered: a declaration naming a right
            # this build does not understand is a declaration this build cannot
            # honestly evaluate.
            raise RightsProvisioningError(
                "declared rights are outside the closed vocabulary: "
                + ", ".join(unknown)
            )


@dataclass(frozen=True)
class OwnerNotice:
    """The record that the owner was told about one grant."""

    notice_id: str
    subject_kind: SubjectKind
    subject_id: str
    rights: tuple[str, ...]
    authority_bearing: tuple[str, ...]
    build_digest: str
    raised_at: int

    def summary(self) -> str:
        """One line an owner can act on without opening the ledger."""
        scope = (
            "authority-bearing: " + ", ".join(self.authority_bearing)
            if self.authority_bearing
            else "no authority-bearing rights"
        )
        return (
            f"{self.subject_kind.value} '{self.subject_id}' was granted "
            f"{', '.join(self.rights)} on build {self.build_digest[:12]} ({scope}). "
            "Revoke with revoke_right if this was not intended."
        )


@dataclass(frozen=True)
class GrantOutcome:
    """What one provisioning call actually changed."""

    granted: tuple[str, ...]
    withheld_revoked: tuple[str, ...]
    already_held: tuple[str, ...]
    notice: OwnerNotice | None = field(default=None)


def _now(explicit: int | None) -> int:
    return int(time.time()) if explicit is None else int(explicit)


def _notice_id(
    kind: SubjectKind,
    subject_id: str,
    digest: str,
    raised_at: int,
    granted: Iterable[str],
) -> str:
    """Identify a notice by everything that distinguishes it, rights included.

    Without the granted rights in the payload, two grants split across separate
    calls for the same subject and build within one second collide, and the
    second notice takes the identity of the first. The rights are what actually
    differ between them, so they belong in the identity.
    """
    payload = json.dumps(
        [kind.value, subject_id, digest, raised_at, sorted(granted)],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RightsLedger:
    """The durable record of which subject holds which right, and why.

    SQLite rather than process state: a grant that disappeared on restart would
    make "already granted" and "never granted" indistinguishable, and the owner
    notice would then be re-raised on every boot until it was ignored.
    """

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        """Open the ledger. Writable by default, because provisioning writes.

        `read_only=True` opens an *existing* ledger for inspection and nothing
        else: it creates no directory, creates no file, and adds no table. That
        distinction matters because the writable constructor will happily turn
        whatever path it is handed into a rights ledger, and the reader is the
        mode that runs from configuration at startup, where a mistyped path
        should be a refusal rather than a schema change to somebody else's
        database.
        """
        self._path = Path(path)
        self._read_only = read_only
        if read_only:
            self._require_existing_ledger()
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provisioned_right (
                    subject_kind TEXT NOT NULL,
                    subject_id   TEXT NOT NULL,
                    right_name   TEXT NOT NULL,
                    build_digest TEXT NOT NULL,
                    granted_at   INTEGER NOT NULL,
                    notice_id    TEXT NOT NULL,
                    revoked_at   INTEGER,
                    revoked_by   TEXT,
                    PRIMARY KEY (subject_kind, subject_id, right_name)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS owner_notice (
                    notice_id     TEXT PRIMARY KEY,
                    subject_kind  TEXT NOT NULL,
                    subject_id    TEXT NOT NULL,
                    rights        TEXT NOT NULL,
                    authority     TEXT NOT NULL,
                    build_digest  TEXT NOT NULL,
                    raised_at     INTEGER NOT NULL,
                    acknowledged  INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def _require_existing_ledger(self) -> None:
        """Refuse a path that is not already a rights ledger.

        Two separate refusals, because they are different mistakes. A missing
        file is the ordinary opted-out case. A file that exists but carries
        someone else's schema is a misconfiguration — `SQLITE_PATH`, a
        checkpoint database — and reading it as an empty ledger would report
        "this subject holds nothing" when the truth is "this is the wrong file".
        """
        if not self._path.is_file():
            raise RightsProvisioningError("rights ledger file does not exist")
        with closing(self._connect()) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        missing = sorted({"provisioned_right", "owner_notice"} - tables)
        if missing:
            raise RightsProvisioningError(
                "path is not a rights ledger, missing tables: " + ", ".join(missing)
            )

    def _connect(self) -> sqlite3.Connection:
        """Autocommit connection. Callers wrap it in `closing`.

        sqlite3's own context manager ends the transaction but leaves the handle
        open, which on Windows keeps the database file locked against deletion.
        Every call site here closes explicitly for that reason.

        A read-only ledger connects through a `mode=ro` URI and pins
        `query_only`, so no statement reached through this handle can create the
        file, add a table to it, or leave journal artifacts beside it. SQLite
        refuses a read-only open of a database with an unrecovered WAL, which
        surfaces as a `sqlite3.Error` and therefore as a refusal.
        """
        if self._read_only:
            connection = sqlite3.connect(
                self._path.resolve().as_uri() + "?mode=ro",
                uri=True,
                isolation_level=None,
            )
            try:
                connection.execute("PRAGMA query_only=ON")
            except BaseException:
                connection.close()
                raise
            return connection
        connection = sqlite3.connect(self._path, isolation_level=None)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
        except BaseException:
            # A corrupt database raises here, before `closing` has taken
            # ownership of the handle. Without this the file stays locked and
            # the caller's refusal path leaks a connection per attempt.
            connection.close()
            raise
        return connection

    def provision(
        self,
        verification: InstallVerification,
        *,
        now: int | None = None,
    ) -> GrantOutcome:
        """Grant every declared right the owner has not revoked, and notify.

        Revoked rights are reported back rather than silently omitted, so a
        reinstall that expected them cannot mistake refusal for success.
        """
        raised_at = _now(now)
        granted: list[str] = []
        withheld: list[str] = []
        already: list[str] = []
        notice: OwnerNotice | None = None

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for right in sorted(verification.declared_rights):
                    row = connection.execute(
                        """
                        SELECT build_digest, revoked_at FROM provisioned_right
                        WHERE subject_kind = ? AND subject_id = ? AND right_name = ?
                        """,
                        (
                            verification.subject_kind.value,
                            verification.subject_id,
                            right,
                        ),
                    ).fetchone()
                    if row is not None and row[1] is not None:
                        # Revocation outranks reinstallation.
                        withheld.append(right)
                        continue
                    if row is not None and row[0] == verification.build_digest:
                        already.append(right)
                        continue
                    granted.append(right)

                if granted:
                    notice_id = _notice_id(
                        verification.subject_kind,
                        verification.subject_id,
                        verification.build_digest,
                        raised_at,
                        granted,
                    )
                    authority = tuple(
                        r for r in granted if r in AUTHORITY_BEARING_RIGHTS
                    )
                    notice = OwnerNotice(
                        notice_id=notice_id,
                        subject_kind=verification.subject_kind,
                        subject_id=verification.subject_id,
                        rights=tuple(granted),
                        authority_bearing=authority,
                        build_digest=verification.build_digest,
                        raised_at=raised_at,
                    )
                    # The notice is written in the same transaction as the
                    # grants, so a granted right always has a notice behind it.
                    # A plain INSERT, not INSERT OR REPLACE: overwriting on a
                    # colliding id would leave the earlier grant's rows
                    # pointing at a notice describing different rights, which
                    # is the "granted, but nobody was told" state this module
                    # is supposed to be unable to express. A collision instead
                    # aborts the whole attempt.
                    connection.execute(
                        """
                        INSERT INTO owner_notice
                        (notice_id, subject_kind, subject_id, rights, authority,
                         build_digest, raised_at, acknowledged)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                        """,
                        (
                            notice_id,
                            verification.subject_kind.value,
                            verification.subject_id,
                            json.dumps(list(granted)),
                            json.dumps(list(authority)),
                            verification.build_digest,
                            raised_at,
                        ),
                    )
                    for right in granted:
                        # An explicit upsert rather than INSERT OR REPLACE.
                        # OR REPLACE deletes the conflicting row and inserts a
                        # new one, so it rewrites columns this statement never
                        # names and would fire any delete-side trigger or
                        # cascade a later schema adds. Naming the three columns
                        # a re-grant may move keeps the rest of the row exactly
                        # as stored.
                        #
                        # revoked_at and revoked_by are deliberately not in the
                        # update list. A revoked right was already classified as
                        # withheld earlier in this same transaction, which holds
                        # the write lock, so a revoked row cannot reach here;
                        # and if that classification were ever wrong, leaving
                        # the columns alone keeps the right revoked instead of
                        # resurrecting it through a rebuild.
                        connection.execute(
                            """
                            INSERT INTO provisioned_right
                            (subject_kind, subject_id, right_name, build_digest,
                             granted_at, notice_id, revoked_at, revoked_by)
                            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
                            ON CONFLICT (subject_kind, subject_id, right_name)
                            DO UPDATE SET
                                build_digest = excluded.build_digest,
                                granted_at   = excluded.granted_at,
                                notice_id    = excluded.notice_id
                            """,
                            (
                                verification.subject_kind.value,
                                verification.subject_id,
                                right,
                                verification.build_digest,
                                raised_at,
                                notice_id,
                            ),
                        )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

        return GrantOutcome(
            granted=tuple(granted),
            withheld_revoked=tuple(withheld),
            already_held=tuple(already),
            notice=notice,
        )

    def holds(self, kind: SubjectKind, subject_id: str, right: str) -> bool:
        """Introspection only: is there a live grant row, for any build.

        This is not an authorization API and no privileged operation may call
        it. It answers a question about the ledger's history — "was this granted
        and not revoked" — not about the build that is running now, so it
        returns True for a grant bound to a build that was superseded long ago.
        `held_rights_for_build` is the only read that binds to an active build.
        Unknown means no.
        """
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT revoked_at FROM provisioned_right
                WHERE subject_kind = ? AND subject_id = ? AND right_name = ?
                """,
                (kind.value, subject_id, right),
            ).fetchone()
        return row is not None and row[0] is None

    def held_rights(self, kind: SubjectKind, subject_id: str) -> frozenset[str]:
        """Introspection only: every live grant, for any build.

        Carries exactly the caveat `holds` carries. Use it to show an owner what
        the ledger contains; never to decide whether something may proceed.
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT right_name FROM provisioned_right
                WHERE subject_kind = ? AND subject_id = ? AND revoked_at IS NULL
                """,
                (kind.value, subject_id),
            ).fetchall()
        return frozenset(row[0] for row in rows)

    def held_rights_for_build(
        self,
        kind: SubjectKind,
        subject_id: str,
        active_build_digest: str,
    ) -> frozenset[str]:
        """Rights held *for the build that is actually running*.

        The active digest comes from the caller, out of configuration this
        module cannot write. The grant row's own digest cannot answer "is this
        the build that is live" — it only records what was built at grant time
        — so binding a right against it would make every stale grant look
        current. A digest that is not an exact sha256 is refused rather than
        treated as a wildcard.

        This reads the ledger at the moment of the call, so a revocation is
        visible immediately and a new grant is visible immediately. The
        `Settings` rights maps are a construction-time snapshot instead, and
        `main.py` keeps one `Settings` for the life of the process; anything
        that needs a current answer must call this, or
        `config.current_provisioned_rights` where a bounded staleness is
        acceptable and the caller is not about to act on the answer.
        """
        if not _DIGEST.fullmatch(active_build_digest):
            raise RightsProvisioningError("active build digest must be an exact sha256")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT right_name FROM provisioned_right
                WHERE subject_kind = ? AND subject_id = ? AND revoked_at IS NULL
                  AND build_digest = ?
                """,
                (kind.value, subject_id, active_build_digest),
            ).fetchall()
        return frozenset(row[0] for row in rows)

    def subject_ids(self, kind: SubjectKind) -> tuple[str, ...]:
        """Every subject of this kind currently holding at least one right."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT subject_id FROM provisioned_right
                WHERE subject_kind = ? AND revoked_at IS NULL
                ORDER BY subject_id ASC
                """,
                (kind.value,),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def revoke_right(
        self,
        kind: SubjectKind,
        subject_id: str,
        right: str,
        *,
        revoked_by: str,
        now: int | None = None,
    ) -> bool:
        """Withdraw one right. A revoked right survives later builds."""
        if not revoked_by.strip():
            raise RightsProvisioningError("revocation must name the revoking owner")
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE provisioned_right SET revoked_at = ?, revoked_by = ?
                WHERE subject_kind = ? AND subject_id = ? AND right_name = ?
                  AND revoked_at IS NULL
                """,
                (_now(now), revoked_by.strip(), kind.value, subject_id, right),
            )
            return cursor.rowcount > 0

    def restore_right(
        self,
        kind: SubjectKind,
        subject_id: str,
        right: str,
        *,
        restored_by: str,
        now: int | None = None,
    ) -> bool:
        """Explicit owner re-grant. The only way back from a revocation.

        Restoration puts the row back as it was stored, build binding included:
        the right returns bound to whichever digest it was granted against, not
        to whatever is running now. If the subject has rebuilt since, that
        binding is stale and `held_rights_for_build` keeps reporting nothing
        until the subject is reprovisioned against the current build or the
        active-build configuration is realigned with the stored digest. That is
        deliberate — restoring a right says nothing about which build is live.
        """
        if not restored_by.strip():
            raise RightsProvisioningError("restoration must name the restoring owner")
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE provisioned_right SET revoked_at = NULL, revoked_by = NULL,
                       granted_at = ?
                WHERE subject_kind = ? AND subject_id = ? AND right_name = ?
                  AND revoked_at IS NOT NULL
                """,
                (_now(now), kind.value, subject_id, right),
            )
            return cursor.rowcount > 0

    def pending_notices(self) -> tuple[OwnerNotice, ...]:
        """Grants the owner has not acknowledged yet, oldest first."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT notice_id, subject_kind, subject_id, rights, authority,
                       build_digest, raised_at
                FROM owner_notice WHERE acknowledged = 0
                ORDER BY raised_at ASC, notice_id ASC
                """
            ).fetchall()
        return tuple(
            OwnerNotice(
                notice_id=row[0],
                subject_kind=SubjectKind(row[1]),
                subject_id=row[2],
                rights=tuple(json.loads(row[3])),
                authority_bearing=tuple(json.loads(row[4])),
                build_digest=row[5],
                raised_at=row[6],
            )
            for row in rows
        )

    def acknowledge_notice(self, notice_id: str) -> bool:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "UPDATE owner_notice SET acknowledged = 1 WHERE notice_id = ? AND acknowledged = 0",
                (notice_id,),
            )
            return cursor.rowcount > 0


def build_digest_for(payload: Iterable[bytes]) -> str:
    """The exact digest of what was built, for binding a grant to an artifact."""
    digest = hashlib.sha256()
    for chunk in payload:
        digest.update(chunk)
    return digest.hexdigest()


def parse_active_build_digests(raw: str) -> dict[tuple[SubjectKind, str], str]:
    """Parse the operator's statement of which build is live, per subject.

    Shape: `{"provider:antigravity": "<64 hex>"}`. This is deliberately
    configuration rather than anything the ledger derives — it is the
    independent half of the binding, and a subject cannot write it by
    installing.

    Malformed input is refused whole rather than partially honoured: a
    half-parsed map would quietly activate whichever subjects happened to
    parse. Callers that must not fail closed loudly should catch
    `RightsProvisioningError` and treat it as "no subject holds anything".

    Every rejection leaves as `RightsProvisioningError`, including the ones
    `json.loads` raises that are not `JSONDecodeError`: an integer literal past
    CPython's 4300-digit conversion cap raises a bare `ValueError` from the
    scanner, and deep nesting raises `RecursionError`. Those escaping would
    reach `Settings()`, which `main.py` constructs at module scope, so a
    mistyped environment variable would stop the service booting instead of
    yielding no rights.
    """
    text = raw.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError) as exc:
        # ValueError subsumes JSONDecodeError; RecursionError is a RuntimeError
        # and is not caught by it, so both are named.
        raise RightsProvisioningError(
            "active build digests must be valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise RightsProvisioningError("active build digests must be an object")
    parsed: dict[tuple[SubjectKind, str], str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise RightsProvisioningError("active build digest entry is malformed")
        kind_name, separator, subject_id = key.partition(":")
        if not separator:
            raise RightsProvisioningError(
                "active build digest key must be '<subject kind>:<subject id>'"
            )
        try:
            kind = SubjectKind(kind_name)
        except ValueError as exc:
            raise RightsProvisioningError(
                "unknown subject kind in active build digests: " + kind_name
            ) from exc
        if not _SUBJECT_ID.fullmatch(subject_id):
            raise RightsProvisioningError("subject id is not a safe identifier")
        if not _DIGEST.fullmatch(value):
            raise RightsProvisioningError("active build digest must be an exact sha256")
        parsed[(kind, subject_id)] = value
    return parsed
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 08:58 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 09:13 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 10:56 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 19:08 UTC | END]]
