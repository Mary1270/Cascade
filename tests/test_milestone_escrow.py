"""
Tests for MilestoneEscrow: funding validation, milestone status guards,
and the "read real history before judging" mechanism -- verified by
seeding PerformanceRegistry with a real track record and observing that
it measurably changes the score ReviewerConsensusPanel returns for
otherwise-identical evidence (the same kind of test named in
DESIGN_DECISIONS.md as one that would fail if the trust loop were fake).
"""
import json
from unittest.mock import patch

import pytest

from _bootstrap import (
    make_wired, set_caller, set_value,
    ESCROW_ADDRESS, PANEL_ADDRESS, CONTRACTOR_ADDRESS, STRANGER_ADDRESS,
)
from genlayer import gl


def _score(n):
    return json.dumps({"score": n, "reasoning": "test"})


def test_create_project_requires_exact_deposit():
    _, _, escrow = make_wired()
    set_value(999)
    set_caller(CONTRACTOR_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([500, 500]))
    set_value(0)


def test_create_project_rejects_non_positive_allocation():
    _, _, escrow = make_wired()
    set_value(1000)
    set_caller(CONTRACTOR_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([1000, 0]))
    set_value(0)


def test_create_project_stores_milestones_as_open():
    _, _, escrow = make_wired()
    set_value(1500)
    set_caller(CONTRACTOR_ADDRESS)
    project_id = escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([500, 1000]))
    set_value(0)

    record = json.loads(escrow.get_project(project_id))
    assert record["total_allocated"] == 1500
    assert [m["status"] for m in record["milestones"]] == ["open", "open"]


def test_submit_milestone_evidence_rejects_already_awaiting_milestone():
    _, _, escrow = make_wired()
    set_value(1000)
    set_caller(CONTRACTOR_ADDRESS)
    project_id = escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([1000]))
    set_value(0)

    set_caller(CONTRACTOR_ADDRESS)
    with patch.object(gl.nondet.web, "render", return_value="page"):
        with patch.object(gl.nondet, "exec_prompt", side_effect=[_score(50)] * 3):
            escrow.submit_milestone_evidence(project_id, 0, "desc", "https://x.test")

    # Panel already applied the score synchronously in the stub, so the
    # milestone is "scored", not "awaiting_score" -- re-submitting should
    # still be rejected because it's no longer "open".
    with pytest.raises(gl.vm.UserError):
        escrow.submit_milestone_evidence(project_id, 0, "desc again", "https://x.test")


def test_apply_score_rejects_non_panel_caller():
    _, _, escrow = make_wired()
    set_value(1000)
    set_caller(CONTRACTOR_ADDRESS)
    project_id = escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([1000]))
    set_value(0)

    set_caller(STRANGER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        escrow.apply_score(project_id, 0, 50)


def test_good_track_record_relaxes_the_next_evaluation():
    registry, panel, escrow = make_wired()

    # Seed 3 prior scores averaging >= 85 directly on the registry, as the
    # escrow itself would after 3 real milestones.
    set_caller(ESCROW_ADDRESS)
    for s in (90, 88, 92):
        registry.record_score(CONTRACTOR_ADDRESS, s)

    set_value(1000)
    set_caller(CONTRACTOR_ADDRESS)
    project_id = escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([1000]))
    set_value(0)

    set_caller(CONTRACTOR_ADDRESS)
    with patch.object(gl.nondet.web, "render", return_value="page"):
        # 61/61/61 -> mean 61 -> without bonus rounds to 60, with the +5
        # relaxed bonus (66) rounds to 65.
        with patch.object(gl.nondet, "exec_prompt", side_effect=[_score(61)] * 3):
            escrow.submit_milestone_evidence(project_id, 0, "desc", "https://x.test")

    record = json.loads(escrow.get_project(project_id))
    assert record["milestones"][0]["score"] == 65


def test_no_track_record_uses_strict_threshold():
    _, panel, escrow = make_wired()
    set_value(1000)
    set_caller(CONTRACTOR_ADDRESS)
    project_id = escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([1000]))
    set_value(0)

    set_caller(CONTRACTOR_ADDRESS)
    with patch.object(gl.nondet.web, "render", return_value="page"):
        with patch.object(gl.nondet, "exec_prompt", side_effect=[_score(61)] * 3):
            escrow.submit_milestone_evidence(project_id, 0, "desc", "https://x.test")

    record = json.loads(escrow.get_project(project_id))
    assert record["milestones"][0]["score"] == 60
