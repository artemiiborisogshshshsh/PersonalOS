"""
n8n adapter for Personal OS AI Calendar system.
Provides infrastructure for triggering workflows and exchanging data with n8n.
"""

import os
import json
import logging
from typing import Dict, Any, Optional, List
import requests

from .base_adapter import AutomationAdapter


class N8NAdapter(AutomationAdapter):
    """
    n8n adapter implementation.
    Handles triggering workflows and data exchange with n8n automation platform.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize n8n adapter.

        Args:
            config: Configuration dictionary with keys:
                - base_url: Base URL of n8n instance (e.g., http://localhost:5678)
                - api_key: API key for n8n instance (optional if using webhooks)
                - webhook_url: Base URL for webhooks (optional)
                - timeout: Request timeout in seconds (default: 30)
        """
        super().__init__(config)
        self.base_url = self.config.get('base_url') or os.environ.get('N8N_BASE_URL', 'http://localhost:5678')
        self.api_key = self.config.get('api_key') or os.environ.get('N8N_API_KEY')
        self.webhook_url = self.config.get('webhook_url') or os.environ.get('N8N_WEBHOOK_URL')
        self.timeout = self.config.get('timeout', 30)
        self.session = requests.Session()

        # Set up authentication if API key provided
        if self.api_key:
            self.session.headers.update({'X-N8N-API-KEY': self.api_key})

    async def initialize(self) -> bool:
        """
        Initialize n8n adapter by testing connection to n8n instance.

        Returns:
            bool: True if initialization successful
        """
        try:
            # Test connection to n8n instance
            response = self.session.get(
                f"{self.base_url}/rest/workflows",
                timeout=self.timeout
            )
            response.raise_for_status()

            # Check if we got a valid response
            workflows = response.json()
            if isinstance(workflows, list):
                print(f"n8n adapter initialized. Found {len(workflows)} workflows.")
                self._set_initialized(True)
                return True
            else:
                print("Unexpected response from n8n API")
                self._set_initialized(False)
                return False

        except Exception as e:
            print(f"Failed to initialize n8n adapter: {e}")
            self._set_initialized(False)
            return False

    async def health_check(self) -> bool:
        """
        Check if n8n instance is accessible.

        Returns:
            bool: True if healthy, False otherwise
        """
        if not self._is_initialized:
            return False

        try:
            response = self.session.get(
                f"{self.base_url}/rest/workflows",
                timeout=5
            )
            return response.status_code == 200
        except Exception:
            return False

    async def shutdown(self) -> bool:
        """
        Shutdown n8n adapter.

        Returns:
            bool: True (close session)
        """
        self.session.close()
        self._set_initialized(False)
        return True

    async def trigger_workflow(self, workflow_name: str,
                             payload: Optional[Dict[str, Any]] = None) -> bool:
        """
        Trigger an n8n workflow by name.

        Args:
            workflow_name: Name of workflow to trigger
            payload: Data to pass to the workflow

        Returns:
            bool: True if workflow triggered successfully
        """
        if not self._is_initialized:
            print("n8n adapter not initialized")
            return False

        try:
            # First, get the workflow by name
            response = self.session.get(
                f"{self.base_url}/rest/workflows",
                timeout=self.timeout
            )
            response.raise_for_status()

            workflows = response.json()
            target_workflow = None

            for workflow in workflows:
                if workflow.get('name') == workflow_name:
                    target_workflow = workflow
                    break

            if not target_workflow:
                print(f"Workflow '{workflow_name}' not found")
                return False

            workflow_id = target_workflow['id']

            # Trigger the workflow
            trigger_data = {}
            if payload:
                trigger_data['data'] = payload

            response = self.session.post(
                f"{self.base_url}/rest/workflows/{workflow_id}/trigger",
                json=trigger_data,
                timeout=self.timeout
            )
            response.raise_for_status()

            result = response.json()
            if result.get('success'):
                print(f"Workflow '{workflow_name}' triggered successfully")
                return True
            else:
                print(f"Failed to trigger workflow: {result}")
                return False

        except Exception as e:
            print(f"Error triggering n8n workflow: {e}")
            return False

    async def trigger_workflow_by_id(self, workflow_id: str,
                                   payload: Optional[Dict[str, Any]] = None) -> bool:
        """
        Trigger an n8n workflow by ID.

        Args:
            workflow_id: ID of workflow to trigger
            payload: Data to pass to the workflow

        Returns:
            bool: True if workflow triggered successfully
        """
        if not self._is_initialized:
            print("n8n adapter not initialized")
            return False

        try:
            trigger_data = {}
            if payload:
                trigger_data['data'] = payload

            response = self.session.post(
                f"{self.base_url}/rest/workflows/{workflow_id}/trigger",
                json=trigger_data,
                timeout=self.timeout
            )
            response.raise_for_status()

            result = response.json()
            if result.get('success'):
                print(f"Workflow {workflow_id} triggered successfully")
                return True
            else:
                print(f"Failed to trigger workflow: {result}")
                return False

        except Exception as e:
            print(f"Error triggering n8n workflow by ID: {e}")
            return False

    async def trigger_webhook(self, webhook_path: str,
                            payload: Optional[Dict[str, Any]] = None) -> bool:
        """
        Trigger an n8n webhook.

        Args:
            webhook_path: Webhook path (will be appended to base webhook URL)
            payload: Data to send to the webhook

        Returns:
            bool: True if webhook triggered successfully
        """
        if not self._is_initialized:
            print("n8n adapter not initialized")
            return False

        if not self.webhook_url:
            print("Webhook URL not configured")
            return False

        try:
            url = f"{self.webhook_url.rstrip('/')}/{webhook_path.lstrip('/')}"

            response = self.session.post(
                url,
                json=payload or {},
                timeout=self.timeout
            )
            response.raise_for_status()

            print(f"Webhook {webhook_path} triggered successfully")
            return True

        except Exception as e:
            print(f"Error triggering n8n webhook: {e}")
            return False

    async def register_webhook(self, webhook_url: str,
                             event_types: List[str]) -> bool:
        """
        Register a webhook for receiving events from n8n.
        Note: n8n typically uses fixed webhook URLs configured in workflows,
        so this is more of a placeholder for validation.

        Args:
            webhook_url: URL to register as webhook
            event_types: List of event types to listen for

        Returns:
            bool: True if validation successful
        """
        if not self._is_initialized:
            print("n8n adapter not initialized")
            return False

        try:
            # Validate that the URL is reachable (basic check)
            response = self.session.options(webhook_url, timeout=10)
            # We don't expect a specific response, just that it doesn't fail catastrophically
            print(f"Webhook URL {webhook_url} validated for event types: {event_types}")
            return True
        except Exception as e:
            # OPTIONS might not be allowed, try GET instead
            try:
                response = self.session.get(webhook_url, timeout=10)
                print(f"Webhook URL {webhook_url} validated (GET) for event types: {event_types}")
                return True
            except Exception as e2:
                print(f"Could not validate webhook URL: {e2}")
                return False

    async def get_workflows(self) -> List[Dict[str, Any]]:
        """
        Get list of available workflows from n8n instance.

        Returns:
            List of workflow dictionaries
        """
        if not self._is_initialized:
            print("n8n adapter not initialized")
            return []

        try:
            response = self.session.get(
                f"{self.base_url}/rest/workflows",
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error getting workflows from n8n: {e}")
            return []

    async def get_workflow_executions(self, workflow_id: str,
                                    limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent executions of a workflow.

        Args:
            workflow_id: ID of workflow
            limit: Maximum number of executions to return

        Returns:
            List of execution dictionaries
        """
        if not self._is_initialized:
            print("n8n adapter not initialized")
            return []

        try:
            response = self.session.get(
                f"{self.base_url}/rest/workflows/{workflow_id}/executions",
                params={'limit': limit},
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error getting workflow executions: {e}")
            return []