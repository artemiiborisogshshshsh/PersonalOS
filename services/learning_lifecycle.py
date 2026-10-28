"""Durable per-user learning records; Telegram file IDs remain private metadata.

Every mutation reloads and atomically replaces the user's document under a lock.
Notification delivery is an outbox with expiring claims: acknowledge only after
Telegram succeeds, release a claim on failure. Telegram itself has no idempotent
send API, so a crash between sending and acknowledging can still redeliver.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import threading
from uuid import uuid4
from zoneinfo import ZoneInfo
import fcntl

from services.attendance_preferences import course_name

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_ACTIONS = {'attend', 'miss', 'nothing', 'later', 'done', 'incomplete'}


def _value(event, *names, default=None):
    for name in names:
        value = event.get(name) if isinstance(event, dict) else getattr(event, name, None)
        if value is not None:
            return getattr(value, 'value', value)
    return default


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _dt(value, zone=None):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result.tzinfo is None:
        result = result.replace(tzinfo=zone or timezone.utc)
    return result


class LearningLifecycleStore:
    VERSION = 1

    def __init__(self, path: Path, user_id: str):
        self.path = Path(path)
        # macOS exposes its temporary roots through OS-owned aliases. Resolve
        # only those root aliases; user-controlled descendant symlinks fail.
        if self.path.is_absolute():
            for root in (Path('/tmp'), Path('/var')):
                if root.is_symlink() and self.path.is_relative_to(root):
                    self.path = root.resolve() / self.path.relative_to(root)
                    break
        self.user_id = str(user_id)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', self.user_id):
            raise ValueError('Invalid user ID')
        # A caller may use a temporary directory in tests; production paths
        # under users must always match the explicitly selected owner.
        if 'users' in self.path.parts:
            index = len(self.path.parts) - 1 - self.path.parts[::-1].index('users')
            if self.path.parts[index + 1] != self.user_id:
                raise ValueError('Learning state path belongs to another user')
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.path.absolute()), threading.RLock())
        with self._transaction(write=False):
            pass

    def _safe_path(self):
        if self.path.is_symlink() or any(parent.is_symlink() for parent in self.path.parents):
            raise ValueError('Learning state must not use symlinks')

    @contextmanager
    def _transaction(self, *, write=True):
        with self._lock:
            self._safe_path()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self.path.with_suffix(self.path.suffix + '.lock')
            if lock_path.is_symlink():
                raise ValueError('Learning lock must not be a symlink')
            with lock_path.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if self.path.exists():
                    state = json.loads(self.path.read_text(encoding='utf-8'))
                    if state.get('version') != self.VERSION or state.get('user_id') != self.user_id:
                        raise ValueError('Invalid learning state version or owner')
                else:
                    state = {'version': self.VERSION, 'user_id': self.user_id,
                             'secret': uuid4().hex, 'lessons': {}, 'blocks': {},
                             'notifications': {}, 'course_durations': {}, 'context': None}
                before = (deepcopy(state['lessons']), deepcopy(state['course_durations']))
                yield state
                if write:
                    if before != (state['lessons'], state['course_durations']):
                        # Domain change and dirty marker commit in one document.
                        # A crash before the dialogue outbox cannot lose sync.
                        state['calendar_revision'] = state.get('calendar_revision', 0) + 1
                    temporary = None
                    try:
                        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                                prefix='.learning.', suffix='.tmp', delete=False) as output:
                            temporary = Path(output.name)
                            json.dump(state, output, ensure_ascii=False, indent=2)
                            output.flush()
                            os.fsync(output.fileno())
                        os.replace(temporary, self.path)
                        directory_fd = os.open(self.path.parent, os.O_RDONLY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                    finally:
                        if temporary and temporary.exists():
                            temporary.unlink()

    def calendar_revision(self):
        with self._transaction(write=False) as state:
            return state.get('calendar_revision', 0)

    def has_pending_calendar_sync(self):
        with self._transaction(write=False) as state:
            return state.get('calendar_revision', 0) > state.get('synced_calendar_revision', 0)

    def mark_calendar_synced(self, revision):
        with self._transaction() as state:
            if type(revision) is not int or not 0 <= revision <= state.get('calendar_revision', 0):
                raise ValueError('Invalid synchronized learning revision')
            state['synced_calendar_revision'] = max(state.get('synced_calendar_revision', 0), revision)

    def lessons(self):
        with self._transaction(write=False) as state:
            return deepcopy(sorted(state['lessons'].values(), key=lambda lesson: lesson['start']))

    def get_lesson(self, lesson_id):
        with self._transaction(write=False) as state:
            return deepcopy(state['lessons'][lesson_id])

    def reconcile_lessons(self, events, aliases=None):
        """Merge a full source snapshot. Explicit old UID -> new UID aliases
        preserve identity across source UID changes; ambiguous rows stay distinct.
        Previously saved material is retained even for removed lessons.
        """
        rows = []
        for event in events:
            uid = str(_value(event, 'university_event_uid', 'source_uid', 'uid', 'id'))
            title = str(_value(event, 'summary', 'title', default=''))
            start = _value(event, 'dtstart', 'start_time', 'start')
            end = _value(event, 'dtend', 'end_time', 'end')
            if uid in ('None', '') or start is None or end is None:
                raise ValueError('Lesson requires source identity, start and end')
            if _dt(end) <= _dt(start):
                raise ValueError('Lesson end must follow its start')
            metadata = _value(event, 'metadata', default={})
            raw_type = str(_value(event, 'event_type', default=metadata.get('session_type', '')))
            session_type = {'ЛК': 'lecture', 'ЛБ': 'lab', 'ПР': 'practical',
                            'СЕМ': 'seminar', 'ВШ': 'workshop', 'ЭКЗ': 'exam'}.get(raw_type, raw_type)
            cancelled = bool(_value(event, 'is_cancelled', default=False))
            cancelled = cancelled or str(_value(event, 'state', default='')) in {'cancelled', 'possibly_cancelled', 'needs_review'}
            cancelled = cancelled or str(metadata.get('source_status', '')).casefold() == 'cancelled'
            rows.append({'source_uid': uid, 'source_personal_id': _value(event, 'id'), 'title': title,
                         'course': str(_value(event, 'course', default=course_name(title))),
                         'event_type': session_type,
                         'start': _iso(start), 'end': _iso(end),
                         'active': not cancelled})
        if len({row['source_uid'] for row in rows}) != len(rows):
            raise ValueError('Duplicate source lesson identity')
        with self._transaction() as state:
            lookup = {uid: lesson for lesson in state['lessons'].values()
                      for uid in lesson.get('source_aliases', [lesson['source_uid']])}
            personal_lookup = {lesson.get('source_personal_id'): lesson for lesson in state['lessons'].values()
                               if lesson.get('source_personal_id')}
            for old, new in (aliases or {}).items():
                if old in lookup and new not in lookup:
                    lookup[new] = lookup[old]
            changed = []
            seen = set()
            for row in rows:
                lesson = lookup.get(row['source_uid']) or personal_lookup.get(row['source_personal_id'])
                if lesson is None:
                    lesson_id = uuid4().hex[:16]
                    lesson = {'id': lesson_id, 'revision': 0, 'attendance': None,
                              'homework_state': 'unknown', 'materials': [],
                              'duration_minutes': state['course_durations'].get(row['course'].casefold(), 60),
                              'duration_override': False, 'remaining_minutes': None,
                              'remaining_text': '', 'completed': False, 'due_at': None,
                              'due_explicit': False, 'source_aliases': []}
                    state['lessons'][lesson_id] = lesson
                source_changed = any(lesson.get(key) != value for key, value in row.items())
                if source_changed:
                    lesson.update(row)
                    lesson['revision'] += 1
                    if row['source_uid'] not in lesson['source_aliases']:
                        lesson['source_aliases'].append(row['source_uid'])
                    changed.append(lesson)
                seen.add(lesson['id'])
            for lesson in state['lessons'].values():
                if lesson['id'] not in seen and lesson['active']:
                    lesson['active'] = False
                    lesson['revision'] += 1
                    changed.append(lesson)
            for lesson in state['lessons'].values():
                if lesson['due_explicit']:
                    continue
                following = [other for other in state['lessons'].values()
                             if other['active'] and other['course'].casefold() == lesson['course'].casefold()
                             and other['event_type'] == lesson['event_type']
                             and _dt(other['start']) > _dt(lesson['start'])]
                deadline = min(following, key=lambda other: _dt(other['start']))['start'] if following else None
                if lesson['due_at'] != deadline:
                    lesson['due_at'] = deadline
                    if lesson not in changed:
                        changed.append(lesson)
            return deepcopy(changed)

    def get_context(self):
        with self._transaction(write=False) as state:
            return deepcopy(state['context'])

    def set_context(self, lesson_id, kind='material', **payload):
        with self._transaction() as state:
            if lesson_id is not None and lesson_id not in state['lessons']:
                raise KeyError(lesson_id)
            state['context'] = {**payload, 'lesson_id': lesson_id, 'kind': kind}

    def callback_data(self, lesson_id, action):
        if action not in _ACTIONS:
            raise ValueError('Unknown learning action')
        # Persist the secret even before the first mutation.
        with self._transaction() as state:
            lesson = state['lessons'][lesson_id]
            body = f"learn:{lesson_id}:{lesson['revision']}:{action}"
            signature = hmac.new(state['secret'].encode(), body.encode(), sha256).hexdigest()[:10]
            return f'{body}:{signature}'

    def apply_callback(self, data):
        try:
            prefix, lesson_id, revision, action, signature = data.split(':')
        except (ValueError, AttributeError):
            return None
        with self._transaction() as state:
            body = f'{prefix}:{lesson_id}:{revision}:{action}'
            expected = hmac.new(state['secret'].encode(), body.encode(), sha256).hexdigest()[:10]
            lesson = state['lessons'].get(lesson_id)
            if (prefix != 'learn' or action not in _ACTIONS or not hmac.compare_digest(signature, expected)
                    or lesson is None or str(lesson['revision']) != revision or not lesson['active']):
                return None
            if action in {'attend', 'miss'}:
                lesson['attendance'] = action == 'attend'
            elif action in {'nothing', 'later'}:
                lesson['homework_state'] = 'none' if action == 'nothing' else 'later'
                if action == 'nothing':
                    lesson['completed'] = True
                    lesson['remaining_minutes'] = 0
            elif action == 'done':
                lesson.update(completed=True, remaining_minutes=0, remaining_text='')
            lesson['revision'] += 1
            state['context'] = {'lesson_id': lesson_id,
                                'kind': 'remaining' if action == 'incomplete' else 'material'}
            return deepcopy(lesson)

    def add_material(self, lesson_id, text='', kind='homework', file_id=None,
                     file_unique_id=None, source_message_id=None, due_at=None, links=None, file_type='photo'):
        if kind not in {'homework', 'note', 'material'}:
            raise ValueError('Invalid material kind')
        if not text and not file_id and not links:
            raise ValueError('Material must contain text, photo or link')
        with self._transaction() as state:
            lesson = state['lessons'][lesson_id]
            # Telegram retries of the same intake message do not duplicate content.
            if source_message_id is not None and any(
                    item.get('source_message_id') == source_message_id for item in lesson['materials']):
                return deepcopy(lesson)
            material = {'id': uuid4().hex[:16], 'kind': kind, 'text': text,
                        'file_id': file_id, 'file_unique_id': file_unique_id, 'file_type': file_type,
                        'source_message_id': source_message_id, 'links': list(links or [])}
            lesson['materials'].append(material)
            if kind == 'homework':
                lesson.update(homework_state='assigned', completed=False)
                if lesson['remaining_minutes'] in (None, 0):
                    lesson['remaining_minutes'] = lesson['duration_minutes']
            if due_at is not None:
                _dt(due_at)
                lesson.update(due_at=_iso(due_at), due_explicit=True)
            lesson['revision'] += 1
            state['context'] = {'lesson_id': lesson_id, 'kind': 'material'}
            return deepcopy(lesson)

    def set_deadline(self, lesson_id, due_at):
        _dt(due_at)
        with self._transaction() as state:
            lesson = state['lessons'][lesson_id]
            lesson.update(due_at=_iso(due_at), due_explicit=True)
            lesson['revision'] += 1
            return deepcopy(lesson)

    def set_homework_duration(self, lesson_id, minutes):
        self._minutes(minutes)
        with self._transaction() as state:
            lesson = state['lessons'][lesson_id]
            lesson.update(duration_minutes=minutes, duration_override=True)
            if not lesson['completed']:
                lesson['remaining_minutes'] = minutes
            lesson['revision'] += 1
            return deepcopy(lesson)

    def set_course_duration(self, course, minutes):
        self._minutes(minutes)
        with self._transaction() as state:
            state['course_durations'][course.casefold()] = minutes
            for lesson in state['lessons'].values():
                if lesson['course'].casefold() == course.casefold() and not lesson['duration_override']:
                    lesson['duration_minutes'] = minutes
                    if not lesson['completed']:
                        lesson['remaining_minutes'] = minutes
                    lesson['revision'] += 1

    @staticmethod
    def _minutes(minutes):
        if type(minutes) is not int or not 0 < minutes <= 7 * 24 * 60:
            raise ValueError('Preparation minutes must be a positive integer up to one week')

    def record_preparation_result(self, lesson_id, completed, remaining_text='', remaining_minutes=None):
        if not completed:
            self._minutes(remaining_minutes)
        with self._transaction() as state:
            lesson = state['lessons'][lesson_id]
            lesson.update(completed=bool(completed), remaining_text='' if completed else remaining_text,
                          remaining_minutes=0 if completed else remaining_minutes)
            lesson['revision'] += 1
            state['context'] = {'lesson_id': lesson_id, 'kind': 'material' if completed else 'remaining'}
            return deepcopy(lesson)

    def register_preparation_blocks(self, blocks):
        """Replace the active block snapshot without resetting delivery receipts."""
        with self._transaction() as state:
            result = {}
            source = {uid: item['id'] for item in state['lessons'].values()
                      for uid in item['source_aliases']}
            for block in blocks:
                block_id = str(_value(block, 'id', 'uid'))
                lesson_id = (_value(block, 'lesson_id') or source.get(str(_value(block, 'source_event_uid')))
                             or source.get(str(_value(block, 'source_event_id'))))
                if lesson_id is None:
                    reference = str(_value(block, 'source_event_id'))
                    lesson_id = next((item['id'] for item in state['lessons'].values()
                                      if item.get('source_personal_id') == reference), None)
                    if reference in state['lessons']:
                        lesson_id = reference
                if lesson_id not in state['lessons']:
                    raise ValueError('Preparation block references an unknown lesson')
                start, end = _value(block, 'start', 'dtstart', 'start_time'), _value(block, 'end', 'dtend', 'end_time')
                if start is None or end is None or _dt(end) <= _dt(start):
                    raise ValueError('Preparation block requires valid start and end')
                result[block_id] = {'id': block_id, 'lesson_id': lesson_id, 'start': _iso(start), 'end': _iso(end)}
            state['blocks'] = result

    def preparation_context(self, block_id):
        with self._transaction(write=False) as state:
            block = state['blocks'][block_id]
            return {'block': deepcopy(block), 'lesson': deepcopy(state['lessons'][block['lesson_id']])}

    @staticmethod
    def _missing(lesson):
        missing = []
        if lesson['attendance'] is None:
            missing.append('attendance')
        if lesson['homework_state'] in {'unknown', 'later'}:
            missing.append('homework')
        return missing

    def due_notifications(self, now, timezone_name='Asia/Tomsk', travel_minutes=0):
        if type(travel_minutes) is not int or travel_minutes < 0:
            raise ValueError('Travel minutes must be nonnegative')
        zone = ZoneInfo(timezone_name)
        now = _dt(now, zone).astimezone(zone)
        with self._transaction() as state:
            candidates = []
            active = [lesson for lesson in state['lessons'].values() if lesson['active']]
            for lesson in active:
                start, end = _dt(lesson['start'], zone).astimezone(zone), _dt(lesson['end'], zone).astimezone(zone)
                missing = self._missing(lesson)
                # Do not replay old attendance prompts after a long outage.
                if lesson['attendance'] is None and end - timedelta(minutes=10) <= now < end:
                    candidates.append({'id': f"attendance:{lesson['id']}", 'kind': 'attendance',
                                       'lesson_id': lesson['id'], 'due_at': (end - timedelta(minutes=10)).isoformat(),
                                       'missing': missing})
                if missing and start.date() == now.date():
                    day = [item for item in active if _dt(item['start'], zone).astimezone(zone).date() == start.date()]
                    last_end = max(_dt(item['end'], zone).astimezone(zone) for item in day)
                    at_five = datetime.combine(start.date(), time(17), tzinfo=zone)
                    evening = at_five if last_end <= at_five else max(last_end, last_end + timedelta(minutes=travel_minutes - 15))
                    if now >= evening:
                        candidates.append({'id': f"evening:{lesson['id']}", 'kind': 'evening',
                                           'lesson_id': lesson['id'], 'due_at': evening.isoformat(), 'missing': missing})
            for block in state['blocks'].values():
                lesson = state['lessons'][block['lesson_id']]
                if lesson['completed'] or not lesson['active']:
                    continue
                start, end = _dt(block['start'], zone), _dt(block['end'], zone)
                for kind, due, expires in [('preparation_start', start, end),
                                            ('preparation_end', end, end + timedelta(hours=12))]:
                    if due <= now < expires:
                        # A moved block needs a new prompt at its new time.
                        fingerprint_value = block['start'] if kind == 'preparation_start' else f"{block['start']}:{block['end']}"
                        fingerprint = sha256(fingerprint_value.encode()).hexdigest()[:8]
                        candidates.append({'id': f"{kind}:{block['id']}:{fingerprint}", 'kind': kind,
                                           'lesson_id': block['lesson_id'], 'block_id': block['id'],
                                           'due_at': due.isoformat()})
            for item in candidates:
                state['notifications'].setdefault(item['id'], {'status': 'pending'})
            return [deepcopy(item) for item in candidates if state['notifications'][item['id']]['status'] != 'sent'
                    and not self._claimed(state['notifications'][item['id']], now)]

    @staticmethod
    def _claimed(notification, now):
        return (notification.get('status') == 'claimed'
                and _dt(notification['claim_until']) > _dt(now))

    def claim_notification(self, notification_id, now, lease_seconds=300):
        with self._transaction() as state:
            notification = state['notifications'].get(notification_id)
            if notification is None or notification['status'] == 'sent' or self._claimed(notification, now):
                return False
            notification.update(status='claimed', claim_until=(_dt(now) + timedelta(seconds=lease_seconds)).isoformat())
            return True

    def ack_notification(self, notification_id, message_id=None):
        with self._transaction() as state:
            notification = state['notifications'][notification_id]
            notification.update(status='sent', message_id=message_id)

    def release_notification(self, notification_id):
        with self._transaction() as state:
            notification = state['notifications'].get(notification_id)
            if notification and notification['status'] != 'sent':
                notification.update(status='pending')


def plan_learning_preparations(store, settings, now, events, fixed_commitments, existing_blocks=()):
    """Plan whole homework across several blocks using the existing engine.

    The returned blocks replace future app-owned homework preparation only.
    Personal appointments and lessons reserve their exact intervals. Retained
    preparations also reserve the configured pause before and after their
    intervals, including a recently finished block. An incomplete plan raises
    before publication.
    Homework with no known deadline stays saved without an invented due date.
    """
    from math import ceil
    from planning_engine import (PlanningEngine, PlanningItem, PlanningItemType,
                                 SleepConstraint, MaxContinuousWorkConstraint,
                                 PreparationBreakConstraint)
    from services.adaptive_preparation_service import (
        AdaptivePreparationService, DraftPreparationBlock, _PreparationBusyConstraint,
    )
    from services.weekly_plan_service import CommitmentType, FixedCommitment

    profile = settings.profile
    events = list(events)
    existing_blocks = list(existing_blocks)
    zone = ZoneInfo(profile.timezone)
    aware = now.tzinfo is not None

    def local(value):
        result = _dt(value, zone).astimezone(zone)
        return result if aware else result.replace(tzinfo=None)

    now = local(now)
    lessons = store.lessons()
    owned = {lesson.get('source_personal_id') or lesson['id'] for lesson in lessons
             if lesson['homework_state'] == 'assigned' or lesson['completed']}
    underway = {str(_value(block, 'source_event_id')) for block in existing_blocks
                if local(_value(block, 'start')) <= now < local(_value(block, 'end'))}
    requested = [lesson for lesson in lessons if lesson['active'] and not lesson['completed']
                 and lesson['homework_state'] == 'assigned' and lesson['due_at']
                 and (lesson.get('source_personal_id') or lesson['id']) not in underway]
    if not requested:
        return []
    for lesson in requested:
        if local(lesson['due_at']) <= now:
            raise ValueError(f"{lesson['course']}: срок ДЗ уже прошёл. Укажи новый срок для продолжения.")
    horizon_end = min(max(local(lesson['due_at']) for lesson in requested),
                      local(profile.planning_horizon_end(now)))
    if horizon_end <= now:
        raise ValueError('В допустимом окне нет времени для подготовки.')
    # Use the established source planner for campus time, travel and recovery.
    commitments = list(fixed_commitments)
    commitments.extend(settings.sleep_commitments(now, horizon_end))
    commitments.extend(settings.routine_commitments(now, horizon_end, events))
    commitments.extend(AdaptivePreparationService(profile)._university_commitments(events, now, horizon_end))
    preparation_pause = timedelta(minutes=max(0, profile.preparation_break_minutes))
    for block in existing_blocks:
        start, end = local(_value(block, 'start')), local(_value(block, 'end'))
        source = str(_value(block, 'source_event_id'))
        # Retained blocks are fixed commitments rather than new preparation
        # PlanningItems, so the engine's PreparationBreakConstraint cannot
        # see their pauses. Keep the trailing pause after a finished block
        # and both pauses around a retained future block. Own future blocks
        # being replaced remain free for the replan.
        if end + preparation_pause > now and (source not in owned or start <= now):
            commitments.append(FixedCommitment(
                id=f"existing:{_value(block, 'id')}", title=str(_value(block, 'title', default='Подготовка')),
                start=start - preparation_pause, end=end + preparation_pause,
                commitment_type=CommitmentType.OTHER,
            ))
    intervals = tuple((local(item.start), local(item.end)) for item in commitments
                      if local(item.end) > now and local(item.start) < horizon_end)
    engine = PlanningEngine(num_candidates=1, strategy_weights={'greedy': 1.0})
    if any(item.commitment_type == CommitmentType.SLEEP for item in commitments):
        engine.constraints = [item for item in engine.constraints if not isinstance(item, SleepConstraint)]
    for constraint in engine.constraints:
        if isinstance(constraint, MaxContinuousWorkConstraint):
            constraint.max_continuous_work_minutes = profile.max_continuous_deep_work_minutes
        if isinstance(constraint, PreparationBreakConstraint):
            constraint.break_minutes = profile.preparation_break_minutes
    engine.constraints.append(_PreparationBusyConstraint(intervals))
    maximum = min(profile.max_single_block_minutes, profile.max_continuous_deep_work_minutes)
    if maximum < 5:
        raise ValueError('Максимальный блок подготовки должен быть не короче 5 минут.')
    maximum -= maximum % 5
    items, requests = [], {}
    for lesson in sorted(requested, key=lambda row: local(row['due_at'])):
        minutes = lesson['remaining_minutes'] or lesson['duration_minutes']
        chunks = []
        while minutes > 0:
            chunk = min(maximum, minutes)
            chunks.append(int(ceil(chunk / 5) * 5))
            minutes -= chunk
        deadline = min(local(lesson['due_at']), horizon_end)
        earliest = max(now, local(lesson['end']))
        for index, minutes in enumerate(chunks):
            request_id = f"learning:{lesson['id']}:{index}"
            requests[request_id] = (lesson, index, minutes)
            items.append(PlanningItem(
                id=request_id, title=f"Подготовка: {lesson['course']}",
                description=lesson['remaining_text'], item_type=PlanningItemType.PREPARATION_BLOCK,
                duration_minutes=minutes, earliest_start=earliest, latest_end=deadline,
                preferred_start=earliest, preferred_end=earliest + timedelta(minutes=minutes),
                priority=1_000_000 - int((deadline - now).total_seconds() / 60),
                metadata={'is_preparation': True, 'deep_work': True, 'cognitive_load': 'medium'},
            ))
    grid = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
    schedule = engine.schedule_items(items, grid, horizon_end, 5)
    blocks = []
    for request_id, (lesson, index, minutes) in requests.items():
        slots = [slot for slot in schedule.slots if slot.scheduled_item_id == request_id]
        if not slots:
            raise ValueError(f"{lesson['course']}: не хватает свободного времени до срока ДЗ. "
                             'Можно изменить срок, длительность или ограничения.')
        start, end = min(slot.start for slot in slots), max(slot.end for slot in slots)
        if (end - start).total_seconds() != minutes * 60 or any(start < stop and end > begin
                                                                             for begin, stop in intervals):
            raise ValueError(f"{lesson['course']}: не найден непрерывный свободный блок.")
        identifier = sha256(f"{request_id}:{start.isoformat()}:{end.isoformat()}".encode()).hexdigest()[:20]
        blocks.append(DraftPreparationBlock(
            id=f'learning-prep:{identifier}', source_event_id=lesson.get('source_personal_id') or lesson['id'],
            title=f"Подготовка: {lesson['course']}", start=start, end=end, minutes=minutes,
            reason='ДЗ и оставшаяся работа; свободное время с учётом сна, дороги и личных событий.',
            status='confirmed', session_type=lesson['event_type'],
        ))
    return sorted(blocks, key=lambda block: block.start)
