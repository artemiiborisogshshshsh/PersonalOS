"""Read-only AlfaCRM v2 source for a teacher's work schedule.

The connector intentionally reads only planned lessons.  It never creates or
updates records in AlfaCRM; the returned events become fixed commitments for
the personal planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from services.weekly_plan_service import CommitmentType, FixedCommitment
from services.sync_retry import retry_read


class AlfaCRMError(RuntimeError):
    """AlfaCRM refused a request or returned an incomplete lesson."""


@dataclass(frozen=True)
class AlfaCRMConnection:
    """Per-user runtime configuration; ``api_key`` is never persisted here."""

    base_url: str
    branch_id: int | str
    email: str
    api_key: str = field(repr=False)
    teacher_id: int | str = ''
    timezone: str = 'Asia/Tomsk'

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {'https'} or not parsed.netloc:
            raise ValueError('AlfaCRM base_url must be an HTTPS URL')
        if (
            not str(self.branch_id).strip() or not self.email.strip()
            or not self.api_key or not str(self.teacher_id).strip()
        ):
            raise ValueError('AlfaCRM branch_id, email, api_key and teacher_id are required')
        ZoneInfo(self.timezone)

    @property
    def endpoint_root(self) -> str:
        return self.base_url.rstrip('/')


@dataclass(frozen=True)
class AlfaCRMLesson:
    """Provider-neutral representation of one planned work lesson."""

    id: str
    title: str
    start: datetime
    end: datetime
    location: str = ''
    subject: str = ''
    group: str = ''
    students: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def mode_key(self) -> str:
        """Stable preference key; a changed roster intentionally asks again."""
        values = (self.subject or self.title, self.group, *sorted(self.students))
        return sha256('|'.join(value.strip().casefold() for value in values).encode()).hexdigest()[:24]

    @property
    def content_hash(self) -> str:
        payload = '|'.join((
            self.id, self.title, self.subject, self.group, ','.join(self.students),
            self.location, self.start.isoformat(), self.end.isoformat(),
        ))
        return sha256(payload.encode()).hexdigest()

    @property
    def display_name(self) -> str:
        subject = self.subject or self.title or 'Занятие AlfaCRM'
        audience = self.group or ', '.join(self.students)
        return f'{subject} — {audience}' if audience else subject

    def to_fixed_commitment(self) -> FixedCommitment:
        return FixedCommitment(
            id=f'alfacrm:{self.id}',
            title=f'Работа: {self.display_name}',
            start=self.start,
            end=self.end,
            commitment_type=CommitmentType.TUTORING,
            location=self.location,
            metadata={
                'provider': 'alfacrm',
                'lesson_id': self.id,
                'mode_key': self.mode_key,
                'content_hash': self.content_hash,
                **self.metadata,
            },
        )


class AlfaCRMScheduleSource:
    """Fetches planned teacher lessons using the documented AlfaCRM v2 API."""

    PLANNED_STATUS = 1
    MAX_PAGES = 100

    def __init__(
        self,
        connection: AlfaCRMConnection,
        post: Optional[Callable[..., Any]] = None,
    ):
        self.connection = connection
        self._post = post or requests.post
        self._reference_names: dict[str, dict[str, str]] = {
            'subject': {}, 'group': {}, 'customer': {},
        }

    def fetch(self, start: datetime, end: datetime) -> list[AlfaCRMLesson]:
        """Return planned lessons in ``[start, end)`` in the user's timezone."""
        if end <= start:
            raise ValueError('AlfaCRM fetch end must be after start')
        timezone = ZoneInfo(self.connection.timezone)
        start = self._in_timezone(start, timezone)
        end = self._in_timezone(end, timezone)
        token = self._authenticate()
        records: list[dict[str, Any]] = []
        for page in range(self.MAX_PAGES):
            payload: dict[str, Any] = {
                'page': page,
                'status': self.PLANNED_STATUS,
                'date_from': start.date().isoformat(),
                'date_to': end.date().isoformat(),
            }
            payload['teacher_id'] = self.connection.teacher_id
            response = self._request(
                f'/v2api/{self.connection.branch_id}/lesson/index', payload, token,
            )
            page_records = self._records(response, require_container=True)
            records.extend(page_records)
            if not page_records or not self._has_next_page(response, page, len(page_records)):
                break
        else:
            raise AlfaCRMError('AlfaCRM pagination did not finish safely')

        references = self._reference_lookup(records, token)
        lessons: list[AlfaCRMLesson] = []
        seen: set[str] = set()
        for record in records:
            # The API filter is authoritative, but retaining this check makes
            # a malformed/custom deployment unable to add cancelled lessons.
            if self._status(record) != self.PLANNED_STATUS:
                continue
            lesson = self._lesson_from_record(record, timezone, references)
            if lesson.id in seen or lesson.end <= start or lesson.start >= end:
                continue
            seen.add(lesson.id)
            lessons.append(lesson)
        return sorted(lessons, key=lambda lesson: (lesson.start, lesson.id))

    def fixed_commitments(self, start: datetime, end: datetime) -> list[FixedCommitment]:
        """Convenience boundary for PlanningEngine and preparation planning."""
        return [lesson.to_fixed_commitment() for lesson in self.fetch(start, end)]

    def _authenticate(self) -> str:
        # AlfaCRM access tokens are short-lived. Authenticating at the start
        # of each read cycle is cheap and prevents a long-running bot from
        # silently using an expired credential hours later.
        response = self._request(
            '/v2api/auth/login',
            {'email': self.connection.email, 'api_key': self.connection.api_key},
        )
        token = response.get('token')
        if not token and isinstance(response.get('data'), dict):
            token = response['data'].get('token')
        if not isinstance(token, str) or not token:
            raise AlfaCRMError('AlfaCRM did not return an API token')
        return token

    def _request(
        self, path: str, payload: dict[str, Any], token: Optional[str] = None,
    ) -> dict[str, Any]:
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if token:
            headers['X-ALFACRM-TOKEN'] = token

        def read_response():
            response = self._post(
                f'{self.connection.endpoint_root}{path}', json=payload,
                headers=headers, timeout=20,
            )
            response.raise_for_status()
            return response

        try:
            # This source exposes only read endpoints (AlfaCRM uses POST for
            # queries). Never reuse this retry policy for CRM mutations.
            response = retry_read(read_response)
            body = response.json()
        except requests.RequestException as error:
            raise AlfaCRMError('Could not reach AlfaCRM') from error
        except ValueError as error:
            raise AlfaCRMError('AlfaCRM returned invalid JSON') from error
        if not isinstance(body, dict):
            raise AlfaCRMError('AlfaCRM returned an unexpected response')
        if body.get('error') or body.get('success') is False:
            raise AlfaCRMError(str(body.get('message') or body.get('error') or 'AlfaCRM request failed'))
        return body

    @staticmethod
    def _records(body: dict[str, Any], *, require_container: bool = False) -> list[dict[str, Any]]:
        """Extract a provider record list without mistaking an unknown body for empty.

        An empty recognised ``items``/``data``/``result`` list is a valid
        timetable snapshot. A successful-looking body with none of those
        containers is not evidence that all lessons disappeared, so the
        primary lesson reader fails closed instead of authorising cleanup.
        Reference-name lookups intentionally retain the permissive default.
        """
        recognised = False
        for key in ('items', 'data', 'result'):
            if key not in body:
                continue
            recognised = True
            value = body.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                for nested_key in ('items', 'data', 'result'):
                    if nested_key not in value:
                        continue
                    recognised = True
                    nested = value[nested_key]
                    if isinstance(nested, list):
                        return [item for item in nested if isinstance(item, dict)]
        if require_container and not recognised:
            raise AlfaCRMError('AlfaCRM returned a lesson snapshot without a recognised records container')
        return []

    @staticmethod
    def _has_next_page(body: dict[str, Any], page: int, count: int) -> bool:
        meta = body.get('meta') or body.get('_meta') or body.get('pagination') or {}
        if isinstance(meta, dict):
            for key in ('pageCount', 'page_count', 'pages'):
                if meta.get(key) is not None:
                    return page + 1 < int(meta[key])
            for key in ('hasNextPage', 'has_next_page'):
                if key in meta:
                    return bool(meta[key])
        # AlfaCRM's documented endpoint is paginated. Continue only while a
        # full conventional page was returned; this avoids needless requests.
        return count >= 100

    @staticmethod
    def _status(record: dict[str, Any]) -> int:
        try:
            return int(record.get('status', AlfaCRMScheduleSource.PLANNED_STATUS))
        except (TypeError, ValueError):
            return 0

    def _lesson_from_record(
        self, record: dict[str, Any], timezone: ZoneInfo,
        references: dict[str, dict[str, str]],
    ) -> AlfaCRMLesson:
        lesson_id = record.get('id')
        if lesson_id is None:
            raise AlfaCRMError('AlfaCRM lesson has no id')
        date_value = str(record.get('date') or record.get('lesson_date') or '').strip()
        time_from = str(record.get('time_from') or '').strip()
        time_to = str(record.get('time_to') or '').strip()
        if not date_value or not time_from:
            raise AlfaCRMError(f'AlfaCRM lesson {lesson_id} has no start time')
        start = self._parse_datetime(date_value, time_from, timezone)
        if time_to:
            end = self._parse_datetime(date_value, time_to, timezone)
            if end <= start:
                end += timedelta(days=1)
        else:
            try:
                duration = int(record.get('duration', 0))
            except (TypeError, ValueError):
                duration = 0
            if duration <= 0:
                raise AlfaCRMError(f'AlfaCRM lesson {lesson_id} has no end time or duration')
            end = start + timedelta(minutes=duration)
        subject_id = record.get('subject_id')
        subject = str(record.get('subject_name') or record.get('subject') or '').strip()
        if not subject and subject_id is not None:
            subject = references['subject'].get(str(subject_id), '')
        fallback_title = str(record.get('name') or record.get('topic') or '').strip()
        if not subject and fallback_title.casefold() != 'занятие alfacrm':
            subject = fallback_title
        group = str(
            record.get('group_name') or record.get('group') or record.get('group_title') or ''
        ).strip()
        group_ids = self._ids(record.get('group_ids') or record.get('group_id'))
        if not group and group_ids:
            group = ', '.join(
                references['group'].get(identifier, '') for identifier in group_ids
                if references['group'].get(identifier, '')
            )
        students = self._names(
            record.get('customer_names') or record.get('students') or record.get('customers')
            or record.get('customer_name') or record.get('customer')
        )
        customer_ids = self._ids(record.get('customer_ids') or record.get('customer_id'))
        if not students and customer_ids:
            students = tuple(
                references['customer'].get(identifier, '') for identifier in customer_ids
                if references['customer'].get(identifier, '')
            )
        title = subject or fallback_title or 'Занятие AlfaCRM'
        location = str(
            record.get('location_name') or record.get('room_name')
            or record.get('room') or ''
        ).strip()
        return AlfaCRMLesson(
            id=f'{self.connection.branch_id}:{lesson_id}', title=title,
            start=start, end=end, location=location,
            subject=subject, group=group, students=students,
            metadata={
                'branch_id': self.connection.branch_id,
                'teacher_ids': record.get('teacher_ids', []),
                'lesson_type_id': record.get('lesson_type_id'),
                'subject_id': subject_id,
                'group_ids': group_ids,
                'customer_ids': customer_ids,
                'updated_at': record.get('updated_at'),
            },
        )

    def _reference_lookup(
        self, records: Iterable[dict[str, Any]], token: str,
    ) -> dict[str, dict[str, str]]:
        """Resolve lesson references using only documented ``index`` reads."""
        requested = {
            'subject': {str(item['subject_id']) for item in records if item.get('subject_id') is not None},
            'group': {identifier for item in records for identifier in self._ids(
                item.get('group_ids') or item.get('group_id'))},
            'customer': {identifier for item in records for identifier in self._ids(
                item.get('customer_ids') or item.get('customer_id'))},
        }
        for kind, identifiers in requested.items():
            for identifier in identifiers:
                if identifier in self._reference_names[kind]:
                    continue
                name = self._fetch_reference_name(kind, identifier, token)
                if name:
                    self._reference_names[kind][identifier] = name
        return self._reference_names

    def _fetch_reference_name(self, kind: str, identifier: str, token: str) -> str:
        try:
            response = self._request(
                f'/v2api/{self.connection.branch_id}/{kind}/index',
                {'id': int(identifier) if identifier.isdigit() else identifier, 'page': 0}, token,
            )
            records = self._records(response)
            if not records:
                return ''
            record = next((item for item in records if str(item.get('id')) == identifier), records[0])
            return str(record.get('name') or record.get('full_name') or record.get('title') or '').strip()
        except Exception:
            # Some school roles expose lessons but restrict one of the
            # reference dictionaries. The timetable remains usable.
            return ''

    @staticmethod
    def _ids(value: Any) -> tuple[str, ...]:
        raw = value if isinstance(value, (list, tuple)) else [value]
        return tuple(str(item).strip() for item in raw if item is not None and str(item).strip())

    @staticmethod
    def _names(value: Any) -> tuple[str, ...]:
        """Accept both usual AlfaCRM arrays and custom tenant payloads."""
        if value is None:
            return ()
        raw = value if isinstance(value, (list, tuple)) else [value]
        names: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                item = item.get('name') or item.get('full_name') or item.get('title') or ''
            name = str(item).strip()
            if name and name not in names:
                names.append(name)
        return tuple(names)

    @staticmethod
    def _in_timezone(value: datetime, timezone: ZoneInfo) -> datetime:
        return value.astimezone(timezone) if value.tzinfo else value.replace(tzinfo=timezone)

    @staticmethod
    def _parse_datetime(date_value: str, time_value: str, timezone: ZoneInfo) -> datetime:
        # Some AlfaCRM installations return ``time_from`` as a plain
        # ``HH:MM:SS`` while others return the full database timestamp
        # ``YYYY-MM-DD HH:MM:SS``. Prefer the latter when present: it avoids
        # accidentally combining a date twice (as in the reported error).
        timestamp = time_value.strip().replace('Z', '+00:00')
        if 'T' in timestamp or (len(timestamp) >= 10 and timestamp[:4].isdigit()
                              and timestamp[4:5] == '-'):
            try:
                parsed = datetime.fromisoformat(timestamp.replace(' ', 'T', 1))
                return parsed.astimezone(timezone) if parsed.tzinfo else parsed.replace(tzinfo=timezone)
            except ValueError:
                pass
        date_value = date_value.split('T', maxsplit=1)[0].split(' ', maxsplit=1)[0].strip()
        for pattern in ('%Y-%m-%d', '%d.%m.%Y'):
            try:
                return datetime.strptime(
                    f'{date_value} {time_value[:8]}', f'{pattern} %H:%M:%S',
                ).replace(tzinfo=timezone)
            except ValueError:
                try:
                    return datetime.strptime(
                        f'{date_value} {time_value[:5]}', f'{pattern} %H:%M',
                    ).replace(tzinfo=timezone)
                except ValueError:
                    continue
        raise AlfaCRMError(f'Cannot parse AlfaCRM lesson time: {date_value} {time_value}')
