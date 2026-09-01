#!/usr/bin/env python3
"""
Generate decision journal note from template.
"""
import datetime
import uuid
from pathlib import Path


def get_current_datetime(date=None):
    """
    Return current date and time
    """
    if date is None:
        date = datetime.datetime.now()
    return date


def main():
    import sys

    # Get decision title from command line argument or prompt
    if len(sys.argv) < 2:
        title = input("Enter decision title: ")
    else:
        title = " ".join(sys.argv[1:])

    # Paths
    base_dir = Path(__file__).parent.parent
    template_path = base_dir / "99-Templates" / "Decision-Journal-Template.md"
    output_dir = base_dir / "05-Life" / "Decision-Journal"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate metadata
    now = get_current_datetime()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    generation_time = now.strftime("%Y-%m-%d %H:%M:%S")
    decision_id = str(uuid.uuid4())[:8]  # Short ID for reference

    # Output filename - sanitize title for filename
    safe_title = "".join(
        char if char.isalnum() or char in [' ', '-', '_'] else '_'
        for char in title
    )
    safe_title = safe_title.strip().replace(' ', '-')
    filename = f"Decision Journal - {safe_title} - {date_str}.md"
    output_path = output_dir / filename

    if output_path.exists():
        print(f"Decision journal note already exists: {output_path}")
        return

    # Read template
    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace frontmatter fields
    content = content.replace('date: \n', f'date: {date_str}\n')

    # Replace placeholders in body
    content = content.replace('{{date}}', date_str)
    content = content.replace('{{time}}', time_str)
    content = content.replace('{{generation_time}}', generation_time)
    content = content.replace('{{decision_id}}', decision_id)
    content = content.replace('{{decision_title}}', title)

    # Replace other placeholders with empty values or prompts
    replacements = {
        '{{context}}': '',
        '{{decision_question}}': '',
        '{{option_1}}': 'Option 1',
        '{{option_2}}': 'Option 2',
        '{{option_3}}': 'Option 3 (Status Quo)',
        '{{chosen_option}}': '',
        '{{decision_time}}': time_str,
        '{{follow_up_date}}': (
            now + datetime.timedelta(days=7)
        ).strftime("%Y-%m-%d"),
    }

    for placeholder, replacement in replacements.items():
        content = content.replace(placeholder, replacement)

    # Write output
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"Created decision journal note: {output_path}")
    print(f"Decision ID: {decision_id}")


if __name__ == '__main__':
    main()
