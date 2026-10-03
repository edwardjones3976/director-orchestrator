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
source group. Duplicate identities and conflicting evidence are refused.
All four original support sections must be present and visibly nonempty:
Non-Goals, Notes/Constraints/Caveats, Alpha Graduation Criteria, and Test Plan.
The `(Optional)` heading annotation is editorial, not a separate section.
Fake headings in comments or code fences do not qualify a source.

Inputs also need a genuine first H1 title and a nonempty original Goals section
for the objective. Missing input facts are refused, never invented. Context
contains the original cited excerpts, with exact half-open Unicode character
spans into the verified source. Section names belong to audit metadata, not
additional model-input fields. The shared input has only `objective`,
`repository`, `title`, and `context`, as defined by `train/prompt_template.py`.

Freeze candidate order before reading bodies: lexicographic SIG order, numeric
KEP number and path within each SIG, one candidate per SIG each round. Qualified
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

The tool is qualified with synthetic archive/source fixtures only. The eventual
authorized host corpus run must recheck all pins before reading public proposal
bodies. Existing KEP-2802 extraction and v1 artifacts are preserved.

After independent review and merge, WO-R2 validates this input protocol and chooses
12 records by SHA-256 rank of `source_group`. These inputs are not accepted rows:
teacher generation, filtering, leak checks, independent grading and training
admission remain separate gates under their own work orders.
