"""
Full Cascade lifecycle across all three contracts, the way it will run
live on Studio: create a 4-milestone project, submit evidence for each
milestone in order, and confirm (a) the released amount for at least one
milestone is neither 0% nor 100% of its allocation -- the test named in
DESIGN_DECISIONS.md as the one that would fail if this were secretly a
binary wrapper -- and (b) a strong 3-milestone track record measurably
relaxes the panel's read of the 4th milestone's evidence, exactly the
"read real state before judging" pattern this project is built around.
"""
import json
from unittest.mock import patch

import pytest

from _bootstrap import make_wired, set_caller, set_value, CONTRACTOR_ADDRESS
from genlayer import gl


def _score(n):
    return json.dumps({"score": n, "reasoning": "test"})


def test_full_lifecycle_non_binary_and_trust_adjusted():
    registry, panel, escrow = make_wired()

    set_value(4000)
    set_caller(CONTRACTOR_ADDRESS)
    project_id = escrow.create_project(
        CONTRACTOR_ADDRESS, json.dumps([1000, 1000, 1000, 1000])
    )
    set_value(0)

    def submit(index, sub_scores):
        set_caller(CONTRACTOR_ADDRESS)
        with patch.object(gl.nondet.web, "render", return_value="evidence page"):
            with patch.object(
                gl.nondet, "exec_prompt",
                side_effect=[_score(s) for s in sub_scores],
            ):
                escrow.submit_milestone_evidence(
                    project_id, index, f"milestone {index}", "https://x.test"
                )

    # Milestone 0: no history yet -> strict threshold regardless.
    submit(0, [85, 85, 85])
    # Milestone 1: still fewer than 3 prior scores -> strict.
    submit(1, [85, 85, 85])
    # Milestone 2: still fewer than 3 prior scores (2 so far) -> strict.
    submit(2, [95, 95, 95])

    record = json.loads(escrow.get_project(project_id))
    assert [m["score"] for m in record["milestones"][:3]] == [85, 85, 95]
    assert [m["released"] for m in record["milestones"][:3]] == [850, 850, 950]
    assert registry.get_score_count(CONTRACTOR_ADDRESS) == 3
    # Recent average = (85+85+95)/3 = 88.33 >= 85 -> milestone 3 is relaxed.
    assert json.loads(registry.get_recent_scores(CONTRACTOR_ADDRESS, 3)) == [85, 85, 95]

    # Milestone 3: identical raw evidence quality to a case that would
    # round to 60 without the trust bonus -- confirms the relaxed flag
    # actually changed the outcome, not just that it was computed.
    submit(3, [61, 61, 61])

    record = json.loads(escrow.get_project(project_id))
    milestone_3 = record["milestones"][3]
    assert milestone_3["score"] == 65  # 61 + 5 relaxed bonus -> rounds to 65
    assert milestone_3["released"] == 650

    scores = [m["score"] for m in record["milestones"]]
    assert any(0 < s < 100 for s in scores), "at least one score must be a genuine percentage"

    assert record["total_released"] == 850 + 850 + 950 + 650
    assert record["total_released"] == sum(m["released"] for m in record["milestones"])
