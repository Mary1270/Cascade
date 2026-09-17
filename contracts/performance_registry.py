# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
import json


def _normalize_address(value) -> Address:
    if isinstance(value, Address):
        return value
    if isinstance(value, int):
        return Address(value.to_bytes(20, "big"))
    return Address(value)


class PerformanceRegistry(gl.Contract):
    owner: Address
    escrow: Address
    escrow_set: bool
    history: TreeMap[Address, str]

    def __init__(self):
        self.owner = gl.message.sender_address
        self.escrow = Address(int(0).to_bytes(20, "big"))
        self.escrow_set = False

    @gl.public.write
    def set_escrow(self, escrow_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set escrow")
        if self.escrow_set:
            raise gl.vm.UserError("escrow already set")
        self.escrow = _normalize_address(escrow_address)
        self.escrow_set = True

    # Append-only, no policy of its own. Restricted to the escrow contract
    # because a fake/self-reported history would defeat the whole point of
    # reading it back before the next milestone.
    @gl.public.write
    def record_score(self, contractor, score: u256) -> None:
        if not self.escrow_set or gl.message.sender_address != self.escrow:
            raise gl.vm.UserError("only the escrow contract can record scores")
        addr = _normalize_address(contractor)
        existing = json.loads(self.history[addr]) if addr in self.history else []
        existing.append(int(score))
        self.history[addr] = json.dumps(existing)

    # Pure, deterministic read - no LLM, no policy decision. MilestoneEscrow
    # decides what the numbers mean; this contract only stores them.
    @gl.public.view
    def get_recent_scores(self, contractor, count: u256) -> str:
        addr = _normalize_address(contractor)
        if addr not in self.history:
            return json.dumps([])
        all_scores = json.loads(self.history[addr])
        n = int(count)
        if n <= 0:
            return json.dumps([])
        return json.dumps(all_scores[-n:])

    @gl.public.view
    def get_score_count(self, contractor) -> u256:
        addr = _normalize_address(contractor)
        if addr not in self.history:
            return u256(0)
        return u256(len(json.loads(self.history[addr])))
