# Personal OS AI Calendar - Deployment Guide

This document provides instructions for deploying the Personal OS AI Calendar system in various environments.

## System Overview

The Personal OS AI Calendar is a modular Python application consisting of:

- **Domain Services**: University, Project, and Knowledge management
- **Planning Engine**: Deterministic scheduling engine with constraint satisfaction
- **Infrastructure Adapters**: Google Calendar, Telegram, and n8n integrations
- **CLI Interface**: Command-line interface for system interaction
- **Vision Functions**: Daily briefings, weekly reviews, planning scenarios, decision journal
- **Wrapper Scripts**: Convenience scripts for common operations

## Prerequisites

- Python 3.8 or higher
- pip (Python package manager)
- Git (for version control)
- Optional: External service accounts for full functionality:
  - Google Calendar API credentials
  - Telegram Bot token
  - n8n instance

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd personal-os-ai-calendar
```

### 2. Install Dependencies

The system is designed to work with Python's standard library for core functionality.
External service adapters may require additional packages:

```bash
# Optional: Install requests for adapter functionality
pip install requests

# For development/testing
pip install pytest
```

### 3. Environment Configuration

Create a `.env` file in the root directory (or set environment variables):

```dotenv
# Google Calendar API
GOOGLE_CALENDAR_CREDENTIALS=~/.config/google/credentials.json
GOOGLE_CALENDAR_TOKEN=~/.config/google/token.json
GOOGLE_CALENDAR_NAME=Personal OS AI Calendar

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here
TELEGRAM_PARSE_MODE=HTML

# n8n
N8N_BASE_URL=http://localhost:5678
N8N_API_KEY=your_n8n_api_key_here

# Scheduler
SCHEDULER_INTERVAL=300  # seconds (default: 5 minutes)

# Mode
DRY_RUN=0  # Set to 1 for testing without external API calls
```

Note: For initial testing, you can set `DRY_RUN=1` to validate the system without external API calls.

## Deployment Options

### Option 1: Direct Execution (Development/Testing)

```bash
# Run the CLI directly
python3 -m cli.main --help

# Run specific commands
python3 -m cli.main university list
python3 -m cli.main project create "My Project" --description "A test project"
python3 -m cli.main knowledge create-idea "My Idea"
```

### Option 2: Using Wrapper Scripts

Make the scripts executable and add them to your PATH:

```bash
chmod +x scripts/personal-os-calendar
chmod +x scripts/scheduler-agent
chmod +x scripts/telegram-bot
chmod +x scripts/daily_briefing.py
chmod +x scripts/weekly_review.py
chmod +x scripts/planning_scenario.py
chmod +x scripts/decision_journal.py

# Example usage
./scripts/personal-os-calendar university list
./scripts/personal-os-calendar project create "My Project"
./scripts/daily_briefing.py
./scripts/weekly_review.py
```

### Option 3: Docker Deployment

The system includes a docker-compose file for running n8n:

```bash
# Start n8n service
docker-compose up -d n8n

# The main application runs outside Docker but can connect to the n8n service
```

### Option 4: Systemd Service (Linux Production)

Create systemd services for the scheduler agent:

```ini
# /etc/systemd/system/personal-os-calendar-scheduler.service
[Unit]
Description=Personal OS AI Calendar Scheduler Agent
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/personal-os-ai-calendar
Environment=DRY_RUN=0
EnvironmentFile=/path/to/personal-os-ai-calendar/.env
ExecStart=/path/to/personal-os-ai-calendar/scripts/scheduler-agent
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable personal-os-calendar-scheduler
sudo systemctl start personal-os-calendar-scheduler
```

### Option 5: LaunchAgent (macOS)

Create a LaunchAgent plist file:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>personal-os-calendar-scheduler</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/personal-os-ai-calendar/scripts/scheduler-agent</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DRY_RUN</key>
        <string>0</string>
        <key>CONFIG_FILE</key>
        <string>/path/to/personal-os-ai-calendar/.env</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>/path/to/personal-os-ai-calendar</string>
</dict>
</plist>
```

Save as `~/Library/LaunchAgents/personal-os-calendar-scheduler.plist` and load:
```bash
launchctl load ~/Library/LaunchAgents/personal-os-calendar-scheduler.plist
```

## Directory Structure

The system expects the following directory structure for vision functions:

```
00-Inbox/
01-University/
02-Teaching/
03-Projects/
04-Development/
05-Life/
    Daily-Briefings/
    Weekly-Reviews/
    Planning-Scenarios/
    Decision-Journal/
09-Archives/
99-Templates/
    Weekly-Review-Template.md
    Daily-Briefing-Template.md
    Planning-Scenario-Template.md
    Decision-Journal-Template.md
999-Templates/
```

These directories are created automatically when needed.

## Health Checks and Monitoring

### Built-in Health Checks

Each adapter provides a health check method:
- Google Calendar: Checks API connectivity and token validity
- Telegram: Checks bot API accessibility  
- n8n: Checks instance availability and API key validity

You can test health via CLI:
```bash
python3 -m cli.main adapter test
```

### Logging

The system outputs status information to stdout/stderr.
For production deployment, consider redirecting output to log files:
```bash
./scripts/scheduler-agent >> logs/scheduler.log 2>&1
```

## Backup and Data Persistence

The system stores data in:
- User's note-taking system (obsidian-standard markdown files in the directory structure)
- Configuration in `.env` file
- No internal database - all data is stored as files

To backup:
```bash
# Backup the entire directory (excluding virtual environments and caches)
tar -czf personal-os-calendar-backup-$(date +%Y%m%d).tar.gz \
    --exclude=__pycache__ \
    --exclude=.pytest_cache \
    --exclude=.env \
    personal-os-ai-calendar/
```

## Upgrading

To upgrade to a new version:

```bash
git pull origin main  # or your default branch
# Check for any new dependencies
# Restart services if running as daemons
```

## Troubleshooting

### Common Issues

1. **Module not found errors**: Ensure you're running from the project root or have PYTHONPATH set correctly
2. **Permission issues**: Make sure script files are executable (`chmod +x`)
3. **External service connection failures**: Verify API keys, tokens, and network connectivity
4. **Port conflicts**: If running n8n via Docker, ensure port 5678 is available

### Debugging

Enable verbose output:
```bash
# Set DRY_RUN=1 to test without external calls
DRY_RUN=1 python3 -m cli.main adapter test

# Run end-to-end validation
python3 end_to_end_test.py
```

## Extending the System

### Adding New Domain Services

1. Create a new service in `services/` following the pattern of existing services
2. Add import and instantiation in `cli/main.py`
3. Add corresponding CLI commands
4. Update `services/__init__.py` to export the new service

### Adding New Adapters

1. Create a new adapter in `adapters/` inheriting from the appropriate base class
2. Add initialization logic in `cli/main.py`
3. Add adapter-specific CLI commands
4. Update `adapters/__init__.py` to export the new adapter

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For issues and questions, please refer to the project documentation or contact the maintainers.