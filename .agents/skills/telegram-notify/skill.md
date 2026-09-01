# Telegram Notify Skill

Sends notifications via Telegram using MCP server integration.

When invoked, this skill will send a message to a configured Telegram chat or user via the Telegram MCP server.

## Setup Required

1. Install a Telegram MCP server (e.g., from https://github.com/chigwell/telegram-mcp or https://github.com/antongsm/mcp-telegram)
2. Configure the MCP server with your Telegram bot token and chat ID
3. Add the MCP server to your Codex configuration

## Usage

Run `/telegram-notify <message>` to send a notification via Telegram.

## Implementation

```bash
# This skill assumes the Telegram MCP server is configured and available
# The actual implementation would use the MCP server to send messages
echo "Telegram notification skill configured. To use:"
echo "1. Install Telegram MCP server"
echo "2. Configure with your bot credentials"
echo "3. Use MCP tools to send messages"
```