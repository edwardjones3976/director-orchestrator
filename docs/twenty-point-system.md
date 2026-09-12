<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:05 UTC | BEGIN]] -->
# The two twenty-point controls

The Director has two separate measures: **who receives the next assignment** and
**whether the project has proved its required outcomes**. A good allocation does
not prove a working product, and a completion score does not grant permission to
dispatch, merge or spend.

## Assignment balance

The [engineering ledger](engineering-workload-ledger.md) records actual bounded
assignments in one local SQLite journal. On 2026-09-12 the owner chose to keep the
existing five-provider engineering allocation:

| Allocation lane | Target | Target count in twenty | Hard maximum in twenty |
| --- | ---: | ---: | ---: |
| OpenAI | 40% | 8 | 9 |
| Claude | 25% | 5 | 6 |
| Cursor | 15% | 3 | 4 |
| Direct Grok | 10% | 2 | 3 |
| Gemini / Antigravity | 10% | 2 | 3 |

The normal tolerance is three percentage points; the hard ceiling is the target
plus five. Each assignment contributes five percentage points. Failed, cancelled,
reserved and ambiguous work stays in history. Retries belong to the same
assignment and cannot erase its cost or create a new allowance. Pure coordination
is recorded separately.

Actual **Grok Bot is a separate product** from direct Grok and from Cursor's Grok
models. The current allocation has no Grok Bot binding. The journal reports that
gap instead of attributing its work to a different product. Product identity,
allocation and reviewer eligibility are separate fields.

The journal is not populated with guessed past work. A new installation either
imports an attested complete last twenty or starts at an explicitly recorded
quiescent boundary with earlier history marked unknown. Empty slots are visible;
they are not invented assignments. Local admission is enforced by transactions;
external launchers must consume the resulting permit immediately before actual
submission. The journal cannot intercept an unrelated browser or agent tool.

## Completion evidence

The [completion scorecard](twenty-check-completion-scorecard.md) contains twenty
fixed outcomes covering routing, intake, Slack delivery, primary ownership,
recovery, independent evidence, workload, budgets and a successful paid result.
It generates a readable report and matching JSON from one bounded collection.

Each check is **PASS**, **FAIL** or **UNKNOWN**. Only a fresh observation from an
implemented source collector can earn a point. An unavailable collector, copied
receipt, stale observation or source mismatch cannot. Checks stay visible while
their live verification is unfinished.

```
verified score = count of current PASS checks / 20
verified percent = 5 * count of current PASS checks
```

This percentage describes evidence coverage, not an estimate of hours remaining.
The report keeps the reviewed candidate separate from the image actually running.
Tests of a candidate do not prove that candidate is deployed. An offline simulated
result cannot qualify as a real paid result, and unknown billing cannot become
zero cost.

## Operator entry points

Run the commands from the selected Director worktree with its normal Python
environment and `PYTHONPATH=src`:

```powershell
$env:PYTHONPATH = 'src'
python scripts/engineering-workload.py --db <existing-journal.sqlite3> --human status
python scripts/director-scorecard.py --candidate-sha <full-commit-sha> --live --github --ledger <existing-journal.sqlite3> --output-dir <report-directory>
```

Reading an absent journal reports unknown history; it does not create or reset a
database. Follow the ledger document for enrollment and bounded admission, and the
scorecard document for supported authoritative collectors. Both tools preserve
existing policy, security, exact-candidate and spending gates.
<!-- [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 09:05 UTC | END]] -->
