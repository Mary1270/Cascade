"""
Tests for PerformanceRegistry in isolation: append-only history, no
policy of its own, restricted to the escrow contract for writes.
"""
import json

import pytest

from _bootstrap import (
    make_wired, set_caller,
    OWNER_ADDRESS, ESCROW_ADDRESS, CONTRACTOR_ADDRESS, STRANGER_ADDRESS,
)
from genlayer import gl


def test_empty_history_by_default():
    registry, _, _ = make_wired()
    assert json.loads(registry.get_recent_scores(CONTRACTOR_ADDRESS, 3)) == []
    assert registry.get_score_count(CONTRACTOR_ADDRESS) == 0


def test_record_score_rejects_unauthorized_caller():
    registry, _, _ = make_wired()
    set_caller(STRANGER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        registry.record_score(CONTRACTOR_ADDRESS, 80)


def test_record_score_accepts_escrow_and_appends():
    registry, _, _ = make_wired()
    set_caller(ESCROW_ADDRESS)
    registry.record_score(CONTRACTOR_ADDRESS, 70)
    registry.record_score(CONTRACTOR_ADDRESS, 90)

    assert registry.get_score_count(CONTRACTOR_ADDRESS) == 2
    assert json.loads(registry.get_recent_scores(CONTRACTOR_ADDRESS, 10)) == [70, 90]


def test_get_recent_scores_windowed_to_most_recent():
    registry, _, _ = make_wired()
    set_caller(ESCROW_ADDRESS)
    for score in [50, 60, 70, 80, 90]:
        registry.record_score(CONTRACTOR_ADDRESS, score)

    assert json.loads(registry.get_recent_scores(CONTRACTOR_ADDRESS, 3)) == [70, 80, 90]


def test_set_escrow_owner_only_and_updatable():
    registry, _, _ = make_wired()

    # Non-owner can never update it, regardless of current value.
    with pytest.raises(gl.vm.UserError):
        set_caller(STRANGER_ADDRESS)
        registry.set_escrow(STRANGER_ADDRESS)

    # Owner CAN update it again (no longer call-once) - this is what lets
    # a future MilestoneEscrow redeploy repoint this registry without
    # redeploying the registry itself.
    new_escrow = "0x" + "dd" * 20
    set_caller(OWNER_ADDRESS)
    registry.set_escrow(new_escrow)

    # The old escrow address can no longer record scores; the new one can.
    with pytest.raises(gl.vm.UserError):
        set_caller(ESCROW_ADDRESS)
        registry.record_score(CONTRACTOR_ADDRESS, 80)

    set_caller(new_escrow)
    registry.record_score(CONTRACTOR_ADDRESS, 80)
    assert registry.get_score_count(CONTRACTOR_ADDRESS) == 1
