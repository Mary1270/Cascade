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


STATUS_OPEN = "open"
STATUS_AWAITING_SCORE = "awaiting_score"
STATUS_SCORED = "scored"

# History window read from PerformanceRegistry before each new evaluation,
# and the average-score bar a contractor must clear over that window for
# MilestoneEscrow to ask ReviewerConsensusPanel for a relaxed (more
# lenient) read. Both numbers are a deliberate, documented policy choice
# living here (not in PerformanceRegistry, which stays policy-free) - see
# DESIGN_DECISIONS.md section on PerformanceRegistry.
TRUST_WINDOW = 3
TRUST_AVERAGE_THRESHOLD = 85


class MilestoneEscrow(gl.Contract):
    owner: Address
    panel: Address
    registry: Address
    next_project_id: u256
    projects: TreeMap[u256, str]

    def __init__(self, panel_address, registry_address):
        self.owner = gl.message.sender_address
        self.panel = _normalize_address(panel_address)
        self.registry = _normalize_address(registry_address)
        self.next_project_id = u256(0)

    # Funds a new project. `milestone_allocations_json` is a JSON array of
    # integer amounts (same 18-decimal units as gl.message.value) - passed
    # as a JSON string rather than a typed list parameter, since no
    # contract in this series has yet live-verified a list-typed public
    # method argument and there is no reason to introduce that risk here
    # when a JSON string is already a proven pattern (Tribunal, Vigil).
    @gl.public.write.payable
    def create_project(self, contractor, milestone_allocations_json: str) -> u256:
        allocations = json.loads(milestone_allocations_json)
        if not isinstance(allocations, list) or len(allocations) == 0:
            raise gl.vm.UserError("milestone_allocations_json must be a non-empty JSON array")
        allocations = [int(a) for a in allocations]
        if any(a <= 0 for a in allocations):
            raise gl.vm.UserError("every milestone allocation must be positive")

        total = sum(allocations)
        if int(gl.message.value) != total:
            raise gl.vm.UserError(
                "deposited value must exactly equal the sum of milestone allocations"
            )

        contractor_addr = _normalize_address(contractor)
        project_id = self.next_project_id
        self.next_project_id = self.next_project_id + 1

        record = {
            "contractor": str(contractor_addr),
            "milestones": [
                {
                    "allocation": a,
                    "released": 0,
                    "status": STATUS_OPEN,
                    "description": "",
                    "evidence_url": "",
                    "score": None,
                }
                for a in allocations
            ],
            "total_allocated": total,
            "total_released": 0,
        }
        self.projects[project_id] = json.dumps(record)
        return project_id

    # Reads the contractor's real history from PerformanceRegistry (a plain
    # .view(), outside any nondet block, before ever contacting the panel),
    # then requests scoring from ReviewerConsensusPanel via .emit(). Because
    # .emit() is asynchronous, the score is NOT available when this call
    # returns - it arrives later via apply_score().
    @gl.public.write
    def submit_milestone_evidence(
        self,
        project_id: u256,
        milestone_index: u256,
        description: str,
        evidence_url: str,
    ) -> None:
        if project_id not in self.projects:
            raise gl.vm.UserError("project not found")
        record = json.loads(self.projects[project_id])
        idx = int(milestone_index)
        if idx < 0 or idx >= len(record["milestones"]):
            raise gl.vm.UserError("milestone index out of range")

        milestone = record["milestones"][idx]
        if milestone["status"] != STATUS_OPEN:
            raise gl.vm.UserError("milestone is not open for new evidence")

        contractor_addr = _normalize_address(record["contractor"])

        recent_json = gl.get_contract_at(self.registry).view().get_recent_scores(
            contractor_addr, u256(TRUST_WINDOW)
        )
        recent_scores = json.loads(recent_json)
        relaxed = (
            len(recent_scores) >= TRUST_WINDOW
            and (sum(recent_scores) / len(recent_scores)) >= TRUST_AVERAGE_THRESHOLD
        )

        milestone["status"] = STATUS_AWAITING_SCORE
        milestone["description"] = description
        milestone["evidence_url"] = evidence_url
        self.projects[project_id] = json.dumps(record)

        gl.get_contract_at(self.panel).emit().evaluate_milestone(
            project_id, milestone_index, contractor_addr, description, evidence_url, relaxed
        )

    # Callback from ReviewerConsensusPanel only. Releases exactly
    # allocation * score / 100 - the core of the "proportional, not
    # binary" mechanism.
    @gl.public.write
    def apply_score(self, project_id: u256, milestone_index: u256, score: u256) -> None:
        if gl.message.sender_address != self.panel:
            raise gl.vm.UserError("only the reviewer panel can apply a score")
        if project_id not in self.projects:
            raise gl.vm.UserError("project not found")

        record = json.loads(self.projects[project_id])
        idx = int(milestone_index)
        if idx < 0 or idx >= len(record["milestones"]):
            raise gl.vm.UserError("milestone index out of range")

        milestone = record["milestones"][idx]
        if milestone["status"] != STATUS_AWAITING_SCORE:
            raise gl.vm.UserError("milestone is not awaiting a score")

        score_int = int(score)
        amount = (milestone["allocation"] * score_int) // 100
        milestone["released"] = amount
        milestone["status"] = STATUS_SCORED
        milestone["score"] = score_int
        record["total_released"] = record["total_released"] + amount
        self.projects[project_id] = json.dumps(record)

        contractor_addr = _normalize_address(record["contractor"])
        if amount > 0:
            gl.get_contract_at(contractor_addr).emit_transfer(value=amount)

        gl.get_contract_at(self.registry).emit().record_score(contractor_addr, score)

    @gl.public.view
    def get_project(self, project_id: u256) -> str:
        if project_id not in self.projects:
            raise gl.vm.UserError("project not found")
        return self.projects[project_id]

    @gl.public.view
    def get_project_count(self) -> u256:
        return self.next_project_id
