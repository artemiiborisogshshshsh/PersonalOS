# n8n Workflow Skill

Triggers or manages n8n workflows for orchestrating Personal OS automations.

When invoked, this skill can trigger n8n workflows via webhook or manage workflow executions.

## Setup Required

1. Install and configure n8n instance
2. Create workflows in n8n for desired automations (e.g., schedule sync with retry handling)
3. Configure webhook URLs or API credentials for n8n access
4. Add n8n configuration to Claude Code if needed

## Usage

Run `/n8n-workflow <workflow_name> [data]` to trigger an n8n workflow.

## Implementation

```bash
# This skill assumes n8n is configured and accessible
# The actual implementation would use n8n's API or webhook to trigger workflows
echo "n8n workflow skill configured. To use:"
echo "1. Set up n8n instance"
echo "2. Create workflows for schedule sync, notifications, etc."
echo "3. Configure access credentials"
echo "4. Use HTTP requests or n8n API to trigger workflows"
```