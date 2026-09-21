"""Durable, per-user confirmed tutoring sessions."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from services.tutoring_service import TutoringMode, TutoringSession


class UserTutoringStore:
    """Persist confirmed tutoring sessions in a trusted user state directory."""

    VERSION = 1
    _FIELDS = frozenset({
        "id", "student", "start", "duration_minutes", "mode", "location",
        "travel_before_minutes", "travel_after_minutes",
        "material_preparation_minutes", "homework_review_minutes",
        "weekly_variable_preparation_minutes",
    })

    def __init__(self, state_directory: Path):
        self.path = state_directory / "tutoring_sessions.json"

    def load(self) -> list[TutoringSession]:
        if self.path.is_symlink():
            raise ValueError("Invalid tutoring session state")
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"version", "sessions"}:
                raise ValueError
            if (type(payload["version"]) is not int or payload["version"] != self.VERSION
                    or not isinstance(payload["sessions"], list)):
                raise ValueError
            sessions = [self._from_record(record) for record in payload["sessions"]]
            if len({session.id for session in sessions}) != len(sessions):
                raise ValueError
            return sorted(sessions, key=lambda session: (session.start, session.id))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("Invalid tutoring session state") from None

    def upsert(self, session: TutoringSession, *, confirmed: bool = False) -> None:
        if confirmed is not True:
            raise PermissionError("Tutoring session must be confirmed")
        self._record(session)
        sessions = self.load()
        by_id = {existing.id: existing for existing in sessions}
        by_id[session.id] = session
        self._write(sorted(by_id.values(), key=lambda item: (item.start, item.id)))

    @classmethod
    def _from_record(cls, record: object) -> TutoringSession:
        if not isinstance(record, dict) or set(record) != cls._FIELDS:
            raise ValueError
        identifier = record["id"]
        student = record["student"]
        start_text = record["start"]
        location = record["location"]
        mode_value = record["mode"]
        minute_fields = (
            "duration_minutes", "travel_before_minutes", "travel_after_minutes",
            "material_preparation_minutes", "homework_review_minutes",
            "weekly_variable_preparation_minutes",
        )
        if (not isinstance(identifier, str) or not identifier.strip()
                or not isinstance(student, str) or not student.strip()
                or not isinstance(start_text, str)
                or not isinstance(location, str)
                or not isinstance(mode_value, str)
                or any(type(record[field]) is not int or record[field] < 0
                       for field in minute_fields)):
            raise ValueError
        start = datetime.fromisoformat(start_text)
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError
        return TutoringSession(
            id=identifier, student=student, start=start,
            duration_minutes=record["duration_minutes"], mode=TutoringMode(mode_value),
            location=location,
            travel_before_minutes=record["travel_before_minutes"],
            travel_after_minutes=record["travel_after_minutes"],
            material_preparation_minutes=record["material_preparation_minutes"],
            homework_review_minutes=record["homework_review_minutes"],
            weekly_variable_preparation_minutes=record["weekly_variable_preparation_minutes"],
        )

    @classmethod
    def _record(cls, session: TutoringSession) -> dict:
        if not isinstance(session, TutoringSession):
            raise ValueError("Invalid tutoring session")
        record = {
            "id": session.id,
            "student": session.student,
            "start": session.start.isoformat() if isinstance(session.start, datetime) else None,
            "duration_minutes": session.duration_minutes,
            "mode": session.mode.value if isinstance(session.mode, TutoringMode) else None,
            "location": session.location,
            "travel_before_minutes": session.travel_before_minutes,
            "travel_after_minutes": session.travel_after_minutes,
            "material_preparation_minutes": session.material_preparation_minutes,
            "homework_review_minutes": session.homework_review_minutes,
            "weekly_variable_preparation_minutes": session.weekly_variable_preparation_minutes,
        }
        try:
            cls._from_record(record)
        except (TypeError, ValueError):
            raise ValueError("Invalid tutoring session") from None
        return record

    def _write(self, sessions: list[TutoringSession]) -> None:
        if self.path.is_symlink():
            raise ValueError("Invalid tutoring session state")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False,
                prefix=f".{self.path.name}.", suffix=".tmp",
            ) as output:
                temporary = Path(output.name)
                json.dump(
                    {"version": self.VERSION,
                     "sessions": [self._record(session) for session in sessions]},
                    output, ensure_ascii=False, indent=2,
                )
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
