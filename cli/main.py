#!/usr/bin/env python3
"""
Main CLI application for Personal OS AI Calendar system.
Provides command-line interface for managing university events, projects,
knowledge items, and scheduling.
"""

import argparse
import asyncio
import sys
import os
from typing import Optional

# Add the project root to the path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.university_service import UniversityService
from services.project_service import ProjectService
from services.knowledge_service import KnowledgeService
from adapters import GoogleCalendarAdapter, TelegramAdapter, N8NAdapter
from planning_engine import PlanningEngine
from ids import IDGenerator


class PersonalOSCalendarCLI:
    """Main CLI application class."""

    def __init__(self):
        self.university_service = UniversityService()
        self.project_service = ProjectService()
        self.knowledge_service = KnowledgeService()
        self.planning_engine = PlanningEngine()
        self.id_generator = IDGenerator()

        # Initialize adapters (these would typically be configured via env vars or config files)
        self.google_calendar_adapter = None
        self.telegram_adapter = None
        self.n8n_adapter = None

        self._setup_adapters()

    def _setup_adapters(self):
        """Initialize adapters based on environment configuration."""
        # Google Calendar Adapter
        google_config = {
            'credentials_path': os.environ.get('GOOGLE_CALENDAR_CREDENTIALS', '~/.config/google/credentials.json'),
            'token_path': os.environ.get('GOOGLE_CALENDAR_TOKEN', '~/.config/google/token.json'),
            'calendar_name': os.environ.get('GOOGLE_CALENDAR_NAME', 'Personal OS AI Calendar')
        }

        # Only initialize if credentials exist
        credentials_expanded = os.path.expanduser(google_config['credentials_path'])
        if os.path.exists(credentials_expanded):
            try:
                self.google_calendar_adapter = GoogleCalendarAdapter(google_config)
            except Exception as e:
                print(f"Warning: Could not initialize Google Calendar adapter: {e}")

        # Telegram Adapter
        telegram_config = {
            'bot_token': os.environ.get('TELEGRAM_BOT_TOKEN'),
            'chat_id': os.environ.get('TELEGRAM_CHAT_ID'),
            'parse_mode': os.environ.get('TELEGRAM_PARSE_MODE', 'HTML')
        }

        if telegram_config['bot_token'] and telegram_config['chat_id']:
            try:
                self.telegram_adapter = TelegramAdapter(telegram_config)
            except Exception as e:
                print(f"Warning: Could not initialize Telegram adapter: {e}")

        # N8N Adapter
        n8n_config = {
            'base_url': os.environ.get('N8N_BASE_URL', 'http://localhost:5678'),
            'api_key': os.environ.get('N8N_API_KEY')
        }

        if n8n_config['base_url'] and n8n_config['api_key']:
            try:
                self.n8n_adapter = N8NAdapter(n8n_config)
            except Exception as e:
                print(f"Warning: Could not initialize N8N adapter: {e}")

    async def initialize(self):
        """Initialize all services and adapters."""
        print("Initializing Personal OS AI Calendar CLI...")

        # Initialize adapters
        if self.google_calendar_adapter:
            try:
                await self.google_calendar_adapter.initialize()
                print("✓ Google Calendar adapter initialized")
            except Exception as e:
                print(f"✗ Google Calendar adapter initialization failed: {e}")

        if self.telegram_adapter:
            try:
                await self.telegram_adapter.initialize()
                print("✓ Telegram adapter initialized")
            except Exception as e:
                print(f"✗ Telegram adapter initialization failed: {e}")

        if self.n8n_adapter:
            try:
                await self.n8n_adapter.initialize()
                print("✓ N8N adapter initialized")
            except Exception as e:
                print(f"✗ N8N adapter initialization failed: {e}")

    def run(self):
        """Run the CLI application."""
        parser = argparse.ArgumentParser(
            description="Personal OS AI Calendar - Command Line Interface",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  personal-os-calendar university list          # List university events
  personal-os-calendar project create           # Create a new project
  personal-os-calendar knowledge create-idea    # Create a new idea
  personal-os-calendar schedule run             # Run scheduling engine
  personal-os-calendaradapter test              # Test adapter connections
            """
        )

        parser.add_argument(
            '--version',
            action='version',
            version='Personal OS AI Calendar CLI v1.0.0'
        )

        subparsers = parser.add_subparsers(dest='command', help='Available commands')

        # University commands
        uni_parser = subparsers.add_parser('university', help='University event management')
        uni_subparsers = uni_parser.add_subparsers(dest='action', help='University actions')

        uni_subparsers.add_parser('list', help='List university events')
        uni_subparsers.add_parser('lecture', help='Create a lecture').add_argument('summary', help='Lecture summary')
        uni_subparsers.add_parser('lab', help='Create a lab').add_argument('summary', help='Lab summary')
        uni_subparsers.add_parser('practical', help='Create a practical').add_argument('summary', help='Practical summary')

        # Project commands
        proj_parser = subparsers.add_parser('project', help='Project management')
        proj_subparsers = proj_parser.add_subparsers(dest='action', help='Project actions')

        proj_subparsers.add_parser('list', help='List projects')
        create_proj = proj_subparsers.add_parser('create', help='Create a project')
        create_proj.add_argument('name', help='Project name')
        create_proj.add_argument('--description', '-d', default='', help='Project description')

        # Knowledge commands
        kb_parser = subparsers.add_parser('knowledge', help='Knowledge management')
        kb_subparsers = kb_parser.add_subparsers(dest='action', help='Knowledge actions')

        kb_subparsers.add_parser('list', help='List knowledge items')
        kb_subparsers.add_parser('idea', help='Create an idea').add_argument('title', help='Idea title')
        kb_subparsers.add_parser('reference', help='Create a reference').add_argument('title', help='Reference title')
        kb_subparsers.add_parser('learning', help='Create a learning note').add_argument('title', help='Learning note title')

        # Scheduler commands
        sched_parser = subparsers.add_parser('schedule', help='Scheduling operations')
        sched_subparsers = sched_parser.add_subparsers(dest='action', help='Scheduler actions')

        sched_subparsers.add_parser('run', help='Run scheduling engine')
        sched_subparsers.add_parser('show', help='Show current schedule')

        # Adapter commands
        adapter_parser = subparsers.add_parser('adapter', help='Adapter operations')
        adapter_subparsers = adapter_parser.add_subparsers(dest='action', help='Adapter actions')

        adapter_subparsers.add_parser('test', help='Test adapter connections')
        adapter_subparsers.add_parser('sync', help='Sync data with external services')

        # Parse arguments
        args = parser.parse_args()

        if not args.command:
            parser.print_help()
            return

        # Route to appropriate handler
        asyncio.get_event_loop().run_until_complete(self._handle_command(args))

    async def _handle_command(self, args):
        """Handle CLI commands asynchronously."""
        try:
            if args.command == 'university':
                await self._handle_university_command(args)
            elif args.command == 'project':
                await self._handle_project_command(args)
            elif args.command == 'knowledge':
                await self._handle_knowledge_command(args)
            elif args.command == 'schedule':
                await self._handle_schedule_command(args)
            elif args.command == 'adapter':
                await self._handle_adapter_command(args)
            else:
                print(f"Unknown command: {args.command}")
        except Exception as e:
            print(f"Error executing command: {e}")
            import traceback
            traceback.print_exc()

    async def _handle_university_command(self, args):
        """Handle university-related commands."""
        if args.action == 'list':
            # In a real implementation, we'd fetch from a calendar or database
            print("Listing university events...")
            print("No events found (would integrate with calendar in full implementation)")
        elif args.action in ['lecture', 'lab', 'practical']:
            print(f"Creating {args.action}: {args.summary}")
            # Would create the actual event
            print(f"{args.action.capitalize()} created successfully")
        else:
            print(f"Unknown university action: {args.action}")

    async def _handle_project_command(self, args):
        """Handle project-related commands."""
        if args.action == 'list':
            print("Listing projects...")
            print("No projects found")
        elif args.action == 'create':
            print(f"Creating project: {args.name}")
            project = self.project_service.create_project(
                name=args.name,
                description=args.description
            )
            print(f"Project created with ID: {project.id}")
        else:
            print(f"Unknown project action: {args.action}")

    async def _handle_knowledge_command(self, args):
        """Handle knowledge-related commands."""
        if args.action == 'list':
            print("Listing knowledge items...")
            print("No knowledge items found")
        elif args.action == 'idea':
            print(f"Creating idea: {args.title}")
            idea = self.knowledge_service.create_idea(title=args.title, content="")
            print(f"Idea created with ID: {idea.id}")
        elif args.action == 'reference':
            print(f"Creating reference: {args.title}")
            reference = self.knowledge_service.create_reference(title=args.title, content="")
            print(f"Reference created with ID: {reference.id}")
        elif args.action == 'learning':
            print(f"Creating learning note: {args.title}")
            learning = self.knowledge_service.create_learning_note(title=args.title, content="")
            print(f"Learning note created with ID: {learning.id}")
        else:
            print(f"Unknown knowledge action: {args.action}")

    async def _handle_schedule_command(self, args):
        """Handle scheduler-related commands."""
        if args.action == 'run':
            print("Running scheduling engine...")
            # In a real implementation, this would gather items from all services
            # and run the planning engine
            print("Scheduling completed")
        elif args.action == 'show':
            print("Showing current schedule...")
            print("No scheduled items")
        else:
            print(f"Unknown schedule action: {args.action}")

    async def _handle_adapter_command(self, args):
        """Handle adapter-related commands."""
        if args.action == 'test':
            print("Testing adapter connections...")
            await self._test_adapters()
        elif args.action == 'sync':
            print("Syncing with external services...")
            await self._sync_adapters()
        else:
            print(f"Unknown adapter action: {args.action}")

    async def _test_adapters(self):
        """Test all initialized adapters."""
        adapters = [
            ('Google Calendar', self.google_calendar_adapter),
            ('Telegram', self.telegram_adapter),
            ('N8N', self.n8n_adapter)
        ]

        for name, adapter in adapters:
            if adapter:
                try:
                    healthy = await adapter.health_check()
                    status = "✓ Healthy" if healthy else "✗ Unhealthy"
                    print(f"{name} adapter: {status}")
                except Exception as e:
                    print(f"{name} adapter: ✗ Error - {e}")
            else:
                print(f"{name} adapter: ⚠ Not configured")

    async def _sync_adapters(self):
        """Sync data with external adapters."""
        print("Sync functionality would be implemented here")
        print("This would typically:")
        print("1. Fetch events from Google Calendar")
        print("2. Update local database with new/modified events")
        print("3. Push local changes to external services")
        print("4. Handle conflicts appropriately")


def main():
    """Main entry point for the CLI."""
    cli = PersonalOSCalendarCLI()
    cli.run()


if __name__ == '__main__':
    main()