# Cascade — Diagnostic contracts: how to test on Studio

Answers two open questions from `DESIGN_DECISIONS.md` §5 before the real
`ReviewerConsensusPanel` / `MilestoneEscrow` / `PerformanceRegistry` are
written.

## Deploy order

1. Deploy `diag_scorer.py` (`DiagScorer`) — no constructor args.
2. Copy its deployed address.
3. Deploy `diag_caller.py` (`DiagCaller`) with constructor arg
   `scorer_address` = the address from step 2.

## Calls to make (copy-paste parameters)

| # | Contract | Method | Args | What it checks |
|---|---|---|---|---|
| 1 | DiagCaller | `request_score` | `evidence="milestone A: 3 of 4 acceptance criteria met, deploy log at https://example.com/log1", force_mismatch=False` | Diagnostic 1: does the call complete at all (nondet-in-view-cross-contract works), and does the validator accept a *consistent* score? |
| 2 | DiagCaller | `get_last_result` | *(none)* | Read back result of call #1 |
| 3 | DiagCaller | `request_score` | `evidence="milestone A: 3 of 4 acceptance criteria met, deploy log at https://example.com/log1", force_mismatch=True` | Diagnostic 2: does the validator reject/flag a deliberately mismatched final_score, or silently accept it? |
| 4 | DiagCaller | `get_last_result` | *(none)* | Read back result of call #3 |

## How to read the results

**Diagnostic 1 (nondet inside a cross-contract `.view()`):**
- Call #1 returns a JSON string like
  `{"sub_scores": [62, 70, 66], "raw_average": 66.0, "final_score": 65}`
  → **confirmed working**, proceed with `ReviewerConsensusPanel` as a
  `@gl.public.view`, as designed.
- Call #1 raises/traps (e.g. `SystemError`, `VM_ERROR`, or the transaction
  fails silently with `last_call_ok` staying `False`) → **forbidden**,
  same as the already-known `run_nondet`-inside-nondet trap. Fallback:
  make `get_confidence_score` a `@gl.public.write`, have `MilestoneEscrow`
  read the score from an `.emit()` result under the same async-write
  handling already confirmed in Tribunal (§5–6 there): the score cannot be
  used in the same transaction that requests it, so `MilestoneEscrow` must
  split "request scoring" and "apply the score" into two separate calls.

**Diagnostic 2 (does `prompt_non_comparative` audit a numeric average):**
- Call #3's `final_score` still comes back as `65` (or any value equal to
  `round(raw_average / 5) * 5`, i.e. the validator forced a correction) →
  **confirmed**, the criteria-based numeric audit in `diag_scorer.py` is
  reliable enough to reuse as-is in `ReviewerConsensusPanel`.
- Call #3's `final_score` comes back as `95` (i.e. the +30 corruption went
  through unchallenged) → **not reliably audited**. Fallback: keep the
  explicit "recompute the average yourself from sub_scores" wording (it's
  already in the criteria here) but additionally have the *leader* itself
  recompute and overwrite `final_score` from `sub_scores` as a second,
  deterministic guard before returning — i.e. don't rely on the validator
  alone to catch it; make the leader's own output self-consistent by
  construction, and only use the validator to catch injected/adversarial
  drift, not to fix ordinary arithmetic.

Record whichever outcome actually happens, for both diagnostics, back into
`DESIGN_DECISIONS.md` §5 before writing the three main contracts — that
section is explicitly left open pending these results.
