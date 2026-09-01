# Python LSP Skill

Configures and uses Python Language Server Protocol (pylsp or similar) for enhanced code editing assistance.

When invoked, this skill helps set up or configure Python LSP for better autocomplete, diagnostics, and code navigation in Personal OS development.

## Setup Required

1. Install Python LSP server: `pip install python-lsp-server[all]` or similar
2. Configure Claude Code to use the LSP server for Python files
3. Optionally install additional plugins for Django, Flask, etc.

## Usage

Run `/python-lsp` to check or configure Python LSP settings.

## Implementation

```bash
# This skill helps configure Python LSP for Claude Code
echo "Python LSP skill configured. To use:"
echo "1. Install python-lsp-server: pip install python-lsp-server[all]"
echo "2. Configure Claude Code settings to use LSP for .py files"
echo "3. Enjoy enhanced code completion, diagnostics, and navigation"
```