"""
Unit tests for personal event calendar sync service.
"""
import unittest
from unittest.mock import Mock, create_autospec
from datetime import datetime, timedelta, timezone

from models import PersonalUniversityEvent, PersonalEventState, AttendanceConfidence, MatchType
from services.calendar.personal_event_sync_service import PersonalEventSyncService
from adapters.google_calendar_adapter import GoogleCalendarAdapter


class TestPersonalEventSyncService(unittest.TestCase):
    def setUp(self):
        # Create a mock calendar adapter
        self.mock_adapter = create_autospec(GoogleCalendarAdapter, instance=True)
        self.mock_adapter._get_or_create_calendar.return_value = "test_calendar_id"
        self.mock_adapter.get_event_by_uid.return_value = None
        self.mock_adapter.list_events_in_calendar.return_value = []  # no existing events in range
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
        self.mock_adapter.list_events_in_calendar.return_value = []

        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")

        # Should have called insert_event
        self.mock_adapter._insert_event.assert_called_once()
        self.assertEqual(event_id, "new_event_id")
        # Should not have called delete_event
        self.mock_adapter._delete_event.assert_not_called()

    def test_sync_projects_session_type_for_calendar_colour(self):
        """The adapter receives TPU's colour key rather than a default."""
        event = self._create_personal_event()
        event.metadata['session_type'] = 'lab'

        self.service.sync_personal_event_to_calendar(event, "test_calendar_id")

        projected = self.mock_adapter._insert_event.call_args.args[0]
        self.assertEqual(projected.event_type, 'ЛБ')

    def test_colour_mapping_participates_in_idempotency_hash(self):
        event = self._create_personal_event()
        event.metadata['session_type'] = 'lecture'
        lecture_hash = self.service._compute_event_hash(event)
        event.metadata['session_type'] = 'practical'

        self.assertNotEqual(lecture_hash, self.service._compute_event_hash(event))

    def test_sync_confirmed_high_confidence_updates_existing_event(self):
        """Test that a CONFIRMED event with high confidence updates existing event."""
        event = self._create_personal_event(state=PersonalEventState.CONFIRMED,
                                            confidence=AttendanceConfidence.HIGH)
        # Simulate existing event by UID
        self.mock_adapter.get_event_by_uid.return_value = {
            "id": "existing_event_id", "iCalUID": "personal-123",
        }

        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")

        self.mock_adapter._update_event.assert_called_once()
        self.mock_adapter._delete_event.assert_not_called()
        self.mock_adapter._insert_event.assert_not_called()
        self.assertEqual(event_id, "updated_event_id")

    def test_sync_moved_high_confidence_inserts_event(self):
        """Test that a MOVED event with high confidence triggers an insert (treated as new)."""
        event = self._create_personal_event(state=PersonalEventState.MOVED,
                                            confidence=AttendanceConfidence.HIGH)
        self.mock_adapter.list_events_in_calendar.return_value = []

        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")

        self.mock_adapter._insert_event.assert_called_once()
        self.assertEqual(event_id, "new_event_id")

    def test_repeated_sync_with_same_hash_is_noop(self):
        """An unchanged event returns the Google event ID without rewriting it."""
        event = self._create_personal_event()
        existing_description = self.service._append_hash_and_version_to_description(
            "Existing description", event
        )
        self.mock_adapter.get_event_by_uid.return_value = {
            "id": "google-event-id",
            "iCalUID": event.id,
            "description": existing_description,
        }

        event_id = self.service.sync_personal_event_to_calendar(
            event, "test_calendar_id"
        )

        self.assertEqual(event_id, "google-event-id")
        self.mock_adapter._update_event.assert_not_called()
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
        self.mock_adapter.get_event_by_uid.return_value = {
            "id": "google-event-id",
            "iCalUID": moved.id,
            "description": existing_description,
        }

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
        self.mock_adapter.get_event_by_uid.return_value = {
            "id": "existing_event_id", "iCalUID": "personal-123",
        }

        event_id = self.service.sync_personal_event_to_calendar(event, "test_calendar_id")

        # Should have called delete_event
        self.mock_adapter._delete_event.assert_called_once_with("test_calendar_id", "existing_event_id")
        # Should not have called insert_event
        self.mock_adapter._insert_event.assert_not_called()
        self.assertIsNone(event_id)

    def test_sync_cancelled_no_existing_event_does_nothing(self):
        """Test that a cancelled event with no existing event does nothing."""
        event = self._create_personal_event(state=PersonalEventState.CANCELLED)
        self.mock_adapter.list_events_in_calendar.return_value = []

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
        self.mock_adapter.list_events_in_calendar.return_value = [{
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
        """A version-2 marked projection with a stale UID upgrades in place."""
        event = self._create_personal_event()
        owned_description = (
            "Owned projection\n"
            f"Стабильный ID личного события: {event.id}\n"
            "Хеш: " + "0" * 64 + "\nВерсия: 2\n"
        )
        self.mock_adapter.list_events_in_calendar.return_value = [{
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

    def test_stable_marker_requires_the_entire_id_line(self):
        """A marker prefix must not allow overwriting a foreign event."""
        event = self._create_personal_event()
        self.mock_adapter.list_events_in_calendar.return_value = [{
            "id": "foreign-google-id",
            "iCalUID": "stale-uid",
            "summary": event.title,
            "description": (
                "Стабильный ID личного события: " + event.id + "-other\n"
            ),
            "start": {"dateTime": event.start_time.isoformat()},
        }]

        self.assertIsNone(self.service.sync_personal_event_to_calendar(
            event, "test_calendar_id",
        ))
        self.mock_adapter._update_event.assert_not_called()
        self.mock_adapter._insert_event.assert_not_called()

    def test_timezone_aware_event_search_is_supported(self):
        event = self._create_personal_event()
        event.start_time = event.start_time.replace(tzinfo=timezone.utc)
        event.end_time = event.end_time.replace(tzinfo=timezone.utc)
        self.mock_adapter.list_events_in_calendar.return_value = []

        event_id = self.service.sync_personal_event_to_calendar(
            event, "test_calendar_id"
        )

        self.assertEqual(event_id, "new_event_id")


    def test_insert_passes_destination_using_real_adapter_signature(self):
        event = self._create_personal_event()
        self.service.sync_personal_event_to_calendar(event, "destination")
        args, kwargs = self.mock_adapter._insert_event.call_args
        self.assertEqual(args[0].uid, event.id)
        self.assertEqual(kwargs, {"calendar_id": "destination", "strict": True})
        self.mock_adapter.get_event_by_uid.assert_called_once_with(
            "destination", event.id, strict=True,
        )
        for call in self.mock_adapter.list_events_in_calendar.call_args_list:
            self.assertEqual(call.args[0], "destination")

    def test_failed_lookup_propagates_without_mutation(self):
        cases = (
            (RuntimeError("lookup failed"), None),
            (None, RuntimeError("duplicate lookup failed")),
        )
        for uid_error, duplicate_error in cases:
            with self.subTest(uid_error=uid_error, duplicate_error=duplicate_error):
                self.mock_adapter.reset_mock()
                self.mock_adapter.get_event_by_uid.side_effect = uid_error
                self.mock_adapter.get_event_by_uid.return_value = None
                self.mock_adapter.list_events_in_calendar.side_effect = duplicate_error
                with self.assertRaises(RuntimeError):
                    self.service.sync_personal_event_to_calendar(
                        self._create_personal_event(), "destination"
                    )
                self.mock_adapter._insert_event.assert_not_called()
                self.mock_adapter._update_event.assert_not_called()
                self.mock_adapter._delete_event.assert_not_called()

    def test_real_adapter_mocked_api_insert_rerun_update(self):
        # Bypass initialization entirely: no credentials, files, or network.
        adapter = object.__new__(GoogleCalendarAdapter)
        adapter.calendar_id = "unrelated-default"
        adapter.service = Mock()
        api = adapter.service.events.return_value
        rows = []
        api.list.return_value.execute.side_effect = lambda: {"items": list(rows)}
        api.insert.return_value.execute.side_effect = lambda: {"id": "google-id"}
        api.update.return_value.execute.return_value = {"id": "google-id"}
        service = PersonalEventSyncService(adapter)
        event = self._create_personal_event()

        self.assertEqual(service.sync_personal_event_to_calendar(event, "destination"),
                         "google-id")
        body = api.insert.call_args.kwargs["body"]
        rows.append(dict(body, id="google-id"))
        api.insert.assert_called_once()
        self.assertEqual(api.insert.call_args.kwargs["calendarId"], "destination")

        self.assertEqual(service.sync_personal_event_to_calendar(event, "destination"),
                         "google-id")
        api.insert.assert_called_once()
        api.update.assert_not_called()
        api.delete.assert_not_called()

        event.title = "Changed class"
        self.assertEqual(service.sync_personal_event_to_calendar(event, "destination"),
                         "google-id")
        self.assertEqual(api.update.call_args.kwargs["calendarId"], "destination")
        self.assertEqual(api.update.call_args.kwargs["eventId"], "google-id")
        for call in api.list.call_args_list:
            self.assertEqual(call.kwargs["calendarId"], "destination")
        self.assertEqual(adapter.calendar_id, "unrelated-default")

        api.reset_mock()
        api.list.return_value.execute.side_effect = RuntimeError("read failed")
        with self.assertRaisesRegex(RuntimeError, "read failed"):
            service.sync_personal_event_to_calendar(event, "destination")
        api.insert.assert_not_called()
        api.update.assert_not_called()
        api.delete.assert_not_called()

if __name__ == '__main__':
    unittest.main()
