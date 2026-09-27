# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
import json
import re


def _normalize_address(value) -> Address:
    if isinstance(value, Address):
        return value
    if isinstance(value, int):
        return Address(value.to_bytes(20, "big"))
    return Address(value)


def _extract_json_object(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start:end + 1]
    return t


def _clamp_score(value) -> int:
    try:
        n = int(value)
    except Exception:
        n = 0
    return max(0, min(100, n))


def _extract_score_fallback(raw: str) -> int:
    """Used only when the LLM's response isn't valid JSON at all (found
    live: with prompt_comparative, every validator independently makes
    its own LLM calls, so far more total calls happen than under the
    original prompt_non_comparative design, and at least one occasionally
    ignores the "strict JSON only" instruction). First look for a
    'score': <n> style fragment even inside broken JSON; failing that,
    fall back to the first 1-3 digit number in the text. Raises if truly
    nothing numeric is found, rather than silently guessing 0 - a wrong
    but confident 0 would unfairly tank a real evaluation."""
    match = re.search(r'"?score"?\s*[:=]\s*(\d{1,3})', raw, re.IGNORECASE)
    if not match:
        match = re.search(r'\b(\d{1,3})\b', raw)
    if not match:
        raise gl.vm.UserError(
            "could not extract a numeric score from the model response"
        )
    return _clamp_score(int(match.group(1)))


# The canonical rounding bucket for final_score. This is deliberately wide:
# a first version rounded to the nearest multiple of 5 and let the
# equivalence principle treat any two final_scores within 15 points as
# "equivalent" - but final_score is the exact number that determines the
# payout percentage and the value recorded in PerformanceRegistry, so a
# steward review correctly rejected that as allowing materially different
# payouts (e.g. 65 vs 90) to both pass consensus. The fix binds validation
# to the payout-preserving value itself: final_score is now rounded to the
# nearest multiple of ROUND_BUCKET, and prompt_comparative below requires
# EXACT equality of that value, not a tolerance window on it. The bucket is
# widened from 5 to 20 specifically so that ordinary LLM sampling noise
# across independently-run leader/validator evaluations is likely to still
# land in the same bucket, keeping exact-match consensus achievable while
# the mechanism remains clearly non-binary (0/20/40/60/80/100).
ROUND_BUCKET = 20


class ReviewerConsensusPanel(gl.Contract):
    owner: Address
    escrow: Address
    escrow_set: bool

    def __init__(self):
        self.owner = gl.message.sender_address
        self.escrow = Address(int(0).to_bytes(20, "big"))
        self.escrow_set = False

    # Owner-updatable, not call-once - see PerformanceRegistry.set_escrow
    # for the rationale (avoids a full redeploy every time MilestoneEscrow
    # changes address).
    @gl.public.write
    def set_escrow(self, escrow_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set escrow")
        self.escrow = _normalize_address(escrow_address)
        self.escrow_set = True

    # Called by MilestoneEscrow via .emit() (fire-and-forget, async - see
    # DESIGN_DECISIONS.md #5 for why this is a @gl.public.write and not a
    # @gl.public.view: nondet/eq_principle execution inside a method reached
    # cross-contract via .view() was never live-verified by any prior
    # project in this series, so the architecture was deliberately built to
    # not depend on it at all, rather than gambling on unconfirmed GenVM
    # behavior. The result is delivered back to MilestoneEscrow with a
    # second, separate .emit() call (apply_score) once scoring finishes.
    @gl.public.write
    def evaluate_milestone(
        self,
        project_id: u256,
        milestone_index: u256,
        contractor,
        description: str,
        evidence_url: str,
        relaxed: bool,
        attempt: u256,
    ) -> None:
        if not self.escrow_set or gl.message.sender_address != self.escrow:
            raise gl.vm.UserError("only the escrow contract can request scoring")

        contractor_addr = _normalize_address(contractor)

        def analyze() -> str:
            page_text = gl.nondet.web.render(evidence_url, mode="text")

            def ask(framing: str) -> int:
                prompt = (
                    "You are scoring how complete a project milestone is, "
                    "from 0 (not started) to 100 (fully done), based on the "
                    "milestone description and the submitted evidence "
                    "page.\n\n"
                    "Framing for this evaluation: " + framing + "\n\n"
                    "Milestone description:\n<milestone>\n" + description +
                    "\n</milestone>\n\n"
                    "Evidence page content:\n<evidence>\n" + page_text +
                    "\n</evidence>\n\n"
                    "Respond with strict JSON only, no other text, no "
                    "markdown fence:\n"
                    '{"score": <integer 0-100>, "reasoning": "<max 300 chars>"}'
                )
                raw = gl.nondet.exec_prompt(prompt)
                try:
                    parsed = json.loads(_extract_json_object(raw))
                    return _clamp_score(parsed.get("score", 0))
                except (json.JSONDecodeError, AttributeError, TypeError):
                    return _extract_score_fallback(raw)

            literal_score = ask(
                "Strict literal reading: only count acceptance criteria "
                "explicitly listed in the milestone description as "
                "explicitly and verifiably satisfied by the evidence."
            )
            outcome_score = ask(
                "Outcome/impact reading: judge whether the practical goal "
                "behind the milestone was achieved, even if the evidence "
                "does not literally restate every listed criterion."
            )
            skeptical_score = ask(
                "Skeptical, worst-case reading: assume ambiguous or "
                "unstated details were NOT done; only credit what the "
                "evidence unambiguously demonstrates."
            )

            sub_scores = [literal_score, outcome_score, skeptical_score]
            # The three-way average is still deterministic Python, not LLM
            # arithmetic - but correctness no longer rests on that alone.
            # Because this whole function is wrapped in prompt_comparative
            # (not prompt_non_comparative), every validator independently
            # re-executes this entire function - fetching the real evidence
            # page itself and generating its own three sub-scores - rather
            # than merely auditing the leader's self-reported JSON for
            # internal consistency. final_score is rounded to the nearest
            # ROUND_BUCKET (see its definition above for why 20, not 5) so
            # that ordinary LLM sampling noise across nodes is likely to
            # still land on the same canonical value - the principle below
            # requires that value to match EXACTLY, not approximately.
            raw_average = sum(sub_scores) / len(sub_scores)
            if relaxed:
                raw_average = min(100.0, raw_average + 5.0)
            final_score = int(round(raw_average / ROUND_BUCKET)) * ROUND_BUCKET
            final_score = _clamp_score(final_score)

            payload = {
                "sub_scores": sub_scores,
                "final_score": final_score,
            }
            return json.dumps(payload)

        result_json = gl.eq_principle.prompt_comparative(
            analyze,
            (
                "Each node independently fetches the evidence page at the "
                "given URL and produces its own JSON with 'sub_scores' (3 "
                "integers 0-100 under the three stated framings) and "
                f"'final_score' (their mean, rounded to the nearest "
                f"multiple of {ROUND_BUCKET}, plus a +5 trust bonus before "
                "rounding if relaxed). final_score is the exact number "
                "that determines the contractor's payout percentage and "
                "the value recorded in their performance history, so two "
                "outputs are equivalent ONLY if their final_score values "
                "are EXACTLY equal - not merely close. A final_score that "
                "is off by even one bucket must be rejected as "
                "non-equivalent, because it would produce a materially "
                "different, real payout. Do NOT accept an output merely "
                "because it is internally self-consistent (correct JSON "
                "shape, sub_scores in range, final_score matching its own "
                "reported sub_scores) - an output must also be rejected as "
                "non-equivalent if its final_score diverges from what an "
                "independent, honest reading of the actual evidence at the "
                "URL would produce, even if that output's own internal "
                "arithmetic is consistent."
            ),
        )

        parsed = json.loads(_extract_json_object(result_json))
        final_score = _clamp_score(parsed.get("final_score", 0))

        # Cross-contract .emit() calls, strictly outside the nondet/eq_principle
        # block above, per the standing rule confirmed since Tribunal.
        gl.get_contract_at(self.escrow).emit().apply_score(
            project_id, milestone_index, u256(final_score), attempt
        )
