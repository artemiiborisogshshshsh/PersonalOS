# Deterministic Planning Engine for Personal OS AI Calendar

This module implements a deterministic planning engine for scheduling events, tasks, and activities based on constraints and scoring algorithms.

## Features

- **Constraint-based scheduling**: Handles time windows, dependencies, resource conflicts, and duration requirements
- **Multi-slot support**: Items can span multiple consecutive time slots (e.g., a 2.5-hour task in 30-minute slots)
- **Priority-based ordering**: Higher priority items are scheduled first
- **Dependency awareness**: Ensures dependencies are scheduled before dependents
- **Resource conflict prevention**: Prevents scheduling conflicts for shared resources
- **Time window constraints**: Respects earliest start, latest end, and preferred times
- **Schedule optimization**: Uses scoring functions to minimize gaps and keep schedule compact
- **Deterministic algorithm**: Produces consistent results given the same inputs

## Components

### PlanningItem
Base class for schedulable items with properties:
- `id`: Unique identifier
- `title`: Descriptive title
- `description`: Detailed description
- `item_type`: Type of item (university_event, preparation_block, project_task, knowledge_activity, custom)
- `duration_minutes`: Required duration in minutes
- `earliest_start`/`latest_end`: Time window constraints
- `preferred_start`/`preferred_end`: Preferred timing (soft constraint)
- `flexible`: Whether item can be moved within time window
- `dependencies`: Set of item IDs that must be completed first
- `required_resources`: Set of resources needed
- `priority`: Priority level (higher = more important)
- `metadata`: Extensible metadata storage

### Constraint Implementations
1. **TimeWindowConstraint**: Ensures items stay within their time windows
2. **DependencyConstraint**: Ensures dependencies are scheduled before dependents
3. **ResourceConstraint**: Prevents scheduling conflicts for required resources
4. **DurationConstraint**: Ensures allocated time meets duration requirements

### PlanningEngine
Main orchestrator that:
- Generates discrete time slots within a planning horizon
- Sorts items by priority, dependency count, and duration
- Places items in optimal time slots using constraint evaluation and scoring
- Handles multi-slot items by checking consecutive free slot blocks
- Uses scoring system (priority, gap minimization, compactness) to optimize schedules
- Tracks unscheduled items when constraints cannot be satisfied

## Usage

```python
from datetime import datetime, timedelta
from planning_engine import PlanningEngine, PlanningItem, PlanningItemType

# Create engine
engine = PlanningEngine()

# Define planning horizon
start_time = datetime(2026, 9, 1, 9, 0)   # 9:00 AM
end_time = datetime(2026, 9, 1, 17, 0)    # 5:00 PM

# Create items to schedule
items = [
    PlanningItem(
        "task-1", "Quick Task", "15-minute task", PlanningItemType.PROJECT_TASK,
        duration_minutes=15,
        earliest_start=start_time,
        latest_end=end_time,
        priority=2
    ),
    PlanningItem(
        "task-2", "Long Task", "2.5-hour task", PlanningItemType.PROJECT_TASK,
        duration_minutes=150,
        earliest_start=start_time,
        latest_end=end_time,
        priority=1
    )
]

# Schedule items
schedule = engine.schedule_items(items, start_time, end_time, granularity_minutes=30)

# Check results
scheduled_items = schedule.get_scheduled_items()
unscheduled_items = schedule.get_unscheduled_items()

print(f"Scheduled: {len(scheduled_items)} items")
print(f"Unscheduled: {len(unscheduled_items)} items")
```

## Testing

Run the test suite:
```bash
python3 -m pytest tests/test_planning_engine.py -v
```

## Demonstration

See the demo scripts for practical examples:
- `demo_multislot.py`: Shows multi-slot scheduling with various durations
- `demo_multislot_simple.py`: Simplified demonstration
- `verify_multislot.py`: Verification of multi-slot scheduling correctness

## Implementation Details

The engine uses a greedy algorithm with the following approach:
1. Generate time slots based on specified granularity
2. Sort items by: priority (highest first), dependency count (fewest first), duration (shortest first)
3. For each item, find the best placement by:
   - Checking for consecutive free slot sequences that fit the item's duration
   - Evaluating each possible placement using constraint checking and scoring functions
   - Selecting the placement with the highest score
4. Mark selected slots as scheduled with the item
5. Continue until all items are processed or no more placements are available

Scoring functions include:
- Priority score: Higher priority items get higher scores
- Gap minimization: Prefers placements that minimize gaps between scheduled items
- Compactness: Prefers scheduling earlier in the day when possible

## Integration with Personal OS AI Calendar

The engine includes helper functions to convert existing domain models to PlanningItems:
- `create_planning_item_from_university_event()`
- `create_planning_item_from_task()`
- `create_planning_item_from_preparation_block()`

This allows seamless integration with the existing calendar synchronization and preparation block generation systems.