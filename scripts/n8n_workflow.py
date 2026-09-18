#!/usr/bin/env python3
"""
Trigger an n8n workflow.
"""
import sys
import os
import json
import argparse
import requests


def main():
    parser = argparse.ArgumentParser(description='Trigger n8n workflow')
    parser.add_argument(
        'workflow_id',
        nargs='?',
        help='Workflow ID or webhook URL')
    parser.add_argument('--payload', type=str, help='JSON payload to send')
    parser.add_argument('--url', type=str, help='Base URL of n8n instance')
    parser.add_argument(
        '--api-key',
        type=str,
        help='API key for authentication')
    parser.add_argument(
        '--webhook',
        action='store_true',
        help='Treat workflow_id as webhook URL')
    args = parser.parse_args()

    if not args.workflow_id:
        parser.print_help()
        sys.exit(1)

    # Determine payload
    payload = {}
    if args.payload:
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError:
            print("Error: --payload must be valid JSON")
            sys.exit(1)

    if args.webhook:
        # Use workflow_id as the webhook URL
        webhook_url = args.workflow_id
        if not webhook_url.startswith('http'):
            print("Error: webhook URL must start with http or https")
            sys.exit(1)
        try:
            response = requests.post(webhook_url, json=payload)
            response.raise_for_status()
            print("Successfully triggered webhook")
        except Exception:
            print("Failed to trigger webhook")
            sys.exit(1)
    else:
        # Use n8n API to execute a workflow
        base_url = args.url or os.environ.get('N8N_BASE_URL')
        api_key = args.api_key or os.environ.get('N8N_API_KEY')
        if not base_url or not api_key:
            print(
                "Error: N8N_BASE_URL and N8N_API_KEY must be set "
                "(via env or --url/--api-key)"
            )
            sys.exit(1)
        # Ensure base_url does not have trailing slash
        base_url = base_url.rstrip('/')
        execute_url = f"{base_url}/api/v1/workflows/{args.workflow_id}/execute"
        headers = {
            'X-N8N-API-KEY': api_key
        }
        try:
            response = requests.post(
                execute_url, json=payload, headers=headers)
            response.raise_for_status()
            print("Successfully triggered workflow")
        except Exception:
            print("Failed to trigger workflow")
            sys.exit(1)


if __name__ == '__main__':
    main()
