# Kubernetes Enhancement Proposals: corpus admission v1

Status: draft; no corpus admission, accepted training rows or training release.

Work order: [#407](https://github.com/edwardjones3976/director-orchestrator/issues/407).

## Frozen source and selection

- Public source: `kubernetes/enhancements` at `f7be055669b365e9b4ab3905bda6ef385e0c84ca`.
- Apache-2.0 LICENSE SHA-256: `b40930bbcf80744c86c46a12bc9da056641d722716c378f5659b9e555ef833e1`.
- Source archive SHA-256: `da037ba8c8e68aa7033d256beff5da7cf3605b83678d46b0f6c403947c574618`.
- Metadata-only census: 648 records; 227 implemented with creation date before 2023-01-01. These are metadata candidates, not 227 admissible rows; the section qualification result is recorded below.
- Selection policy, frozen before proposal target reads: `15d7d9970e8085962b2e8b0e89e23c6b39c5731ca55e01ce75a4894ee52ed3de`.
- Sort SIG names lexicographically and KEP IDs numerically within SIG; take one candidate per SIG each round, stride 1. Preserve every skipped candidate and reason. Do not select by grading results.
- Require nonempty Summary, Motivation, Goals, Non-Goals, Notes/Constraints/Caveats, Alpha Graduation Criteria and Test Plan. One KEP ID is one group across versions.

## Extraction contract

The owner approved cited original supporting sections at both training and inference. Input carries original excerpts, without answer JSON or grader annotations. Targets are selected, itemised source text, with exact source spans and preserved qualifiers. Do not rewrite targets, invent obligations, treat examples as requirements or paste an entire section as one answer.

Map Non-Goals to `non_goals`; actual Notes/Constraints/Caveats limits to `constraints`; Alpha Graduation Criteria to `acceptance_checks`; Test Plan evidence items to `evidence_requirements`. Reject a row if any field is empty after unsupported items are removed.

Use an explicit `licensed_extraction` provenance route and a hashed extraction record. No model name, inference receipt or real-history label may be fabricated. Existing source/context integrity, scrub, schema, canonical row identity and leak checks still apply.

## Acceptance and scope

First deliverable: one candidate row. Two independent whole-row primary graders are frozen as xAI `grok-4.6` and Z.ai `glm-5.3`, tools off. Both complete authenticated ACCEPTs are required; disagreement rejects the row. First-row grading is conditional on independent corpus review and validated packet/digest. No grading call has been made.

Preserve all protected evaluation sets; the sole custodian returns aggregate leak evidence covering Set A, historical Set B and prospective Set B. The source writer does not read protected reference bodies. One accepted row does not release training or the 96-row batch. The audit is capped at 12 proposals; later expansion keeps its separate gates and existing budget subcaps.

Model manifest approval and integrity incident evidence are recorded separately. Extraction makes zero inference calls and needs no GPU. The current GPU recovery hold remains in force for baseline, probe and training operations.

## First qualified source and immutable construction evidence

The section check follows the frozen order and records every skip. It first qualified KEP-2079, `keps/sig-network/2079-network-policy-port-range/README.md`, after 179 earlier proposal bodies lacked one or more required sections. This is one qualified source, not a completed 12-row audit or a claim that 96 KEPs qualify.

- Source SHA-256: `a17070c7d198a977fe4bb817b3426214841ce14aa25613166c441c11ba8312d3`.
- Source Git blob: `7ea711c31f674af1db783e00105fe8bb035df790`, recomputed from the exact bytes and verified against the pinned tree.
- Corpus candidate manifest SHA-256: `d3e82b4f9a8af3bb25e4765e5ac2f16855c52efbcbf4f65391e741d1e39c42d4`; 224 positive, unique KEP IDs from the 227 metadata candidates. Three legacy zero IDs are excluded because they cannot identify independent source groups. Capturing and hashing these candidates does not establish section qualification or row acceptance.
- Construction-only admission SHA-256: `efaf1df009bae28ea6b57b72abc19f5c8864120e74de45e512533ab9cbc46ba9`.
- Selected extraction-record SHA-256: `c5ee0c1a962bcff4bc106b4f9b25092530d9b2c4bb151898d80ef6d39b9f6185`.

The provisional extraction has two non-goals, one caveat retaining the word “might,” three Alpha criteria, and three prerequisite test duties retaining the CNI data-path caveat. Later coverage measurements and observed flakes are not test requirements and are excluded whole. Nine of nine selected items are exact original spans also supplied in context; context is 2,884 characters. Containment is a diagnostic, not a target percentage.

Public capture artifacts remain local until independent corpus review; the pinned upstream path and blob provide reproducible source evidence. Whole-row grading, three-set custodian leak clearance and training release remain unproven. No inference or grading call was made during construction. A source/schema pass cannot stand in for those gates.
