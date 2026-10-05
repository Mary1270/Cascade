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
    match = re.search(r'"?score"?\s*[:=]\s*(\d{1,3})', raw, re.IGNORECASE)
    if not match:
        match = re.search(r'\b(\d{1,3})\b', raw)
    if not match:
        raise gl.vm.UserError("could not extract a numeric score from the model response")
    return _clamp_score(int(match.group(1)))


ROUND_BUCKET = 20


class ReviewerConsensusPanel(gl.Contract):
    owner: Address
    escrow: Address
    escrow_set: bool

    def __init__(self):
        self.owner = gl.message.sender_address
        self.escrow = Address(int(0).to_bytes(20, "big"))
        self.escrow_set = False

    @gl.public.write
    def set_escrow(self, escrow_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set escrow")
        self.escrow = _normalize_address(escrow_address)
        self.escrow_set = True

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

        gl.get_contract_at(self.escrow).emit().apply_score(
            project_id, milestone_index, u256(final_score), attempt
        )
