#!/usr/bin/env python3
"""
Demo script to test preparation integration with attendance service.
"""
from datetime import datetime
from models import UniversityEvent, EventType
from services.attendance_service import AttendanceRuleService
from services.preparation.preparation_integration_service import PreparationIntegrationService
from services.preparation.preparation_block_service import PreparationBlockService
from adapters.google_calendar_adapter import GoogleCalendarAdapter
from adapters.telegram_adapter import TelegramAdapter
from adapters.n8n_adapter import N8NAdapter

# Create dummy adapters that do nothing
class DummyCalendarAdapter:
    def _get_or_create_calendar(self, calendar_summary: str) -> str:
        return "dummy_calendar_id"
    def _get_events_in_range(self, time_min: str, time_max: str):
        return []
    def event_exists_by_uid(self, calendar_id: str, uid: str) -> bool:
        return False
    def _insert_event(self, event_data):
        return "dummy_event_id"

class DummyNotificationAdapter:
    def send_notification(self, message: str) -> None:
        pass

class DummyAutomationAdapter:
    def trigger_webhook(self, payload: dict) -> None:
        pass

def main():
    print("Creating preparation block service with dummy adapters...")
    calendar_adapter = DummyCalendarAdapter()
    notification_adapter = DummyNotificationAdapter()
    automation_adapter = DummyAutomationAdapter()

    prep_block_service = PreparationBlockService(
        calendar_adapter=calendar_adapter,
        notification_adapter=notification_adapter,
        automation_adapter=automation_adapter
    )

    print("Creating preparation integration service...")
    prep_integration_service = PreparationIntegrationService(prep_block_service)

    print("Creating attendance rule service with preparation integration...")
    attendance_service = AttendanceRuleService(
        preparation_integration_service=prep_integration_service
    )

    # Create a sample university event that should match (based on stable ID containing "math")
    event = UniversityEvent(
        uid="event-123",
        summary="Математический анализ лекция",
        description="Лекция по пределам. Преподаватель: Иванов И.И.",
        location="Аудитория 205",
        dtstart=datetime(2026, 9, 1, 10, 0),
        dtend=datetime(2026, 9, 1, 11, 30),
        event_type=EventType.LECTURE,
        is_group_event=True
    )

    print(f"\nProcessing event: {event.summary}")
    personal_events = attendance_service.find_personal_events([event])

    print(f"\nFound {len(personal_events)} personal events:")
    for pe in personal_events:
        print(f"  - Title: {pe.title}")
        print(f"    State: {pe.state.value}")
        print(f"    Is matched: {pe.is_matched()}")
        if pe.is_matched():
            print(f"    Match confidence: {pe.match_confidence.value if pe.match_confidence else 'None'}")
            print(f"    Match type: {pe.match_type.value if pe.match_type else 'None'}")
            print(f"    Preparation block UID: {pe.preparation_block_uid}")
            print(f"    Task UID: {pe.task_uid}")
            print(f"    Metadata: {pe.metadata}")
        print()

    print("Demo completed successfully!")

if __name__ == "__main__":
    main()