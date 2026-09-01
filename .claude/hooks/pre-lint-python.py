#!/usr/bin/env python3
"""
Hook script to lint Python files before they are saved.
Runs the lint-scripts skill on the changed Python file.
"""
import os
import sys
import subprocess

def lint_python_file(file_path):
    """Lint a single Python file using the lint-scripts skill."""
    if not file_path.endswith('.py'):
        return 0

    # Get the project root
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Run the lint-scripts skill on the specific file
    try:
        # We'll lint the whole scripts directory for simplicity,
        # but in a real hook we might want to lint just the changed file
        result = subprocess.run([
            'python3', '-m', 'flake8',
            os.path.join(project_root, 'scripts')
        ], cwd=project_root, capture_output=True, text=True)

        if result.returncode != 0:
            print("Линтер обнаружил проблемы:")
            print(result.stdout)
            if result.stderr:
                print(result.stderr)
            return result.returncode
        else:
            print("Линтер прошел успешно для Python файлов.")
            return 0
    except Exception as e:
        print(f"Ошибка при запуске линтера: {e}")
        return 1

if __name__ == '__main__':
    # If a file path is provided as argument, lint it; otherwise lint all scripts
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        sys.exit(lint_python_file(file_path))
    else:
        # No specific file, lint all scripts in the directory
        sys.exit(lint_python_file(None))