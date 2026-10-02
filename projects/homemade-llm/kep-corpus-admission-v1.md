# Kubernetes Enhancement Proposals: corpus admission v1

Status: draft; no corpus admission, accepted training rows or training release.

Work order: [#407](https://github.com/edwardjones3976/director-orchestrator/issues/407).

## Frozen source and selection

- Public source: `kubernetes/enhancements` at `f7be055669b365e9b4ab3905bda6ef385e0c84ca`.
- Apache-2.0 LICENSE SHA-256: `b40930bbcf80744c86c46a12bc9da056641d722716c378f5659b9e555ef833e1`.
- Source archive SHA-256: `da037ba8c8e68aa7033d256beff5da7cf3605b83678d46b0f6c403947c574618`.
- Metadata-only census: 648 records; 227 implemented with creation date before 2023-01-01. Section qualification has not been performed; these are not 227 admissible rows.
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