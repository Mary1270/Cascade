# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
import json


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


class DiagScorer(gl.Contract):
    owner: Address

    def __init__(self):
        self.owner = gl.message.sender_address

    # DIAGNOSTIC 1: is this method (exposed as @gl.public.view, meant to be
    # reached cross-contract via .view()) actually able to run a nondet
    # block / eq_principle internally? If GenVM forbids nondet execution
    # inside a view-reached method, this call will raise/trap instead of
    # returning a score, and MilestoneEscrow -> ReviewerConsensusPanel must
    # be restructured as a @gl.public.write per the fallback noted in
    # DESIGN_DECISIONS.md §2.
    @gl.public.view
    def get_confidence_score(self, evidence: str, force_mismatch: bool) -> str:

        def analyze() -> str:
            # Three independent framings of the same evidence, each asked
            # to return only a 0-100 integer. In a real ReviewerConsensusPanel
            # these would be three separate gl.nondet.exec_prompt calls with
            # distinct prompts; here they are simulated deterministically so
            # this diagnostic isolates the two questions above from ordinary
            # prompt-quality noise.
            sub_scores = [62, 70, 66]
            raw_average = sum(sub_scores) / len(sub_scores)
            final_score = int(round(raw_average / 5.0)) * 5

            # DIAGNOSTIC 2: deliberately corrupt the stated final score so it
            # no longer matches the sub_scores also being reported. If the
            # prompt_non_comparative validator (guided only by the criteria
            # text below) rejects/flags this, criteria-based numeric
            # auditing is confirmed reliable. If it silently accepts a
            # mismatched final_score, the fallback in DESIGN_DECISIONS.md §2
            # (validator must recompute from sub_scores, stated explicitly
            # in criteria) is required, not optional.
            if force_mismatch:
                final_score = min(100, final_score + 30)

            payload = {
                "sub_scores": sub_scores,
                "raw_average": raw_average,
                "final_score": final_score,
            }
            return json.dumps(payload)

        result_json = gl.eq_principle.prompt_non_comparative(
            analyze,
            task=(
                "Report three independent sub-scores (0-100 each) for the "
                "given evidence, their average, and a final_score rounded "
                "to the nearest multiple of 5."
            ),
            criteria=(
                "The response must be strict JSON with keys 'sub_scores' "
                "(a list of exactly 3 numbers between 0 and 100), "
                "'raw_average' (the arithmetic mean of sub_scores), and "
                "'final_score' (raw_average rounded to the nearest multiple "
                "of 5). Independently recompute raw_average from the "
                "reported sub_scores and recompute the nearest-multiple-of-5 "
                "rounding yourself. Reject the response if final_score does "
                "not equal your own recomputation, or if raw_average does "
                "not equal the mean of the reported sub_scores, or if any "
                "sub_score is outside 0-100."
            ),
        )
        return _extract_json_object(result_json)
