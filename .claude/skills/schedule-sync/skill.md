# Schedule Sync

Synchronizes ICS university schedule to Google Calendar.

When invoked, this skill syncs events from the ICS file to Google Calendar,
using iCalUID for deduplication to prevent duplicate events.

## Usage

Run `/schedule-sync` to synchronize your university schedule to Google Calendar.

## Implementation

```bash
python3 scripts/gcalendar_sync.py
```
