# Cascade — Design Decisions

## 0. Context and goal

Fourth project, after AccreditationCheck (accepted, 100 points), Covenant
(three escrows, strictness tuned to evidence type), and Tribunal (three-
contract adjudication pipeline with real cross-contract memory, deployed
live and verified end-to-end). A fifth project, Vigil (reputation-gated
fact-verification oracle, three contracts), was built in parallel and
finished first; its confirmed findings are folded in below (§4) rather
than rediscovered. Every prior multi-contract project shares one
structural trait: **every judgment they ever produce is binary or
categorical** (fulfilled/not, BREACH/NO_BREACH, CONFIRMED/REJECTED). The
explicit lesson going into Cascade: reusing that same binary-verdict
skeleton with new names is not a new mechanism, even if the contracts are
wired together the way Tribunal's or Vigil's were. Cascade must produce a
judgment of a fundamentally different *shape* — a continuous, gradable
quantity — not just a new binary decided by a new prompt.

## 1. The new mechanism

**A milestone escrow that releases payment proportional to an aggregated
confidence score (0–100), not an all-or-nothing vote.** If a milestone is
assessed at 65% complete, 65% of that milestone's allocated funds are
released — nothing is rounded up to "pass" or down to "fail." This is a
different consensus *primitive* from Tribunal/Vigil, not a different
consensus *topic*: their equivalence principles all converge on a label
drawn from a small fixed set; Cascade's must converge on a number from a
continuous range.

The second new element is that the score is not evaluated in a vacuum: a
contractor's track record (from `PerformanceRegistry`) is read live,
*before* judging a new milestone, and is allowed to change how the new
evidence is read. Tribunal's `PrecedentRegistry` fed prior verdicts back in
as *context for an LLM prompt*; Vigil's `ReputationLedger` fed a
reputation number back in to pick which *tier* (and therefore which
equivalence principle) applied. Cascade's `PerformanceRegistry` does
something different from both: it feeds prior scores back in as a
deterministic *bonus applied in plain Python* to a continuous score,
computed after the LLM's three sub-scores come back, not before tier
selection and not as prompt context.

Three primitives, and why none of them stands alone:
- **MilestoneEscrow** alone is just an escrow with a fuzzy release rule —
  meaningless without a real scorer behind it.
- **ReviewerConsensusPanel** alone is an unused scoring function with
  nothing to pay out — a "hello, LLM" wrapper, exactly what the portal
  excludes.
- **PerformanceRegistry** alone is an append-only log — inert unless
  something both writes to it after every milestone and reads from it
  before the next one, changing behavior.

The test that would fail if this cycle were faked (implemented in
`tests/test_milestone_escrow.py::test_good_track_record_relaxes_the_next_evaluation`
and `test_no_track_record_uses_strict_threshold`, and end-to-end in
`tests/test_end_to_end.py`): identical raw LLM sub-scores (61, 61, 61) are
submitted twice — once for a contractor with no history, once for a
contractor with three prior scores averaging ≥ 85 — and the released
amount is asserted to differ (60% vs 65% of the milestone). A second test
that would fail under a disguised binary wrapper: at least one milestone
in the end-to-end run resolves to a percentage that is neither 0 nor 100,
and the GEN amount transferred is asserted to equal exactly
`allocation * score // 100`, not a rounded-to-tier amount.

## 2. The three contracts

### ReviewerConsensusPanel — the graded-score primitive
**What it does:** given milestone evidence (description + URL) and a
`relaxed` flag, the leader fetches the evidence page once, then asks the
LLM for three independent 0–100 completion scores under three different
framings (strict-literal, outcome/impact, skeptical worst-case). **The
three-way average and the multiple-of-5 rounding are computed in plain
Python inside the leader function, never asked of the LLM.** The LLM only
ever produces the three independent sub-scores; the arithmetic that turns
them into a final number is ordinary, deterministic code.

**Why this matters for the two open questions in the original design
draft:** the original plan considered making the score-then-average step
itself something the LLM does, with the equivalence-principle validator
auditing whether the LLM's own arithmetic was correct. That would have
made correctness depend on an unverified capability (a `prompt_non_comparative`
validator reliably catching a numeric mismatch from criteria text alone —
see the standalone diagnostic contracts, `diag_scorer.py`/`diag_caller.py`,
kept in `diagnostics/` as a smoke test but no longer load-bearing). By
having Python do the averaging instead, `prompt_non_comparative`'s
`criteria` only needs to check simple bounds (right number of sub-scores,
each 0–100, final score a multiple of 5 and within a fixed tolerance of
the mean) — a much lower bar than "audit LLM arithmetic," and one already
exercised successfully by Tribunal/Vigil's non_comparative criteria for
qualitative checks.

**Why `prompt_non_comparative` and not `prompt_comparative` or
`strict_eq`:** `prompt_comparative` is built to check that independent
top-level generations converge on the same value — it fits Tribunal's/Vigil's
"do two or three labels agree" case, but here there is one leader
computation (which itself makes three internal LLM calls) whose output
needs a bounds-and-consistency audit, not a cross-generation comparison.
`strict_eq` is inapplicable — nothing about a graded score is expected to
be byte-identical across runs.

**Interaction shape — `@gl.public.write`, called via `.emit()`, not
`.view()`:** the original design draft considered exposing scoring as a
`@gl.public.view` so `MilestoneEscrow` could get the score back
synchronously in the same transaction. That shape has never been
exercised by any project in this series — Tribunal and Vigil only ever
called `.view()` on *plain, non-nondet* read methods, never on a method
that itself runs `run_nondet`/`eq_principle` internally. Rather than
depend on unconfirmed GenVM behavior for the architecture's central call,
**Cascade is built entirely on interaction shapes every prior project has
already proven live**: `evaluate_milestone` is a normal `@gl.public.write`
that runs its nondet block exactly the way Tribunal's `_resolve_medium`/
`_resolve_high` and Vigil's tier-based verdicts already do, called via
`MilestoneEscrow`'s `.emit()` (async, fire-and-forget), and it reports its
result back with a *second*, independent `.emit()` call to
`MilestoneEscrow.apply_score` — the same request/callback shape Tribunal
already used for `request_appeal` → `AppealsCourt.file_appeal` →
(refund) `.emit_transfer()`. The `diag_scorer.py`/`diag_caller.py`
contracts in `diagnostics/` remain available to test the `.view()`+nondet
shape live if a future project wants the lower-latency single-transaction
version, but Cascade does not depend on the answer.

### MilestoneEscrow — proportional release, trust-adjusted, async callback
**What it does:** holds funds for a project with N milestones (funded
up front via `create_project`, payable, one deposit covering every
milestone's allocation). When evidence for milestone *i* is submitted
(`submit_milestone_evidence`), it first reads the contractor's history
from `PerformanceRegistry` with a real `.view()` (a plain deterministic
read, the same already-proven shape as Tribunal's precedent lookup and
Vigil's reputation lookup), derives a `relaxed` flag from that history,
marks the milestone `awaiting_score`, and requests scoring from
`ReviewerConsensusPanel` via `.emit()`. Because that call is async, the
score is not available yet when `submit_milestone_evidence` returns — it
arrives later via `apply_score`, called back by the panel, which computes
`allocation_i * score / 100`, releases it via `emit_transfer`, and records
the outcome to `PerformanceRegistry` via its own `.emit()`. All four
`gl.get_contract_at(...)` calls across the system happen in the plain
body of a `@gl.public.write` method, strictly outside any
`run_nondet`/`eq_principle` block.

**Why proportional release is the escrow's job and not the panel's:** the
panel's only responsibility is producing a trustworthy number; converting
that number into an actual fund transfer, and deciding what fraction of
which milestone's allocation it applies to, is inherently escrow-specific
state (allocations, already-released amounts, per-milestone status) that
has no business living in a stateless scoring contract. Keeping this
split means `ReviewerConsensusPanel` stays reusable by a future project
that wants graded scoring without an escrow attached.

**Why `record_score` is called by the escrow, not the panel:** an earlier
sketch had the panel write directly to `PerformanceRegistry`, which would
have required the panel to also hold a registry address and pass an
authorization check there. Having the escrow do it instead (right after
`apply_score` computes the release) means `ReviewerConsensusPanel` needs
to know only one address (the escrow, for its own caller-authorization
check and its callback), keeping the panel's responsibility strictly to
"turn evidence into a number."

### PerformanceRegistry — append-only history, read before judgment, policy-free
**What it does:** stores an append-only per-contractor list of past
scores (`TreeMap[Address, str]`, JSON-encoded list — confirmed live in
Vigil that `TreeMap` keyed by `Address` works exactly like `TreeMap` keyed
by `u256`, so this is not a new risk). Exposes a deterministic
`get_recent_scores(contractor, count)` view. `record_score` and the
address-normalization/authorization pattern are restricted to the escrow
contract's address — the same "cross-contract-only write, authorized by
comparing `sender_address` to a previously `set_*`-stored address"
pattern confirmed live in Vigil's `ReputationLedger.apply_delta`.

**Why the threshold policy lives in `MilestoneEscrow`, not here:** the
decision is that `PerformanceRegistry` stays a pure data store with zero
policy — it neither computes an average nor decides what counts as "good"
history. `MilestoneEscrow` pulls the raw recent scores (`TRUST_WINDOW = 3`)
and applies the policy itself: if the last three recorded scores average
`TRUST_AVERAGE_THRESHOLD = 85` or above, a `+5` bonus is added to the
panel's raw average before the multiple-of-5 rounding (implemented,
tested); otherwise the raw average is used unmodified. Keeping the policy
in `MilestoneEscrow` means `PerformanceRegistry` can be reused unmodified
by a future project with a completely different trust policy — the same
reasoning Tribunal used to keep `PrecedentRegistry` policy-free.

## 3. What is deliberately NOT built

No thin "ask the AI to grade this 0–100" wrapper with no downstream
consequence — the score is only meaningful because it deterministically
gates a real fund transfer and is fed back into future strictness. No
generic multi-milestone project-management CRUD beyond what's needed to
demonstrate the mechanism (no task assignment, no dispute path — that
would just be re-deriving Tribunal). No fourth contract added merely to
raise the count, per the standing lesson from Penumbra's low score
(Tribunal §3).

## 4. Confirmed GenVM constraints inherited from AccreditationCheck, Covenant, Tribunal, and Vigil (not rediscovered)

- Contract file header must be exactly two comment lines, nothing else
  immediately after — even one extra line causes `VM_ERROR: invalid_contract`
  with empty stdout/stderr:
  ```
  # v0.1.0
  # { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
  ```
- Always `raise gl.vm.UserError(...)`, never bare `UserError`.
- `gl.vm.run_nondet(leader_fn, validator_fn)` — positional only.
- Every validator calls `gl.vm.unpack_result(...)` on the leader result
  before using it.
- Constructor address args are normalized (`_normalize_address`, handles
  raw `int`/`str`/`Address`).
- No raw `int` for persistent fields — use `u256`/`i32`/etc.
- Strip a markdown fence from LLM JSON output before `json.loads`.
- `gl.eq_principle.prompt_comparative(fn, principle)` — second arg
  positional, not `task=`.
- `gl.eq_principle.prompt_non_comparative(fn, *, task, criteria)` —
  keyword-only, as documented.
- All `gl.get_contract_at(...)` calls (`.view()`/`.emit()`) happen strictly
  **outside** any `run_nondet`/`eq_principle` block in the *caller*.
- `.emit()` (with or without `emit_transfer`) is asynchronous — an
  immediate same-transaction `.view()` of the written-to contract still
  shows the old value.
- `gl.message.sender_address`, inside a contract reached via `.emit()`, is
  the calling *contract's* address, not the original human sender.
- `gl.message.value` is 18-decimal (wei-like).
- Time source: `https://www.cloudflare.com/cdn-cgi/trace`, not
  `worldtimeapi.org` (410).
- GEN transfer: `gl.get_contract_at(addr).emit_transfer(value=amount)`.
- `TreeMap[Address, V]` deploys and reads/writes correctly, same as
  `TreeMap[u256, V]` — confirmed live in Vigil (`PerformanceRegistry.history`
  relies on this).
- A write method reached only via cross-contract calls can safely
  authorize by checking `gl.message.sender_address` against a previously
  `set_*`-stored contract address — confirmed live in Vigil (`PerformanceRegistry.record_score`
  and `MilestoneEscrow.apply_score` both rely on this).

## 5. Why the two originally-planned live diagnostics are no longer blocking

The initial design draft flagged two questions to verify on Studio with a
small diagnostic contract pair (`diagnostics/diag_scorer.py` and
`diagnostics/diag_caller.py`, kept in the repo as a standalone smoke test)
before writing the three main contracts:

1. Whether `run_nondet`/`eq_principle` can execute inside a method reached
   cross-contract via `.view()`.
2. Whether a `prompt_non_comparative` validator can be relied on, via
   criteria text alone, to catch a numeric average that doesn't match its
   own reported sub-scores.

**Neither question is load-bearing for the architecture actually built**
(§2): `ReviewerConsensusPanel.evaluate_milestone` is a `@gl.public.write`
called via `.emit()` — the same shape already proven live by Tribunal and
Vigil — which makes question 1 moot. And the three-sub-score average is
computed by Python, not the LLM, which means question 2's failure mode
(an LLM silently getting its own arithmetic wrong and a validator failing
to catch it) cannot occur — `prompt_non_comparative`'s criteria here only
audits simple bounds, not arithmetic correctness. This was a deliberate
choice to build on already-confirmed GenVM behavior rather than spend a
live round-trip confirming a shape the final design doesn't need. The
diagnostic contracts are kept in the repo, undeployed, in case a future
project wants the lower-latency `.view()`-based shape and needs the
answer to question 1.

## 6. Architecture consequences of the request/callback design

- `MilestoneEscrow.submit_milestone_evidence` cannot return a score or a
  released amount — it only starts the process. Callers must read
  `get_project` after the fact (or watch for the panel's/escrow's
  transactions in the explorer) to see the result, exactly as Tribunal's
  appeal flow and Vigil's dispute flow already required.
- Every milestone has an explicit `status` field (`open` → `awaiting_score`
  → `scored`) specifically so `apply_score` can reject a stray or repeated
  callback, and so `submit_milestone_evidence` can reject new evidence
  for a milestone that's already mid-flight.
- `ReviewerConsensusPanel` and `PerformanceRegistry` each need to know
  only the escrow's address (not each other's), keeping the dependency
  graph a simple star around `MilestoneEscrow` rather than a triangle.

## 7. Status

Design finalized; all three contracts written
(`contracts/performance_registry.py`, `contracts/reviewer_consensus_panel.py`,
`contracts/milestone_escrow.py`); offline test suite written and actually
executed (18/18 passing) against a hand-written stub of the `genlayer` SDK
reused from Vigil (`tests/genlayer_stub/`), including the end-to-end
scenario in §1. One real gap was found and fixed while running these
tests: the reused stub had no `@gl.public.write.payable` support (never
exercised by Vigil, needed here for `create_project`) — fixed in
`tests/genlayer_stub/genlayer/__init__.py`. Not yet done: live deployment
and wiring on Studio, the live end-to-end test, and `LESSONS_LEARNED.md`
(which will only record what live testing actually confirms).
