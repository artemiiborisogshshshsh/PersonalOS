#!/usr/bin/env python3
"""
Generate planning scenario note from template.
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

    # Get scenario title from command line argument or prompt
    if len(sys.argv) < 2:
        title = input("Enter scenario title: ")
    else:
        title = " ".join(sys.argv[1:])

    # Paths
    base_dir = Path(__file__).parent.parent
    template_path = base_dir / "99-Templates" / "Planning-Scenario-Template.md"
    output_dir = base_dir / "05-Life" / "Planning-Scenarios"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate metadata
    now = get_current_datetime()
    creation_date = now.strftime("%Y-%m-%d")
    last_updated = creation_date
    generation_time = now.strftime("%Y-%m-%d %H:%M:%S")
    scenario_id = str(uuid.uuid4())[:8]  # Short ID for reference

    # Output filename - sanitize title for filename
    safe_title = "".join(
        char if char.isalnum() or char in [' ', '-', '_'] else '_'
        for char in title
    )
    safe_title = safe_title.strip().replace(' ', '-')
    filename = f"Planning Scenario - {safe_title}.md"
    output_path = output_dir / filename

    if output_path.exists():
        print(f"Planning scenario note already exists: {output_path}")
        return

    # Read template
    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace frontmatter fields
    content = content.replace('created: \n', f'created: {creation_date}\n')

    # Replace placeholders in body
    content = content.replace('{{title}}', title)
    content = content.replace('{{creation_date}}', creation_date)
    content = content.replace('{{last_updated}}', last_updated)
    content = content.replace('{{generation_time}}', generation_time)
    content = content.replace('{{scenario_id}}', scenario_id)

    # Replace other placeholders with empty values or prompts
    replacements = {
        '{{description}}': '',
        '{{scenario_type}}': '',
        '{{time_horizon}}': '',
        '{{key_question}}': '',
        '{{option_a_name}}': 'Option A',
        '{{option_b_name}}': 'Option B',
        '{{option_c_name}}': 'Option C (Status Quo)',
    }

    for placeholder, replacement in replacements.items():
        content = content.replace(placeholder, replacement)

    # Write output
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"Created planning scenario note: {output_path}")
    print(f"Scenario ID: {scenario_id}")


if __name__ == '__main__':
    main()
