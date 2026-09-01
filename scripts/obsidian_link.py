#!/usr/bin/env python3
"""
Link ICS events to Obsidian notes.
For each event, finds or creates a note in the Obsidian vault under
01-University/01-Subjects/ based on the subject.
Adds event metadata to the note.
"""

import re
from pathlib import Path
try:
    from scripts.parse_ics import parse_ics
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from parse_ics import parse_ics


VAULT_ROOT = Path('/Users/artemijborisov/Desktop/clode/forMyAiCalendar')
SUBJECTS_DIR = VAULT_ROOT / '01-University' / '01-Subjects'


def clean_subject(summary):
    """
    Clean summary to get subject name.
    Remove parentheses and their contents, strip.
    Example: "ОС (ЛБ)" -> "ОС"
    """
    # Remove text in parentheses
    cleaned = re.sub(r'\([^)]*\)', '', summary)
    # Also remove any trailing spaces
    cleaned = cleaned.strip()
    # If cleaned becomes empty, fallback to summary
    if not cleaned:
        cleaned = summary.strip()
    return cleaned


def sanitize_filename(filename):
    """
    Make a string safe to use as a filename.
    Keep letters, digits, space, hyphen, underscore, dot.
    Replace any other character with underscore.
    """
    # Replace any character not in allowed set with underscore
    # Allowed: letters (including Unicode), digits, underscore, space, hyphen,
    # dot
    cleaned = re.sub(r'[^\w .-]', '_', filename, flags=re.UNICODE)
    # Strip leading/trailing spaces
    cleaned = cleaned.strip()
    # If empty, return a default
    if not cleaned:
        cleaned = 'untitled'
    # Limit length to 200 chars
    if len(cleaned) > 200:
        cleaned = cleaned[:200]
    return cleaned


def find_or_create_note(subject):
    """
    Find a markdown file in SUBJECTS_DIR that matches subject
    (case-insensitive) after sanitization for filename.
    If not found, create a new file named <sanitized_subject>.md.
    Returns Path to the note.
    """
    # Normalize subject for comparison
    cleaned = clean_subject(subject)
    safe_name = sanitize_filename(cleaned)
    # Look for existing .md files with stem matching safe_name
    # (case-insensitive)
    for md_file in SUBJECTS_DIR.glob('*.md'):
        if md_file.stem.lower() == safe_name.lower():
            return md_file
    # Not found: create new file
    new_file = SUBJECTS_DIR / f"{safe_name}.md"
    # Ensure directory exists
    SUBJECTS_DIR.mkdir(parents=True, exist_ok=True)
    # Create with a basic header using the cleaned subject (not sanitized) for
    # title
    new_file.write_text(f'# {cleaned}\n\n', encoding='utf-8')
    return new_file


def append_event_to_note(note_path, event):
    """
    Append event metadata to the note.
    Avoid duplicating the same event (by UID).
    We'll add a section like:
    ## Event: UID
    - Start: ...
    - End: ...
    - Summary: ...
    - Location: ...
    - Description: ...
    """
    uid = event['uid']
    # Read existing content
    content = (
        note_path.read_text(encoding='utf-8')
        if note_path.exists()
        else ''
    )
    # Check if we already have a section for this UID
    if f'## Event: {uid}' in content:
        # Already present, skip
        return
    # Prepare event section
    start_str = event['dtstart'].isoformat()
    end_str = event['dtend'].isoformat()
    section = f'\n## Event: {uid}\n'
    section += f'- **Start**: {start_str}\n'
    section += f'- **End**: {end_str}\n'
    section += f'- **Summary**: {event["summary"]}\n'
    if event.get('location'):
        section += f'- **Location**: {event["location"]}\n'
    if event.get('description'):
        # Limit description length maybe
        desc = event['description'].replace('\n', ' ')[:200]
        section += f'- **Description**: {desc}\n'
    # Append to note
    with note_path.open('a', encoding='utf-8') as f:
        f.write(section)
    print(f"Added event {uid} to note {note_path.name}")


def link_events(ics_file_path):
    """
    Main function: parse ICS, filter group events, link each to Obsidian note.
    """
    events = parse_ics(ics_file_path)
    group_events = [ev for ev in events if ev['is_group_event']]
    print(f"Found {len(group_events)} events for group 8И41.")
    for ev in group_events:
        subject = clean_subject(ev['summary'])
        note = find_or_create_note(subject)
        append_event_to_note(note, ev)


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python obsidian_link.py <ics_file>")
        sys.exit(1)
    link_events(sys.argv[1])
