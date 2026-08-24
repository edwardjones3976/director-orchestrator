<!-- [[Codex | GPT-5.6 | 2026-08-24 | 05:14 UTC | BEGIN]] -->
# Director Completion Gates

**Status:** Contract-only candidate; no runtime implementation or authority
**Schema authority:** `director-gate-ledger/v1` and `director-gate-result/v1`
**Frozen starting point:** `21c4c70ad4bb1135e82d7b69df9866a1634f3546`

## Purpose

Director Completion Gates define immutable, provider-neutral evidence contracts for
substantial assignments. They record what must be observed before work begins and
what a producer or independent reviewer actually observed at an exact commit.

Gate evidence supplements the existing work/result packets, independent exact-SHA
review, Codex Security, trusted CI, and deterministic merge policy. It never grants
assignment, provider-selection, budget, repository, approval, review, merge,
deployment, LangGraph, provider-dispatch, or external-write authority.

This first increment adds documentation, schemas, examples, and offline contract
tests only. It does not add a runner, feature flag, provider prompt, hook, service,
database, deployment setting, or production execution path.

## Applicability

The Director should propose Completion Gates for medium-or-higher complexity,
multi-component work, long implementations, new dependencies or persistence,
recovery/concurrency/idempotency changes, security boundaries, cross-platform
validation, external-effect boundaries, LangGraph/provider/merge-gate work, or an
owner request for exhaustive completion discipline.

Factual replies, formatting changes, spelling corrections, one-line safe edits,
trivial fixtures, and the existing bounded minimal-integration-fix procedure do not
receive a gate ledger unless their scope or risk expands.

## Companion-contract boundary

The accepted `model-work-packet-v1` and `model-result-packet-v1` contracts remain
unchanged. Completion Gates are companion documents:

- `director-gate-ledger/v1`: Director-issued immutable gate definitions.
- `director-gate-result/v1`: one immutable observation attempt for one gate.

Once a later increment implements persistence, authoritative instances must live
in an append-only, Director-controlled evidence store outside the repository,
canonical task tables, and LangGraph checkpoint storage. That store does not
exist in this increment. Checked repository examples prove only the contract.

## Immutable ledger lifecycle

An issued ledger is never edited in place.

1. A `PLANNED` revision has `expected_head_sha: null`.
2. A producer may propose additional gates but cannot edit either gate array.
3. The Director may adopt those proposals only by issuing a successor ledger with
   a new `ledger_id`, incremented `ledger_revision`, and
   `predecessor_ledger_digest`.
4. When implementation returns, the Director issues a `HEAD_BOUND` successor
   whose `expected_head_sha` is the exact candidate SHA and whose
   `candidate_result_digest` binds to the producer result that supplied it.
   The successor also records every authenticated `material_author`.
5. A new commit requires another successor and invalidates all gate, review,
   security, CI, and merge evidence bound to the old head.

`ledger_series_id` links immutable revisions. Revision 1 has no predecessor and
must be `PLANNED`; a `HEAD_BOUND` ledger must be revision 2 or later with a
predecessor. Old-head evidence is retained immutably but is
ineligible for current completion. A `PLANNED` ledger cannot yield a result
eligible for aggregate completion; eligible results bind to the current
`HEAD_BOUND` ledger whose expected head equals the executed SHA. Cross-document
lineage, equality, and monotonicity are deterministic semantic checks scheduled
for the models/binding increment; JSON Schema alone cannot authenticate them.

## Gate ownership

`director_gates` and `producer_added_gates` are separate arrays.

- Director gates are mandatory policy evidence unless the Director originally
  classified one as advisory.
- A producer may propose additional required or advisory coverage.
- Producer additions cannot remove, replace, waive, reorder, rename, or weaken a
  Director gate.
- A producer proposal becomes authoritative only when the Director issues a new
  immutable ledger revision containing it.
- Once adopted as `REQUIRED`, a producer-added gate is as completion-blocking as
  a required Director gate, but it still cannot weaken a Director gate.
- Self-declared author fields are provenance data, not authentication.

`director_gate_set_digest` independently binds the ordered Director gate array;
`producer_gate_set_digest` binds the ordered adopted-proposal array. The ledger
digest changes when any gate, ordering, requiredness, path, runner binding,
dependency, abandonment rule, author, or expected head changes.

Every applicable ledger must contain at least one `REQUIRED` Director gate. An
empty required set is never vacuous success; a later aggregator must fail closed
unless schema validation and semantic binding establish that invariant.

## Gate kinds

### Runnable

A runnable gate selects an allowlisted `runner_id`, a pinned runner-definition
digest, a pre-registered parameter-profile ID and digest, a repository-relative
working directory, expected exit code, structured output matcher, timeout, output
ceiling, risk classification, dependencies, and abandonment policy.

There is no raw command, shell string, or free-form argument array. A future runner
must resolve the exact argument vector from the runner/parameter-profile registry,
reject interpreter-control flags and unregistered options, and use `shell=False`.

`repository_owned_script` additionally binds a repository-relative path and
exact Git blob SHA. Changing that script requires a new ledger and fresh approval.

### Manual

A manual gate describes the eligible evaluator roles, independence requirement,
and human or provider evidence required. It is not an executable runner and
cannot carry executable, argument, or working-directory fields. A manual `PASS`
must bind an authenticated evaluator, exact artifact/head, clean review state,
and evidence digests.

### External CI

An external-CI gate binds a repository, workflow path, workflow blob SHA and
definition digest, required check name, and trusted GitHub App identity. A result
also binds run, suite, and check IDs, exact observed head, terminal conclusion,
completion/retrieval times, and authenticated API readback. It observes existing
CI only: this contract does not authorize triggering CI. It is not a
provider-executed runner and not acceptance by itself.

## Digest rules

All contract digests use RFC 8785 canonical JSON and lowercase SHA-256 with the
`sha256:` prefix. The exact construction is
`SHA-256(UTF-8(domain) || 0x00 || RFC8785(value))`.

- `gate_definition_digest`: domain `director-gate-definition/v1`; canonical gate
  object with only that field omitted.
- `director_gate_set_digest`: domain `director-gate-set/v1`; canonical ordered
  Director gate array, including each gate-definition digest.
- `producer_gate_set_digest`: domain `producer-gate-set/v1`; canonical ordered
  adopted producer gate array, including each gate-definition digest.
- `ledger_digest`: domain `director-gate-ledger/v1`; canonical ledger object with
  only that field omitted.
- `result_digest`: domain `director-gate-result/v1`; canonical result object with
  only that field omitted.

Every other field remains inside its digest, including timestamps, expected head,
origin, requiredness, registry identity, dependencies, abandonment, environment,
redaction, and reverification data. Digests detect mutation; they are not
signatures and grant no authority.

## Result meaning

One result document represents one immutable attempt for one gate. It binds:

- task, assignment, work packet, authority snapshot, ledger revision and digest;
- gate ID and gate-definition digest;
- exact executed SHA, every material author, and an authenticated evidence actor;
- producer or independent-review role;
- timestamps, clean-worktree evidence, environment, and exact runner resolution
  where applicable;
- sanitized decisive output, status, and reverification lineage.

Evidence-actor identity is separate from resolved-runner identity. A provider,
human evaluator, or trusted external-CI principal cannot be represented as a
runner. A runnable result binds the runner registry/version/digest, definition and
implementation digests, executable fingerprint/version, canonical argument-vector
digest, and sanitized-environment profile digest.

`ABANDONED` requires a permitted reason code, authenticated actor, timestamp,
attempt count, last evidence, lost coverage, and one of `BLOCKS_ACCEPTANCE`,
`OWNER_DECISION_REQUIRED`, or `RESHAPE_REQUIRED`. It is honest terminal evidence
for that attempt and never acceptance. `FAIL`, `BLOCKED`, and `ABANDONED` never
contribute to aggregate completion. Aggregate
completion is derived only when every current required gate has a matching
`PASS` on the same HEAD_BOUND ledger and exact SHA. Even then, independent
acceptance, Codex Security, trusted CI, and deterministic merge policy remain
separate.

The `full_output_digest` uses the domain
`director-gate-sanitized-output/v1` and commits to complete sanitized streams
before persistence truncation. Its byte payload is
`"stdout\0" || uint64be(stdout_length) || stdout_utf8 || "stderr\0" ||
uint64be(stderr_length) || stderr_utf8`. Results record captured, persisted, and
omitted byte counts, the output ceiling, truncation, sanitized decisive excerpt
and digest, redaction/scanner identities and digests, counts, and categories.
Raw output is never persisted or hashed by this contract. Both redaction and secret
scanning must cover every persisted text field and pass before a result can be
`PASS`; the post-redaction secret finding count and categories must be empty.
Captured, persisted, and omitted byte counts describe sanitized bytes. The semantic
validator must prove `stdout + stderr = total`, `persisted + omitted = total`,
`persisted <= ceiling`, and that `truncated` exactly matches whether bytes were
omitted.

## Schema validity is not completion eligibility

Every result declares `director-gate-result-semantic-binding/v1`, but that value is
not self-authenticating. Before any runtime activation, the deterministic binding
increment must load the authenticated current ledger and prove all of the following:

- task, assignment, work packet, authority snapshot, repository, ledger series,
  ledger ID/revision/digest, gate ID/type/definition digest, and expected/executed
  SHA all match;
- resolved runner, registry, runner definition, parameter profile, implementation,
  working directory, and script/CI workflow identities match the gate and ledger;
- worktree before/after heads equal the expected head, and CI repository, workflow,
  trusted app, check name, and observed head equal their authenticated bindings;
- the evidence actor and provider run/readback are authenticated, and an independent
  reviewer is absent from every material-author record;
- dependencies reference current-ledger `PASS` result digests; and
- output digests, byte arithmetic, redaction, scanning, and abandonment policy are
  internally and cross-document consistent.

A schema-valid document that has not passed that validator is untrusted data and
cannot contribute to completion, review, security, CI, merge, deployment, dispatch,
or any other authority decision. PR 1 deliberately implements no such consumer;
the validator is a hard prerequisite for the separately reviewed binding increment.

## Future runner boundary

A later, separately reviewed runner may initially register:

- `python_unittest`
- `python_compileall`
- `pip_check`
- `json_schema_validate`
- `compose_config`
- `container_build`
- `git_diff_check`
- `secret_hygiene`
- `powershell_parse`
- `repository_owned_script`

`github_trusted_ci` and `manual_review` remain non-runnable gate kinds.

The future runtime must reject unknown runners, shell strings, parameter-profile or
interpreter-option injection,
directory escape, symlink/hard-link escape, wrong repository/worktree/SHA,
credential inheritance, unbounded execution/output/regex, stale evidence, and
non-atomic writeback. Sanitized process execution is not a complete sandbox;
containers or disposable environments are required when stronger isolation is
needed.

## Semantic checks deferred from JSON Schema

The next models/binding increment must enforce:

- unique gate IDs and order values;
- dependency existence, ordering, and acyclicity;
- ledger revision/predecessor lineage;
- cross-document task, assignment, digest, gate and SHA equality;
- producer additions being monotonic;
- authenticated Director issuance and provider identity;
- evidence actors differing from every material author for independent review;
- repository realpath, symlink and hard-link containment;
- result freshness and atomic evidence persistence.

Canonical repository identities and repository-relative paths already reject dot
components, `.git`, Windows device/alternate-stream syntax, absolute paths,
backslashes, repeated separators, and trailing separators. The future runtime must
still prove Git-tree membership and realpath, symlink, and hard-link containment
before every run.

The hardened runner, provider handoff, Slack status, pilots, and promotion metrics
remain later independently reviewable increments.

## Default-off and rollback rule

No Completion Gate executes until a later feature flag ships default-off and a
named assignment explicitly enables an isolated proof lane. No global Unlazy
installation, Claude Stop hook, ambient shell execution, production credential,
external write, deployment, or LangGraph writer connection is authorized here.

Removing these new documents and tests fully rolls back this contract-only
increment without affecting runtime state.
<!-- [[Codex | GPT-5.6 | 2026-08-24 | 05:14 UTC | END]] -->
