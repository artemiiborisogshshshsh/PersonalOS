"""Approval boundary between AI interpretation and deterministic commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
import uuid

from services.application.command import BaseCommand


class ProposalStatus(Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


@dataclass
class ProposedCommand:
    """An AI suggestion that cannot mutate state until explicitly approved."""
    command: BaseCommand
    original_input: str
    interpretation: str
    confidence: float
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: ProposalStatus = ProposalStatus.PROPOSED
    validation_error: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    result: Any = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Proposal confidence must be between 0 and 1")


class ProposedCommandService:
    def __init__(self, application_service: Any):
        self.application_service = application_service

    def validate(self, proposal: ProposedCommand) -> bool:
        if proposal.status != ProposalStatus.PROPOSED:
            raise ValueError("Only a proposed command can be validated")
        if not proposal.command.validate():
            proposal.status = ProposalStatus.FAILED
            proposal.validation_error = proposal.command.error or 'Command validation failed'
            return False
        proposal.status = ProposalStatus.VALIDATED
        return True

    def approve(self, proposal: ProposedCommand, approved_by: str = 'user') -> None:
        if proposal.status != ProposalStatus.VALIDATED:
            raise ValueError("Proposal must be validated before approval")
        proposal.status = ProposalStatus.APPROVED
        proposal.approved_by = approved_by
        proposal.approved_at = datetime.now()

    def reject(self, proposal: ProposedCommand) -> None:
        if proposal.status not in (ProposalStatus.PROPOSED, ProposalStatus.VALIDATED):
            raise ValueError("Proposal can no longer be rejected")
        proposal.status = ProposalStatus.REJECTED

    async def execute(self, proposal: ProposedCommand) -> Any:
        if proposal.status != ProposalStatus.APPROVED:
            raise PermissionError("AI proposal requires explicit approval before execution")
        try:
            proposal.result = await self.application_service.process_command(proposal.command)
            proposal.status = ProposalStatus.EXECUTED
            return proposal.result
        except Exception:
            proposal.status = ProposalStatus.FAILED
            raise
