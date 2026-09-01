#!/usr/bin/env python3
"""
Quick system validation script for Personal OS AI Calendar.
"""
import os
import sys
from pathlib import Path

def check_structure():
    """Check that required directories exist."""
    base_dir = Path('.')
    required_dirs = [
        '00-Inbox',
        '01-University',
        '02-Teaching',
        '03-Projects',
        '04-Development',
        '05-Life',
        '05-Life/Daily-Briefings',
        '05-Life/Weekly-Reviews',
        '05-Life/Planning-Scenarios',
        '05-Life/Decision-Journal',
        '09-Archives',
        '99-Templates',
        '999-Templates',
        'adapters',
        'services',
        'cli',
        'scripts',
        'tests'
    ]

    missing = []
    for dir_path in required_dirs:
        if not (base_dir / dir_path).exists():
            missing.append(dir_path)

    if missing:
        print(f"❌ Missing directories: {missing}")
        return False
    else:
        print("✅ Directory structure OK")
        return True

def check_templates():
    """Check that required templates exist."""
    templates_dir = Path('99-Templates')
    required_templates = [
        'Weekly-Review-Template.md',
        'Daily-Briefing-Template.md',
        'Planning-Scenario-Template.md',
        'Decision-Journal-Template.md'
    ]

    missing = []
    for template in required_templates:
        if not (templates_dir / template).exists():
            missing.append(template)

    if missing:
        print(f"❌ Missing templates: {missing}")
        return False
    else:
        print("✅ Template files OK")
        return True

def check_key_files():
    """Check that key system files exist."""
    key_files = [
        'models.py',
        'planning_engine.py',
        'services/__init__.py',
        'services/university_service.py',
        'services/project_service.py',
        'services/knowledge_service.py',
        'adapters/__init__.py',
        'adapters/base_adapter.py',
        'adapters/google_calendar_adapter.py',
        'adapters/telegram_adapter.py',
        'adapters/n8n_adapter.py',
        'cli/__init__.py',
        'cli/main.py',
        'scripts/personal-os-calendar',
        'end_to_end_test.py'
    ]

    missing = []
    for file_path in key_files:
        if not Path(file_path).exists():
            missing.append(file_path)

    if missing:
        print(f"❌ Missing key files: {missing}")
        return False
    else:
        print("✅ Key files OK")
        return True

def check_python_imports():
    """Check that key modules can be imported."""
    # Add current directory to path
    sys.path.insert(0, '.')

    modules_to_test = [
        ('models', 'models'),
        ('planning_engine', 'planning_engine'),
        ('university_service', 'services.university_service'),
        ('project_service', 'services.project_service'),
        ('knowledge_service', 'services.knowledge_service'),
        ('google_adapter', 'adapters.google_calendar_adapter'),
        ('telegram_adapter', 'adapters.telegram_adapter'),
        ('n8n_adapter', 'adapters.n8n_adapter'),
        ('cli_main', 'cli.main')
    ]

    failed = []
    for module_name, import_path in modules_to_test:
        try:
            __import__(import_path)
        except Exception as e:
            failed.append(f"{module_name}: {e}")

    if failed:
        print(f"❌ Import failures: {failed}")
        return False
    else:
        print("✅ Python imports OK")
        return True

def main():
    """Run all validation checks."""
    print("🔍 Personal OS AI Calendar System Validation")
    print("=" * 50)

    checks = [
        check_structure,
        check_templates,
        check_key_files,
        check_python_imports
    ]

    all_passed = True
    for check in checks:
        if not check():
            all_passed = False
        print()  # Empty line for readability

    if all_passed:
        print("🎉 ALL VALIDATION CHECKS PASSED!")
        print("✅ System is ready for deployment")
        return True
    else:
        print("❌ SOME VALIDATION CHECKS FAILED!")
        print("⚠️  Please address the issues above before deployment")
        return False

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)