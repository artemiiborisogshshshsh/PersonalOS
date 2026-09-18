import asyncio
import sys
from unittest.mock import Mock

import pytest

from adapters.n8n_adapter import N8NAdapter
from scripts import n8n_workflow


SECRET = "https://n8n.private.test/hook?token=TOP_SECRET"


def test_adapter_failure_logs_do_not_echo_exception_details(capsys):
    adapter = N8NAdapter({"base_url": SECRET, "api_key": "TOP_SECRET"})
    adapter.session.get = Mock(side_effect=RuntimeError(SECRET))

    assert asyncio.run(adapter.initialize()) is False

    output = capsys.readouterr().out
    assert "Failed to initialize n8n adapter" in output
    assert "TOP_SECRET" not in output
    assert "n8n.private.test" not in output


def test_adapter_failure_logs_do_not_echo_provider_response_body(capsys):
    adapter = N8NAdapter()
    adapter._set_initialized(True)
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"success": False, "error": SECRET}
    adapter.session.post = Mock(return_value=response)

    assert asyncio.run(adapter.trigger_workflow_by_id("workflow-1")) is False

    output = capsys.readouterr().out
    assert "Failed to trigger workflow" in output
    assert "TOP_SECRET" not in output
    assert "n8n.private.test" not in output


def test_cli_failure_does_not_echo_request_exception(capsys, monkeypatch):
    monkeypatch.setattr(n8n_workflow.requests, "post", Mock(side_effect=RuntimeError(SECRET)))
    monkeypatch.setattr(sys, "argv", [
        "n8n_workflow.py", "workflow-1", "--url", SECRET, "--api-key", "TOP_SECRET",
    ])

    with pytest.raises(SystemExit) as exited:
        n8n_workflow.main()

    assert exited.value.code == 1
    output = capsys.readouterr().out
    assert "Failed to trigger workflow" in output
    assert "TOP_SECRET" not in output
    assert "n8n.private.test" not in output


def test_cli_webhook_success_does_not_echo_webhook_url(capsys, monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    monkeypatch.setattr(n8n_workflow.requests, "post", Mock(return_value=response))
    monkeypatch.setattr(sys, "argv", ["n8n_workflow.py", SECRET, "--webhook"])

    n8n_workflow.main()

    output = capsys.readouterr().out
    assert "Successfully triggered webhook" in output
    assert "TOP_SECRET" not in output
    assert "n8n.private.test" not in output


def test_cli_workflow_success_does_not_echo_workflow_identifier(capsys, monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    monkeypatch.setattr(n8n_workflow.requests, "post", Mock(return_value=response))
    monkeypatch.setattr(sys, "argv", [
        "n8n_workflow.py", "workflow-TOP_SECRET", "--url", "https://n8n.test", "--api-key", "key",
    ])

    n8n_workflow.main()

    output = capsys.readouterr().out
    assert "Successfully triggered workflow" in output
    assert "TOP_SECRET" not in output
