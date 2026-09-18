"""
Deterministic planning engine for Personal OS AI Calendar system.
Handles scheduling of events, tasks, and activities based on constraints and scoring.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Set, Dict, Any, Protocol, Callable, Tuple
from enum import Enum
import heapq
import itertools
import time

from models import (
    UniversityEvent,
    PreparationBlock,
    PreparationRequirement,
    Project,
    Task,
    KnowledgeItem,
    WeeklyCapacityModel,
)
from ids import IDGenerator


class PlanningItemType(Enum):
    """Types of items that can be scheduled."""
    UNIVERSITY_EVENT = "university_event"
    PREPARATION_BLOCK = "preparation_block"
    PROJECT_TASK = "project_task"
    KNOWLEDGE_ACTIVITY = "knowledge_activity"
    CUSTOM = "custom"


@dataclass
class PlanningItem:
    """Base class for items that can be scheduled by the planning engine."""
    id: str
    title: str
    description: str
    item_type: PlanningItemType
    preferred_start: Optional[datetime] = None
    preferred_end: Optional[datetime] = None
    duration_minutes: int = 0
    earliest_start: Optional[datetime] = None
    latest_end: Optional[datetime] = None
    # For items that are flexible within a time window
    flexible: bool = True
    # Dependencies - items that must be completed before this one
    dependencies: Set[str] = field(default_factory=set)
    # Resources required (simplified as strings for now)
    required_resources: Set[str] = field(default_factory=set)
    # Priority for scoring (higher = more important)
    priority: int = 1
    # Metadata for extensibility
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TimeSlot:
    """Represents a available time slot for scheduling."""
    start: datetime
    end: datetime
    # Score for this slot (higher is better)
    score: float = 0.0
    # Breakdown of scores by category for explainability
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    # Which item is scheduled here (if any)
    scheduled_item_id: Optional[str] = None
    # Metadata about the slot
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_minutes(self) -> int:
        """Get duration of the slot in minutes."""
        return int((self.end - self.start).total_seconds() / 60)


@dataclass
class Schedule:
    """Represents a complete schedule."""
    items: Dict[str, PlanningItem] = field(default_factory=dict)
    slots: List[TimeSlot] = field(default_factory=list)
    # Unscheduled items that couldn't be placed
    unscheduled_items: List[PlanningItem] = field(default_factory=list)
    # Total score of the schedule
    total_score: float = 0.0
    # Generation metadata
    generated_at: datetime = field(default_factory=datetime.now)
    generator_version: str = "1.0"

    def get_item(self, item_id: str) -> Optional[PlanningItem]:
        """Get an item by ID."""
        return self.items.get(item_id)

    def get_scheduled_items(self) -> List[PlanningItem]:
        """Get all successfully scheduled items."""
        item_ids = {slot.scheduled_item_id for slot in self.slots if slot.scheduled_item_id}
        return [self.items[item_id] for item_id in item_ids if item_id in self.items]

    def get_unscheduled_items(self) -> List[PlanningItem]:
        """Get items that could not be scheduled."""
        return self.unscheduled_items


class Constraint(Protocol):
    """Protocol for scheduling constraints."""
    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """
        Evaluate how well an item fits in a slot given current schedule.
        Returns a score modifier (can be negative for hard constraints).
        """
        ...


@dataclass
class TimeWindowConstraint:
    """Constraint that ensures items stay within their time windows."""
    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Check if slot fits within item's time constraints."""
        score = 0.0

        # Hard constraint: must fit within earliest_start and latest_end
        if item.earliest_start and slot.start < item.earliest_start:
            return float('-inf')  # Hard constraint violation
        if item.latest_end and slot.end > item.latest_end:
            return float('-inf')  # Hard constraint violation

        # Soft constraint: preference for preferred times
        if item.preferred_start and item.preferred_end:
            # Calculate how close we are to preferred time
            pref_center = item.preferred_start + (item.preferred_end - item.preferred_start) / 2
            slot_center = slot.start + (slot.end - slot.start) / 2
            time_diff = abs((slot_center - pref_center).total_seconds() / 60)  # minutes
            # Prefer closer to preferred time (negative score for distance)
            score -= min(time_diff / 60, 2.0)  # Max penalty of 2 points for being far

        return score


@dataclass
class DependencyConstraint:
    """Constraint that ensures dependencies are scheduled before dependents."""
    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Check if all dependencies are satisfied."""
        # Check if all dependencies are scheduled and end before this slot starts
        for dep_id in item.dependencies:
            dep_item = schedule.get_item(dep_id)
            if not dep_item:
                # Dependency not in schedule at all
                return float('-inf')

            # Find when dependency is scheduled
            dep_slot = None
            for slot_obj in schedule.slots:
                if slot_obj.scheduled_item_id == dep_id:
                    dep_slot = slot_obj
                    break

            if not dep_slot:
                # Dependency exists but not scheduled yet
                return float('-inf')

            # Dependency must end before this item starts
            if dep_slot.end > slot.start:
                return float('-inf')  # Hard constraint: dependency not finished

        return 0.0  # All dependencies satisfied


@dataclass
class ResourceConstraint:
    """Constraint that prevents scheduling conflicts for required resources."""
    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Check if required resources are available during the slot."""
        # Check for conflicts with already scheduled items
        for scheduled_slot in schedule.slots:
            if scheduled_slot.scheduled_item_id is None:
                continue

            # Check time overlap
            if not (slot.end <= scheduled_slot.start or slot.start >= scheduled_slot.end):
                # Time overlap - check resource conflicts
                scheduled_item = schedule.get_item(scheduled_slot.scheduled_item_id)
                if scheduled_item:
                    # Check if they share any required resources
                    shared_resources = item.required_resources.intersection(scheduled_item.required_resources)
                    if shared_resources:
                        return float('-inf')  # Hard constraint: resource conflict

        return 0.0  # No resource conflicts


@dataclass
class DurationConstraint:
    """Constraint that ensures slot duration matches item duration."""
    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Check if slot duration matches item requirements."""
        if item.duration_minutes > 0:
            slot_duration = slot.duration_minutes
            if slot_duration < item.duration_minutes:
                return float('-inf')  # Hard constraint: not enough time

            # Soft constraint: prefer exact duration match
            duration_diff = slot_duration - item.duration_minutes
            # Prefer minimal extra time (but some extra is OK)
            return min(0.0, -duration_diff / 60)  # Small penalty for excess time

        return 0.0


@dataclass
class SleepConstraint:
    """Constraint that ensures sleep time is respected and not scheduled for other activities."""
    # Typical sleep hours (can be made configurable)
    sleep_start_hour: int = 22  # 10 PM
    sleep_end_hour: int = 6     # 6 AM
    min_sleep_hours: int = 7    # Minimum sleep required

    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Ensure sleep time is not scheduled for other activities and adequate sleep is provided."""
        # Explicit fixed commitments are authoritative.  They may themselves
        # represent sleep, travel or an externally committed overnight block;
        # rejecting them here would leave their time falsely available.
        if item.metadata.get('is_fixed_commitment', False):
            return 0.0
        # Compare actual intervals, not only integer hours. Otherwise a block
        # such as 21:30–22:30 slips through an overnight 22:00–06:00 window.
        is_sleep_time = False
        day_anchor = slot.start.replace(hour=0, minute=0, second=0, microsecond=0)
        day_anchor -= timedelta(days=1)
        final_anchor = slot.end.replace(hour=0, minute=0, second=0, microsecond=0)
        while day_anchor <= final_anchor:
            sleep_start = day_anchor + timedelta(hours=self.sleep_start_hour)
            sleep_end = day_anchor + timedelta(hours=self.sleep_end_hour)
            if self.sleep_start_hour >= self.sleep_end_hour:
                sleep_end += timedelta(days=1)
            if slot.start < sleep_end and slot.end > sleep_start:
                is_sleep_time = True
                break
            day_anchor += timedelta(days=1)

        # If it's sleep time, only allow items specifically marked as sleep
        if is_sleep_time:
            # Check if item is sleep-related
            is_sleep_item = (
                item.metadata.get('is_sleep', False) or
                'sleep' in item.title.lower() or
                item.item_type.name.lower() == 'sleep'
            )

            if not is_sleep_item:
                return float('-inf')  # Hard constraint: cannot schedule non-sleep during sleep time

        return 0.0


@dataclass
class FixedCommitmentConstraint:
    """Constraint that ensures fixed commitments are not moved and are scheduled at their fixed times."""
    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Ensure fixed commitments stay at their scheduled times."""
        # Check if item is a fixed commitment (not flexible and has specific time)
        is_fixed = not item.flexible and item.earliest_start and item.latest_end

        if is_fixed:
            # For fixed commitments, the slot must match the commitment's time exactly
            # Allow small tolerance for scheduling granularity
            tolerance_minutes = 15  # 15 minutes tolerance

            start_diff = abs((slot.start - item.earliest_start).total_seconds() / 60)
            end_diff = abs((slot.end - item.latest_end).total_seconds() / 60)

            if start_diff > tolerance_minutes or end_diff > tolerance_minutes:
                return float('-inf')  # Hard constraint: fixed commitment moved

        return 0.0


@dataclass
class TravelTimeConstraint:
    """Constraint that ensures adequate travel time between activities that require travel."""
    # Default travel time between activities (can be made configurable based on location/metadata)
    default_travel_time: int = 15  # minutes

    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Ensure adequate travel time is scheduled between activities that require it."""
        # Check if this item requires travel time before or after
        requires_travel_before = item.metadata.get('requires_travel_before', False)
        requires_travel_after = item.metadata.get('requires_travel_after', False)
        travel_time_needed = item.metadata.get('travel_time', self.default_travel_time)

        score = 0.0

        # Check for activity before this slot
        if requires_travel_before:
            # Look for the latest ending activity before this slot
            latest_end_before = None
            for s in schedule.slots:
                if s.end <= slot.start and s.scheduled_item_id is not None:
                    if latest_end_before is None or s.end > latest_end_before:
                        latest_end_before = s.end

            if latest_end_before:
                # Check if adequate travel time exists between previous activity and this slot
                travel_gap = (slot.start - latest_end_before).total_seconds() / 60
                if travel_gap < travel_time_needed:
                    return float('-inf')  # Hard constraint: insufficient travel time

        # Check for activity after this slot
        if requires_travel_after:
            # Look for the earliest starting activity after this slot
            earliest_start_after = None
            for s in schedule.slots:
                if s.start >= slot.end and s.scheduled_item_id is not None:
                    if earliest_start_after is None or s.start < earliest_start_after:
                        earliest_start_after = s.start

            if earliest_start_after:
                # Check if adequate travel time exists between this slot and next activity
                travel_gap = (earliest_start_after - slot.end).total_seconds() / 60
                if travel_gap < travel_time_needed:
                    return float('-inf')  # Hard constraint: insufficient travel time

        return 0.0


@dataclass
class MaxContinuousWorkConstraint:
    """Constraint that prevents scheduling too much continuous deep work without breaks."""
    max_continuous_work_minutes: int = 180  # 3 hours
    min_break_minutes: int = 15             # Minimum break required after max work

    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Prevent more than max_continuous_work_minutes of continuous deep work."""
        # Fixed commitments reserve time but are not cognitive work.  In
        # particular, an all-night sleep block or a long university day must
        # never become unschedulable merely because fixed items use priority 5.
        if item.metadata.get('is_fixed_commitment', False):
            return 0.0
        # Only apply to deep work/intellectual tasks
        is_deep_work = (
            item.metadata.get('deep_work', False) or
            'deep work' in item.title.lower() or
            item.metadata.get('cognitive_load', 'low') in ['high', 'medium'] or
            item.priority >= 3  # High priority tasks often require deep work
        )

        if not is_deep_work:
            return 0.0  # Not deep work, no constraint

        # Check continuous work before this slot.  ``schedule.slots`` stores
        # only occupied fragments and is not guaranteed to be insertion-time
        # sorted, so an earlier implementation accidentally summed deep work
        # from different days.  A gap of ``min_break_minutes`` ends a streak.
        continuous_work_before = 0
        current_time = slot.start

        # Look backwards for adjacent deep-work fragments.
        earlier_slots = sorted(
            (item_slot for item_slot in schedule.slots if item_slot.end <= slot.start),
            key=lambda item_slot: item_slot.end,
            reverse=True,
        )
        for s in earlier_slots:
            if current_time - s.end >= timedelta(minutes=self.min_break_minutes):
                break

            prev_item = schedule.get_item(s.scheduled_item_id)
            if prev_item:
                if prev_item.metadata.get('is_fixed_commitment', False):
                    break
                prev_is_deep_work = (
                    prev_item.metadata.get('deep_work', False) or
                    'deep work' in prev_item.title.lower() or
                    prev_item.metadata.get('cognitive_load', 'low') in ['high', 'medium'] or
                    prev_item.priority >= 3
                )
                if prev_is_deep_work:
                    continuous_work_before += (s.end - s.start).total_seconds() / 60
                    current_time = s.start
                else:
                    break  # Non-deep work breaks continuity
            else:
                break  # Can't determine, assume breaks continuity

        # Check continuous work after this slot
        continuous_work_after = 0
        current_time = slot.end

        # Look forwards for adjacent deep-work fragments.
        later_slots = sorted(
            (item_slot for item_slot in schedule.slots if item_slot.start >= slot.end),
            key=lambda item_slot: item_slot.start,
        )
        for s in later_slots:
            if s.start - current_time >= timedelta(minutes=self.min_break_minutes):
                break

            next_item = schedule.get_item(s.scheduled_item_id)
            if next_item:
                if next_item.metadata.get('is_fixed_commitment', False):
                    break
                next_is_deep_work = (
                    next_item.metadata.get('deep_work', False) or
                    'deep work' in next_item.title.lower() or
                    next_item.metadata.get('cognitive_load', 'low') in ['high', 'medium'] or
                    next_item.priority >= 3
                )
                if next_is_deep_work:
                    continuous_work_after += (s.end - s.start).total_seconds() / 60
                    current_time = s.end
                else:
                    break  # Non-deep work breaks continuity
            else:
                break  # Can't determine, assume breaks continuity

        # Total continuous work including this slot
        this_slot_work = slot.duration_minutes
        total_continuous_work = continuous_work_before + this_slot_work + continuous_work_after

        if total_continuous_work > self.max_continuous_work_minutes:
            return float('-inf')  # Hard constraint: too much continuous deep work

        return 0.0


@dataclass
class PreparationBreakConstraint:
    """Require a real pause between distinct preparation blocks.

    Preparation items are represented by several grid fragments in ``Schedule``.
    The constraint therefore compares whole item spans rather than individual
    five-minute fragments.
    """
    break_minutes: int = 15

    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        if not item.metadata.get('is_preparation', False):
            return 0.0
        spans: Dict[str, Tuple[datetime, datetime]] = {}
        for scheduled in schedule.slots:
            scheduled_id = scheduled.scheduled_item_id
            if not scheduled_id:
                continue
            other = schedule.get_item(scheduled_id)
            if other is None or not other.metadata.get('is_preparation', False):
                continue
            start, end = spans.get(scheduled_id, (scheduled.start, scheduled.end))
            spans[scheduled_id] = (min(start, scheduled.start), max(end, scheduled.end))
        required_break = timedelta(minutes=self.break_minutes)
        for other_start, other_end in spans.values():
            if slot.end <= other_start and other_start - slot.end < required_break:
                return float('-inf')
            if other_end <= slot.start and slot.start - other_end < required_break:
                return float('-inf')
        return 0.0


@dataclass
class PreparationTimingConstraint:
    """User-approved weekend and late-evening rules for automatic preparation."""
    late_hour: int = 21
    late_minute: int = 30

    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        if not item.metadata.get('is_preparation', False):
            return 0.0
        if item.metadata.get('emergency_preparation', False):
            return 0.0
        # Saturday is a normal study day after 10:00. Sunday is reserved for
        # Monday/overdue urgent preparations only, also after 10:00.
        if slot.start.weekday() == 5 and slot.start.hour < 10:
            return float('-inf')
        if slot.start.weekday() == 6:
            if slot.start.hour < 10 or not item.metadata.get('urgent', False):
                return float('-inf')
        late_boundary = slot.start.replace(
            hour=self.late_hour, minute=self.late_minute, second=0, microsecond=0,
        )
        if slot.end > late_boundary:
            return float('-inf')
        return 0.0


@dataclass
class PreparationBeforeEventConstraint:
    """Constraint that ensures preparation happens before its target event."""
    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Ensure preparation blocks are scheduled before their target events."""
        # Check if this item is a preparation block
        is_preparation = (
            item.item_type.name == 'PREPARATION_BLOCK' or
            item.metadata.get('is_preparation', False) or
            'preparation' in item.title.lower()
        )

        if not is_preparation:
            return 0.0  # Not a preparation, no constraint

        # Get the target event UID from metadata
        target_event_uid = item.metadata.get('source_event_uid') or \
                          item.metadata.get('event_uid') or \
                          item.metadata.get('target_event_id')

        if not target_event_uid:
            # No target event specified, can't enforce constraint
            return 0.0

        # Find the target event in the schedule
        target_event_item = schedule.get_item(target_event_uid)
        if not target_event_item:
            # Target event not in schedule yet, can't evaluate
            return 0.0

        # Find when the target event is scheduled
        target_event_slot = None
        for s in schedule.slots:
            if s.scheduled_item_id == target_event_uid:
                target_event_slot = s
                break

        if not target_event_slot:
            # Target event exists but not scheduled yet
            return 0.0  # Can't evaluate yet

        # Preparation must end before the target event starts
        if slot.end > target_event_slot.start:
            return float('-inf')  # Hard constraint: preparation not before event

        return 0.0


@dataclass
class WeeklyCapacityConstraint:
    """Constraint that ensures total scheduled time doesn't exceed weekly available capacity."""
    weekly_capacity: Optional[WeeklyCapacityModel] = None

    def set_weekly_capacity(self, capacity: WeeklyCapacityModel):
        """Set the weekly capacity reference from the planning engine."""
        self.weekly_capacity = capacity

    def evaluate(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Check if scheduling this item would exceed weekly capacity."""
        # Fixed commitments have already been subtracted by WeeklyCapacityModel.
        # Counting them again here would reduce flexible capacity twice.
        if not item.flexible or item.metadata.get('capacity_exempt', False):
            return 0.0

        def flexible_scheduled_minutes() -> int:
            return sum(
                scheduled_slot.duration_minutes
                for scheduled_slot in schedule.slots
                if scheduled_slot.scheduled_item_id is not None
                and schedule.items.get(scheduled_slot.scheduled_item_id) is not None
                and schedule.items[scheduled_slot.scheduled_item_id].flexible
                and not schedule.items[scheduled_slot.scheduled_item_id].metadata.get(
                    'capacity_exempt', False
                )
            )

        # If we don't have a capacity reference, fall back to default behavior
        if self.weekly_capacity is None:
            # Calculate total scheduled time if this item is added
            current_scheduled_minutes = flexible_scheduled_minutes()

            # Add the duration of the current item being considered
            item_duration = item.duration_minutes if item.duration_minutes > 0 else slot.duration_minutes
            total_scheduled_if_added = current_scheduled_minutes + item_duration

            # Use a default limit of 40 hours per week
            weekly_limit_minutes = 40 * 60  # 40 hours per week as default

            if total_scheduled_if_added > weekly_limit_minutes:
                return float('-inf')  # Hard constraint: would exceed weekly capacity

            return 0.0

        # Calculate total scheduled time if this item is added
        current_scheduled_minutes = flexible_scheduled_minutes()

        # Add the duration of the current item being considered
        # For multi-slot items, we need to consider the actual duration
        item_duration = item.duration_minutes if item.duration_minutes > 0 else slot.duration_minutes
        total_scheduled_if_added = current_scheduled_minutes + item_duration

        # Check against the weekly capacity from the model
        available_capacity = self.weekly_capacity.available_capacity

        # If adding this item would exceed available capacity, reject it
        if total_scheduled_if_added > available_capacity:
            return float('-inf')  # Hard constraint: would exceed weekly capacity

        return 0.0


@dataclass
class PlanningEngine:
    """
    Deterministic planning engine that schedules items based on constraints and scoring.
    """
    constraints: List[Constraint] = field(default_factory=list)
    scoring_functions: List[Callable[[PlanningItem, TimeSlot, Schedule], float]] = field(default_factory=list)
    # Configuration for candidate generation
    num_candidates: int = 5
    strategy_weights: Dict[str, float] = field(default_factory=lambda: {
        "greedy": 0.4,
        "backtracking": 0.3,
        "random": 0.2,
        "priority_only": 0.1
    })
    enable_metrics: bool = True
    # Metrics tracking
    metrics: Dict[str, Any] = field(default_factory=dict)
    # Weekly capacity model for tracking available time
    weekly_capacity: WeeklyCapacityModel = field(default_factory=WeeklyCapacityModel)

    def __post_init__(self):
        """Initialize with default constraints if none provided."""
        if not self.constraints:
            self.constraints = [
                TimeWindowConstraint(),
                DependencyConstraint(),
                ResourceConstraint(),
                DurationConstraint(),
                SleepConstraint(),
                FixedCommitmentConstraint(),
                TravelTimeConstraint(),
                MaxContinuousWorkConstraint(),
                PreparationBreakConstraint(),
                PreparationTimingConstraint(),
                PreparationBeforeEventConstraint(),
                WeeklyCapacityConstraint()
            ]
            # Set the weekly capacity reference for the capacity constraint
            for constraint in self.constraints:
                if isinstance(constraint, WeeklyCapacityConstraint):
                    constraint.set_weekly_capacity(self.weekly_capacity)

        if not self.scoring_functions:
            self.scoring_functions = [
                self._score_priority,
                self._score_deadline_urgency,
                self._score_energy_fit,
                self._score_morning_intellectual_work,
                self._score_recovery_after_university,
                self._score_preferred_reading_time,
                self._score_group_similar_work,
                self._score_minimize_context_switching,
                self._score_proximity_to_target_event,
                self._score_continuity,
                self._score_recovery_opportunity,
                self._score_buffer_time,
                self._score_fragmentation_penalty,
                self._score_gap_minimization,
                self._score_compactness
            ]

        # Initialize metrics if not already set
        if not hasattr(self, 'metrics') or not self.metrics:
            self.metrics = {}

    def _score_priority(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Score based on item priority."""
        return float(item.priority)

    def _score_gap_minimization(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Score based on minimizing gaps in schedule."""
        # Prefer slots that minimize gaps between scheduled items
        gap_score = 0.0

        # Check gap before this slot
        before_gap = float('inf')
        for s in schedule.slots:
            if s.end <= slot.start and s.scheduled_item_id is not None:
                gap = (slot.start - s.end).total_seconds() / 60  # minutes
                if gap < before_gap:
                    before_gap = gap

        # Check gap after this slot
        after_gap = float('inf')
        for s in schedule.slots:
            if s.start >= slot.end and s.scheduled_item_id is not None:
                gap = (s.start - slot.end).total_seconds() / 60  # minutes
                if gap < after_gap:
                    after_gap = gap

        # Score negatively for large gaps (we want to minimize them)
        max_gap = max(before_gap if before_gap != float('inf') else 0,
                      after_gap if after_gap != float('inf') else 0)
        if max_gap > 0:
            gap_score = -min(max_gap / 120, 1.0)  # Max penalty of 1 point for gaps > 2 hours

        return gap_score

    def _score_compactness(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Score based on keeping schedule compact."""
        # Prefer scheduling earlier in the day when possible
        hour_of_day = slot.start.hour
        # Prefer morning/afternoon over late night (assuming 8am-8pm is preferred)
        if 8 <= hour_of_day < 20:
            return 1.0  # Bonus for preferred hours
        else:
            return -0.5  # Penalty for off-hours

    # Soft preference scoring functions

    def _score_morning_intellectual_work(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Preference for morning intellectual work."""
        # Check if this is intellectual work
        is_intellectual = (
            item.metadata.get('cognitive_load', 'low') in ['high', 'medium'] or
            item.priority >= 3 or
            'study' in item.title.lower() or
            'read' in item.title.lower() or
            'learn' in item.title.lower() or
            item.item_type.name in ['PROJECT_TASK', 'KNOWLEDGE_ACTIVITY']
        )

        if not is_intellectual:
            return 0.0

        # Prefer morning hours (8 AM to 12 PM)
        hour_of_day = slot.start.hour
        if 8 <= hour_of_day < 12:
            # Linear preference: 8 AM = 1.0, 11:59 AM = 0.0
            return 1.0 - ((hour_of_day - 8) / 4.0)
        elif 12 <= hour_of_day < 18:
            # Afternoon gets lower but still positive score
            return 0.5 * (1.0 - ((hour_of_day - 12) / 6.0))
        else:
            # Night/early morning gets penalty
            return -0.5

    def _score_recovery_after_university(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Preference for recovery activities after university events."""
        # Check if this is a recovery activity
        is_recovery = (
            item.metadata.get('activity_type') == 'recovery' or
            'recovery' in item.title.lower() or
            'break' in item.title.lower() or
            'rest' in item.title.lower() or
            'relax' in item.title.lower()
        )

        if not is_recovery:
            return 0.0

        # Look for university events recently scheduled before this slot
        recent_university_event = None
        min_time_gap = float('inf')

        for s in schedule.slots:
            if s.scheduled_item_id is None:
                continue

            scheduled_item = schedule.get_item(s.scheduled_item_id)
            if scheduled_item and scheduled_item.item_type.name == 'UNIVERSITY_EVENT':
                # Calculate gap between university event end and slot start
                if s.end <= slot.start:
                    time_gap = (slot.start - s.end).total_seconds() / 60  # minutes
                    if time_gap < min_time_gap and time_gap <= 120:  # Within 2 hours
                        min_time_gap = time_gap
                        recent_university_event = scheduled_item

        if recent_university_event:
            # Prefer scheduling recovery soon after university events
            # Score decreases as time gap increases (max 2 hours)
            return max(0.0, 1.0 - (min_time_gap / 120.0))

        return 0.0

    def _score_preferred_reading_time(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Preference for preferred time for reading activities."""
        # Check if this is a reading activity
        is_reading = (
            item.metadata.get('activity_type') == 'reading' or
            'read' in item.title.lower() or
            'reading' in item.title.lower() or
            'book' in item.title.lower()
        )

        if not is_reading:
            return 0.0

        # Check if user has a preferred reading time in metadata
        preferred_hour = item.metadata.get('preferred_reading_hour')
        if preferred_hour is None:
            # Default preferred reading time: evening (19-21) or morning (9-11)
            hour_of_day = slot.start.hour
            if 19 <= hour_of_day < 21:  # Evening preference
                return 1.0 - ((hour_of_day - 19) / 2.0)
            elif 9 <= hour_of_day < 11:  # Morning preference
                return 1.0 - ((hour_of_day - 9) / 2.0)
            else:
                return 0.0
        else:
            # User-specified preferred reading hour
            hour_diff = abs(slot.start.hour - preferred_hour)
            if hour_diff <= 1:  # Within 1 hour
                return 1.0 - (hour_diff / 1.0)
            elif hour_diff <= 2:  # Within 2 hours
                return 0.5 * (1.0 - ((hour_diff - 1) / 1.0))
            else:
                return 0.0

    def _score_group_similar_work(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Preference for grouping similar work types together."""
        # Get work type from metadata or title
        work_type = item.metadata.get('work_type')
        if not work_type:
            # Infer from title or type
            title_lower = item.title.lower()
            if 'study' in title_lower or 'learn' in title_lower or 'read' in title_lower:
                work_type = 'study'
            elif 'meet' in title_lower or 'call' in title_lower or 'discuss' in title_lower:
                work_type = 'meeting'
            elif 'code' in title_lower or 'program' in title_lower or 'dev' in title_lower:
                work_type = 'development'
            elif 'write' in title_lower or 'compose' in title_lower or 'draft' in title_lower:
                work_type = 'writing'
            else:
                work_type = item.item_type.name.lower()

        # Look for similar work type immediately before or after
        similarity_score = 0.0

        # Check previous slot
        prev_slot = None
        for s in reversed(schedule.slots):
            if s.end > slot.start:  # Overlaps or goes beyond
                continue
            if s.scheduled_item_id is not None:
                prev_slot = s
                break
            # Free time breaks continuity for grouping
            break

        # Check next slot
        next_slot = None
        for s in schedule.slots:
            if s.start < slot.end:  # Overlaps or starts before
                continue
            if s.scheduled_item_id is not None:
                next_slot = s
                break
            # Free time breaks continuity for grouping
            break

        # Score based on adjacent similar work
        similar_adjacent = 0
        total_adjacent = 0

        if prev_slot:
            prev_item = schedule.get_item(prev_slot.scheduled_item_id)
            if prev_item:
                prev_work_type = prev_item.metadata.get('work_type')
                if not prev_work_type:
                    prev_title_lower = prev_item.title.lower()
                    if 'study' in prev_title_lower or 'learn' in prev_title_lower or 'read' in prev_title_lower:
                        prev_work_type = 'study'
                    elif 'meet' in prev_title_lower or 'call' in prev_title_lower or 'discuss' in prev_title_lower:
                        prev_work_type = 'meeting'
                    elif 'code' in prev_title_lower or 'program' in prev_title_lower or 'dev' in prev_title_lower:
                        prev_work_type = 'development'
                    elif 'write' in prev_title_lower or 'compose' in prev_title_lower or 'draft' in prev_title_lower:
                        prev_work_type = 'writing'
                    else:
                        prev_work_type = prev_item.item_type.name.lower()

                if prev_work_type == work_type:
                    similar_adjacent += 1
                total_adjacent += 1

        if next_slot:
            next_item = schedule.get_item(next_slot.scheduled_item_id)
            if next_item:
                next_work_type = next_item.metadata.get('work_type')
                if not next_work_type:
                    next_title_lower = next_item.title.lower()
                    if 'study' in next_title_lower or 'learn' in next_title_lower or 'read' in next_title_lower:
                        next_work_type = 'study'
                    elif 'meet' in next_title_lower or 'call' in next_title_lower or 'discuss' in next_title_lower:
                        next_work_type = 'meeting'
                    elif 'code' in next_title_lower or 'program' in next_title_lower or 'dev' in next_title_lower:
                        next_work_type = 'development'
                    elif 'write' in next_title_lower or 'compose' in next_title_lower or 'draft' in next_title_lower:
                        next_work_type = 'writing'
                    else:
                        next_work_type = next_item.item_type.name.lower()

                if next_work_type == work_type:
                    similar_adjacent += 1
                total_adjacent += 1

        if total_adjacent > 0:
            # Return proportion of similar adjacent work
            return (similar_adjacent / total_adjacent) * 2.0  # Scale up to 2.0 max

        return 0.0

    def _score_minimize_context_switching(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Preference for minimizing context switching."""
        # Context switching penalty when switching between very different types of work
        # Less penalty for similar types

        # Determine current item's cognitive domain
        current_domain = item.metadata.get('cognitive_domain', 'general')
        if current_domain == 'general':
            # Infer from metadata or title
            title_lower = item.title.lower()
            if any(word in title_lower for word in ['study', 'learn', 'read', 'memorize', 'understand']):
                current_domain = 'learning'
            elif any(word in title_lower for word in ['code', 'program', 'develop', 'build', 'create']):
                current_domain = 'creation'
            elif any(word in title_lower for word in ['meet', 'call', 'discuss', 'talk', 'present']):
                current_domain = 'communication'
            elif any(word in title_lower for word in ['write', 'compose', 'draft', 'document']):
                current_domain = 'writing'
            elif any(word in title_lower for word in ['exercise', 'run', 'walk', 'gym', 'sport']):
                current_domain = 'physical'
            elif any(word in title_lower for word in ['meditate', 'relax', 'rest', 'recover', 'break']):
                current_domain = 'recovery'
            else:
                current_domain = 'administrative'

        # Check domain of previous and next items
        domain_changes = 0
        total_transitions = 0

        # Previous transition
        prev_slot = None
        for s in reversed(schedule.slots):
            if s.end > slot.start:
                continue
            if s.scheduled_item_id is not None:
                prev_slot = s
                break
            break

        # Next transition
        next_slot = None
        for s in schedule.slots:
            if s.start < slot.end:
                continue
            if s.scheduled_item_id is not None:
                next_slot = s
                break
            break

        if prev_slot:
            prev_item = schedule.get_item(prev_slot.scheduled_item_id)
            if prev_item:
                prev_domain = prev_item.metadata.get('cognitive_domain', 'general')
                if prev_domain == 'general':
                    prev_title_lower = prev_item.title.lower()
                    if any(word in prev_title_lower for word in ['study', 'learn', 'read', 'memorize', 'understand']):
                        prev_domain = 'learning'
                    elif any(word in prev_title_lower for word in ['code', 'program', 'develop', 'build', 'create']):
                        prev_domain = 'creation'
                    elif any(word in prev_title_lower for word in ['meet', 'call', 'discuss', 'talk', 'present']):
                        prev_domain = 'communication'
                    elif any(word in prev_title_lower for word in ['write', 'compose', 'draft', 'document']):
                        prev_domain = 'writing'
                    elif any(word in prev_title_lower for word in ['exercise', 'run', 'walk', 'gym', 'sport']):
                        prev_domain = 'physical'
                    elif any(word in prev_title_lower for word in ['meditate', 'relax', 'rest', 'recover', 'break']):
                        prev_domain = 'recovery'
                    else:
                        prev_domain = 'administrative'

                if prev_domain != current_domain:
                    domain_changes += 1
                total_transitions += 1

        if next_slot:
            next_item = schedule.get_item(next_slot.scheduled_item_id)
            if next_item:
                next_domain = next_item.metadata.get('cognitive_domain', 'general')
                if next_domain == 'general':
                    next_title_lower = next_item.title.lower()
                    if any(word in next_title_lower for word in ['study', 'learn', 'read', 'memorize', 'understand']):
                        next_domain = 'learning'
                    elif any(word in next_title_lower for word in ['code', 'program', 'develop', 'build', 'create']):
                        next_domain = 'creation'
                    elif any(word in next_title_lower for word in ['meet', 'call', 'discuss', 'talk', 'present']):
                        next_domain = 'communication'
                    elif any(word in next_title_lower for word in ['write', 'compose', 'draft', 'document']):
                        next_domain = 'writing'
                    elif any(word in next_title_lower for word in ['exercise', 'run', 'walk', 'gym', 'sport']):
                        next_domain = 'physical'
                    elif any(word in next_title_lower for word in ['meditate', 'relax', 'rest', 'recover', 'break']):
                        next_domain = 'recovery'
                    else:
                        next_domain = 'administrative'

                if next_domain != current_domain:
                    domain_changes += 1
                total_transitions += 1

        if total_transitions > 0:
            # Penalty for context switching (lower score for more changes)
            # 0 changes = 1.0 score, 2 changes = 0.0 score
            change_ratio = domain_changes / total_transitions
            return max(0.0, 1.0 - change_ratio)

        return 1.0  # No transitions, no context switching penalty

    def _score_deadline_urgency(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Score based on deadline urgency - higher urgency gets higher score."""
        # Only applicable to items with due dates
        if not hasattr(item, 'due_date') or not item.due_date:
            return 0.0

        # Calculate time until due date
        time_until_due = (item.due_date - slot.start).total_seconds() / 3600  # hours

        if time_until_due < 0:
            # Already past due - high urgency
            return 2.0
        elif time_until_due <= 24:  # Due within 24 hours
            # Linear scale: 24 hours = 0.0, 0 hours = 2.0
            return 2.0 * (1.0 - (time_until_due / 24.0))
        elif time_until_due <= 72:  # Due within 3 days
            # Lower urgency but still positive
            return 1.0 * (1.0 - ((time_until_due - 24) / 48.0))
        else:
            # Far in the future - minimal urgency score
            return 0.0

    def _score_energy_fit(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Score based on energy level fit - match item energy requirements with time of day energy levels."""
        # Define energy requirement for item (from metadata or infer)
        energy_required = item.metadata.get('energy_level', 'medium')  # low, medium, high

        # Define typical energy levels by time of day (circadian rhythm)
        hour_of_day = slot.start.hour

        # Typical energy curve: low in early morning, peaks around 10 AM, dip after lunch, second peak around 4 PM, low at night
        if 5 <= hour_of_day < 8:
            time_energy = 'low'  # Early morning
        elif 8 <= hour_of_day < 12:
            time_energy = 'high'  # Morning peak
        elif 12 <= hour_of_day < 14:
            time_energy = 'medium'  # Post-lunch dip
        elif 14 <= hour_of_day < 17:
            time_energy = 'medium'  # Afternoon
        elif 17 <= hour_of_day < 21:
            time_energy = 'high'  # Evening peak
        else:
            time_energy = 'low'  # Night

        # Score based on match
        if energy_required == time_energy:
            return 2.0  # Perfect match
        elif (energy_required == 'high' and time_energy == 'medium') or \
             (energy_required == 'medium' and time_energy in ['high', 'low']) or \
             (energy_required == 'low' and time_energy == 'medium'):
            return 1.0  # Good match
        else:
            return 0.0  # Poor match

    def _score_proximity_to_target_event(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Score based on proximity to target event (for preparation and related activities)."""
        # Check if this item has a target event
        target_event_uid = item.metadata.get('target_event_id') or \
                          item.metadata.get('source_event_uid') or \
                          item.metadata.get('event_uid')

        if not target_event_uid:
            return 0.0

        # Find the target event in schedule
        target_event_item = schedule.get_item(target_event_uid)
        if not target_event_item:
            return 0.0

        # Find when target event is scheduled
        target_event_slot = None
        for s in schedule.slots:
            if s.scheduled_item_id == target_event_uid:
                target_event_slot = s
                break

        if not target_event_slot:
            return 0.0  # Target not scheduled yet

        # Calculate proximity - prefer closeness but respect preparation-before constraint
        # For preparation items, we want to be close but before the event
        is_preparation = (
            item.item_type.name == 'PREPARATION_BLOCK' or
            item.metadata.get('is_preparation', False) or
            'preparation' in item.title.lower()
        )

        if is_preparation:
            # For preparation, score based on how close we are to the event (but must be before)
            if slot.end <= target_event_slot.start:
                time_before = (target_event_slot.start - slot.end).total_seconds() / 60  # minutes
                # Prefer closer (but not too close - need reasonable prep time)
                if time_before <= 0:
                    return 0.0  # Shouldn't happen due to constraint, but safety
                elif time_before <= 30:  # Within 30 minutes
                    return 2.0  # Very close
                elif time_before <= 120:  # Within 2 hours
                    return 1.5  # Close
                elif time_before <= 360:  # Within 6 hours
                    return 1.0  # Reasonable
                else:
                    return 0.5  # Far but still valid
            else:
                # This shouldn't happen due to PreparationBeforeEventConstraint
                return float('-inf')
        else:
            # For non-preparation items, just score based on general proximity
            # Calculate distance to event (absolute time difference)
            if slot.start <= target_event_slot.start:
                time_diff = (target_event_slot.start - slot.start).total_seconds() / 3600  # hours
            else:
                time_diff = (slot.start - target_event_slot.start).total_seconds() / 3600  # hours

            # Closer is better, with decay
            if time_diff <= 1:  # Within 1 hour
                return 2.0
            elif time_diff <= 4:  # Within 4 hours
                return 1.5
            elif time_diff <= 12:  # Within 12 hours
                return 1.0
            elif time_diff <= 24:  # Within 24 hours
                return 0.5
            else:
                return 0.0

    def _score_continuity(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Score based on maintaining continuity of related activities."""
        # Similar to group_similar_work but more focused on logical continuation
        # Check if this item continues a sequence from previous items

        # Look for items that this might continue (based on metadata chains or titles)
        continues_prev = False
        continues_next = False

        # Check previous item
        prev_slot = None
        for s in reversed(schedule.slots):
            if s.end > slot.start:
                continue
            if s.scheduled_item_id is not None:
                prev_slot = s
                break

        if prev_slot:
            prev_item = schedule.get_item(prev_slot.scheduled_item_id)
            if prev_item:
                # Check if there's a explicit continuation link
                if item.metadata.get('continues_from') == prev_item.id:
                    continues_prev = True
                # Check if titles suggest continuation (e.g., "Part 2", "Continued", etc.)
                elif ('part' in item.title.lower() and 'part' in prev_item.title.lower()) or \
                     ('continued' in item.title.lower()) or \
                     (item.metadata.get('sequence') and prev_item.metadata.get('sequence') and
                      item.metadata.get('sequence') == prev_item.metadata.get('sequence') + 1):
                    continues_prev = True
                # Check if same project/task and sequential
                elif (hasattr(prev_item, 'project_id') and hasattr(item, 'project_id') and
                      prev_item.project_id == item.project_id and
                      getattr(prev_item, 'sequence_number', 0) + 1 == getattr(item, 'sequence_number', 1)):
                    continues_prev = True

        # Check next item
        next_slot = None
        for s in schedule.slots:
            if s.start < slot.end:
                continue
            if s.scheduled_item_id is not None:
                next_slot = s
                break

        if next_slot:
            next_item = schedule.get_item(next_slot.scheduled_item_id)
            if next_item:
                # Check if there's a explicit continuation link
                if next_item.metadata.get('continues_from') == item.id:
                    continues_next = True
                # Check if titles suggest continuation
                elif ('part' in next_item.title.lower() and 'part' in item.title.lower()) or \
                     ('continued' in next_item.title.lower()) or \
                     (next_item.metadata.get('sequence') and item.metadata.get('sequence') and
                      next_item.metadata.get('sequence') == item.metadata.get('sequence') + 1):
                    continues_next = True
                # Check if same project/task and sequential
                elif (hasattr(next_item, 'project_id') and hasattr(item, 'project_id') and
                      next_item.project_id == item.project_id and
                      getattr(item, 'sequence_number', 0) + 1 == getattr(next_item, 'sequence_number', 1)):
                    continues_next = True

        # Score based on continuations
        continuation_count = int(continues_prev) + int(continues_next)
        if continuation_count == 2:
            return 2.0  # Continues both previous and next (part of a sequence)
        elif continuation_count == 1:
            return 1.0  # Continues either previous or next
        else:
            return 0.0  # No clear continuation

    def _score_recovery_opportunity(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Score based on providing good recovery opportunities before/after intensive work."""
        # Check if this item is a recovery activity
        is_recovery = (
            item.metadata.get('activity_type') == 'recovery' or
            'recovery' in item.title.lower() or
            'break' in item.title.lower() or
            'rest' in item.title.lower() or
            'relax' in item.title.lower()
        )

        if not is_recovery:
            return 0.0

        # Look for intensive work before and after
        intensive_before = False
        intensive_after = False

        # Definition of intensive work
        def is_intensive_work(it):
            return (
                it.metadata.get('cognitive_load', 'low') in ['high'] or
                it.priority >= 4 or
                it.metadata.get('work_type') in ['development', 'creation', 'learning'] or
                ('study' in it.title.lower() and it.duration_minutes >= 60) or
                ('code' in it.title.lower() and it.duration_minutes >= 60)
            )

        # Check previous item
        prev_slot = None
        for s in reversed(schedule.slots):
            if s.end > slot.start:
                continue
            if s.scheduled_item_id is not None:
                prev_slot = s
                break

        if prev_slot:
            prev_item = schedule.get_item(prev_slot.scheduled_item_id)
            if prev_item and is_intensive_work(prev_item):
                intensive_before = True

        # Check next item
        next_slot = None
        for s in schedule.slots:
            if s.start < slot.end:
                continue
            if s.scheduled_item_id is not None:
                next_slot = s
                break

        if next_slot:
            next_item = schedule.get_item(next_slot.scheduled_item_id)
            if next_item and is_intensive_work(next_item):
                intensive_after = True

        # Recovery is valuable when it surrounds intensive work
        if intensive_before and intensive_after:
            return 2.0  # Sandwiching intensive work - ideal recovery
        elif intensive_before or intensive_after:
            return 1.0  # Adjacent to intensive work - good recovery
        else:
            return 0.5  # Isolated recovery - still beneficial but less optimal

    def _score_buffer_time(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Score based on providing appropriate buffer time between activities."""
        # Check if this item is designated as buffer time
        is_buffer = (
            item.metadata.get('activity_type') == 'buffer' or
            'buffer' in item.title.lower() or
            'transition' in item.title.lower() or
            'setup' in item.title.lower()
        )

        if not is_buffer:
            return 0.0

        # Look at what's before and after to determine buffer appropriateness
        prev_item = None
        next_item = None

        # Previous item
        prev_slot = None
        for s in reversed(schedule.slots):
            if s.end > slot.start:
                continue
            if s.scheduled_item_id is not None:
                prev_slot = s
                break

        if prev_slot:
            prev_item = schedule.get_item(prev_slot.scheduled_item_id)

        # Next item
        next_slot = None
        for s in schedule.slots:
            if s.start < slot.end:
                continue
            if s.scheduled_item_id is not None:
                next_slot = s
                break

        if next_slot:
            next_item = schedule.get_item(next_slot.scheduled_item_id)

        # Buffer is good when between different types of activities
        if prev_item and next_item:
            # Check if types are different
            prev_type = prev_item.metadata.get('work_type', prev_item.item_type.name.lower())
            next_type = next_item.metadata.get('work_type', next_item.item_type.name.lower())

            if prev_type != next_type:
                return 2.0  # Good buffer between different activities
            else:
                return 1.0  # Buffer between similar activities (still useful)
        elif prev_item or next_item:
            return 1.0  # Buffer at schedule edge
        else:
            return 0.5  # Isolated buffer

    def _score_fragmentation_penalty(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """Penalty for creating fragmentation in the schedule (negative score)."""
        # This is a penalty function - returns negative or zero score
        # Check if placing this item here would create small unusable gaps

        # Look at gaps before and after this slot
        gap_before = float('inf')
        gap_after = float('inf')

        # Gap before
        prev_end = None
        for s in reversed(schedule.slots):
            if s.end > slot.start:
                continue
            if s.scheduled_item_id is not None:
                prev_end = s.end
                break

        if prev_end is not None:
            gap_before = (slot.start - prev_end).total_seconds() / 60  # minutes

        # Gap after
        next_start = None
        for s in schedule.slots:
            if s.start < slot.end:
                continue
            if s.scheduled_item_id is not None:
                next_start = s.start
                break

        if next_start is not None:
            gap_after = (next_start - slot.end).total_seconds() / 60  # minutes

        # Penalize small gaps that are too small to be useful (< 15 minutes)
        # but don't penalize if they're exactly zero (perfect fit) or large enough to be useful
        penalty = 0.0
        if gap_before != float('inf') and 0 < gap_before < 15:
            penalty += (15 - gap_before) / 15.0  # Linear penalty up to -1.0
        if gap_after != float('inf') and 0 < gap_after < 15:
            penalty += (15 - gap_after) / 15.0  # Linear penalty up to -1.0

        return -penalty  # Return negative score (penalty)

    def generate_time_slots(self,
                          start_time: datetime,
                          end_time: datetime,
                          granularity_minutes: int = 30) -> List[TimeSlot]:
        """
        Generate discrete time slots for scheduling.
        """
        slots = []
        current = start_time

        while current < end_time:
            slot_end = current + timedelta(minutes=granularity_minutes)
            if slot_end > end_time:
                slot_end = end_time

            slots.append(TimeSlot(start=current, end=slot_end))
            current = slot_end

            if current >= end_time:
                break

        return slots

    def _schedule_greedy(self,
                         items: List[PlanningItem],
                         planning_horizon_start: datetime,
                         planning_horizon_end: datetime,
                         granularity_minutes: int = 30,
                         preserve_item_order: bool = False) -> Schedule:
        """
        Schedule items using greedy algorithm (original schedule_items implementation).
        """
        # Create initial schedule
        schedule = Schedule()
        schedule.items = {item.id: item for item in items}

        # Generate time slots
        all_slots = self.generate_time_slots(
            planning_horizon_start,
            planning_horizon_end,
            granularity_minutes
        )

        if preserve_item_order:
            sorted_items = items
        else:
            sorted_items = sorted(items, key=lambda x: (
                # Hard commitments reserve their immutable time before any
                # flexible task can consume a slot inside that interval.
                x.flexible,
                -x.priority,
                len(x.dependencies),
                x.duration_minutes,
            ))

        # Try to schedule each item in the selected candidate order.
        for item in sorted_items:
            best_placement = None  # Tuple of (start_slot_index, end_slot_exclusive, score)
            best_score = float('-inf')

            # Find best placement for this item (considering multi-slot items)
            # Calculate how many slots we need for this item
            slots_needed = max(1, (item.duration_minutes + granularity_minutes - 1) // granularity_minutes)

            # Try each possible starting slot
            for start_slot_idx in range(len(all_slots) - slots_needed + 1):
                # Check if we have enough consecutive free slots starting from start_slot_idx
                free_slots = True
                for i in range(slots_needed):
                    if all_slots[start_slot_idx + i].scheduled_item_id is not None:
                        free_slots = False
                        break

                if not free_slots:
                    continue

                # We have a valid placement - create a virtual slot representing the combined time period
                start_slot = all_slots[start_slot_idx]
                end_slot = all_slots[start_slot_idx + slots_needed - 1]

                # Create a combined slot for evaluation purposes
                combined_slot = TimeSlot(
                    start=start_slot.start,
                    end=end_slot.end
                )

                # Evaluate this placement for the item
                score = self.evaluate_item_slot(item, combined_slot, schedule)

                # Add a small bonus for tighter packing (less wasted space at the end)
                wasted_minutes = (slots_needed * granularity_minutes) - item.duration_minutes
                if wasted_minutes > 0:
                    # Small penalty for wasted space (encourages better fit)
                    score -= wasted_minutes / 60  # Convert to hours for penalty scale

                if score > best_score:
                    best_score = score
                    best_placement = (start_slot_idx, start_slot_idx + slots_needed, score)

            # If we found a valid placement, schedule the item
            if best_placement and best_score != float('-inf'):
                start_idx, end_idx, score = best_placement
                # Mark all slots in the placement as scheduled with this item
                for i in range(start_idx, end_idx):
                    all_slots[i].scheduled_item_id = item.id
                    all_slots[i].score = score  # This is approximate; in reality each slot might have different score
                    # For explainability, we could store the breakdown from the combined evaluation
                    # but for simplicity we're storing the aggregated score
                    schedule.slots.append(all_slots[i])
            else:
                # Could not schedule this item
                schedule.unscheduled_items.append(item)

        # Sort slots by time
        schedule.slots.sort(key=lambda s: s.start)

        # Calculate total score
        schedule.total_score = sum(slot.score for slot in schedule.slots if slot.score != float('-inf'))

        return schedule

    def _schedule_backtracking(self,
                               items: List[PlanningItem],
                               planning_horizon_start: datetime,
                               planning_horizon_end: datetime,
                               granularity_minutes: int = 30,
                               max_iterations: int = 100) -> Schedule:
        """
        Schedule items using iterative improvement with shuffling (backtracking-like approach).
        """
        best_schedule = self._schedule_greedy(
            items, planning_horizon_start, planning_horizon_end,
            granularity_minutes,
        )

        def candidate_key(schedule: Schedule) -> Tuple[int, float]:
            """Prefer complete schedules before optimizing their soft score."""
            return (len(schedule.unscheduled_items), -schedule.total_score)

        best_key = candidate_key(best_schedule)

        # Try iterative improvements
        for iteration in range(max_iterations):
            shift = iteration % len(items)
            candidate_items = items[shift:] + items[:shift]
            candidate_schedule = self._schedule_greedy(
                candidate_items,
                planning_horizon_start,
                planning_horizon_end,
                granularity_minutes,
                preserve_item_order=True,
            )
            candidate_key_value = candidate_key(candidate_schedule)

            if candidate_key_value < best_key:
                best_schedule = candidate_schedule
                best_key = candidate_key_value

        return best_schedule

    def _schedule_random(self,
                         items: List[PlanningItem],
                         planning_horizon_start: datetime,
                         planning_horizon_end: datetime,
                         granularity_minutes: int = 30) -> Schedule:
        """
        Schedule items using random item ordering.
        """
        # Use a deterministic rotation, so repeated planning runs are stable.
        if len(items) > 1:
            items = items[1:] + items[:1]
        return self._schedule_greedy(
            items,
            planning_horizon_start,
            planning_horizon_end,
            granularity_minutes,
            preserve_item_order=True,
        )

    def _schedule_priority_only(self,
                                items: List[PlanningItem],
                                planning_horizon_start: datetime,
                                planning_horizon_end: datetime,
                                granularity_minutes: int = 30) -> Schedule:
        """
        Schedule items sorted by priority only (ignoring dependencies and duration).
        """
        # Sort items by priority only
        sorted_items = sorted(items, key=lambda x: -x.priority)  # Higher priority first
        return self._schedule_greedy(sorted_items, planning_horizon_start, planning_horizon_end, granularity_minutes)

    def generate_candidate_schedules(self,
                                 items: List[PlanningItem],
                                 planning_horizon_start: datetime,
                                 planning_horizon_end: datetime,
                                 granularity_minutes: int = 30) -> List[Tuple[Schedule, str, float]]:
        """
        Generate multiple candidate schedules using different strategies.

        Returns:
            List of tuples (schedule, strategy_name, score)
        """
        import time
        start_time = time.time()

        candidates = []

        # Define available strategies
        strategies = {
            "greedy": self._schedule_greedy,
            "backtracking": self._schedule_backtracking,
            "random": self._schedule_random,
            "priority_only": self._schedule_priority_only
        }

        # Determine how many candidates to generate for each strategy
        # Based on strategy_weights, ensuring at least 1 per strategy if weight > 0
        strategy_counts = {}
        total_weight = sum(self.strategy_weights.values())

        for strategy, weight in self.strategy_weights.items():
            if weight > 0 and strategy in strategies:
                count = max(1, int(self.num_candidates * weight / total_weight))
                strategy_counts[strategy] = count

        # Adjust to ensure we get approximately num_candidates total
        total_assigned = sum(strategy_counts.values())
        if total_assigned < self.num_candidates:
            # Add remaining to the highest weighted strategy
            if self.strategy_weights:
                max_strategy = max(self.strategy_weights, key=self.strategy_weights.get)
                strategy_counts[max_strategy] += self.num_candidates - total_assigned

        # Generate candidates for each strategy
        for strategy, count in strategy_counts.items():
            if strategy not in strategies:
                continue

            strategy_func = strategies[strategy]

            for i in range(count):
                try:
                    if strategy == "backtracking":
                        # Backtracking strategy needs max_iterations parameter
                        schedule = strategy_func(items, planning_horizon_start, planning_horizon_end, granularity_minutes, max_iterations=50)
                    else:
                        schedule = strategy_func(items, planning_horizon_start, planning_horizon_end, granularity_minutes)

                    # Only consider schedules without hard constraint violations
                    if schedule.total_score != float('-inf'):
                        candidates.append((schedule, strategy, schedule.total_score))
                except Exception as e:
                    # Skip failed candidates
                    if self.enable_metrics:
                        self.metrics.setdefault('failed_candidates', 0)
                        self.metrics['failed_candidates'] += 1
                    continue

        # If no valid candidates were generated, fall back to greedy
        if not candidates:
            schedule = self._schedule_greedy(items, planning_horizon_start, planning_horizon_end, granularity_minutes)
            if schedule.total_score != float('-inf'):
                candidates.append((schedule, "greedy_fallback", schedule.total_score))

        # A valid candidate may still leave some items unplaced.  Always select
        # the candidate that places the most items before comparing soft scores.
        candidates.sort(
            key=lambda candidate: (
                # A candidate that drops a hard commitment is never an
                # acceptable alternative to one that postpones flexible work.
                sum(
                    not item.flexible
                    for item in candidate[0].unscheduled_items
                ),
                len(candidate[0].unscheduled_items),
                -candidate[2],
            )
        )

        # Update metrics
        if self.enable_metrics:
            generation_time = time.time() - start_time
            self.metrics.update({
                'generation_time': generation_time,
                'num_candidates_generated': len(candidates),
                'num_valid_candidates': len([c for c in candidates if c[2] != float('-inf')]),
                'average_score': sum(c[2] for c in candidates if c[2] != float('-inf')) / max(1, len([c for c in candidates if c[2] != float('-inf')])),
                'best_score': candidates[0][2] if candidates else float('-inf'),
                'worst_score': candidates[-1][2] if candidates else float('-inf'),
                'strategy_distribution': {}
            })

            # Count strategies
            for _, strategy, _ in candidates:
                self.metrics['strategy_distribution'][strategy] = self.metrics['strategy_distribution'].get(strategy, 0) + 1

        return candidates

    def schedule_items(self,
                      items: List[PlanningItem],
                      planning_horizon_start: datetime,
                      planning_horizon_end: datetime,
                      granularity_minutes: int = 30) -> Schedule:
        """
        Schedule items within the planning horizon by generating multiple candidate schedules
        and selecting the best one based on scoring model.
        Implements goal #7: Generate multiple candidate schedules and select the best.
        """
        # Handle empty items list
        if not items:
            schedule = Schedule()
            schedule.items = {}
            schedule.slots = []
            schedule.unscheduled_items = []
            schedule.total_score = 0.0
            return schedule

        # Generate candidate schedules
        candidates = self.generate_candidate_schedules(items, planning_horizon_start, planning_horizon_end, granularity_minutes)

        # Select the best valid candidate
        if candidates:
            best_schedule, best_strategy, best_score = candidates[0]

            # Track which strategy was selected
            if self.enable_metrics:
                self.metrics['selected_strategy'] = best_strategy
                self.metrics['selected_score'] = best_score

            return best_schedule
        else:
            # Fallback: create empty schedule if no valid candidates found
            schedule = Schedule()
            schedule.items = {item.id: item for item in items}
            schedule.slots = []
            schedule.unscheduled_items = items.copy()
            schedule.total_score = 0.0
            return schedule

    def evaluate_item_slot(self, item: PlanningItem, slot: TimeSlot, schedule: Schedule) -> float:
        """
        Evaluate how well an item fits in a given time slot.
        Returns the total score (negative infinity if hard constraints are violated).
        Also populates slot.score_breakdown with detailed scoring information.
        """
        # Reset score breakdown
        slot.score_breakdown = {}
        total_score = 0.0

        # Evaluate all hard constraints
        for constraint in self.constraints:
            score = constraint.evaluate(item, slot, schedule)
            constraint_name = constraint.__class__.__name__
            slot.score_breakdown[constraint_name] = score
            if score == float('-inf'):
                # Hard constraint violation - return immediately
                return float('-inf')
            total_score += score

        # Evaluate all scoring functions (soft preferences)
        for scoring_func in self.scoring_functions:
            score = scoring_func(item, slot, schedule)
            # Get function name for breakdown
            func_name = getattr(scoring_func, '__name__', str(scoring_func))
            slot.score_breakdown[func_name] = score
            total_score += score

        return total_score

    def reschedule_with_backtrack(self,
                                 items: List[PlanningItem],
                                 planning_horizon_start: datetime,
                                 planning_horizon_end: datetime,
                                 granularity_minutes: int = 30,
                                 max_iterations: int = 100) -> Schedule:
        """
        Attempt to find a better schedule using iterative improvement.
        Kept for backward compatibility - now uses the backtracking strategy.
        """
        # Use the backtracking strategy from our candidate generation
        return self._schedule_backtracking(items, planning_horizon_start, planning_horizon_end, granularity_minutes, max_iterations)


# Helper functions to create PlanningItems from existing domain models
def create_planning_item_from_university_event(event: UniversityEvent) -> PlanningItem:
    """Create a PlanningItem from a UniversityEvent."""
    return PlanningItem(
        id=event.uid,
        title=event.summary,
        description=event.description,
        item_type=PlanningItemType.UNIVERSITY_EVENT,
        preferred_start=event.dtstart,
        preferred_end=event.dtend,
        duration_minutes=event.duration_minutes,
        earliest_start=event.dtstart,
        latest_end=event.dtend,
        flexible=False,  # University events are usually fixed time
        priority=2,  # Medium-high priority for university events
        metadata={
            'event_type': event.event_type.value if event.event_type else None,
            'location': event.location,
            'is_group_event': event.is_group_event
        }
    )


def create_planning_item_from_task(task: Task) -> PlanningItem:
    """Create a PlanningItem from a Task."""
    return PlanningItem(
        id=task.id,
        title=task.title,
        description=task.description,
        item_type=PlanningItemType.PROJECT_TASK,
        preferred_start=task.due_date - timedelta(hours=2) if task.due_date else None,
        preferred_end=task.due_date,
        duration_minutes=int(task.estimated_hours * 60) if task.estimated_hours else 60,
        earliest_start=datetime.now(),  # Can start anytime from now
        latest_end=task.due_date,
        flexible=True,
        dependencies=set(task.dependencies),
        priority=3 if task.is_high_priority else 1,  # High priority for urgent/high priority tasks
        metadata={
            'project_id': task.project_id,
            'status': task.status.value,
            'priority': task.priority.value,
            'actual_hours': task.actual_hours
        }
    )


def create_planning_item_from_preparation_block(block: PreparationBlock) -> PlanningItem:
    """Create a PlanningItem from a PreparationBlock."""
    return PlanningItem(
        id=block.uid,
        title=block.summary,
        description=block.description,
        item_type=PlanningItemType.PREPARATION_BLOCK,
        preferred_start=block.dtstart,
        preferred_end=block.dtend,
        duration_minutes=block.duration_minutes,
        earliest_start=block.dtstart,
        latest_end=block.dtend,
        flexible=False,  # Preparation blocks are tied to specific events
        priority=2,  # Same priority as university events
        metadata={
            'source_event_uid': block.source_event_uid,
            'event_type': block.source_event_type,
            'preparation_minutes': block.preparation_minutes,
            'calendar_id': block.calendar_id
        }
    )


def create_planning_item_from_preparation_requirement(
    requirement: PreparationRequirement,
    earliest_start: Optional[datetime] = None,
) -> PlanningItem:
    """Create a flexible deep-work item that must finish before its lesson."""
    latest_end = requirement.due_time
    window_start = requirement.metadata.get('preparation_window_start')
    configured_earliest = (
        datetime.fromisoformat(window_start) if isinstance(window_start, str)
        else None
    )
    requested_earliest = earliest_start or datetime.now()
    if configured_earliest is not None:
        if configured_earliest.tzinfo is not None and requested_earliest.tzinfo is None:
            requested_earliest = requested_earliest.replace(tzinfo=configured_earliest.tzinfo)
        elif configured_earliest.tzinfo is None and requested_earliest.tzinfo is not None:
            configured_earliest = configured_earliest.replace(tzinfo=requested_earliest.tzinfo)
        requested_earliest = max(requested_earliest, configured_earliest)
    priority = int(requirement.metadata.get('priority', 2))
    return PlanningItem(
        id=requirement.id,
        title=requirement.title,
        description=requirement.description,
        item_type=PlanningItemType.PREPARATION_BLOCK,
        preferred_end=latest_end,
        duration_minutes=int(requirement.time_estimate.value),
        earliest_start=requested_earliest,
        latest_end=latest_end,
        flexible=True,
        priority=priority,
        metadata={
            **requirement.metadata,
            'is_preparation': True,
            'deep_work': True,
            'target_event_start': requirement.metadata.get(
                'target_event_start', requirement.due_time.isoformat(),
            ),
            'preparation_requirement_id': requirement.id,
        },
    )


if __name__ == "__main__":
    # Example usage
    engine = PlanningEngine()
    print("Deterministic planning engine initialized")
    print(f"Constraints: {[c.__class__.__name__ for c in engine.constraints]}")
    print(f"Scoring functions: {[f.__name__ for f in engine.scoring_functions]}")
