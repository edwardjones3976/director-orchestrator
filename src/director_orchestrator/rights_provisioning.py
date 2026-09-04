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

Nothing here dispatches, writes to a repository, or decides a verdict. It
records which subject holds which right, on what evidence, and whether the owner
has been told. The deterministic policy gates elsewhere remain the authority on
what a held right permits.
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


def _notice_id(kind: SubjectKind, subject_id: str, digest: str, raised_at: int) -> str:
    payload = json.dumps(
        [kind.value, subject_id, digest, raised_at], separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RightsLedger:
    """The durable record of which subject holds which right, and why.

    SQLite rather than process state: a grant that disappeared on restart would
    make "already granted" and "never granted" indistinguishable, and the owner
    notice would then be re-raised on every boot until it was ignored.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
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

    def _connect(self) -> sqlite3.Connection:
        """Autocommit connection. Callers wrap it in `closing`.

        sqlite3's own context manager ends the transaction but leaves the handle
        open, which on Windows keeps the database file locked against deletion.
        Every call site here closes explicitly for that reason.
        """
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
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO owner_notice
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
                        connection.execute(
                            """
                            INSERT OR REPLACE INTO provisioned_right
                            (subject_kind, subject_id, right_name, build_digest,
                             granted_at, notice_id, revoked_at, revoked_by)
                            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
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
        """Whether the subject currently holds the right. Unknown means no."""
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
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT right_name FROM provisioned_right
                WHERE subject_kind = ? AND subject_id = ? AND revoked_at IS NULL
                """,
                (kind.value, subject_id),
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
        """Explicit owner re-grant. The only way back from a revocation."""
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
# [[Claude Code | Claude Opus 5 | 2026-09-04 | 07:46 UTC | END]]
