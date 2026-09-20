from datetime import datetime

from services.adaptive_feedback_service import (
    AdaptiveFeedbackService, PreparationFeedback,
)
from services.adaptive_preparation_service import AdaptivePreparationService
from tests.test_adaptive_preparation_service import personal_event


def test_feedback_creates_explanation_only_for_draft_blocks():
    operation = AdaptivePreparationService().build_draft(
        [personal_event()], now=datetime(2026, 9, 1),
    )
    feedback = PreparationFeedback(operation.blocks[0].id, 'partial', 20, 4)

    proposal = AdaptiveFeedbackService().propose(operation, [feedback])[0]

    assert proposal.block_id == feedback.block_id
    assert 'частично' in proposal.explanation


def test_feedback_can_propose_for_confirmed_operation_without_mutating_it():
    operation = AdaptivePreparationService().build_draft(
        [personal_event()], now=datetime(2026, 9, 1),
    )
    operation.status = 'confirmed'
    feedback = PreparationFeedback(operation.blocks[0].id, 'skipped', 0, 3)

    before = list(operation.blocks)
    proposal = AdaptiveFeedbackService().propose(operation, [feedback])[0]

    assert proposal.action == 'replan_draft'
    assert operation.status == 'confirmed'
    assert operation.blocks == before
