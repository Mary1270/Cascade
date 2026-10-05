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

## 10. v0.2 round (Sep 22 2026): steward-flagged rework, redeployed and re-verified live

The first portal submission (v0.1, addresses in §6) was **rejected** by a
steward with this reasoning: the score determining payment was not
independently validated against the milestone evidence — the original
`prompt_non_comparative` `criteria` only audited the leader's
self-reported JSON for internal consistency, never independently
re-checking it against the real evidence. Full rework rationale is in
`DESIGN_DECISIONS.md` §7. Summary of the code changes:

- `ReviewerConsensusPanel.evaluate_milestone` switched from
  `prompt_non_comparative` to `prompt_comparative`, so every validator
  independently fetches the evidence and computes its own score.
- `MilestoneEscrow.submit_milestone_evidence` now requires the caller to
  be the project's `contractor` (previously anyone could submit evidence
  for anyone's project).
- Added `MilestoneEscrow.reset_stuck_milestone` /
  `refund_stuck_milestone` (payer-only) as a recovery path for a
  milestone permanently stuck `awaiting_score` (this is exactly what
  happened to project 0 in §8 below, live).
- Added an `attempt` nonce per milestone (closes a race where a
  reset-then-resubmitted milestone could otherwise be scored by a stale,
  late-arriving callback from the abandoned attempt).
- A further live-only bug, found during this round's actual redeploy
  (impossible to catch offline, since it depends on real LLM output
  variance): under `prompt_comparative`, far more total LLM calls happen
  per evaluation (every validator repeats all 3 calls, not just the
  leader), and the first live attempt crashed with an uncaught
  `json.decoder.JSONDecodeError` when one call returned plain prose
  instead of strict JSON. Fixed with a regex-based fallback
  (`_extract_score_fallback`) that still resolves to a number instead of
  crashing the whole evaluation, and only raises a clean `UserError` if
  truly no number can be found.

All 28 offline tests (18 original + 10 from this rework) pass.

**Full fresh redeploy was required** (not "Upgrade code"): `panel`/
`registry`/`escrow` addresses are each set once in their respective
constructors with no setter, so changing any one of the three
contracts' code invalidates the others' stored references. All three
were redeployed and rewired from a clean slate to avoid old (v0.1)
project records interacting with new code that expects fields (`payer`,
`attempt`) those old records don't have.

**New deployed addresses:**
- `PerformanceRegistry`: `0x953e68A85DC2dB534c77e8f7C4D623669e7B74e5`
- `ReviewerConsensusPanel`: `0x685A937823100Da528f2Ec04bc2BF11788cf77e4`
- `MilestoneEscrow`: `0x1a59066981596fd26D77D7d1346ea9faAa14d98c`

**`prompt_comparative` confirmed live, directly closing the steward's
gap**: project 0's milestone was submitted with evidence quality that
made the *leader* alone compute `{"sub_scores": [70, 75, 45], "final_score": 65}`
(visible in `EquivalenceOutputs`) — but the transaction's actual
finalized `apply_score` callback carried **`score = 90`**, not 65. This
means the validators, each independently re-fetching the evidence and
re-running the three framings, did not simply rubber-stamp the leader's
self-reported number — the number that actually reached consensus came
from independent re-assessment, exactly the property the steward
required. Released amount: 0.9 GEN of the 1 GEN milestone.

**New authorization check confirmed live**: a second project (project 1)
was created, then `submit_milestone_evidence` was called from a
*different* wallet (`0x9Bf4a51B888C6FFBF337a25BF3179B07A6259128`, not the
project's contractor) and correctly rolled back with "only the project's
contractor can submit evidence".

The regex-based JSON-parsing fallback was not exercised in this specific
run (the evaluation succeeded without needing it) — it remains verified
only by the offline tests (`test_non_json_llm_response_falls_back_to_regex_extraction`,
`test_completely_non_numeric_llm_response_raises_cleanly`) pending a live
occurrence.

**Both new recovery methods confirmed live, including the exact scenario
that motivated them:**

- Project 2's milestone was deliberately submitted with a malformed
  `evidence_url` (literal quote characters typed into the Studio form,
  reproducing the same mistake that got project 0 permanently stuck in
  v0.1). `evaluate_milestone` failed with `NondetException:
  {'causes': ['MALFORMED_URL']}`, exactly as before, leaving the
  milestone stuck `awaiting_score`.
- `reset_stuck_milestone(2, 0)` succeeded, reopening the milestone
  (`status` back to `"open"`, `description`/`evidence_url` cleared).
- The milestone was resubmitted with a correct `evidence_url`. `attempt`
  correctly incremented from `1` to `2`, confirming the nonce guard
  updates on every real submission, not just the first. It resolved
  normally: `sub_scores: [70, 86, 62]`, `final_score: 75`, `released:
  0.75 GEN` — a full, real recovery from a permanently-stuck state.
- A second milestone (project 3) was stuck the same way, and this time
  `refund_stuck_milestone(3, 0)` was used instead: `status` moved to
  `"refunded"` and the 1 GEN allocation was returned to the payer via
  `emit_transfer`.
- Separately, `submit_milestone_evidence` was called from a wallet that
  was neither the project's contractor nor payer, for project 1: it
  correctly rolled back with "only the project's contractor can submit
  evidence", confirming the new authorization check on the redeployed
  contract.

## 11. v0.3 round (Sep 27 2026): second steward rejection, exact-match consensus confirmed live

The v0.2 resubmission (§10) was **rejected again**: the "final scores
within 15 points are equivalent" tolerance allowed materially different
payouts to pass the same consensus round — v0.2's own cited proof (a
leader computing 65, but a paid-out score of 90) was actually evidence of
this flaw, not of the fix working. Full rationale in
`DESIGN_DECISIONS.md` §8.

Fix: `final_score`'s rounding bucket widened from the nearest multiple of
5 to the nearest multiple of 20, and the `prompt_comparative` principle
now requires **exact** equality of `final_score` — no tolerance window at
all. Also added (not steward-mandated, proactive): `PerformanceRegistry`
and `ReviewerConsensusPanel`'s `set_escrow`, and two new
`MilestoneEscrow` methods `set_panel`/`set_registry`, are now
owner-updatable instead of call-once, so a future single-contract fix
won't require redeploying all three again (`DESIGN_DECISIONS.md` §9).
30 offline tests pass in total: 28 carried over from the v0.2 rework
(re-derived for the new bucket width) plus 2 new ones covering the
updatable setters.

**Full fresh redeploy of all three contracts was required again** (same
reasoning as v0.2 → v0.3: `ReviewerConsensusPanel`'s code changed, and
the address-updatability change touched all three). This is expected to
be the last full-redeploy round — future single-contract fixes should
only need `set_panel`/`set_registry`/`set_escrow` calls, not a redeploy
of the unaffected contracts.

**New deployed addresses:**
- `PerformanceRegistry`: `0xC5b75c32d8c5C283aAC6d131EdC6C49aF7D975D0`
- `ReviewerConsensusPanel`: `0x4DBD52C8C3524B9e94D643200717682c476636D4`
- `MilestoneEscrow`: `0xc7227c4220042b00A0D94DD62D8F28cBe2E207c4`

**Exact-match consensus confirmed live, directly closing the second
gap**: a milestone's leader computed
`{"sub_scores": [80, 90, 70], "final_score": 80}` (visible in
`EquivalenceOutputs`), and the finalized `apply_score` callback carried
**the identical `score = 80`** — not merely a close value. `Rotation
Count: 0` on both the `evaluate_milestone` and `apply_score`
transactions confirms every validator agreed on this exact number on the
first attempt, without needing a leader/validator rotation to reach
consensus. Released amount: 0.8 GEN of the 1 GEN milestone.

**New authorization check confirmed live**: `MilestoneEscrow.set_panel`,
called from a non-owner wallet, correctly rolled back with "only owner
can set panel".

**A new failure mode discovered live, and confirmed recoverable:** with
exact-match consensus, ambiguous evidence can cause the three framings
(literal/outcome/skeptical) to diverge so widely across independently-run
nodes (e.g. one run: `sub_scores: [28, 74, 45]` on one node vs `[80, 100,
50]` on a retry) that the network cannot reach exact agreement even after
retrying with different workers, and the transaction ends with status
`CANCELED` (not a clean `ERROR`) - a third distinct failure shape,
alongside the `MALFORMED_URL` `ERROR` seen in v0.2. Confirmed live:
`reset_stuck_milestone` works identically for a `CANCELED` evaluation as
for an `ERROR`'d one, since `MilestoneEscrow` only ever checks the
milestone's own `status` field, not why a callback never arrived. Two
consecutive `CANCELED` results occurred for the same ambiguous,
claim-laden evidence text; switching to a plainly-descriptive,
non-evaluative evidence text (listing only what's literally observable in
the repo, with no claims like "passing CI") produced unanimous agreement
(`sub_scores: [100, 100, 100]`) and finalized cleanly on the very next
attempt (`attempt: 3`). **Practical takeaway for future evidence text:**
prefer plain, verifiable descriptions over persuasive/claim-heavy ones -
the latter invites the skeptical framing to diverge sharply from the
outcome framing, which exact-match consensus can no longer paper over
with a tolerance window.

**Relaxed-bonus trigger condition confirmed live with real data:** after
two milestones scored 80 and 100 (recorded to `PerformanceRegistry`), the
contractor's 3-window average financial history reached 93.3 (>= the 85
threshold), confirmed via `get_recent_scores`. The relaxed bonus's exact
effect on a specific raw average (shifting which 20-wide bucket a
borderline score lands in) was not separately re-demonstrated live in
this round beyond confirming the trigger condition itself activates
correctly from real recorded history - the arithmetic of the +5 bonus
before rounding is already exhaustively covered by controlled-input
offline tests (`test_relaxed_flag_adds_bonus_before_rounding` and
`test_good_track_record_relaxes_the_next_evaluation`), which is the
appropriate place to verify it precisely; live LLM output cannot be
steered to a specific raw average on demand.


## 12. v0.4 round: live verification of the two `apply_score` settlement fixes

Context: the third steward review (an "action needed" request, not a
rejection) asked for two fixes in `MilestoneEscrow.apply_score`: reject
callback scores outside the canonical bucket set before any transfer or
history write, and give every unpaid remainder (score < 100) an explicit
terminal path. Both are implemented in v0.4 (see DESIGN_DECISIONS.md §11).
All three contracts were redeployed so the deployed code matches the
repository source exactly (the v0.4 contract files also had their long
comments removed, with no logic change).

**Deployed addresses (GenLayer Studio):**
- PerformanceRegistry: `0xFBE9a9684C6a42fa322f105089908FdaEaB1D4de`
- ReviewerConsensusPanel: `0x7ac8599894C5c22339e02383c7F0e2CFD0AaFF1E`
- MilestoneEscrow: `0x3C69372F472feD7B84edE975bd0055e42b72c363`

Wiring: `set_escrow` was called on the panel and the registry with the
escrow address. Two wallets were used: A (owner and payer,
`0x6921398611F6c4793D745348660912D44d4F8479`) and B (contractor,
`0xf73699c4A8C35a10fBFa74ca07CbEcA99b148Ffd`). Every project below used
a 1 GEN milestone allocation (`[1000000000000000000]`, deposit 1 GEN).

**Full-marks case (project 0).** Evidence: the project's own GitHub
repository, described neutrally. The leader's equivalence output was
`{"sub_scores": [100, 100, 85], "final_score": 100}` and the accepted
result was the same `final_score` of 100 (consensus: three validators
agreed, one disagreed, one went idle after quorum). `get_project`
returned `score: 100`, `released: 1000000000000000000`,
`status: scored`. One live observation worth keeping honest: while the
transaction was still in `COMMITTING` the explorer displayed a
`final_score` of 80, and the finalized result was 100. The raw
consensus data shows the leader's output was 100, so the 80 was most
likely a validator's independent output (the validator that voted
`disagree`), but the dump did not label it, so this is an inference.

**Remainder refund (fix 2), project 1.** Evidence described a milestone
requiring a web frontend and an external audit report, which the
repository does not contain. Accepted `final_score` was 40
(`sub_scores` `[50, 60, 40]`). `apply_score` (tx
`0x372b2a165172b06ba505512bc7bfd7c189d09137afed2b60997622d4735127c5`)
finalized and triggered three transactions:
- a transfer of 0.40 GEN from the escrow to wallet B (the contractor),
  tx `0x6606b3d007282b18abcb0eae1c4a09627bcad84cf6750800e7d16b3391914e43`
- a transfer of 0.60 GEN from the escrow to wallet A (the payer),
  tx `0xf6c0223dcb0cd64e9c873a9be54d34fdbebf47d0d36bbefa097cf0c61f8c8fad`
- `record_score` on the registry with value 40,
  tx `0x108c8aa050cd93b7d0822a3d39236d3f122df8e0488b7b6f2b889940a157faa0`

`get_project` returned `score: 40`, `released: 400000000000000000`. The
two amounts sum to exactly the 1 GEN allocation, so nothing is left in
the escrow. The 100-score milestone (project 0) produced no refund
transfer, as intended. Caveat: the explorer shows the two transfer
transactions with an `ERROR` execution result and `<unknown>` result
code. Both targets are plain wallets with no contract code to execute,
so this appears to be how the explorer displays a plain value
transfer, but it was not independently confirmed against wallet
balances in this round.

**Out-of-bucket rejection (fix 1), project 2.** To get a milestone
stuck in `awaiting_score` deterministically, evidence was submitted with
the malformed URL `notaurl`; `evaluate_milestone` failed with
`MALFORMED_URL` (tx
`0x23473cf70d845e24664785606459e17250c2a48317eea3c824b946208b431ae1`),
and `get_project` confirmed `awaiting_score`, `attempt: 1`. The owner
then pointed the escrow's panel at wallet A with `set_panel` (so a
direct call could stand in for the panel) and called
`apply_score(2, 0, 55, 1)` (tx
`0x29a32fba543a20b09b5f31ceb518479ad4f02bf57669cbd7777a0b19e1572ff7`).
It reverted with `Rollback` and the message "score callback is outside
the allowed canonical bucket set (0, 20, 40, 60, 80, 100) - refusing to
settle this milestone". No transaction was triggered, and a following
`get_project` showed the milestone unchanged (`awaiting_score`,
`released: 0`, `score: null`). The panel was then restored with
`set_panel` (decimal argument verified to equal the real panel address),
and the stuck milestone was recovered with `refund_stuck_milestone`
(tx `0xff671f76679d61f081cfc65f085b32fa4d4050880534607f4acda97e5ac0c48b`,
`SUCCESS`; the resulting 1 GEN transfer back to the payer was not
separately inspected).

**Smaller practical facts from this round:**
- The Studio value field is in GEN, while allocations inside
  `milestone_allocations_json` are raw 18-decimal units. A first
  `create_project` with `[100]` and a 100 GEN deposit was rejected by
  the exact-deposit guard ("deposited value must exactly equal the sum
  of milestone allocations"); `[1000000000000000000]` with a deposit of
  1 GEN worked.
- Address arguments typed into Studio appear in the decoded input of
  explorer transactions as large decimal integers. The contracts'
  `_normalize_address` already handles this form.
