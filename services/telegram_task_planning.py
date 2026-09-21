"""Telegram boundary for promoting confirmed natural-language tasks."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Callable
from uuid import uuid4

from models import Task, TaskPriority, TaskStatus
from services.natural_text_application import PerUserNaturalCommandExecutor
from services.user_project_store import UserProjectStore


class TelegramTaskPlanningFlow:
    """Offer an explicit estimate before creating a local scheduling task."""

    VERSION = 1
    _SOURCE_ID = re.compile(r"task-[0-9a-f]{20}\Z")
    _PROPOSAL_ID = re.compile(r"[0-9a-f]{32}\Z")
    _PAGE_SIZE = 10
    _QUICK_MINUTES = (15, 30, 60, 90)
    _SOURCE_FIELDS = frozenset({"id", "title", "description", "status", "priority"})

    def __init__(self, owner_chat_id, state_directory, *, now_provider: Callable[[], datetime] | None = None):
        self.owner_chat_id = str(owner_chat_id)
        self.state_directory = Path(state_directory)
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        # Deliberately do no IO here: all authorization happens before state access.

    def handle_text(self, chat_id, text: str) -> dict | None:
        if str(chat_id) != self.owner_chat_id:
            return None
        if not isinstance(text, str):
            return self._invalid()
        parts = text.strip().split()
        if not parts:
            return None
        command = parts[0].split("@", 1)[0]
        if command == "/tasks":
            if len(parts) > 2 or (len(parts) == 2 and not parts[1].isdigit()):
                return self._invalid()
            page = int(parts[1]) if len(parts) == 2 else 1
            if page < 1:
                return self._invalid()
            return self._tasks(page)
        if command == "/task_estimate":
            if len(parts) != 3:
                return self._invalid()
            return self._propose(parts[1], parts[2])
        return None

    def handle_callback(self, chat_id, data: str) -> dict | None:
        if str(chat_id) != self.owner_chat_id:
            return None
        if not isinstance(data, str):
            return self._invalid()
        parts = data.split(":")
        if len(parts) < 3 or parts[0] != "tp":
            return self._invalid()
        action = parts[1]
        if action == "page" and len(parts) == 3 and parts[2].isdigit():
            page = int(parts[2])
            return self._tasks(page) if page >= 1 else self._invalid()
        if action == "pick" and len(parts) == 3:
            return self._pick(parts[2])
        if action == "estimate" and len(parts) == 4:
            return self._propose(parts[2], parts[3])
        if action in {"confirm", "reject"} and len(parts) == 3:
            return self._resolve(action, parts[2])
        return self._invalid()

    def _tasks(self, page: int) -> dict:
        try:
            tasks = self._eligible_tasks()
        except ValueError:
            return self._error()
        total_pages = max(1, (len(tasks) + self._PAGE_SIZE - 1) // self._PAGE_SIZE)
        if page > total_pages:
            return {"text": "Такой страницы задач нет.", "buttons": []}
        selected = tasks[(page - 1) * self._PAGE_SIZE: page * self._PAGE_SIZE]
        if not selected:
            return {"text": "Нет задач из подтверждённого ввода, ожидающих оценки.", "buttons": []}
        buttons = [[{"text": self._button_title(item["title"]), "callback_data": f"tp:pick:{item['id']}"}]
                   for item in selected]
        navigation = []
        if page > 1:
            navigation.append({"text": "‹", "callback_data": f"tp:page:{page - 1}"})
        if page < total_pages:
            navigation.append({"text": "›", "callback_data": f"tp:page:{page + 1}"})
        if navigation:
            buttons.append(navigation)
        return {"text": f"Задачи для оценки, страница {page}/{total_pages}:", "buttons": buttons}

    def _pick(self, source_id: str) -> dict:
        try:
            source = self._find_eligible(source_id)
        except ValueError:
            return self._error()
        if source is None:
            return {"text": "Эта задача уже не доступна для оценки.", "buttons": []}
        buttons = [[
            {"text": f"{minutes} мин", "callback_data": f"tp:estimate:{source_id}:{minutes}"}
            for minutes in self._QUICK_MINUTES
        ]]
        return {
            "text": f"{source['title']}\nВыберите длительность или отправьте /task_estimate {source_id} <минуты>.",
            "buttons": buttons,
        }

    def _propose(self, source_id: str, minute_text: str) -> dict:
        minutes = self._minutes(minute_text)
        if minutes is None:
            return {"text": "Укажите целое число минут от 15 до 480 с шагом 15.", "buttons": []}
        try:
            source = self._find_eligible(source_id)
            if source is None:
                return {"text": "Эта задача уже не доступна для оценки.", "buttons": []}
            created_at = self._proposal_time()
            proposal = {
                "id": uuid4().hex, "source": source, "minutes": minutes,
                "created_at": created_at.isoformat(),
            }
            self._save_pending(proposal)
        except ValueError:
            return self._error()
        token = proposal["id"]
        return {
            "text": f"Добавить задачу «{source['title']}» с оценкой {minutes} мин?",
            "buttons": [[
                {"text": "Подтвердить", "callback_data": f"tp:confirm:{token}"},
                {"text": "Отклонить", "callback_data": f"tp:reject:{token}"},
            ]],
        }

    def _resolve(self, action: str, token: str) -> dict:
        if not self._PROPOSAL_ID.fullmatch(token):
            return self._invalid()
        try:
            proposal = self._load_pending()
        except ValueError:
            return self._error()
        if proposal is None or proposal["id"] != token:
            return {"text": "Это предложение уже не актуально.", "buttons": []}

        expected = self._task_from_proposal(proposal)
        try:
            _, existing_tasks = UserProjectStore(self.state_directory).load()
            existing = next((task for task in existing_tasks if task.id == expected.id), None)
        except ValueError:
            return self._error()
        if existing is not None:
            if existing != expected:
                return {"text": "Задача уже изменилась; предложение не применено.", "buttons": []}
            if action == "reject":
                return {"text": "Задача уже добавлена и остаётся без изменений.", "buttons": []}
            return self._clear_after_success(token, "Задача уже была добавлена.")

        if action == "reject":
            try:
                self._clear_pending()
            except ValueError:
                return self._retry(proposal, "Не удалось сохранить отклонение. Повторите действие.")
            return {"text": "Предложение отклонено. Задача не добавлена.", "buttons": []}

        try:
            current = self._find_eligible(proposal["source"]["id"])
        except ValueError:
            return self._error()
        if current is None or current != proposal["source"]:
            return {"text": "Исходная задача изменилась; предложение больше не актуально.", "buttons": []}
        try:
            UserProjectStore(self.state_directory).upsert_task(expected, confirmed=True)
        except (OSError, ValueError, TypeError):
            return self._error()
        return self._clear_after_success(token, "Задача добавлена в планирование.")

    def _clear_after_success(self, token: str, text: str) -> dict:
        try:
            self._clear_pending()
        except ValueError:
            return self._retry({"id": token}, "Задача добавлена. Повторите подтверждение, чтобы завершить сохранение.")
        return {"text": text, "buttons": []}

    def _retry(self, proposal: dict, text: str) -> dict:
        token = proposal["id"]
        return {"text": text, "buttons": [[
            {"text": "Подтвердить", "callback_data": f"tp:confirm:{token}"},
            {"text": "Отклонить", "callback_data": f"tp:reject:{token}"},
        ]]}

    def _eligible_tasks(self) -> list[dict]:
        source = self._source_tasks()
        _, promoted = UserProjectStore(self.state_directory).load()
        promoted_ids = {task.id for task in promoted}
        return [item for item in source if self._target_id(item["id"]) not in promoted_ids
                and item["status"] in {TaskStatus.TODO.value, TaskStatus.INBOX.value}]

    def _find_eligible(self, source_id: str) -> dict | None:
        if not self._SOURCE_ID.fullmatch(source_id):
            return None
        for item in self._eligible_tasks():
            if item["id"] == source_id:
                return item
        return None

    def _source_tasks(self) -> list[dict]:
        path = self.state_directory / "natural_commands.json"
        if path.is_symlink():
            raise ValueError
        try:
            state = PerUserNaturalCommandExecutor(path).snapshot()
        except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError from None
        if (not isinstance(state, dict) or set(state) != {"version", "tasks", "personal_events", "executed"}
                or type(state["version"]) is not int or state["version"] != 1
                or not isinstance(state["tasks"], list) or not isinstance(state["personal_events"], list)
                or not isinstance(state["executed"], dict)):
            raise ValueError
        tasks = [self._source_record(item) for item in state["tasks"]]
        if len({item["id"] for item in tasks}) != len(tasks):
            raise ValueError
        return tasks

    @classmethod
    def _source_record(cls, record: object) -> dict:
        if not isinstance(record, dict) or set(record) != cls._SOURCE_FIELDS:
            raise ValueError
        identifier, title, description = record["id"], record["title"], record["description"]
        if (not isinstance(identifier, str) or not cls._SOURCE_ID.fullmatch(identifier)
                or not isinstance(title, str) or not title.strip() or not isinstance(description, str)):
            raise ValueError
        try:
            status = TaskStatus(record["status"])
            priority = TaskPriority(record["priority"])
        except (TypeError, ValueError):
            raise ValueError from None
        return {"id": identifier, "title": title, "description": description,
                "status": status.value, "priority": priority.value}

    def _task_from_proposal(self, proposal: dict) -> Task:
        created_at = self._aware_datetime(proposal["created_at"])
        source = proposal["source"]
        return Task(
            id=self._target_id(source["id"]), title=source["title"], description=source["description"],
            project_id=None, status=TaskStatus.TODO, priority=TaskPriority(source["priority"]),
            created_at=created_at, updated_at=created_at, due_date=None,
            estimated_hours=proposal["minutes"] / 60, dependencies=[],
        )

    @staticmethod
    def _target_id(source_id: str) -> str:
        return f"natural-task:{source_id}"

    @staticmethod
    def _minutes(value: str) -> int | None:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
            return None
        if len(value) > 3:
            return None
        minutes = int(value)
        return minutes if 15 <= minutes <= 480 and minutes % 15 == 0 else None

    def _proposal_time(self) -> datetime:
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError
        return now

    @staticmethod
    def _aware_datetime(value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed

    @property
    def _pending_path(self) -> Path:
        return self.state_directory / "task_planning_proposal.json"

    def _load_pending(self) -> dict | None:
        path = self._pending_path
        if path.is_symlink():
            raise ValueError
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError from None
        if (not isinstance(payload, dict) or set(payload) != {"version", "proposal"}
                or type(payload["version"]) is not int or payload["version"] != self.VERSION):
            raise ValueError
        proposal = payload["proposal"]
        if proposal is None:
            return None
        if not isinstance(proposal, dict) or set(proposal) != {"id", "source", "minutes", "created_at"}:
            raise ValueError
        if not isinstance(proposal["id"], str) or not self._PROPOSAL_ID.fullmatch(proposal["id"]):
            raise ValueError
        source = self._source_record(proposal["source"])
        if source["status"] not in {TaskStatus.TODO.value, TaskStatus.INBOX.value}:
            raise ValueError
        if type(proposal["minutes"]) is not int or proposal["minutes"] not in range(15, 481, 15):
            raise ValueError
        self._aware_datetime(proposal["created_at"])
        return {"id": proposal["id"], "source": source, "minutes": proposal["minutes"],
                "created_at": proposal["created_at"]}

    def _save_pending(self, proposal: dict) -> None:
        self._write_pending({"version": self.VERSION, "proposal": proposal})

    def _clear_pending(self) -> None:
        self._write_pending({"version": self.VERSION, "proposal": None})

    def _write_pending(self, payload: dict) -> None:
        path = self._pending_path
        if path.is_symlink():
            raise ValueError
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                    prefix=f".{path.name}.", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                json.dump(payload, output, ensure_ascii=False, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        except OSError:
            raise ValueError from None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _button_title(title: str) -> str:
        return title[:48] + ("…" if len(title) > 48 else "")

    @staticmethod
    def _invalid() -> dict:
        return {"text": "Команда или кнопка не распознана.", "buttons": []}

    @staticmethod
    def _error() -> dict:
        return {"text": "Не удалось безопасно обработать состояние задач.", "buttons": []}
