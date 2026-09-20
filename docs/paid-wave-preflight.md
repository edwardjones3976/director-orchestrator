<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | BEGIN]] -->
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | BEGIN]] -->
# Paid wave setup preflight

Paid primary dispatch selects only cap-safe dependency-ready assignments, constructs
real work packets using accepted upstream outcomes, and checks the entire selected
wave before any ACTIVE reservation. A missing, mismatched, expired or exhausted
signed budget keeps every selected assignment PENDING. The offending assignments
carry the fixed `paid_setup_required` reason; aggregate/checkpoint/projection expose
that reversible setup hold. The existing provider interrupt remains nonterminal.
The API TaskStatus enum has no PAUSED value, so task presentation uses BLOCKED with
an explicit setup explanation. Existing approval evidence is retained; normal
recovery retries readiness without manufacturing a new approval or provider result.

Budget checks reuse signed budget and attempt-limit validators plus read-only
conservative accounting queries. Combined selected-wave reservations count against
shared task/project/day limits. Reads use the existing caller connection, not a
nested funding pool checkout. They do not sign, register, reserve money or create
provider-attempt rows. Atomic attempt admission still revalidates authority and
spending. Changes after preflight can still cause the existing conservative ACTIVE
ambiguity; this patch never releases or replays such a reservation.

The planned attempt descriptor is explicitly not an admitted transport context.
Both preflight and actual admission share the canonical attempt digest function.
Only the selected primary format is preflighted, never speculative recovery formats.
A required or attached Responses meter needs the transport-owned pure `preflight`
interface. Missing interface produces a setup hold before reservation. No meter
producer, signature, API account or token evidence is synthesized.

Integration limits: fresh immutable meter selection and per-attempt pinning require
the separately owned transport/resolver implementation. A void preflight method
alone is not proof of that integration. Team-enabled Antigravity currently holds
because the existing team admission invokes a runtime query factory; a reviewed
pure signed-team readiness interface is still needed. Team-off normal providers
remain eligible with real funded readiness. Offline doubles do not acquire a paid
preflight callback. No live runtime or workflow deployment is part of this patch.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 18:45 UTC | END]] -->

<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-14 | 06:14 UTC | END]] -->
