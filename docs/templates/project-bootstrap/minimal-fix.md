# Minimal integration fix record

Use once for a trivial, unambiguous, reversible correction whose expected state
is already established and whose correctness an existing focused check can
prove. If the attempt fails, expands, or requires interpretation, stop and use
the normal bounded assignment workflow.

```text
MINIMAL_FIX
Issue: <concise defect>
Classification: <why every minimal-fix eligibility condition is satisfied>
Method: <deterministic_autofix | current_session_micro_fix | batched_minor_fix>
Surface: <exact files, fields, links, identifiers, or integration points>
Change: <one-sentence correction>
Validation: <exact focused check and result>
Authority change: none
Behavioral expansion: none
Candidate: <new exact SHA or artifact digest, if applicable>
Next: <continue assignment, review updated candidate, or escalate>
```

Do not use this record for architecture, business logic, public APIs, schemas,
migrations, authentication, authorization, credentials, privacy, security,
dependencies, provider contracts, budgets, permissions, deployment,
publication, merge policy, unexplained failures, destructive work, or a
serious-safety signal. A changed reviewable candidate invalidates all evidence
bound to its predecessor and must continue through fresh independent review,
security, CI, and merge gates.
