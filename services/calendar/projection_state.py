"""Small per-user checkpoint for Calendar projections; no event contents or secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from services.sync_retry import transient_error


class CalendarProjectionState:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.rows: dict[str, dict[str, str]] = {}
        if path is not None and path.exists():
            payload = json.loads(path.read_text(encoding='utf-8'))
            if payload.get('version') != 1:
                raise ValueError('Unsupported Calendar projection state version')
            self.rows = payload.get('rows', {})

    def get(self, key: str) -> dict[str, str]:
        return self.rows.get(key, {})

    def put(self, key: str, **changes: str | None) -> None:
        row = dict(self.rows.get(key, {}))
        for field, value in changes.items():
            if value is None:
                row.pop(field, None)
            else:
                row[field] = value
        self.rows[key] = row
        self._save()

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8',
                                   dir=self.path.parent, delete=False,
                                   prefix=f'.{self.path.name}.', suffix='.tmp') as output:
                json.dump({'version': 1, 'rows': self.rows}, output,
                          ensure_ascii=False, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
                temporary = Path(output.name)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


class CalendarProjectionError(RuntimeError):
    """Safe provider failure with only a numeric HTTP status for diagnostics."""

    def __init__(self, message: str, error: Exception | None = None):
        super().__init__(message)
        status = getattr(getattr(error, 'resp', None), 'status', None)
        if status is None:
            status = getattr(getattr(error, 'response', None), 'status_code', None)
        if isinstance(status, int) or (isinstance(status, str) and status.isdigit()):
            self.status = int(status)


def delete_owned_verified(adapter, calendar_id: str, event_id: str,
                          owns_event) -> bool:
    """Verify ownership and ambiguous DELETE outcome before a bounded retry."""
    reader = getattr(adapter, 'get_event_by_id', None)
    if not callable(reader):
        raise CalendarProjectionError('Calendar: проверка удаления недоступна.')
    for attempt in range(3):
        try:
            remote = reader(calendar_id, event_id, strict=True)
        except Exception:
            raise CalendarProjectionError('Calendar: чтение не подтверждено.') from None
        if remote is None:
            return True
        if not isinstance(remote, dict) or not owns_event(remote):
            raise CalendarProjectionError('Calendar: владелец события не подтверждён.')
        try:
            result = adapter._delete_event(calendar_id, event_id, strict=True)
            if result:
                return True
            error = None
        except TypeError as exc:
            if 'strict' not in str(exc):
                raise CalendarProjectionError('Calendar: удаление не подтверждено.') from None
            try:
                result = adapter._delete_event(calendar_id, event_id)
                if result:
                    return True
                error = None
            except Exception as nested:
                error = nested
        except Exception as exc:
            error = exc
        try:
            if reader(calendar_id, event_id, strict=True) is None:
                return True
        except Exception:
            raise CalendarProjectionError('Calendar: чтение не подтверждено.') from None
        if error is None or not transient_error(error) or attempt == 2:
            raise CalendarProjectionError('Calendar: удаление не подтверждено.', error) from None
    raise CalendarProjectionError('Calendar: удаление не подтверждено.')
