#!/usr/bin/env python3
"""
Parse ICS file and extract events.
"""

import sys
import re
from icalendar import Calendar


def parse_ics(file_path):
    with open(file_path, 'rb') as f:
        calendar = Calendar.from_ical(f.read())

    events = []
    for component in calendar.walk():
        if component.name == "VEVENT":
            uid_value = component.get('UID')
            start_value = component.get('DTSTART')
            if uid_value is None or start_value is None:
                # UID and DTSTART are the minimum identity/time contract for
                # deterministic reconciliation. Ignore malformed VEVENTs.
                continue

            uid = str(uid_value)
            summary = str(component.get('SUMMARY') or '')
            description = str(component.get('DESCRIPTION') or '')
            location = str(component.get('LOCATION') or '')
            dtstart = start_value.dt
            end_value = component.get('DTEND')
            duration_value = component.get('DURATION')
            if end_value is not None:
                dtend = end_value.dt
            elif duration_value is not None:
                dtend = dtstart + duration_value.dt
            else:
                # Cancellation tombstones often omit DTEND. A zero-duration
                # end preserves the source identity without crashing sync.
                dtend = dtstart
            status = str(component.get('STATUS') or 'CONFIRMED').lower()
            try:
                sequence = int(component.get('SEQUENCE') or 0)
            except (TypeError, ValueError):
                sequence = 0
            # Determine if event belongs to group 8И41
            # Check description or summary for group marker
            group_pattern = r'8И41'
            is_group_event = bool(
                re.search(group_pattern, description)
            ) or bool(re.search(group_pattern, summary))

            # Normalize whitespace in summary
            summary_normalized = ' '.join(summary.split())
            # Extract event type from summary parentheses, e.g., (ЛК), (ЛБ),
            # (ПР)
            event_type_match = re.search(r'\(([А-Яа-я]+)\)', summary)
            event_type = event_type_match.group(
                1) if event_type_match else None

            events.append({
                'uid': uid,
                'summary': summary,
                'summary_normalized': summary_normalized,
                'event_type': event_type,
                'description': description,
                'location': location,
                'dtstart': dtstart,
                'dtend': dtend,
                'status': status,
                'sequence': sequence,
                'is_group_event': is_group_event
            })
    return events


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse_ics.py <ics_file>")
        sys.exit(1)
    file_path = sys.argv[1]
    events = parse_ics(file_path)
    print(f"Found {len(events)} events")
    for ev in events:
        if ev['is_group_event']:
            print(f"UID: {ev['uid']}")
            print(f"Summary: {ev['summary']}")
            print(f"Summary Normalized: {ev['summary_normalized']}")
            print(f"Event Type: {ev['event_type']}")
            print(f"Start: {ev['dtstart']}")
            print(f"End: {ev['dtend']}")
            print(f"Location: {ev['location']}")
            print(f"Description: {ev['description'][:100]}...")
            print("---")


if __name__ == "__main__":
    main()
