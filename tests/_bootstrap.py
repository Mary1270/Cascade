"""
Shared test bootstrap - wires up the offline genlayer SDK stub and loads
all three Cascade contracts once. Same pattern used by the sibling
Tribunal/Vigil projects.
"""
import importlib.util
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_STUB_DIR = os.path.join(_THIS_DIR, "genlayer_stub")
if _STUB_DIR not in sys.path:
    sys.path.insert(0, _STUB_DIR)

_CONTRACTS_DIR = os.path.join(os.path.dirname(_THIS_DIR), "contracts")


def _load(module_name, filename):
    path = os.path.join(_CONTRACTS_DIR, filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_registry_module = _load("cascade_performance_registry", "performance_registry.py")
_panel_module = _load("cascade_reviewer_consensus_panel", "reviewer_consensus_panel.py")
_escrow_module = _load("cascade_milestone_escrow", "milestone_escrow.py")

PerformanceRegistry = _registry_module.PerformanceRegistry
ReviewerConsensusPanel = _panel_module.ReviewerConsensusPanel
MilestoneEscrow = _escrow_module.MilestoneEscrow

from genlayer import gl, Address, register_contract, clear_registry  # noqa: E402

# Fixed, valid, distinct addresses reused across test files.
REGISTRY_ADDRESS = "0x" + "aa" * 20
PANEL_ADDRESS = "0x" + "bb" * 20
ESCROW_ADDRESS = "0x" + "cc" * 20
OWNER_ADDRESS = "0x" + "33" * 20
CONTRACTOR_ADDRESS = "0x" + "11" * 20
STRANGER_ADDRESS = "0x" + "22" * 20


def set_caller(address_str: str) -> None:
    """Simulate a specific wallet/contract calling the next method."""
    gl.message.sender_address = Address(address_str)


def set_value(amount: int) -> None:
    """Simulate a payable call depositing `amount` (wei-style units)."""
    gl.message.value = amount


def make_wired():
    """Deploy and wire all three contracts, in the exact order intended
    for Studio: PerformanceRegistry -> ReviewerConsensusPanel ->
    MilestoneEscrow (needs both addresses) -> panel.set_escrow ->
    registry.set_escrow."""
    clear_registry()

    set_caller(OWNER_ADDRESS)
    registry = PerformanceRegistry()
    register_contract(REGISTRY_ADDRESS, registry)

    set_caller(OWNER_ADDRESS)
    panel = ReviewerConsensusPanel()
    register_contract(PANEL_ADDRESS, panel)

    set_caller(OWNER_ADDRESS)
    escrow = MilestoneEscrow(PANEL_ADDRESS, REGISTRY_ADDRESS)
    register_contract(ESCROW_ADDRESS, escrow)

    set_caller(OWNER_ADDRESS)
    panel.set_escrow(ESCROW_ADDRESS)
    set_caller(OWNER_ADDRESS)
    registry.set_escrow(ESCROW_ADDRESS)

    set_value(0)
    return registry, panel, escrow
