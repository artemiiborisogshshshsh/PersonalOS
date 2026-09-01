#!/usr/bin/env python3
"""
Manage Python LSP installation and execution.
"""
import sys
import subprocess


def install():
    print("Installing python-lsp-server via pip...")
    # Install with all useful plugins
    subprocess.run([sys.executable, "-m", "pip", "install",
                   "python-lsp-server[all]"], check=False)


def start():
    print("Python LSP server can be started by your editor's LSP client.")
    print(
        "For example, in VS Code, install the "
        "'Python Language Server' extension."
    )
    print("If you want to run it manually, you can use:")
    print(f"  {sys.executable} -m pylsp --tcp --host 127.0.0.1 --port 5000")
    print("But typically, editors manage the server lifecycle.")


def version():
    try:
        result = subprocess.run([sys.executable, "-m", "pylsp", "--version"],
                                capture_output=True, text=True, check=False)
        if result.returncode == 0:
            print(result.stdout.strip())
        else:
            print(
                "python-lsp-server not installed. "
                "Run 'python_lsp.py install' first."
            )
    except Exception as e:
        print(f"Error getting version: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python_lsp.py {install|start|version}")
        sys.exit(1)
    command = sys.argv[1]
    if command == "install":
        install()
    elif command == "start":
        start()
    elif command == "version":
        version()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == '__main__':
    main()
