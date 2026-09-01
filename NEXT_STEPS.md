# Next Steps for Personal OS MVP (University Schedule Integration)

## What has been set up

1. **Directory structure**:
   - `.claude/skills/` – for custom skills
   - `.claude/hooks/` – for hook scripts (empty for now)
   - `scripts/` – Python scripts for core functionality
   - `EXTENSIONS.md` – inventory of extensions

2. **Core scripts**:
   - `scripts/parse_ics.py` – parses ICS file and filters events for group 8И41
   - `scripts/gcalendar_sync.py` – syncs events to Google Calendar (uses iCalUID for deduplication)
   - `scripts/obsidian_link.py` – links events to Obsidian notes in `01-University/01-Subjects/`
   - `scripts/prep_blocks.py` – generates preparation block events in Google Calendar based on event type

3. **Skills** (invokable via `/skill-name`):
   - `schedule-sync` – Level 1: basic ICS → Google Calendar sync
   - `obsidian-link` – Level 3: link events to Obsidian notes
   - `prep-blocks` – Level 4: generate preparation blocks

   (Note: Level 2 validation (group filtering, deduplication, normalization) is built into `schedule-sync` and `obsidian_link`.)

## What you need to do before testing

### 1. Set up Google Calendar API credentials
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a project or use an existing one.
   - Enable the Google Calendar API for that project.
   - Create OAuth 2.0 Client IDs (Desktop app) and download the JSON file.
   - Place the downloaded JSON file at:
     ```
     ~/.config/google/credentials.json
     ```
   - The first time you run a skill that uses Google Calendar, a browser window will open for you to authorize the application. After authorization, a token file will be saved at `~/.config/google/token.json`.

### 2. (Optional) Adjust preparation durations
   - Edit `scripts/prep_blocks.py` function `prep_duration_for_type` to change the base preparation minutes for lecture (ЛК), lab (ЛБ), practical (ПР).
   - You can also add subject-specific overrides.

## Testing the MVP

Run the skills in the following order to verify each level:

### Level 1 – Basic sync
```
/schedule-sync
```
   - Check your Google Calendar for a new calendar named "University Schedule" (or the name you specified).
   - Events from the ICS file for group 8И41 should appear.
   - Running the skill again should not create duplicate events (thanks to iCalUID deduplication).

### Level 2 – Validation (built-in)
   - Verify that only events containing group 8И41 in the description are synced.
   - Check that duplicate events (if you manually duplicate an entry in the ICS) are not created twice.

### Level 3 – Obsidian linking
```
/obsidian-link
```
   - Check the directory `01-University/01-Subjects/` for markdown notes.
   - Each note should have a title (cleaned subject) and sections for each event with metadata (start, end, summary, location, description).
   - Running the skill again will append new events but will not duplicate event sections (checked by UID).

### Level 4 – Preparation blocks
```
/prep-blocks
```
   - Check your Google Calendar for new events with summary starting with "Подготовка: ".
   - These preparation blocks should be scheduled before each event, with duration based on event type.
   - The skill avoids overlapping with existing events and will shift preparation earlier if necessary (up to 2 hours before the event).

## Extending the system

- You can add more skills (e.g., `/weekly-review`, `/schedule-audit`) following the same pattern.
- You can add hooks in `.claude/hooks/` to run formatters or linters on script changes.
- You can integrate Telegram for notifications using the official Telegram plugin or MCP.
- You can use n8n for more complex orchestration if needed.

## Notes

- The system is designed to be idempotent: running the same skill multiple times should not create duplicate data.
- The ICS file is assumed to be located at the project root with the name `8И41, (31.08.2026 - 13.09.2026).ics`. If you rename or move the file, update the arguments in the skill definitions.
- All scripts log to stdout; you can observe progress when invoking skills.

## Reminder

Never share your `credentials.json` or `token.json` with anyone. Keep them secure.