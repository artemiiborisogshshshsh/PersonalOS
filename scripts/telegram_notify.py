#!/usr/bin/env python3
"""
Send a notification via Telegram Bot API.
"""
import sys
import os
import requests


def main():
    # Get arguments
    args = sys.argv[1:]
    if not args:
        print(
            "Usage: telegram_notify.py <message> "
            "[--chat-id ID] [--token TOKEN]"
        )
        sys.exit(1)
    message = args[0]

    # Override via command line options (optional)
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    token = os.environ.get('TELEGRAM_BOT_TOKEN')

    # Parse optional arguments
    i = 1
    while i < len(args):
        if args[i] == '--chat-id' and i + 1 < len(args):
            chat_id = args[i + 1]
            i += 2
        elif args[i] == '--token' and i + 1 < len(args):
            token = args[i + 1]
            i += 2
        else:
            i += 1

    if not token or not chat_id:
        print(
            "Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set "
            "(via env or --token/--chat-id)"
        )
        sys.exit(1)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'HTML'  # optional, allows basic HTML formatting
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print(f"Notification sent to Telegram chat {chat_id}")
    except Exception as e:
        print(f"Failed to send Telegram notification: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
