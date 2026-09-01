"""
Demonstration script showing how to use the infrastructure adapters
with the Personal OS AI Calendar system.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from adapters import GoogleCalendarAdapter, TelegramAdapter, N8NAdapter
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType
from models import UniversityEvent, Task, PreparationBlock
from ids import IDGenerator

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def demo_google_calendar_adapter():
    """Demonstrate Google Calendar adapter usage."""
    print("\n=== Google Calendar Adapter Demo ===")

    # Configuration (in real usage, these would come from environment vars or config file)
    config = {
        'credentials_path': '~/.config/google/credentials.json',
        'token_path': '~/.config/google/token.json',
        'calendar_name': 'Personal OS AI Calendar'
    }

    # Create adapter
    adapter = GoogleCalendarAdapter(config)

    try:
        # Initialize
        print("Initializing Google Calendar adapter...")
        if await adapter.initialize():
            print("✓ Google Calendar adapter initialized successfully")

            # Health check
            if await adapter.health_check():
                print("✓ Google Calendar adapter health check passed")
            else:
                print("✗ Google Calendar adapter health check failed")

            # Show that we can create sample events to sync
            print("\nCreating sample events for demonstration...")
            start_time = datetime(2026, 9, 1, 9, 0)
            end_time = datetime(2026, 9, 1, 17, 0)

            sample_events = [
                UniversityEvent(
                    uid="demo-lecture-001",
                    summary="Демонстрационная лекция: Планирование времени",
                    description="Лекция по эффективному планированию времени для студентов",
                    location="Аудитория 101",
                    dtstart=start_time + timedelta(hours=1),
                    dtend=start_time + timedelta(hours=3),
                    event_type="ЛК",
                    is_group_event=True
                ),
                Task(
                    id="demo-task-001",
                    title="Домашнее задание: План семестра",
                    description="Создать детальный план учебного семестра с учетом всех дедлайнов",
                    project_id="demo-project-001",
                    status="todo",
                    priority="high",
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                    due_date=datetime(2026, 9, 10, 23, 59),
                    estimated_hours=3.0
                )
            ]

            print(f"Created {len(sample_events)} sample events")
            print("In a real implementation, these would be synced to Google Calendar")

        else:
            print("✗ Failed to initialize Google Calendar adapter")

    except Exception as e:
        print(f"Error in Google Calendar demo: {e}")
    finally:
        await adapter.shutdown()

async def demo_telegram_adapter():
    """Demonstrate Telegram adapter usage."""
    print("\n=== Telegram Adapter Demo ===")

    # Configuration (would normally come from environment variables)
    config = {
        'bot_token': 'YOUR_BOT_TOKEN_HERE',  # Would come from TELEGRAM_BOT_TOKEN env var
        'chat_id': 'YOUR_CHAT_ID_HERE',      # Would come from TELEGRAM_CHAT_ID env var
        'parse_mode': 'HTML'
    }

    # Create adapter
    adapter = TelegramAdapter(config)

    try:
        print("Initializing Telegram adapter...")
        # Note: This will fail without valid credentials, but shows the interface
        if await adapter.initialize():
            print("✓ Telegram adapter initialized successfully")

            # Health check
            if await adapter.health_check():
                print("✓ Telegram adapter health check passed")
            else:
                print("✗ Telegram adapter health check failed (expected without real credentials)")

            # Demonstrate sending notifications
            print("\nTesting notification sending...")

            # Simple notification
            await adapter.send_notification(
                "🔔 Тестовое уведомление от Personal OS AI Calendar",
                priority="normal"
            )

            # Priority notification
            await adapter.send_notification(
                "🚨 Срочное напоминание: Завтра экзамен по математике!",
                priority="urgent"
            )

            # Schedule update
            sample_events = [
                {
                    'title': 'Лекция: Алгоритмы и структуры данных',
                    'time': '10:00-12:00'
                },
                {
                    'title': 'Семинар: Теория графов',
                    'time': '14:00-16:00'
                }
            ]

            await adapter.send_schedule_update(sample_events)

            print("✓ Notification demos completed (would send real messages with valid credentials)")

        else:
            print("✗ Telegram adapter initialization failed (expected without real credentials)")
            print("  To test with real credentials, set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables")

    except Exception as e:
        print(f"Error in Telegram demo: {e}")
    finally:
        await adapter.shutdown()

async def demo_n8n_adapter():
    """Demonstrate n8n adapter usage."""
    print("\n=== n8n Adapter Demo ===")

    # Configuration (would normally come from environment variables)
    config = {
        'base_url': 'http://localhost:5678',  # Default n8n port
        'api_key': 'YOUR_API_KEY_HERE',       # Would come from N8N_API_KEY env var
        'webhook_url': 'http://localhost:5678/webhook'  # Would come from N8N_WEBHOOK_URL env var
    }

    # Create adapter
    adapter = N8NAdapter(config)

    try:
        print("Initializing n8n adapter...")
        # Note: This will fail without n8n running, but shows the interface
        if await adapter.initialize():
            print("✓ n8n adapter initialized successfully")

            # Health check
            if await adapter.health_check():
                print("✓ n8n adapter health check passed")
            else:
                print("✗ n8n adapter health check failed (expected without n8n running)")

            # Demonstrate triggering workflows
            print("\nTesting workflow triggering...")

            # Trigger a workflow by name
            await adapter.trigger_workflow(
                "schedule-sync-workflow",
                payload={
                    "action": "sync_schedule",
                    "timestamp": datetime.now().isoformat(),
                    "source": "personal_os_ai_calendar"
                }
            )

            # Trigger a webhook
            await adapter.trigger_webhook(
                "schedule-update",
                payload={
                    "events_count": 5,
                    "last_updated": datetime.now().isoformat()
                }
            )

            print("✓ Workflow triggering demos completed (would work with real n8n instance)")

        else:
            print("✗ n8n adapter initialization failed (expected without n8n running)")
            print("  To test with real n8n, ensure n8n is running on http://localhost:5678")
            print("  and set N8N_API_KEY and N8N_WEBHOOK_URL environment variables if needed")

    except Exception as e:
        print(f"Error in n8n demo: {e}")
    finally:
        await adapter.shutdown()

async def demo_integrated_usage():
    """Demonstrate how adapters could work together with the planning engine."""
    print("\n=== Integrated Usage Demo ===")
    print("Showing how adapters could work with the planning engine...")

    # This demonstrates the concept - in reality, you'd have proper configuration
    print("""
    In a real implementation:

    1. Planning Engine creates/modifies schedule
    2. Google Calendar Adapter syncs changes to Google Calendar
    3. Telegram Adapter sends notifications about schedule changes
    4. n8n Adapter triggers workflows for complex automations (e.g.,
       sending attendance reports, updating external systems, etc.)

    Example workflow:
    - User updates task in planning interface
    - Planning Engine recalculates schedule
    - Google Calendar Adapter updates corresponding events
    - Telegram Adapter notifies user of changes
    - n8n Adapter triggers workflow to update project management tools
    """)

    print("✓ Integrated usage concept demonstrated")

async def main():
    """Run all demonstrations."""
    print("Personal OS AI Calendar - Infrastructure Adapters Demonstration")
    print("=" * 65)

    # Run demos
    await demo_google_calendar_adapter()
    await demo_telegram_adapter()
    await demo_n8n_adapter()
    await demo_integrated_usage()

    print("\n" + "=" * 65)
    print("Demonstration completed!")
    print("\nNext steps:")
    print("1. Configure credentials for each service")
    print("2. Integrate adapters with your application logic")
    print("3. Use the adapters in your Personal OS AI Calendar system")
    print("4. Consider creating MCP servers for enhanced integration")

if __name__ == "__main__":
    asyncio.run(main())