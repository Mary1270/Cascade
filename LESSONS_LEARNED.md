# Cascade — Lessons Learned (live-verified on GenLayer Studio, Sep 18 2026)

This document records only behaviors that were **live-tested and confirmed
on Studio**, not anything assumed from documentation or from prior
projects' `LESSONS_LEARNED.md` alone.

## 1. All previously confirmed GenVM constraints held with no new failures

Every constraint recorded in Tribunal's and Vigil's `LESSONS_LEARNED.md`
reconfirmed cleanly while deploying and testing Cascade:

- 2-line header comment limit, nothing more.
- Always `raise gl.vm.UserError(...)`.
- Constructor address inputs normalized on entry.
- `TreeMap[Address, V]` (used in `PerformanceRegistry.history`) works
  exactly like `TreeMap[u256, V]`.
- A write method reached only via cross-contract calls can safely
  authorize by checking `gl.message.sender_address` against a previously
  `set_*`-stored contract address — confirmed live three separate times
  in one afternoon (§3 below), not just once.
- `.emit()` is asynchronous — every multi-step flow in this project
  (submit → evaluate → apply_score → record_score) genuinely spans
  multiple, separately-mined transactions, several minutes apart in
  practice, not just a documentation claim.

## 2. The core mechanism confirmed live end to end, twice, with two different real scores

| Project | Sub-scores (from `EquivalenceOutputs`) | Raw avg | Final score | Released |
|---|---|---|---|---|
| 1 | `[78, 92, 65]` | 78.33 | **80** | 0.8 GEN (of 1 GEN) |
| 2 | (not individually captured) | — | **40** | 0.4 GEN (of 1 GEN) |

This is the exact test named in `DESIGN_DECISIONS.md` §1 as the one that
would fail if the mechanism were secretly a binary wrapper: two
structurally identical calls (same contract, same milestone shape, same
evidence URL) produced two different, non-0/non-100 percentages, and the
GEN amount transferred was confirmed (via the escrow's on-chain balance
change) to equal exactly that percentage of the milestone's allocation —
not a rounded-to-tier amount. `ReviewerConsensusPanel`'s
`EquivalenceOutputs` for project 1 shows the raw three-sub-score JSON
(`{"sub_scores": [78, 92, 65], "final_score": 80}`), directly confirming
the leader really did run three independent framings and the deterministic
Python averaging (not LLM arithmetic) is what produced the final number.

**One real finding**: identical evidence quality is not guaranteed to
produce a similar score across different milestones with different
prompt text — project 2's evidence was arguably stronger-sounding
("Fully complete: ... wired together correctly, and all offline tests
passing") than project 1's, yet scored lower (40 vs 80). This is a
property of live LLM judgment, not a bug: the three-framing average is
working as designed (skeptical/worst-case framing pulling scores down),
but it means test scenarios that need a *specific* score range (e.g. to
trigger the relaxed-trust bonus, which needs a 3-call rolling average
≥ 85) cannot be reliably engineered live by writing more convincing
evidence text alone. The relaxed-bonus logic itself remains fully
verified — deterministically, with controlled inputs — by the offline
suite (`test_good_track_record_relaxes_the_next_evaluation` /
`test_no_track_record_uses_strict_threshold` in
`tests/test_milestone_escrow.py`), which is the appropriate place to
verify it precisely; live testing was used for what only live testing can
show (real LLM scores, real async timing, real fund transfers), not
re-proving arithmetic already covered offline.

## 3. Authorization checks confirmed live against all three cross-contract-only write methods

All three deliberately rejected a direct call from the human wallet
address instead of the expected calling contract:

| Method | Called from | Result |
|---|---|---|
| `MilestoneEscrow.apply_score` | human wallet (not panel) | `ERROR`: "only the reviewer panel can apply a score" |
| `ReviewerConsensusPanel.evaluate_milestone` | human wallet (not escrow) | `ERROR`: "only the escrow contract can request scoring" |
| `PerformanceRegistry.record_score` | human wallet (not escrow) | `ERROR`: "only the escrow contract can record scores" |

This directly confirms, a third time in this series (after Vigil's
`ReputationLedger.apply_delta`), that comparing `gl.message.sender_address`
to a previously `set_*`-stored contract address is a reliable
authorization pattern for cross-contract-only write methods on live GenVM.

## 4. Call-once and status guards confirmed live

- `PerformanceRegistry.set_escrow`, called a second time (any address),
  correctly rolled back with "escrow already set".
- `MilestoneEscrow.submit_milestone_evidence`, called again on a
  milestone already in `scored` status, correctly rolled back with
  "milestone is not open for new evidence".
- A malformed live call (project 0's evidence URL submitted with stray
  quote characters baked into the string, from a form-input mistake, not
  a contract bug) caused `gl.nondet.web.render` to fail inside the leader
  function, which correctly rolled the whole `evaluate_milestone`
  transaction back (`ERROR`, no partial state change) rather than
  corrupting state or silently proceeding with bad data — confirming
  `run_nondet`/`eq_principle` failures propagate as a full rollback, not
  a partial write, matching the same behavior already relied on in
  Tribunal/Vigil.

## 5. Deployment and wiring order confirmed live

`PerformanceRegistry` (no args) → `ReviewerConsensusPanel` (no args) →
`MilestoneEscrow(panel_address, registry_address)` →
`ReviewerConsensusPanel.set_escrow` → `PerformanceRegistry.set_escrow`.
All five steps returned `SUCCESS`/`Accepted` on the first attempt.

## 6. Final deployed addresses for Cascade (Studio, Sep 18 2026)

- `PerformanceRegistry`: `0x78D2d8A42d709d25195a6A6665dBB911F1Cf654e`
- `ReviewerConsensusPanel`: `0xa1542fc6e006F13201fE57eCAf06D827b81C4524`
- `MilestoneEscrow`: `0xc5af9d7a67dde05608F397e18Aa8EE8bD3a4b3CC`

## 7. Multi-milestone isolation and the deposit guard confirmed live

A fourth project (project 3) was funded with **two** milestones
(`[500000000000000000, 500000000000000000]`, i.e. 0.5 GEN each). Only
milestone 0 was submitted for evidence. After the full async chain
completed:

```
milestone[0]: score=45, released=0.225 GEN (45% of 0.5 GEN), status="scored"
milestone[1]: released=0, status="open", description="", evidence_url=""
total_released = 0.225 GEN
```

This confirms `apply_score`'s proportional calculation is scoped to the
individual milestone's own allocation (not the project total), and that
submitting evidence for one milestone has zero effect on any other
milestone in the same project — each stays independently `open` until
its own evidence is submitted.

Separately, `create_project` was called with `milestone_allocations_json
= [1000000000000000000]` (1 GEN) but an actual deposited `Value` of 2
GEN. This correctly rolled back with "deposited value must exactly equal
the sum of milestone allocations" and created no project record — the
exact-match funding guard works as designed under a real payable call,
not just in the offline stub.

## 8. Studio form-input gotcha (not a GenVM/contract bug — worth recording anyway)

The Studio "run-debug" write-method form takes each `str` parameter
as-typed, with no implicit quoting. Typing a value WITH surrounding quote
characters (as if writing a Python string literal, e.g. `"https://..."`)
sends those literal quote characters as part of the string value, not as
delimiters. This caused one live milestone (project 0) to be submitted
with a corrupted `evidence_url` (leading/trailing `"` characters baked
into the URL), which made `gl.nondet.web.render` fail and left that
milestone permanently stuck in `awaiting_score` (the contract has no
retry/reset path for a failed evaluation, by design — see
`DESIGN_DECISIONS.md`). No contract code change was needed; the fix was
purely typing plain, unquoted text into the form fields for subsequent
projects (1, 2, and the authorization/guard tests). Future projects
using this same Studio form should type string parameters bare, never
wrapped in quotes.

## 9. Offline test harness — no changes needed, but one real gap found and fixed while actually running it

The `tests/genlayer_stub/genlayer/__init__.py` stub, reused directly from
Vigil, was missing `@gl.public.write.payable` support (`AttributeError:
'function' object has no attribute 'payable'`) — Vigil never had a
payable method, so this path was never exercised. Fixed by giving
`gl.public.write` a small `_WriteDecorator` class with both `__call__` and
a `.payable` staticmethod, both wrapping the same underlying method
(the stub doesn't simulate balance holding, so payable behavior only
depends on `gl.message.value`, which tests already set directly via
`set_value()`). Found by actually executing all 18 offline tests with a
minimal manual harness (this sandbox had no network to install real
`pytest`), not by inspection — confirming the standing practice of always
running tests rather than only syntax-checking them. All 18 tests passed
after the fix; the real GitHub Actions CI run (`pytest`, with network
access) subsequently confirmed the same 18/18 pass live.
