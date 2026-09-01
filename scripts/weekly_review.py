#!/usr/bin/env python3
"""
Generate weekly review note from template.
"""
import datetime
from pathlib import Path


def get_week_range(date=None):
    """
    Return (monday, sunday) of the week for given date
    (defaults to today).
"""
    if date is None:
        date = datetime.date.today()
    # Monday is weekday 0 in Python's datetime (if we want Monday as start)
    # ISO week: Monday is day 1, but we can use simple approach.
    # Find most recent Monday (including today if today is Monday)
    monday = date - datetime.timedelta(days=date.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday


def main():
    # Paths
    base_dir = Path(__file__).parent.parent
    template_path = base_dir / "99-Templates" / "Weekly-Review-Template.md"
    output_dir = base_dir / "05-Life" / "Weekly-Reviews"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine week range
    monday, sunday = get_week_range()
    start_str = monday.isoformat()
    end_str = sunday.isoformat()

    # Output filename
    filename = f"Weekly Review - {start_str} to {end_str}.md"
    output_path = output_dir / filename

    if output_path.exists():
        print(f"Weekly review note already exists: {output_path}")
        return

    # Read template
    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace frontmatter fields
    content = content.replace('week-start: \n', f'week-start: {start_str}\n')
    content = content.replace('week-end: \n', f'week-end: {end_str}\n')
    # Replace placeholders in body
    content = content.replace('{{week-start}}', start_str)
    content = content.replace('{{week-end}}', end_str)

    # Write output
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"Created weekly review note: {output_path}")


if __name__ == '__main__':
    main()
