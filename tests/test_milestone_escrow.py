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
        escrow.apply_score(project_id, 0, 50, 0)


def test_apply_score_rejects_stale_attempt():
    """Isolates the attempt check from the status check: the milestone is
    manually placed in 'awaiting_score' with a known attempt (simulating a
    real in-flight evaluation that hasn't called back yet), so a
    mismatched-attempt callback must be rejected specifically because of
    the attempt, not because the status already changed."""
    _, _, escrow = make_wired()
    set_value(1000)
    set_caller(CONTRACTOR_ADDRESS)
    project_id = escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([1000]))
    set_value(0)

    record = json.loads(escrow.get_project(project_id))
    record["milestones"][0]["status"] = "awaiting_score"
    record["milestones"][0]["attempt"] = 1
    escrow.projects[project_id] = json.dumps(record)

    # Wrong attempt while status is still genuinely "awaiting_score" ->
    # must be rejected by the attempt check itself.
    set_caller(PANEL_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        escrow.apply_score(project_id, 0, 99, 2)

    # Confirm nothing was applied by the rejected call, then confirm the
    # correct attempt genuinely succeeds against that same "awaiting_score"
    # state.
    record = json.loads(escrow.get_project(project_id))
    assert record["milestones"][0]["status"] == "awaiting_score"
    assert record["milestones"][0]["score"] is None

    set_caller(PANEL_ADDRESS)
    escrow.apply_score(project_id, 0, 80, 1)
    record = json.loads(escrow.get_project(project_id))
    assert record["milestones"][0]["status"] == "scored"
    assert record["milestones"][0]["score"] == 80


def test_reset_then_resubmit_ignores_a_late_stale_callback():
    """The exact race the attempt nonce closes: a milestone is reset and
    resubmitted, but the FIRST (pre-reset) evaluation's callback still
    lands late, claiming the original attempt number. It must be rejected
    rather than silently overwriting the new submission's real score."""
    _, panel, escrow = make_wired()
    project_id = _create_and_get_stuck(escrow)  # attempt stays 0 (forced stuck)

    # Reset and resubmit for real -- this bumps attempt to 1 and, via the
    # stub's synchronous cross-contract calls, immediately resolves with a
    # real score.
    set_caller(CONTRACTOR_ADDRESS)
    escrow.reset_stuck_milestone(project_id, 0)
    with patch.object(gl.nondet.web, "render", return_value="page"):
        with patch.object(gl.nondet, "exec_prompt", side_effect=[_score(90)] * 3):
            set_caller(CONTRACTOR_ADDRESS)
            escrow.submit_milestone_evidence(project_id, 0, "real desc", "https://x.test")

    record = json.loads(escrow.get_project(project_id))
    assert record["milestones"][0]["status"] == "scored"
    assert record["milestones"][0]["score"] == 90

    # Now the original (attempt=0) evaluation, which was abandoned by the
    # reset, finally "arrives" and tries to apply a stale, wrong score.
    set_caller(PANEL_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        escrow.apply_score(project_id, 0, 10, 0)

    # The real, correctly-scored result must be untouched.
    record = json.loads(escrow.get_project(project_id))
    assert record["milestones"][0]["score"] == 90


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


def test_submit_milestone_evidence_rejects_non_contractor():
    _, _, escrow = make_wired()
    set_value(1000)
    set_caller(CONTRACTOR_ADDRESS)
    project_id = escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([1000]))
    set_value(0)

    set_caller(STRANGER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        escrow.submit_milestone_evidence(project_id, 0, "not my milestone", "https://x.test")


def _create_and_get_stuck(escrow, allocation=1000):
    """Create a project and put milestone 0 into 'awaiting_score' directly,
    simulating a real evaluate_milestone transaction that failed/rolled
    back before ever calling apply_score (as happened live - see
    LESSONS_LEARNED.md)."""
    set_value(allocation)
    set_caller(CONTRACTOR_ADDRESS)
    project_id = escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([allocation]))
    set_value(0)

    record = json.loads(escrow.get_project(project_id))
    record["milestones"][0]["status"] = "awaiting_score"
    record["milestones"][0]["description"] = "desc"
    record["milestones"][0]["evidence_url"] = "https://bad.example/404"
    escrow.projects[project_id] = json.dumps(record)
    return project_id


def test_reset_stuck_milestone_rejects_non_payer():
    _, _, escrow = make_wired()
    project_id = _create_and_get_stuck(escrow)

    set_caller(STRANGER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        escrow.reset_stuck_milestone(project_id, 0)


def test_reset_stuck_milestone_rejects_when_not_awaiting():
    _, _, escrow = make_wired()
    set_value(1000)
    set_caller(CONTRACTOR_ADDRESS)
    project_id = escrow.create_project(CONTRACTOR_ADDRESS, json.dumps([1000]))
    set_value(0)

    # Milestone 0 is "open", not "awaiting_score" -- nothing to reset.
    set_caller(CONTRACTOR_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        escrow.reset_stuck_milestone(project_id, 0)


def test_reset_stuck_milestone_reopens_for_the_payer():
    _, _, escrow = make_wired()
    project_id = _create_and_get_stuck(escrow)

    set_caller(CONTRACTOR_ADDRESS)  # payer == contractor in this test
    escrow.reset_stuck_milestone(project_id, 0)

    record = json.loads(escrow.get_project(project_id))
    milestone = record["milestones"][0]
    assert milestone["status"] == "open"
    assert milestone["description"] == ""
    assert milestone["evidence_url"] == ""

    # And it can now genuinely be resubmitted.
    with patch.object(gl.nondet.web, "render", return_value="page"):
        with patch.object(gl.nondet, "exec_prompt", side_effect=[_score(80)] * 3):
            set_caller(CONTRACTOR_ADDRESS)
            escrow.submit_milestone_evidence(project_id, 0, "desc", "https://x.test")

    record = json.loads(escrow.get_project(project_id))
    assert record["milestones"][0]["status"] == "scored"


def test_refund_stuck_milestone_rejects_non_payer():
    _, _, escrow = make_wired()
    project_id = _create_and_get_stuck(escrow)

    set_caller(STRANGER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        escrow.refund_stuck_milestone(project_id, 0)


def test_refund_stuck_milestone_marks_refunded():
    _, _, escrow = make_wired()
    project_id = _create_and_get_stuck(escrow, allocation=1000)

    set_caller(CONTRACTOR_ADDRESS)  # payer == contractor in this test
    escrow.refund_stuck_milestone(project_id, 0)

    record = json.loads(escrow.get_project(project_id))
    assert record["milestones"][0]["status"] == "refunded"

    # A refunded milestone cannot be reset or refunded again.
    with pytest.raises(gl.vm.UserError):
        escrow.reset_stuck_milestone(project_id, 0)
    with pytest.raises(gl.vm.UserError):
        escrow.refund_stuck_milestone(project_id, 0)
