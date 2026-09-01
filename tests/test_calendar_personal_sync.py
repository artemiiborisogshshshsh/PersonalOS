"""
Unit tests for personal event calendar sync service.
"""
import unittest
from unittest.mock import Mock
from datetime import datetime, timedelta, timezone

from models import PersonalUniversityEvent, PersonalEventState, AttendanceConfidence, MatchType
from services.calendar.personal_event_sync_service import PersonalEventSyncService
from adapters.base_adapter import CalendarAdapter


class TestPersonalEventSyncService(unittest.TestCase):
    def setUp(self):
        # Create a mock calendar adapter
        self.mock_adapter = Mock()
        self.mock_adapter._get_or_create_calendar.return_value = "test_calendar_id"
        self.mock_adapter.event_exists_by_uid.return_value = None  # assume no existing event by default
        self.mock_adapter._get_events_in_range.return_value = []  # no existing events in range
        self.mock_adapter._insert_event.return_value = "new_event_id"
        self.mock_adapter._update_event.return_value = "updated_event_id"
        self.mock_adapter._delete_event.return_value = True

        # Create service
        self.service = PersonalEventSyncService(calendar_adapter=self.mock_adapter)

        # Common test data
        self.base_time = datetime(2026, 9, 1, 10, 0)
        self.event_uid = "test-event-uid"

    def _create_personal_event(self, state=PersonalEventState.CONFIRMED,
                               confidence=AttendanceConfidence.HIGH,
                               match_type=MatchType.EXACT,
                               university_event_uid=None):
        """Helper to create a personal university event."""
        if university_event_uid is None:
            university_event_uid = self.event_uid
        return PersonalUniversityEvent(
            id="personal-123",
            title="Test Event",
            description="Test description",
            start_time=self.base_time,
            end_time=self.base_time + timedelta(hours=1),
            university_event_uid=university_event_uid,
            state=state,
            match_confidence=confidence,
            match_type=match_type,
            metadata={}
        )

    def test_sync_confirmed_high_confidence_inserts_event(self):
        """Test that a CONFIRMED event with high confidence triggers an insert."""
        event = self._create_personal_event(state=PersonalEventState.CONFIRMED,
                                            confidence=AttendanceConfidence.HIGH)
        # Assume no existing event
        self.mock_adapter.event_exists_by_uid.return_value = None
        self.mock_adapter._get_events_in_range.return_value = []

        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")

        # Should have called insert_event
        self.mock_adapter._insert_event.assert_called_once()
        self.assertEqual(event_id, "new_event_id")
        # Should not have called delete_event
        self.mock_adapter._delete_event.assert_not_called()

    def test_sync_confirmed_high_confidence_updates_existing_event(self):
        """Test that a CONFIRMED event with high confidence updates existing event."""
        event = self._create_personal_event(state=PersonalEventState.CONFIRMED,
                                            confidence=AttendanceConfidence.HIGH)
        # Simulate existing event by UID
        self.mock_adapter.event_exists_by_uid.return_value = "existing_event_id"
        self.mock_adapter._get_events_in_range.return_value = []

        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")

        self.mock_adapter._update_event.assert_called_once()
        self.mock_adapter._delete_event.assert_not_called()
        self.mock_adapter._insert_event.assert_not_called()
        self.assertEqual(event_id, "updated_event_id")

    def test_sync_moved_high_confidence_inserts_event(self):
        """Test that a MOVED event with high confidence triggers an insert (treated as new)."""
        event = self._create_personal_event(state=PersonalEventState.MOVED,
                                            confidence=AttendanceConfidence.HIGH)
        self.mock_adapter.event_exists_by_uid.return_value = None
        self.mock_adapter._get_events_in_range.return_value = []

        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")

        self.mock_adapter._insert_event.assert_called_once()
        self.assertEqual(event_id, "new_event_id")

    def test_repeated_sync_with_same_hash_is_noop(self):
        """An unchanged event returns the Google event ID without rewriting it."""
        event = self._create_personal_event()
        existing_description = self.service._append_hash_and_version_to_description(
            "Existing description", event
        )
        self.mock_adapter.event_exists_by_uid.return_value = "google-event-id"
        self.mock_adapter._get_events_in_range.return_value = [{
            "id": "google-event-id",
            "iCalUID": event.id,
            "description": existing_description,
        }]

        event_id = self.service.sync_personal_event_to_calendar(
            event, "test_calendar_id"
        )

        self.assertEqual(event_id, "google-event-id")
        self.mock_adapter._delete_event.assert_not_called()
        self.mock_adapter._insert_event.assert_not_called()

    def test_state_change_with_same_time_is_an_update_not_noop(self):
        confirmed = self._create_personal_event(state=PersonalEventState.CONFIRMED)
        existing_description = self.service._append_hash_and_version_to_description(
            "Existing description", confirmed
        )
        moved = self._create_personal_event(
            state=PersonalEventState.MOVED,
            confidence=AttendanceConfidence.MEDIUM,
            match_type=MatchType.PARTIAL_TIME,
        )
        self.mock_adapter.event_exists_by_uid.return_value = "google-event-id"
        self.mock_adapter._get_events_in_range.return_value = [{
            "id": "google-event-id",
            "iCalUID": moved.id,
            "description": existing_description,
        }]

        event_id = self.service.sync_personal_event_to_calendar(
            moved, "test_calendar_id"
        )

        self.assertEqual(event_id, "updated_event_id")
        self.mock_adapter._update_event.assert_called_once()
        updated = self.mock_adapter._update_event.call_args.args[2]
        self.assertIn('(перенесено)', updated.summary)
        self.mock_adapter._insert_event.assert_not_called()

    def test_confirmed_state_is_authoritative_even_with_low_confidence(self):
        event = self._create_personal_event(state=PersonalEventState.CONFIRMED,
                                            confidence=AttendanceConfidence.LOW)
        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")
        self.mock_adapter._insert_event.assert_called_once()
        self.mock_adapter._delete_event.assert_not_called()
        self.assertEqual(event_id, "new_event_id")

    def test_moved_medium_confidence_is_synced(self):
        """MOVED normally comes from a medium-confidence partial-time match."""
        event = self._create_personal_event(state=PersonalEventState.MOVED,
                                            confidence=AttendanceConfidence.MEDIUM)
        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")
        self.mock_adapter._insert_event.assert_called_once()
        self.mock_adapter._delete_event.assert_not_called()
        self.assertEqual(event_id, "new_event_id")

    def test_sync_cancelled_deletes_existing_event(self):
        """Test that a cancelled event deletes the existing calendar event."""
        event = self._create_personal_event(state=PersonalEventState.CANCELLED)
        # Simulate existing event
        self.mock_adapter.event_exists_by_uid.return_value = "existing_event_id"
        self.mock_adapter._get_events_in_range.return_value = []

        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")

        # Should have called delete_event
        self.mock_adapter._delete_event.assert_called_once_with("test_calendar_id", "existing_event_id")
        # Should not have called insert_event
        self.mock_adapter._insert_event.assert_not_called()
        self.assertIsNone(event_id)

    def test_sync_cancelled_no_existing_event_does_nothing(self):
        """Test that a cancelled event with no existing event does nothing."""
        event = self._create_personal_event(state=PersonalEventState.CANCELLED)
        self.mock_adapter.event_exists_by_uid.return_value = None
        self.mock_adapter._get_events_in_range.return_value = []

        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")

        self.mock_adapter._delete_event.assert_not_called()
        self.mock_adapter._insert_event.assert_not_called()
        self.assertIsNone(event_id)

    def test_sync_event_not_confirmed_or_moved_skips(self):
        """Test that events with state EXPECTED are skipped."""
        event = self._create_personal_event(state=PersonalEventState.EXPECTED)
        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")
        self.mock_adapter._insert_event.assert_not_called()
        self.mock_adapter._delete_event.assert_not_called()
        self.assertIsNone(event_id)

    def test_external_same_summary_and_start_is_conflict(self):
        """An unowned user event must never be deleted or overwritten."""
        event = self._create_personal_event()
        self.mock_adapter._get_events_in_range.return_value = [{
            "id": "user-event-id",
            "iCalUID": "user-event-uid",
            "summary": event.title,
            "description": "Created manually by the user",
            "start": {"dateTime": event.start_time.isoformat()},
        }]

        event_id = self.service.sync_personal_event_to_calendar(
            event, "test_calendar_id"
        )

        self.assertIsNone(event_id)
        self.mock_adapter._delete_event.assert_not_called()
        self.mock_adapter._update_event.assert_not_called()
        self.mock_adapter._insert_event.assert_not_called()

    def test_owned_duplicate_with_stale_uid_is_repaired_in_place(self):
        """A marked projection with a stale UID is updated, not recreated."""
        event = self._create_personal_event()
        owned_description = self.service._append_hash_and_version_to_description(
            "Owned projection", event
        )
        self.mock_adapter._get_events_in_range.return_value = [{
            "id": "stale-google-id",
            "iCalUID": "stale-uid",
            "summary": event.title,
            "description": owned_description,
            "start": {"dateTime": event.start_time.isoformat()},
        }]

        event_id = self.service.sync_personal_event_to_calendar(
            event, "test_calendar_id"
        )

        self.assertEqual(event_id, "updated_event_id")
        self.mock_adapter._update_event.assert_called_once()
        args = self.mock_adapter._update_event.call_args.args
        self.assertEqual(args[:2], ("test_calendar_id", "stale-google-id"))
        self.assertEqual(args[2].uid, event.id)
        self.mock_adapter._delete_event.assert_not_called()
        self.mock_adapter._insert_event.assert_not_called()

    def test_timezone_aware_event_search_is_supported(self):
        event = self._create_personal_event()
        event.start_time = event.start_time.replace(tzinfo=timezone.utc)
        event.end_time = event.end_time.replace(tzinfo=timezone.utc)
        self.mock_adapter._get_events_in_range.return_value = []

        event_id = self.service.sync_personal_event_to_calendar(
            event, "test_calendar_id"
        )

        self.assertEqual(event_id, "new_event_id")

if __name__ == '__main__':
    unittest.main()
