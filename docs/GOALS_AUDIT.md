# Проверка целей 1–20

Дата проверки: 2026-09-01.

Команды проверки:

```bash
python3 -m compileall -q .
python3 -m pytest -q
```

Результат: **178 passed in 2.00s**, без warnings и failures.

Внешние API (Google Calendar, Telegram) в автоматических тестах проверяются через детерминированные adapter boundaries и mocks; live OAuth/сетевая доставка не запускаются без пользовательских credentials.

| № | Статус | Реализация | Проверка и invariant |
|---:|:---:|---|---|
| 1 | ✅ | `services/attendance_service.py`, `services/preparation/preparation_integration_service.py` | `tests/test_preparation_integration.py`: подготовка только для `CONFIRMED/MOVED`; перенос обновляет существующий block/task; отмена не создаёт новый block и безопасно снимает собственную проекцию. |
| 2 | ✅ | `services/university_service.py`, `services/attendance_service.py` | `tests/test_schedule_change_handling.py`: PlanningItem получает `course`, `session_type`, Unicode-safe `stable_id`, `state`, `is_moved`, old/new timestamps и историю переноса. |
| 3 | ✅ | `services/calendar/personal_event_sync_service.py`, `adapters/google_calendar_adapter.py` | `tests/test_calendar_personal_sync.py`: state является авторитетным gate; покрыты CREATE/UPDATE/DELETE/NOOP/CONFLICT, включая `MOVED + MEDIUM`, timezone-aware даты и защиту чужого события. |
| 4 | ✅ | `services/calendar/personal_event_sync_service.py`, `services/calendar/calendar_sync_service.py`, `services/calendar/weekly_plan_projection_service.py` | `tests/test_calendar_personal_sync.py`, `tests/test_calendar_sync_idempotency.py`, `tests/test_weekly_plan_projection.py`: versioned SHA-256 markers, повторный sync → NOOP, изменения → in-place UPDATE, stale UID ремонтируется без дубля. |
| 5 | ✅ | `UniversityEventStatus/sequence` в `models.py`; reconciliation в `services/attendance_service.py`; `services/sync_coordinator.py`; resilient parser в `scripts/parse_ics.py` | `tests/test_schedule_change_handling.py`, `tests/test_parse_ics.py`: move, time/location change, replacement, explicit cancellation, disappearance grace, reappearance, regenerated UID и cancellation tombstone. |
| 6 | ✅ | Unit + integration + deterministic E2E suite в `tests/` | Полный `pytest -q`: 178/178; attendance, reconciliation, preparation, calendar sync и MVP E2E проходят совместно. |
| 7 | ✅ | `WeeklyCapacityModel` в `models.py`; `TutoringService.apply_capacity`; capacity derivation в `services/mvp_pipeline.py` | `tests/test_models.py`, `tests/test_tutoring_service.py`, `tests/test_weekly_plan_pipeline.py`: sleep/fixed/university/teaching/travel/recovery/buffer вычитаются без отрицательных значений и двойного учёта. |
| 8 | ✅ | `WeeklyCapacityConstraint` в `planning_engine.py`; independent validation в `services/weekly_plan_service.py` | `tests/test_planning_engine.py`, `tests/test_weekly_plan_pipeline.py`: flexible work сверх capacity получает hard rejection; fixed commitments не вычитаются второй раз. |
| 9 | ✅ | Immutable `FixedCommitment` + `CommitmentType` в `services/weekly_plan_service.py`; tutoring fixed blocks в `services/tutoring_service.py` | `tests/test_weekly_plan_pipeline.py`, `tests/test_tutoring_service.py`: exact, non-flexible sleep/university/tutoring/travel/other blocks. |
| 10 | ✅ | `PlanningEngine.generate_candidate_schedules`, стратегии и `num_candidates`; `WeeklyPlanPipeline.generate` | `tests/test_weekly_plan_pipeline.py`: создаются и сохраняются несколько кандидатов, а не только первый допустимый. |
| 11 | ✅ | Hard constraints и soft scoring functions в `planning_engine.py`; candidate scoring/selection в `services/weekly_plan_service.py` | `tests/test_weekly_plan_pipeline.py`: кандидаты валидируются, получают score с penalty за unscheduled work, выбирается максимум среди valid. |
| 12 | ✅ | `WeeklyPlanStatus`, `WeeklyPlan`, `WeeklyPlanPipeline` | `tests/test_weekly_plan_pipeline.py`: отдельные GENERATE → VALIDATE → SCORE → SELECT → APPROVE → COMMIT; commit без approval запрещён; VALIDATE повторно проверяет sleep/travel/deep-work/capacity. |
| 13 | ✅ | `PreparationPolicy`, `PreparationIntegrationService.build_preparation_requirement` | `tests/test_preparation_integration.py`: requirement только для посещаемого события; учитываются session type, course, difficulty, priority, materials, prerequisites и deadline. |
| 14 | ✅ | `create_planning_item_from_preparation_requirement`; Sleep/Fixed/Travel/PreparationBeforeEvent/MaxContinuousWork constraints | `tests/test_preparation_integration.py`, `tests/test_planning_engine.py`, `tests/test_weekly_plan_pipeline.py`: flexible preparation заканчивается до занятия/travel buffer; sleep overlap и deep work >180 минут отвергаются. |
| 15 | ✅ | `TutoringSession`, `TutoringMode`, `TutoringService` | `tests/test_tutoring_service.py`: 45/90 минут, online/offline, before/after travel, materials, homework review и variable weekly preparation. |
| 16 | ✅ | `RegisteredScheduleSource`, daily/weekly cadence, aggregate `sync_due_sources` в `services/sync_coordinator.py` | `tests/test_sync_coordinator.py`: все зарегистрированные источники запускаются по cadence; identical snapshots не replanning; несколько изменений дают один aggregate replan. |
| 17 | ✅ | `WeeklyPlanCalendarProjectionService` | `tests/test_weekly_plan_projection.py`: university, tutoring, preparation, projects, reading, sleep, travel, recovery, buffer; ownership markers и CREATE/UPDATE/DELETE/NOOP/CONFLICT. |
| 18 | ✅ | Marker-scoped atomic projection в `services/obsidian_projection_service.py` | `tests/test_obsidian_projection.py`, `tests/test_mvp_pipeline_e2e.py`: Course → Event → Task → PreparationBlock; повторный запуск idempotent; пользовательский текст сохраняется byte-for-byte вне marker section. |
| 19 | ✅ | `ProposedCommand`, `ProposalStatus`, `ProposedCommandService` | `tests/test_proposed_command.py`: AI только предлагает; deterministic validation и explicit approval обязательны; invalid/unapproved proposal не достигает Application Layer. |
| 20 | ✅ | `PersonalOSMvpPipeline`, immutable `ExecutionRecord` + feedback supersession | `tests/test_mvp_pipeline_e2e.py`: university source/8И41 → attendance rule → reconciliation → personal event → preparation/tasks → capacity/scheduler/WeeklyPlan → Google/Obsidian/Telegram → execution record → immutable feedback. |

## Ключевые гарантии

- Ни один destructive calendar action не использует source UID вместо Google event ID.
- Чужие Google Calendar events без ownership marker никогда не обновляются и не удаляются.
- Исчезновение из university snapshot не равно немедленной отмене: сначала `POSSIBLY_CANCELLED`, затем `CANCELLED` после grace; reappearance восстанавливает состояние.
- Replacement не принимается автоматически: исходная связь сохраняется, событие переходит в `NEEDS_REVIEW`.
- Weekly plan не коммитится до explicit approval и не проходит VALIDATE при нарушении hard constraints.
- Obsidian projection владеет только содержимым между собственными markers.
