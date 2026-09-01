Goal #4: Implement versioning, hashing and idempotency for PersonalUniversityEvent and CalendarSync, чтобы повторная синхронизация никогда не создавала дубли.

Completed:
- Added VERSION constant to CalendarSyncService.
- Implemented _compute_event_hash() method to hash essential event details (summary, description, location, dtstart, dtend, event_type).
- Implemented _get_hash_and_version_from_description() and _append_hash_and_version_to_description() methods.
- Modified sync_ics_to_gcalendar() to use hash-based logic:
  * When existing event found by UID: compare hashes, skip if match, update if mismatch (delete + insert).
  * When checking for duplicates by summary+time: if found, compare hashes to determine if same event with new UID (update) or different event (insert as new).
- This ensures repeated synchronization never creates duplicate events in Google Calendar.

All tests pass, and the service compiles without errors.