# Construction-only KEP inputs, rule v2

## Authority and boundary

WO-R1 is authorized by the owner relay in issue #299, comment 5973258630.
This extends licensed KEP preparation with a target-free input artifact. It does
not admit training rows, run a model, grade output, or open any held-out set.
The target-bearing extraction route and its historical evidence stay unchanged.

The constructor consumes the pinned public Kubernetes enhancements archive,
LICENSE, tree and metadata census. It must not invoke `construct_rows`, initialize
`HeldOutSet`, call a leak validator, access a model transport, or extract archive
paths onto disk. Python's [tarfile documentation](https://docs.python.org/3/library/tarfile.html)
distinguishes regular files and links; the constructor reads only bounded,
uniquely named regular members after validating their paths and evidence pins.

## Qualification and frozen order

Rule v2 accepts exact metadata statuses `implemented` and `implementable` and
does not filter by creation date. A positive unique KEP number identifies one
source group among status-eligible entries. Duplicate eligible positive identities
and conflicting evidence are refused. A replaced/ineligible entry and one
implemented entry with the same authenticated number retain two census decisions
but only one eligible source group; they do not veto unrelated candidates.
The authenticated YAML `kep-number`, equal to the census ID, is the identity
authority. A directory numeric-prefix disagreement receives one explicit
`DIRECTORY_ID_MISMATCH` refusal in the qualification census and aggregate counts;
that entry is never accepted, reidentified or silently dropped, and unrelated
valid entries may continue. Nonpositive IDs retain their counted refusal when
their directory prefix agrees. Before any README read, every census entry
(including directory-refused and ineligible entries) must match the number
and exact status in its independently pinned `kep.yaml` bytes and Git blob.
Every census decision retains that authenticated number, including ineligible
and nonpositive entries; a null number must not erase the audited identity.
Untrusted evidence (a hash failure, YAML/census identity or status disagreement,
or duplicate eligible positive identity) still refuses the whole run.
Metadata is inspected as YAML nodes with the existing PyYAML dependency; no YAML
object constructors run. Duplicate top-level keys, merge keys and ambiguous or
non-scalar identity fields are refused rather than reinterpreted.
All four original support sections must be present and visibly nonempty:
Non-Goals, Notes/Constraints/Caveats, Alpha Graduation Criteria, and Test Plan.
The `(Optional)` heading annotation is editorial, not a separate section.
Fake headings in comments or code fences do not qualify a source.
Comment literals inside fences do not open HTML comments; a real unterminated
comment remains invisible through EOF. Optional annotations also normalize the
Graduation Criteria parent. Closing heading hashes require preceding whitespace,
and the selected H1 title cannot also supply a mapped input section.
An inline-comment ambiguity in the first visible H1 refuses the input rather
than selecting a later H1. Fence delimiters and their info strings do not make
an otherwise empty support section nonempty; actual fenced body text may count.

Inputs also need a genuine first H1 title and a nonempty original Goals section
for the objective. Missing input facts are refused, never invented. Context
contains the original cited excerpts, with exact half-open Unicode character
spans into the verified source. Section names belong to audit metadata, not
additional model-input fields. The shared input has only `objective`,
`repository`, `title`, and `context`, as defined by `train/prompt_template.py`.

The census field `sig` is a legacy name for the original namespace: validated
`sig-*` and historical `provider-*` paths remain in their own buckets. No entry
is dropped or remapped because its namespace is a provider. Unsafe paths and
unrecognized namespace prefixes are refused before status filtering.

Freeze candidate order before reading bodies: lexicographic namespace order,
numeric KEP number and path within each bucket, one candidate per bucket each
round. The existing `sig-round-robin/numeric-id-path/v1` policy identifier is
retained with this namespace interpretation. Qualified
records keep that rank even when earlier candidates are refused. Qualification
is independent of later record clocks. Approximately 130 sources is an estimate,
not an output requirement; actual counts and every refusal must reconcile.

## Target-free protocol

The new `kep-input/v1` artifact is distinct from `kep-extraction/v1` and the
director training-row schema. A record has `disposition: construction_only`,
source identity, frozen selection rank and digests, source capture and generation
times, the shared input, and title/objective/context spans. It has no target,
target spans, grade, accepted status, train split, training row ID or model receipt.
An exact key contract rejects accidental target-bearing fields.

`inputs.jsonl` is UTF-8 with LF, one record per usable qualified source in frozen
order. Its manifest binds the exact output bytes and each line digest, the
archive/commit/LICENSE/tree/census, rule-v2 policy, qualified selection and
complete per-candidate census. The census distinguishes metadata candidates,
section-qualified sources, usable inputs and refusal reasons.

## Evidence and publication

Every source is bound to the archive, its SHA-256 and upstream Git blob. A new
v2 policy/order freeze precedes v2 source capture and qualification; historical
archive capture remains separate. Each input has its actual generation time.
Source spans must reproduce the exact verified text, and chronology cannot be
backdated or reversed. Manifest digests must not be cyclic with record timestamps.

Validate the complete artifact before publishing one new destination. Existing
outputs are refused without replacement. No usable input means no successful
published input artifact. Errors and stdout contain counts and reasons only,
never source excerpts. A dry run qualifies and validates without publishing.

The two-file output format is retained. Publication is qualified on Windows only;
other platforms, including POSIX, refuse `OUTPUT_PLATFORM_UNQUALIFIED` before
creating output directories. Dry-run qualification publishes nothing and does not
claim publication compatibility. The previous POSIX mkdir/stat/open sequence did
not establish newly created directory birth identity and is not a safe fallback.

Existing Windows ancestor identities are captured before acquisition and must
equal the acquired objects; pre-acquisition changes refuse publication. Retained
[CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
directory handles request `GENERIC_READ` (metadata-only access did not enforce
the rename-sharing restriction in the synthetic regression), use
`BACKUP_SEMANTICS` and `OPEN_REPARSE_POINT`, deliberately
omitting `FILE_SHARE_DELETE` on every ancestor and output, preventing their rename
or deletion through the publication window. Reparse points are refused. Handle
identity compares both volume serial and the full 128-bit file ID via
[FILE_ID_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_info),
without assuming Python's Windows inode width.

Every new ancestor and output directory is atomically created relative to an
already acquired parent by the documented user-mode
[NtCreateFile](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntcreatefile).
`RootDirectory` binds the parent object; a single-component name, `FILE_CREATE`
and `FILE_DIRECTORY_FILE` create one new directory and return its birth handle.
The handle requests `FILE_LIST_DIRECTORY`, `FILE_READ_ATTRIBUTES`, `SYNCHRONIZE`
and no delete sharing; `FILE_SYNCHRONOUS_IO_NONALERT` and
`FILE_OPEN_REPARSE_POINT` are used. Success must report `FILE_CREATED`.
There is no pathname `mkdir` followed by a first snapshot of a possibly
substituted object. The returned birth handle already denies rename before its
first identity query. An object inserted before creation is refused rather than
opened; an unexpected new ancestor is `OUTPUT_PATH_RACE`, and an occupied output
is `OUTPUT_EXISTS`. Staging files retain handles without delete sharing until
publication completes. Unsupported native/identity/filesystem APIs fail closed;
the actual synthetic Windows test does not qualify other filesystems or hosts.

Files are written exclusively through opened handles, flushed, and installed
without replacement using hard links. `manifest.json` is linked last as a
complete atomic commit marker; consumers ignore a directory lacking it. This
does not claim an atomic two-file directory rename, nor protection against an
actor authorized to modify file contents after successful publication. An
interruption may leave an incomplete reserved directory; later invocation refuses
it instead of deleting or replacing it. Cleanup concerns only constructor-owned
temporary filenames through the acquired destination, never recursive pathname
cleanup. Hard-link and handle API failures have no weaker pathname fallback.
Protected held-out paths and symlink/reparse artifact/output paths are refused.

## Constructor interface

`eval/prepare_kep_inputs.py` takes `--archive`, `--tree`, `--census`, `--license`,
`--policy`, `--policy-sha256`, and `--out`; `--dry-run` publishes nothing. The first
four files have immutable production pins in the tool. The policy must be frozen
before source reads, contain exactly these keys, and bind the actual new policy
bytes through the command's expected SHA-256:

```json
{
  "protocol": "kep-input-selection/v2",
  "repository": "kubernetes/enhancements",
  "commit": "f7be055669b365e9b4ab3905bda6ef385e0c84ca",
  "statuses": ["implemented", "implementable"],
  "creation_date_filter": null,
  "required_sections": ["Non-Goals", "Notes/Constraints/Caveats", "Alpha Graduation Criteria", "Test Plan"],
  "order": "sig-round-robin/numeric-id-path/v1",
  "frozen_at": "<actual explicit UTC time before construction>",
  "authority": "issue299/comment5973258630"
}
```

The citation binds the declared policy; it is not a signed owner-approval receipt.
Source/group identity and external admission still require the normal independent
review and recorded work-order gates. Goals and the four supporting sections are
context; visibly nonempty Summary and Motivation are included when present.
Missing title or Goals are input-usability refusals distinct from section
qualification. A zero-usable-input result exits nonzero and prints only its
aggregate census counts/reasons. Successful manifests retain every metadata
candidate disposition and refusal reason.

Per-record SHA-256 covers the exact JSONL line including its LF. Qualification
SHA-256 covers the canonical qualification object embedded in the manifest;
qualification includes the frozen-order digest and census but no record
generation timestamps, so there is no cyclic record hash. Source capture is the
new v2 archive-member read, not a backdated claim about historical capture.

The shared, standard-library `prompt_template` input helpers and the repository's
existing PyYAML dependency are imported; metadata node parsing executes no YAML
object constructors.
The constructor never imports the target-bearing generator or held-out validator.
Literal secret-marker screening is conservative and does not constitute a full
secret scan or admission verdict. The authorized host performs remaining scan,
review and consumer-binding checks before any model work.

The tool is qualified with synthetic archive/source fixtures only. The eventual
authorized host corpus run must recheck all pins before reading public proposal
bodies. Existing KEP-2802 extraction and v1 artifacts are preserved.

After independent review and merge, WO-R2 validates this input protocol and chooses
12 records by SHA-256 rank of `source_group`. These inputs are not accepted rows:
teacher generation, filtering, leak checks, independent grading and training
admission remain separate gates under their own work orders.
