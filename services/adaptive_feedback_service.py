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
    session_type: str = ''
    suggested_minutes: int | None = None


class AdaptiveFeedbackService:
    """Produces explainable proposals; it never mutates confirmed blocks."""

    def propose(
        self, operation: DraftOperation, feedback: Iterable[PreparationFeedback],
    ) -> List[ReplanProposal]:
        drafts = {block.id: block for block in operation.blocks}
        proposals = []
        for item in feedback:
            block = drafts.get(item.block_id)
            if block is None or operation.status not in {'draft', 'confirmed'}:
                continue
            if item.outcome == 'skipped':
                reason = 'Блок не выполнен; черновой план требует перепланирования.'
                action = 'replan_draft'
            elif item.outcome == 'partial':
                reason = 'Блок выполнен частично; оставшаяся подготовка переносится в черновик.'
                action = 'replan_draft'
            elif (block.session_type in {'lecture', 'practical', 'lab'}
                  and abs(item.actual_minutes - block.minutes) >= 10):
                suggested = max(10, min(120, 5 * round(
                    ((block.minutes + item.actual_minutes) / 2) / 5,
                )))
                reason = (
                    f'Фактически {item.actual_minutes} мин вместо {block.minutes}; '
                    f'предлагается {suggested} мин для будущих блоков.'
                )
                proposals.append(ReplanProposal(
                    operation.id, block.id, reason, 'update_estimate',
                    block.session_type, suggested,
                ))
                continue
            elif item.difficulty >= 4:
                reason = 'Высокая сложность сохранена; план и профиль не изменяются.'
                action = 'keep_draft'
            else:
                reason = 'Feedback учтён; подтверждённые блоки не изменяются автоматически.'
                action = 'keep_draft'
            proposals.append(ReplanProposal(operation.id, block.id, reason, action))
        return proposals
