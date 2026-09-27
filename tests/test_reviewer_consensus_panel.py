"""
Tests for ReviewerConsensusPanel via genuine (in-process, synchronous)
cross-contract calls: evaluate_milestone calls back into MilestoneEscrow's
apply_score through the stub's get_contract_at registry -- exactly the
interaction genlayer-test's Direct Mode was confirmed NOT to support (see
LESSONS_LEARNED.md from the Vigil project).

Only gl.nondet.exec_prompt / gl.nondet.web.render are mocked; the 3-way
average and the canonical-bucket rounding (see ROUND_BUCKET in
reviewer_consensus_panel.py) are real project code, not LLM-generated,
so these tests exercise the actual arithmetic.
"""
import json
from unittest.mock import patch

import pytest

from _bootstrap import (
    make_wired, set_caller, set_value,
    ESCROW_ADDRESS, CONTRACTOR_ADDRESS, STRANGER_ADDRESS,
)
from genlayer import gl


def _score(n):
    return json.dumps({"score": n, "reasoning": "test"})


def _fund_project(escrow, allocation=1000):
    """Create a project and put milestone 0 directly into
    'awaiting_score', mirroring what MilestoneEscrow.submit_milestone_evidence
    does right before it calls the panel -- these tests call the panel
    directly (bypassing submit_milestone_evidence) so they can control the
    `relaxed` flag explicitly rather than deriving it from seeded history."""
    set_value(allocation)
    set_caller(CONTRACTOR_ADDRESS)
    project_id = escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([allocation]))
    set_value(0)

    record = json.loads(escrow.get_project(project_id))
    record["milestones"][0]["status"] = "awaiting_score"
    record["milestones"][0]["attempt"] = 1
    escrow.projects[project_id] = json.dumps(record)
    return project_id


def test_evaluate_milestone_rejects_unauthorized_caller():
    _, panel, _ = make_wired()
    set_caller(STRANGER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        panel.evaluate_milestone(0, 0, CONTRACTOR_ADDRESS, "desc", "https://x.test", False, 1)


def test_evaluate_milestone_averages_three_framings_and_releases_proportionally():
    registry, panel, escrow = make_wired()
    project_id = _fund_project(escrow, 1000)

    set_caller(ESCROW_ADDRESS)
    with patch.object(gl.nondet.web, "render", return_value="milestone evidence page"):
        with patch.object(
            gl.nondet, "exec_prompt",
            side_effect=[_score(60), _score(70), _score(65)],
        ):
            panel.evaluate_milestone(
                project_id, 0, CONTRACTOR_ADDRESS, "do the thing", "https://x.test", False, 1
            )

    record = json.loads(escrow.get_project(project_id))
    milestone = record["milestones"][0]
    # average(60, 70, 65) = 65 -> rounds to the nearest bucket of 20 -> 60
    assert milestone["score"] == 60
    assert milestone["status"] == "scored"
    assert milestone["released"] == 600  # 1000 * 60 // 100
    assert registry.get_score_count(CONTRACTOR_ADDRESS) == 1


def test_relaxed_flag_adds_bonus_before_rounding():
    _, panel, escrow = make_wired()
    project_id = _fund_project(escrow, 1000)

    set_caller(ESCROW_ADDRESS)
    with patch.object(gl.nondet.web, "render", return_value="page"):
        with patch.object(
            gl.nondet, "exec_prompt",
            side_effect=[_score(70), _score(70), _score(67)],
        ):
            panel.evaluate_milestone(
                project_id, 0, CONTRACTOR_ADDRESS, "desc", "https://x.test", True, 1
            )

    record = json.loads(escrow.get_project(project_id))
    # average(70,70,67)=69, +5 relaxed bonus=74 -> rounds to bucket 80
    # (without the bonus, 69 would round to bucket 60 instead - see
    # test_no_track_record_uses_strict_threshold in test_milestone_escrow.py
    # for the same inputs without the bonus).
    assert record["milestones"][0]["score"] == 80


def test_final_score_never_exceeds_100_with_relaxed_bonus():
    _, panel, escrow = make_wired()
    project_id = _fund_project(escrow, 1000)

    set_caller(ESCROW_ADDRESS)
    with patch.object(gl.nondet.web, "render", return_value="page"):
        with patch.object(
            gl.nondet, "exec_prompt",
            side_effect=[_score(100), _score(98), _score(100)],
        ):
            panel.evaluate_milestone(
                project_id, 0, CONTRACTOR_ADDRESS, "desc", "https://x.test", True, 1
            )

    record = json.loads(escrow.get_project(project_id))
    assert record["milestones"][0]["score"] == 100


def test_score_is_not_binary_across_a_range_of_inputs():
    _, panel, escrow = make_wired()
    project_id = _fund_project(escrow, 1000)

    set_caller(ESCROW_ADDRESS)
    with patch.object(gl.nondet.web, "render", return_value="page"):
        with patch.object(
            gl.nondet, "exec_prompt",
            side_effect=[_score(40), _score(45), _score(42)],
        ):
            panel.evaluate_milestone(
                project_id, 0, CONTRACTOR_ADDRESS, "desc", "https://x.test", False, 1
            )

    record = json.loads(escrow.get_project(project_id))
    score = record["milestones"][0]["score"]
    assert score not in (0, 100)
    assert record["milestones"][0]["released"] == 1000 * score // 100


def test_non_json_llm_response_falls_back_to_regex_extraction():
    """Found live: with prompt_comparative, every validator makes its own
    independent LLM calls, so far more total calls happen than under the
    original design, and at least one occasionally ignores the "strict
    JSON only" instruction and returns plain prose instead. This must
    still resolve to a score, not crash the whole evaluation."""
    _, panel, escrow = make_wired()
    project_id = _fund_project(escrow, 1000)

    set_caller(ESCROW_ADDRESS)
    with patch.object(gl.nondet.web, "render", return_value="page"):
        with patch.object(
            gl.nondet, "exec_prompt",
            side_effect=[
                "I'd estimate the completion at about 76 out of 100.",
                _score(70),
                'Sure, here it is: {"score": 68, "reasoning": "close enough"}',
            ],
        ):
            panel.evaluate_milestone(
                project_id, 0, CONTRACTOR_ADDRESS, "desc", "https://x.test", False, 1
            )

    record = json.loads(escrow.get_project(project_id))
    milestone = record["milestones"][0]
    assert milestone["status"] == "scored"
    # average(76, 70, 68) = 71.33 -> rounds to the nearest bucket of 20 -> 80
    assert milestone["score"] == 80


def test_completely_non_numeric_llm_response_raises_cleanly():
    _, panel, _ = make_wired()

    set_caller(ESCROW_ADDRESS)
    with patch.object(gl.nondet.web, "render", return_value="page"):
        with patch.object(
            gl.nondet, "exec_prompt",
            return_value="I refuse to evaluate this without more context.",
        ):
            with pytest.raises(gl.vm.UserError):
                panel.evaluate_milestone(
                    0, 0, CONTRACTOR_ADDRESS, "desc", "https://x.test", False, 1
                )


def test_set_escrow_owner_only_and_updatable():
    from _bootstrap import OWNER_ADDRESS

    _, panel, _ = make_wired()

    with pytest.raises(gl.vm.UserError):
        set_caller(STRANGER_ADDRESS)
        panel.set_escrow(STRANGER_ADDRESS)

    # Owner can repoint it, not just set it once - this is what lets a
    # future MilestoneEscrow redeploy repoint this panel without
    # redeploying the panel itself.
    new_escrow = "0x" + "ee" * 20
    set_caller(OWNER_ADDRESS)
    panel.set_escrow(new_escrow)

    with pytest.raises(gl.vm.UserError):
        set_caller(ESCROW_ADDRESS)
        panel.evaluate_milestone(0, 0, CONTRACTOR_ADDRESS, "desc", "https://x.test", False, 1)
