"""Feedback-driven, explainable proposals for draft preparation blocks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List

from services.adaptive_preparation_service import DraftOperation, DraftPreparationBlock


@dataclass(frozen=True)
class PreparationFeedback:
    block_id: str
    outcome: str  # done, partial, skipped
    actual_minutes: int
    difficulty: int
    comment: str = ''
    energy: int | None = None
    recorded_at: datetime = datetime.min

    def __post_init__(self) -> None:
        if self.outcome not in {'done', 'partial', 'skipped'}:
            raise ValueError('Unsupported feedback outcome')
        if self.actual_minutes < 0 or not 1 <= self.difficulty <= 5:
            raise ValueError('Invalid feedback values')


@dataclass(frozen=True)
class ReplanProposal:
    operation_id: str
    block_id: str
    explanation: str
    action: str = 'keep_draft'


class AdaptiveFeedbackService:
    """Produces explainable proposals; it never mutates confirmed blocks."""

    def propose(
        self, operation: DraftOperation, feedback: Iterable[PreparationFeedback],
    ) -> List[ReplanProposal]:
        drafts = {block.id: block for block in operation.blocks}
        proposals = []
        for item in feedback:
            block = drafts.get(item.block_id)
            if block is None or operation.status != 'draft':
                continue
            if item.outcome == 'skipped':
                reason = 'Блок не выполнен; черновой план требует перепланирования.'
            elif item.outcome == 'partial':
                reason = 'Блок выполнен частично; оставшаяся подготовка переносится в черновик.'
            elif item.difficulty >= 4:
                reason = 'Высокая сложность отмечена; будущие черновики получат повышенный приоритет.'
            else:
                reason = 'Feedback учтён; подтверждённые блоки не изменяются автоматически.'
            proposals.append(ReplanProposal(operation.id, block.id, reason))
        return proposals
