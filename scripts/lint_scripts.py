#!/usr/bin/env python3
"""
Lint Python scripts in the scripts directory using flake8.
"""
import os
import subprocess
import sys


def main():
    scripts_dir = os.path.join(os.path.dirname(__file__))
    # Check if flake8 is available
    try:
        subprocess.run([sys.executable, "-m", "flake8", "--version"],
                       check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Installing flake8...")
        subprocess.run([sys.executable, "-m", "pip", "install", "flake8"],
                       check=True)

    # Run flake8 on the scripts directory
    result = subprocess.run(
        [sys.executable, "-m", "flake8", scripts_dir],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print("No linting errors found.")
    else:
        print("Linting errors:")
        print(result.stdout)
        if result.stderr:
            print("Stderr:", result.stderr)
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
