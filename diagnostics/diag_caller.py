# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *


def _normalize_address(value) -> Address:
    if isinstance(value, Address):
        return value
    if isinstance(value, int):
        return Address(value.to_bytes(20, "big"))
    return Address(value)


class DiagCaller(gl.Contract):
    owner: Address
    scorer: Address
    last_result: str
    last_call_ok: bool

    def __init__(self, scorer_address):
        self.owner = gl.message.sender_address
        self.scorer = _normalize_address(scorer_address)
        self.last_result = ""
        self.last_call_ok = False

    # Calls DiagScorer.get_confidence_score via .view(), strictly outside
    # any run_nondet/eq_principle block on this side (per Tribunal §4/§6,
    # already-confirmed rule for the CALLER). What this diagnostic actually
    # tests is whether the CALLEE can run nondet internally when reached
    # this way -- see diag_scorer.py.
    @gl.public.write
    def request_score(self, evidence: str, force_mismatch: bool) -> str:
        result = gl.get_contract_at(self.scorer).view().get_confidence_score(
            evidence, force_mismatch
        )
        self.last_result = result
        self.last_call_ok = True
        return result

    @gl.public.view
    def get_last_result(self) -> str:
        return self.last_result

    @gl.public.view
    def get_last_call_ok(self) -> bool:
        return self.last_call_ok
