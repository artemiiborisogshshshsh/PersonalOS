"""
Integration tests for preparation integration service.
"""
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

from models import PersonalUniversityEvent, PersonalEventState, PreparationRequirement, PreparationPolicy, EstimateSource, TaskStatus, TaskPriority
from services.preparation.preparation_integration_service import PreparationIntegrationService
from services.preparation.preparation_block_service import PreparationBlockService
from services.university_service import UniversityService
from ids import IDGenerator
from planning_engine import create_planning_item_from_preparation_requirement


class TestPreparationIntegrationService(unittest.TestCase):
    def setUp(self):
        # Create a mock preparation block service
        self.mock_prep_block_service = Mock(spec=PreparationBlockService)
        self.mock_prep_block_service.get_or_create_calendar.return_value = "test_calendar_id"
        self.mock_prep_block_service.find_free_slot.return_value = (datetime(2026, 9, 1, 8, 0), datetime(2026, 9, 1, 9, 0))
        self.mock_prep_block_service.insert_preparation_block.return_value = "prep_block_event_id"
        self.mock_prep_block_service.update_preparation_block_event.return_value = "prep_block_event_id"
        self.mock_prep_block_service.delete_preparation_block_event.return_value = True
        self.mock_prep_block_service.event_exists_by_uid.return_value = None
        self.mock_prep_block_service.get_existing_events.return_value = []

        # Create service
        self.service = PreparationIntegrationService(preparation_block_service=self.mock_prep_block_service)

        # Common test data
        self.base_time = datetime(2026, 9, 1, 10, 0)
        self.event_uid = "test-event-uid"
        self.personal_event_id = "personal-123"

    def _create_personal_event(self, state=PersonalEventState.CONFIRMED, university_event_uid=None):
        """Helper to create a personal university event."""
        if university_event_uid is None:
            university_event_uid = self.event_uid
        return PersonalUniversityEvent(
            id=self.personal_event_id,
            title="Test Event",
            description="Test description",
            start_time=self.base_time,
            end_time=self.base_time + timedelta(hours=1),
            university_event_uid=university_event_uid,
            state=state,
            match_confidence=None,
            match_type=None,
            metadata={}
        )

    def test_preparation_created_only_for_confirmed_or_moved(self):
        """Test that preparation is created only for CONFIRMED or MOVED states."""
        # Test EXPECTED state -> no preparation
        event_expected = self._create_personal_event(state=PersonalEventState.EXPECTED)
        self.service.integrate_preparation(event_expected)
        # Should not have called any preparation block service methods
        self.mock_prep_block_service.find_free_slot.assert_not_called()
        self.mock_prep_block_service.insert_preparation_block.assert_not_called()

        # Reset mock
        self.mock_prep_block_service.reset_mock()

        # Test CONFIRMED state -> preparation created
        event_confirmed = self._create_personal_event(state=PersonalEventState.CONFIRMED)
        self.service.integrate_preparation(event_confirmed)
        self.mock_prep_block_service.find_free_slot.assert_called_once()
        self.mock_prep_block_service.insert_preparation_block.assert_called_once()

        # Reset mock
        self.mock_prep_block_service.reset_mock()

        # Test MOVED state -> preparation created
        event_moved = self._create_personal_event(state=PersonalEventState.MOVED)
        self.service.integrate_preparation(event_moved)
        self.mock_prep_block_service.find_free_slot.assert_called_once()
        self.mock_prep_block_service.insert_preparation_block.assert_called_once()

        # Reset mock
        self.mock_prep_block_service.reset_mock()

        # Test CANCELLED state -> no new preparation (cancellation path only)
        event_cancelled = self._create_personal_event(state=PersonalEventState.CANCELLED)
        self.service.integrate_preparation(event_cancelled)
        self.mock_prep_block_service.find_free_slot.assert_not_called()
        self.mock_prep_block_service.insert_preparation_block.assert_not_called()

    def test_preparation_block_moved_correctly_when_event_moved(self):
        """Test that when personal event is MOVED, preparation block is rescheduled accordingly."""
        # Create a personal event with MOVED state and a new time
        new_time = self.base_time + timedelta(days=1)
        event_moved = PersonalUniversityEvent(
            id=self.personal_event_id,
            title="Test Event Moved",
            description="Test description",
            start_time=new_time,
            end_time=new_time + timedelta(hours=1),
            university_event_uid=self.event_uid,
            state=PersonalEventState.MOVED,
            match_confidence=None,
            match_type=None,
            metadata={}
        )
        # Also set the preparation_block_uid to simulate existing preparation block
        event_moved.preparation_block_uid = "old-prep-block-uid"
        event_moved.task_uid = "old-task-uid"
        self.mock_prep_block_service.event_exists_by_uid.return_value = "google-prep-event-id"

        # Call integrate_preparation
        self.service.integrate_preparation(event_moved)

        # A move updates the existing Google projection by its Google event ID.
        self.mock_prep_block_service.insert_preparation_block.assert_not_called()
        self.mock_prep_block_service.update_preparation_block_event.assert_called_once()
        update_args = self.mock_prep_block_service.update_preparation_block_event.call_args.args
        self.assertEqual(update_args[1], "google-prep-event-id")
        find_slot_args = self.mock_prep_block_service.find_free_slot.call_args.args
        self.assertEqual(
            find_slot_args[1], new_time.replace(
                hour=6, minute=0, second=0, microsecond=0,
            ),
        )

        # Also ensure that the personal event's preparation_block_uid and task_uid were updated
        # (the service sets them after successful scheduling)
        # Since we mocked insert_preparation_block to return an event ID, the service should have set the UIDs.
        # However, we are not checking the actual event object because we passed in a mock? Actually we passed the real event_moved.
        # After the method, event_moved should have updated UIDs.
        self.assertIsNotNone(event_moved.preparation_block_uid)
        self.assertIsNotNone(event_moved.task_uid)
        self.assertNotEqual(event_moved.preparation_block_uid, "old-prep-block-uid")
        self.assertEqual(event_moved.task_uid, "old-task-uid")

    def test_no_preparation_when_event_cancelled(self):
        """Test that no preparation is created when event is cancelled."""
        event_cancelled = self._create_personal_event(state=PersonalEventState.CANCELLED)
        # Set existing preparation links and an owned Google projection.
        event_cancelled.preparation_block_uid = "existing-prep-uid"
        event_cancelled.task_uid = "existing-task-uid"
        self.mock_prep_block_service.event_exists_by_uid.return_value = (
            "google-prep-event-id"
        )

        self.service.integrate_preparation(event_cancelled)

        # No new work is generated; the owned calendar projection is removed.
        self.mock_prep_block_service.find_free_slot.assert_not_called()
        self.mock_prep_block_service.insert_preparation_block.assert_not_called()
        self.mock_prep_block_service.delete_preparation_block_event.assert_called_once_with(
            "test_calendar_id", "google-prep-event-id"
        )
        self.assertIsNone(event_cancelled.preparation_block_uid)
        self.assertIsNone(event_cancelled.task_uid)
        self.assertEqual(
            event_cancelled.metadata['cancelled_preparation_block_uid'],
            "existing-prep-uid",
        )
        self.assertEqual(
            event_cancelled.metadata['cancelled_preparation_task_uid'],
            "existing-task-uid",
        )

    def test_failed_preparation_cancellation_preserves_active_links(self):
        event = self._create_personal_event(state=PersonalEventState.CANCELLED)
        event.preparation_block_uid = 'existing-prep-uid'
        event.task_uid = 'existing-task-uid'
        self.mock_prep_block_service.event_exists_by_uid.return_value = (
            'google-prep-event-id'
        )
        self.mock_prep_block_service.delete_preparation_block_event.return_value = False

        self.service.integrate_preparation(event)

        self.assertEqual(event.preparation_block_uid, 'existing-prep-uid')
        self.assertEqual(event.task_uid, 'existing-task-uid')
        self.assertEqual(
            event.metadata['preparation_cancellation_error'],
            'calendar_delete_failed',
        )

    def test_task_creation_uses_personal_event_data(self):
        """Test that the created task includes course_id and event_type_code from personal event."""
        # We need to mock the preparation requirement creation to control its values
        with patch.object(self.service, '_create_preparation_requirement') as mock_create_req, \
             patch.object(self.service, '_create_task_from_preparation_requirement') as mock_create_task, \
             patch.object(self.service, '_schedule_preparation_block') as mock_schedule:
            # Setup mocks
            mock_prep_req = Mock(spec=PreparationRequirement)
            mock_prep_req.id = "prep-req-123"
            mock_prep_req.title = "Test Preparation"
            mock_prep_req.description = "Test preparation description"
            mock_prep_req.due_time = self.base_time
            mock_prep_req.time_estimate = Mock()
            mock_prep_req.time_estimate.value = 60
            mock_create_req.return_value = mock_prep_req

            mock_task = Mock()
            mock_task.id = "task-123"
            mock_create_task.return_value = mock_task

            mock_schedule.return_value = "prep-block-uid"

            # Create a personal event with some metadata to extract course and type
            # We'll mock the extraction methods to return known values
            with patch.object(self.service, '_extract_course_id', return_value="CS101"), \
                 patch.object(self.service, '_extract_event_type_code', return_value="ЛК"):
                personal_event = self._create_personal_event(state=PersonalEventState.CONFIRMED)
                # Call integrate_preparation
                self.service.integrate_preparation(personal_event)

                # Verify that _create_task_from_preparation_requirement was called with the prep_req and personal_event
                mock_create_task.assert_called_once()
                call_args = mock_create_task.call_args
                prep_req_arg = call_args[0][0]
                personal_event_arg = call_args[0][1]
                self.assertEqual(prep_req_arg, mock_prep_req)
                self.assertEqual(personal_event_arg, personal_event)

                # Now we need to inspect the task that was created (the mock_task) but we didn't capture its metadata.
                # Instead we can check that the method was called; we cannot inspect the internal task without refactoring.
                # For the purpose of this test, we can trust that our modification to _create_task_from_preparation_requirement
                # includes the course_id and event_type_code in metadata.
                # We could also verify by checking that the mock_task was constructed with expected metadata,
                # but since we mocked the method, we don't have the actual Task instance.
                # We'll instead test the actual method directly in a separate unit test.
                # For integration test, we just ensure the flow works.

        # Additionally, we can test the _create_task_from_preparation_requirement method directly.
        # Let's do that in a separate test method.

    def test_task_metadata_includes_course_and_event_type(self):
        """Unit test for _create_task_from_preparation_requirement to verify metadata includes course and type."""
        # Create a mock preparation requirement
        prep_req = Mock(spec=PreparationRequirement)
        prep_req.id = "prep-req-456"
        prep_req.title = "Test Prep"
        prep_req.description = "Test Prep Desc"
        prep_req.due_time = self.base_time
        prep_req.time_estimate = Mock()
        prep_req.time_estimate.value = 30

        # Create a personal event with known course and event type (we'll mock the extraction methods)
        personal_event = self._create_personal_event(state=PersonalEventState.CONFIRMED)
        # Override the extraction methods on the instance
        self.service._extract_course_id = Mock(return_value="CS101")
        self.service._extract_event_type_code = Mock(return_value="ЛБ")

        # Call the method
        task = self.service._create_task_from_preparation_requirement(prep_req, personal_event)

        # Verify metadata
        self.assertIn("course_id", task.metadata)
        self.assertEqual(task.metadata["course_id"], "CS101")
        self.assertIn("event_type_code", task.metadata)
        self.assertEqual(task.metadata["event_type_code"], "ЛБ")
        # Also ensure original fields are present
        self.assertEqual(task.metadata["personal_event_id"], personal_event.id)
        self.assertEqual(task.metadata["university_event_uid"], personal_event.university_event_uid)
        self.assertEqual(task.metadata["preparation_requirement_id"], prep_req.id)

        # Verify other task fields
        self.assertEqual(task.title, prep_req.title)
        self.assertEqual(task.description, prep_req.description)
        self.assertEqual(task.status, TaskStatus.INBOX)
        self.assertEqual(task.priority, TaskPriority.MEDIUM)
        self.assertEqual(task.time_estimate.value, 30)

    def test_planning_engine_works_with_personal_event_planning_item(self):
        """Test that PlanningEngine can schedule a PlanningItem built from a PersonalUniversityEvent."""
        from planning_engine import PlanningEngine
        # Create a personal event
        personal_event = self._create_personal_event(state=PersonalEventState.CONFIRMED)
        # Use UniversityService to create planning item
        uni_service = UniversityService()
        planning_item = uni_service.create_planning_item_from_personal_event(personal_event)
        # Create a planning engine
        engine = PlanningEngine()
        # Define a planning horizon
        horizon_start = personal_event.start_time - timedelta(hours=1)
        horizon_end = personal_event.end_time + timedelta(hours=1)
        # Schedule the item
        result = engine.schedule_items([planning_item], horizon_start, horizon_end, 30)
        # Verify that the item was scheduled (i.e., appears in scheduled items)
        scheduled_items = result.get_scheduled_items()
        self.assertEqual(len(scheduled_items), 1)
        scheduled_item = scheduled_items[0]
        self.assertEqual(scheduled_item.id, personal_event.id)
        # Optionally, check that the scheduled time matches the preferred time (within flexibility)
        # Since flexible=False, it should be scheduled at preferred start if possible.
        # We'll just ensure it's scheduled.

    def test_requirement_uses_course_type_difficulty_priority_and_deadline(self):
        event = self._create_personal_event(state=PersonalEventState.CONFIRMED)
        event.title = "Физика (ЛБ)"
        event.metadata.update({
            'course': 'Физика',
            'session_type': 'lab',
            'difficulty': 1.5,
            'priority': 4,
            'required_materials': ['Методичка'],
        })

        requirement = self.service._create_preparation_requirement(event)

        self.assertEqual(requirement.metadata['course_id'], 'Физика')
        self.assertEqual(requirement.metadata['session_type'], 'lab')
        self.assertEqual(requirement.metadata['difficulty'], 1.5)
        self.assertEqual(requirement.metadata['priority'], 4)
        self.assertGreater(requirement.time_estimate.value, 60)
        self.assertEqual(
            requirement.due_time, event.start_time.replace(
                hour=6, minute=0, second=0, microsecond=0,
            )
        )
        self.assertEqual(
            requirement.metadata['preparation_window_start'],
            (event.start_time.replace(hour=6, minute=0, second=0, microsecond=0)
             - timedelta(hours=36)).isoformat(),
        )
        self.assertEqual(requirement.required_materials, ['Методичка'])

    def test_requirement_becomes_flexible_deep_work_before_lesson(self):
        event = self._create_personal_event(state=PersonalEventState.CONFIRMED)
        event.metadata.update({'priority': 3, 'travel_buffer_minutes': 30})
        requirement = self.service._create_preparation_requirement(event)

        item = create_planning_item_from_preparation_requirement(
            requirement,
            earliest_start=self.base_time - timedelta(days=2),
        )

        self.assertTrue(item.flexible)
        self.assertTrue(item.metadata['deep_work'])
        self.assertEqual(
            item.latest_end,
            event.start_time.replace(hour=6, minute=0, second=0, microsecond=0),
        )
        self.assertEqual(
            item.earliest_start,
            event.start_time.replace(hour=6, minute=0, second=0, microsecond=0)
            - timedelta(hours=36),
        )

if __name__ == '__main__':
    unittest.main()
