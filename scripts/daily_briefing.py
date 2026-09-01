#!/usr/bin/env python3
"""
Generate daily briefing note from template.
"""
import datetime
from pathlib import Path


def get_today_date(date=None):
    """
    Return today's date in YYYY-MM-DD format
    """
    if date is None:
        date = datetime.date.today()
    return date.isoformat()


def main():
    # Paths
    base_dir = Path(__file__).parent.parent
    template_path = base_dir / "99-Templates" / "Daily-Briefing-Template.md"
    output_dir = base_dir / "05-Life" / "Daily-Briefings"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine today's date
    today_str = get_today_date()
    generation_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Output filename
    filename = f"Daily Briefing - {today_str}.md"
    output_path = output_dir / filename

    if output_path.exists():
        print(f"Daily briefing note already exists: {output_path}")
        return

    # Read template
    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace frontmatter fields
    content = content.replace('date: \n', f'date: {today_str}\n')
    # Replace placeholders in body
    content = content.replace('{{date}}', today_str)
    content = content.replace('{{generation_time}}', generation_time)

    # Write output
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"Created daily briefing note: {output_path}")


if __name__ == '__main__':
    main()
