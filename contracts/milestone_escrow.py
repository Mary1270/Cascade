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
STATUS_REFUNDED = "refunded"

TRUST_WINDOW = 3
TRUST_AVERAGE_THRESHOLD = 85

VALID_SCORE_BUCKETS = (0, 20, 40, 60, 80, 100)


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

    @gl.public.write
    def set_panel(self, panel_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set panel")
        self.panel = _normalize_address(panel_address)

    @gl.public.write
    def set_registry(self, registry_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set registry")
        self.registry = _normalize_address(registry_address)

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
        payer_addr = gl.message.sender_address
        project_id = self.next_project_id
        self.next_project_id = self.next_project_id + 1

        record = {
            "contractor": str(contractor_addr),
            "payer": str(payer_addr),
            "milestones": [
                {
                    "allocation": a,
                    "released": 0,
                    "status": STATUS_OPEN,
                    "description": "",
                    "evidence_url": "",
                    "score": None,
                    "attempt": 0,
                }
                for a in allocations
            ],
            "total_allocated": total,
            "total_released": 0,
        }
        self.projects[project_id] = json.dumps(record)
        return project_id

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
        if gl.message.sender_address != contractor_addr:
            raise gl.vm.UserError("only the project's contractor can submit evidence")

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
        milestone["attempt"] = milestone["attempt"] + 1
        attempt = milestone["attempt"]
        self.projects[project_id] = json.dumps(record)

        gl.get_contract_at(self.panel).emit().evaluate_milestone(
            project_id, milestone_index, contractor_addr, description, evidence_url,
            relaxed, u256(attempt),
        )

    @gl.public.write
    def apply_score(
        self, project_id: u256, milestone_index: u256, score: u256, attempt: u256
    ) -> None:
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
        if int(attempt) != milestone["attempt"]:
            raise gl.vm.UserError("stale evaluation attempt - milestone was reset since this evaluation started")

        score_int = int(score)
        if score_int not in VALID_SCORE_BUCKETS:
            raise gl.vm.UserError(
                "score callback is outside the allowed canonical bucket set "
                f"{VALID_SCORE_BUCKETS} - refusing to settle this milestone"
            )

        allocation = milestone["allocation"]
        amount = (allocation * score_int) // 100
        remainder = allocation - amount

        milestone["released"] = amount
        milestone["status"] = STATUS_SCORED
        milestone["score"] = score_int
        record["total_released"] = record["total_released"] + amount
        self.projects[project_id] = json.dumps(record)

        contractor_addr = _normalize_address(record["contractor"])
        if amount > 0:
            gl.get_contract_at(contractor_addr).emit_transfer(value=amount)

        if remainder > 0:
            payer_addr = _normalize_address(record["payer"])
            gl.get_contract_at(payer_addr).emit_transfer(value=remainder)

        gl.get_contract_at(self.registry).emit().record_score(contractor_addr, score)

    @gl.public.write
    def reset_stuck_milestone(self, project_id: u256, milestone_index: u256) -> None:
        if project_id not in self.projects:
            raise gl.vm.UserError("project not found")
        record = json.loads(self.projects[project_id])
        payer_addr = _normalize_address(record["payer"])
        if gl.message.sender_address != payer_addr:
            raise gl.vm.UserError("only the project's payer can recover a stuck milestone")
        idx = int(milestone_index)
        if idx < 0 or idx >= len(record["milestones"]):
            raise gl.vm.UserError("milestone index out of range")
        milestone = record["milestones"][idx]
        if milestone["status"] != STATUS_AWAITING_SCORE:
            raise gl.vm.UserError("milestone is not stuck awaiting a score")

        milestone["status"] = STATUS_OPEN
        milestone["description"] = ""
        milestone["evidence_url"] = ""
        self.projects[project_id] = json.dumps(record)

    @gl.public.write
    def refund_stuck_milestone(self, project_id: u256, milestone_index: u256) -> None:
        if project_id not in self.projects:
            raise gl.vm.UserError("project not found")
        record = json.loads(self.projects[project_id])
        payer_addr = _normalize_address(record["payer"])
        if gl.message.sender_address != payer_addr:
            raise gl.vm.UserError("only the project's payer can recover a stuck milestone")
        idx = int(milestone_index)
        if idx < 0 or idx >= len(record["milestones"]):
            raise gl.vm.UserError("milestone index out of range")
        milestone = record["milestones"][idx]
        if milestone["status"] != STATUS_AWAITING_SCORE:
            raise gl.vm.UserError("milestone is not stuck awaiting a score")

        milestone["status"] = STATUS_REFUNDED
        self.projects[project_id] = json.dumps(record)
        gl.get_contract_at(payer_addr).emit_transfer(value=milestone["allocation"])

    @gl.public.view
    def get_project(self, project_id: u256) -> str:
        if project_id not in self.projects:
            raise gl.vm.UserError("project not found")
        return self.projects[project_id]

    @gl.public.view
    def get_project_count(self) -> u256:
        return self.next_project_id
