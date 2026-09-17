# Cascade

A GenLayer Intelligent Contract project: a milestone escrow that releases
payment **proportional to a graded confidence score (0–100)**, instead of
an all-or-nothing pass/fail vote — a 65%-complete milestone releases 65%
of its allocated funds, not a rounded pass or fail.

See `DESIGN_DECISIONS.md` for the full architecture rationale, equivalence
principle justifications per contract, and confirmed GenVM constraints.

## Contracts

| Contract | Role |
|---|---|
| `contracts/performance_registry.py` | Append-only, policy-free history of each contractor's past scores. |
| `contracts/reviewer_consensus_panel.py` | Turns milestone evidence into a 0–100 confidence score: three independently-framed LLM readings, averaged and rounded in plain Python. |
| `contracts/milestone_escrow.py` | Holds project funds, reads a contractor's history before each evaluation, requests scoring, and releases exactly `allocation * score / 100`. |

## How the three contracts work together

1. `MilestoneEscrow.create_project` — fund a project with N milestone
   allocations (one deposit covering all of them).
2. `MilestoneEscrow.submit_milestone_evidence` — reads the contractor's
   recent score history from `PerformanceRegistry` (`.view()`), derives a
   `relaxed` flag, and requests scoring from `ReviewerConsensusPanel`
   (`.emit()`, asynchronous).
3. `ReviewerConsensusPanel.evaluate_milestone` — fetches the evidence page,
   asks the LLM for three independent 0–100 sub-scores under three
   framings, averages them in Python, applies the relaxed bonus, rounds to
   the nearest multiple of 5, and calls back `MilestoneEscrow.apply_score`
   (`.emit()`).
4. `MilestoneEscrow.apply_score` — releases `allocation * score / 100` to
   the contractor and records the score to `PerformanceRegistry`
   (`.emit()`).

Because steps 2→3 and 3→4 are asynchronous cross-contract calls, the
released amount is never available in the same transaction that submitted
the evidence — read `get_project` afterward to see the result.

## Deployment order (Studio)

1. `PerformanceRegistry()` — no constructor args.
2. `ReviewerConsensusPanel()` — no constructor args.
3. `MilestoneEscrow(panel_address, registry_address)`.
4. `ReviewerConsensusPanel.set_escrow(escrow_address)`.
5. `PerformanceRegistry.set_escrow(escrow_address)`.

## Testing

Offline tests (`tests/`) use a hand-written pure-Python stub of the
`genlayer` SDK (`tests/genlayer_stub/`) instead of `genlayer-test`'s Direct
Mode, which was confirmed (see the Vigil project's `LESSONS_LEARNED.md`)
to not execute real cross-contract calls at all. Run with:

```
pip install pytest
pytest tests/ -v
```

CI (`.github/workflows/tests.yml`) runs the same suite on every push.

Live deployment, wiring, and end-to-end verification on GenLayer Studio
are tracked in `LESSONS_LEARNED.md` (added once that testing is done).

## Diagnostics

`diagnostics/` contains a standalone two-contract pair
(`diag_scorer.py`, `diag_caller.py`) originally built to verify, live on
Studio, whether nondet execution works inside a method reached
cross-contract via `.view()`. The final architecture does not depend on
the answer (see `DESIGN_DECISIONS.md` §5) — these are kept as a smoke
test / reference for future projects, not part of the deployed system.
