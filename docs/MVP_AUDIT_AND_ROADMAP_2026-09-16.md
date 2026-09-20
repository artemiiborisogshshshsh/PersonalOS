# Personal Academic OS: проверенный аудит и roadmap

Дата: 2026-09-16. Источник статусов — чтение кода и локальный прогон
`python3 -m pytest -q` (**541 passed, 2 subtests passed**). Тесты используют
synthetic fixtures/fake adapters; они не доказывают работу с live TPU, Google
Calendar, Telegram или AlfaCRM.

## Границы MVP

Первый продукт — B2C для студента ТПУ в Томске, Telegram-first. Официальное
расписание TPU — источник истины для `UniversityEvent`; операционное состояние
должно жить локально в приложении; Google Calendar — только читаемая занятость
и проекция. AI может сформировать предложение, но не должен записывать domain
state или принимать scheduling decision.

## Фактическая архитектура

| Область | Статус | Доказательства и ограничение |
|---|---|---|
| Domain | PARTIAL | Большая часть моделей находится в монолитном `models.py`; `PersonalUniversityEvent`, `PreparationRequirement`, `Task`, capacity и weekly-plan types есть. Границы не везде чистые: модель/legacy services смешаны с demo-логикой. |
| Application | PARTIAL | `services/application/`, `services/update_all_workflow.py`, draft workflows и `PersonalOSMvpPipeline` существуют, но production Telegram runtime использует отдельную композицию из `scripts/telegram_schedule_bot.py`, а не единый use case. |
| Infrastructure | PARTIAL | Адаптеры Google/Telegram/n8n, TPU ICS и read-only AlfaCRM есть. Реальные подключения не запускались; n8n и часть generic adapters содержат placeholder paths. |
| Persistence/migrations | PARTIAL | SQLite migrations v1–v4 в `persistence/database.py` и JSON state per directory есть. Нет единой persistent модели `User`, personal events, preparations, source snapshots и интеграционных credentials с ownership/FK. |
| TPU ingestion | PARTIAL | `services/tpu_schedule_source.py` валидирует TPU HTTPS group URL, находит временный ICS export в памяти; `university_schedule_source.py` атомарно сохраняет структурно валидный ICS. Нет подтверждённой семантической полноты/групповой проверки live snapshot или production retention policy. |
| Personal attendance | PARTIAL | Telegram onboarding, course/type rules и выбор lab slot есть (`services/telegram_attendance_onboarding.py`, `attendance_preferences.py`). Runtime реконструирует personal events из текущего feed, не хранит durable identity/history/reconciliation state. |
| Reconciliation | PARTIAL | Typed diff, grace disappearance and replacement review есть в `AttendanceRuleService`. Однако `evaluate_event` содержит stub hooks и demo fallback; production `/update_all` не использует `reconcile_schedule_snapshots`, поэтому переносы/исчезновения не проходят один durable pipeline. |
| Preparation | PARTIAL | Draft planners, manual-conflict fallback, shared study/work queue, feedback and integrity tests существуют. Feedback-driven replan/estimate changes are durable proposals and require explicit apply/reject. Legacy `PreparationIntegrationService` создаёт in-memory task and uses host-time `datetime.now`; это не устойчивое operation store. |
| Deterministic scheduler | PARTIAL | `planning_engine.py`, `WeeklyPlanPipeline`, hard constraints/capacity/candidates существуют. Явного machine-readable `INFEASIBLE` результата с deficit, conflicting constraints и alternatives нет (`rg` не находит `INFEASIBLE`), а Telegram runtime не использует weekly-plan pipeline как единый planner. |
| Google Calendar | PARTIAL | Ownership markers, in-place updates, manual move/delete preservation and integrity checks покрыты fake tests. Calendar всё ещё является частью runtime state via per-user JSON, поэтому не доказан complete internal source of truth. |
| Telegram | PARTIAL | Есть legacy polling bot и отдельный per-user onboarding handler, который проходит `/start → timezone → TPU preview → attendance/lab → sleep/travel → Calendar status → weekly preview`; routing chat→user покрыт tests. Production polling composition всё ещё legacy single-chat, а natural text/voice отсутствуют. |
| AI | PARTIAL | `ProposedCommand` boundary и deterministic interpreter tests есть; `intent_interpreter.py` содержит placeholders и не подключён к Telegram execution. Voice/transcription adapter отсутствует. |
| Tutoring/projects/personal | PARTIAL | Domain services/types существуют; unified runtime/capacity/calendar flow не подтверждён. Tutoring не является работающим Telegram MVP flow. |
| Obsidian | PARTIAL | Marker-scoped projection exists, но docs старого ownership противоречат product direction, а vault onboarding/conflict flow не проверен. Низкий приоритет. |
| Multi-user/auth | PARTIAL | Есть opaque `UserAccount`, chat binding, ownership checks, isolated user directories and router/dispatcher tests. Нет database-row ownership/migrations и public multi-user polling composition. |
| Runtime/deployment | PARTIAL | Runtime schedule/healthcheck, restricted backup/restore and a closed-beta runbook exist. Optional n8n has no public port or default credentials. There is still no tested public bot service, monitoring or deployment. |
| Security | PARTIAL | Recent sanitisation/HTTPS tests and restricted export exist. Secrets lifecycle, encryption/key management, OAuth ownership, backup policy and production threat review absent. |
| Analytics/referrals/subscriptions | PARTIAL | Privacy-minimised lifecycle counters are wired to the legacy runtime and the multi-user onboarding boundary. Referral/payment are allow-listed milestones only; there is no billing provider or Referral/Payment/Subscription domain yet. |

## Проверка заявленных Goals 1–4

| Goal | STATUS | Что подтверждено | Пробелы |
|---|---|---|---|
| 1. Personal attendance models | DONE | `models.py:453–666` defines `PersonalEventState`, `AttendanceConfidence`, `MatchType`, `PersonalUniversityEvent`, `PersonalAttendanceRule`, `AttendanceMatchResult`; covered by `tests/test_personal_attendance.py`. `tests/test_preparation_integration.py` checks downstream links. | This is a model-level DONE only. There is no persistent per-user aggregate for these objects. |
| 2. Rules and matching | PARTIAL | Stable-ID extraction, snapshot diff and tests for UID/stable/replacement paths exist in `services/attendance_service.py`, `tests/test_attendance_service.py`, `tests/test_schedule_change_handling.py`. | `_check_exact_uid_match`, `_check_stable_id_match`, `_check_fuzzy_match` are explicit stubs; fallback fabricates a personal ID. No durable match repository or confidence-driven user decision flow. |
| 3. Attendance state machine | PARTIAL | All named states and `apply_match_result` exist; tests exercise selected transitions. Snapshot processing has disappearance grace/reappearance code. | No notification use case. State transitions leave `POSSIBLY_CANCELLED`/`NEEDS_REVIEW` unchanged even after a high-confidence reappearance in the direct state machine; runtime bypasses persistent reconciliation. |
| 4. Attendance to preparation | PARTIAL | `PreparationIntegrationService` gates requirements to `CONFIRMED/MOVED`; tests cover confirmed/moved/cancelled and move update. `PersonalOSMvpPipeline` creates requirements for active events. | Legacy integration creates transient task objects and schedules directly; production runtime has a separate draft flow and does not persist the event→requirement→task chain or reconcile it via a single source of truth. |

## Historical Goals 5–20 from `GOALS_AUDIT.md`

Those Goal names are retained for traceability; their old green checkmarks are
not accepted as completion evidence.

| Goal | STATUS | Verified code/tests | Remaining reason |
|---|---|---|---|
| 5. Schedule reconciliation | PARTIAL | `AttendanceRuleService.detect_schedule_changes`, `tests/test_schedule_change_handling.py`. | Not wired to durable production `/update_all` state. |
| 6. E2E suite | PARTIAL | `tests/test_mvp_pipeline_e2e.py` passes. | Fake-only, and it exercises a parallel MVP pipeline rather than the Telegram runtime. |
| 7. Weekly capacity | PARTIAL | `WeeklyCapacityModel`, `tests/test_weekly_plan_pipeline.py`. | Runtime inputs are incomplete and capacity is not persisted per user. |
| 8. Capacity constraint | PARTIAL | `WeeklyCapacityConstraint` and planning tests. | No user-facing infeasible outcome/deficit explanation. |
| 9. Fixed commitments | PARTIAL | `FixedCommitment`, tutoring tests. | Not all real personal/work/travel inputs reach one runtime planner. |
| 10. Candidate generation | DONE | `PlanningEngine.generate_candidate_schedules`, weekly plan tests. | Component DONE; it does not prove the MVP flow. |
| 11. Validation/scoring | DONE | hard constraints/scoring and weekly plan tests. | Component DONE; rules still need product-level integration. |
| 12. Weekly plan state flow | DONE | `WeeklyPlanPipeline` approval/commit tests. | Internal component only; Telegram confirmation uses other workflows. |
| 13. Preparation policy | PARTIAL | policy and preparation integration tests. | No durable per-user requirement/task chain. |
| 14. Preparation scheduling constraints | PARTIAL | preparation/planning tests. | Legacy flow and runtime flow differ; no unified infeasibility contract. |
| 15. Tutoring domain | PARTIAL | `TutoringSession`, `TutoringService`, tests. | No Telegram-first lifecycle or unified runtime use. |
| 16. Multi-source sync | PARTIAL | `ScheduleSyncCoordinator`, sync tests. | Snapshot/checkpoint state is in memory, not durable per user. |
| 17. Weekly-plan Calendar projection | PARTIAL | projection service/tests. | Separate from actual draft runtime; no live proof. |
| 18. Obsidian projection | PARTIAL | `ObsidianProjectionService` and tests. | Optional adapter; vault setup/conflicts not production-tested. |
| 19. AI proposal boundary | PARTIAL | `ProposedCommand` and application tests. | Interpreter/Telegram confirmation path is incomplete. |
| 20. Full MVP pipeline | PARTIAL | `PersonalOSMvpPipeline` synthetic test passes. | It is not the actual Telegram `/update_all` production composition. |

## Documentation divergences

- `docs/GOALS_AUDIT.md` calls Goals 1–20 complete based on **178** tests. It is historical and overclaims current end-to-end readiness; it must not drive implementation.
- `docs/DATA_OWNERSHIP.md` says Google Calendar is source of truth for fixed commitments and says Obsidian holds canonical knowledge. Product direction requires application data as operational source of truth and Google Calendar as projection/availability input; Obsidian is optional, not operational.
- `DEPLOYMENT.md` describes generic systemd/LaunchAgent deployment and `.env` variables, but no verified production deployment or secure default compose configuration exists.

## P0 — MVP reliability (do now)

1. Make attendance explicit: unknown course/type/lab selection stays `EXPECTED`; preparation only follows saved `CONFIRMED/MOVED` attendance. Add regression coverage.
2. Create one durable per-user university snapshot/personal-event reconciliation use case and connect it to `/update_all`: complete snapshot validation → diff → high/medium/low confidence result → user-visible review, without silent cancellation or duplicate personal events.
3. Make source completeness explicit: a failed/partial/unverified TPU snapshot retains the last verified snapshot and stops destructive downstream changes.
4. Return a deterministic `INFEASIBLE` weekly-plan result with capacity deficit, violated hard constraints and non-destructive alternatives; make Telegram render it.
5. Prove the critical Telegram path with synthetic end-to-end tests: TPU URL/group → attendance/lab choice → reconcile → preparation → preview/confirm → Calendar projection.

## P1 — closed beta / first students

1. Introduce a minimal `User`/integration ownership store, chat-to-user binding, per-user source/profile/operation state and authorization tests. Do not build B2B tenancy.
2. Telegram onboarding: timezone → TPU URL/group → preview → attendance → sleep/travel → first plan; make help derive from available commands.
3. Harden Calendar projection/recovery against ambiguous provider writes with a durable local operation journal; validate in a sandbox before claiming live support.
4. Add basic product analytics as privacy-minimised counters: registered, source connected, attendance finished, first plan, active week, payment funnel stage.
5. Provide a testable bot runtime/health/back-up-and-restore runbook. Remove insecure compose defaults before any deployment.

### 2026-09-16 — P1.1 complete: minimal multi-user foundation

- `services/user_registry.py` adds an atomic registry for a stable opaque
  local user ID bound to one private Telegram chat, ownership checks and safe
  per-user paths. It stores neither schedule contents nor credentials.
- The current single-user bootstrap now migrates allow-listed state from the
  legacy raw-chat directory into the opaque user directory without copying
  secrets or databases. The migration includes planning profile, attendance,
  source snapshot, draft/work operations, work state, shared queue, update
  journal and onboarding state; it does not copy credentials, databases or
  source feeds.
- `TelegramUserRouter` and `TelegramMultiUserDispatcher` route text/callbacks
  from an authenticated chat to its own handler directory. An unknown chat may
  register only through `/start`; arbitrary commands/callbacks cannot create
  state or select another user. `MultiUserMaintenanceService` evaluates each
  profile in its own timezone. Integration references are opaque and
  provider-independent.

### 2026-09-16 — P1.2 complete locally: Telegram-first onboarding

- `services/telegram_onboarding_handler.py` provides the concrete per-user
  application flow used by `TelegramUserRouter`/`TelegramMultiUserDispatcher`:
  `/start` → explicit timezone → public TPU group URL and validated source
  preview → attendance including selected lab → explicit sleep and travel →
  Google Calendar **connection status** → non-writing first weekly preview.
  It does not use Calendar as source of truth or perform a provider write.
- State is per-user and atomic. It resumes after a handler/process restart;
  attendance callbacks reconstruct their lab mapping from the active source,
  and a repeated `/connect_tpu` replaces the same `tpu-primary` source instead
  of adding a duplicate. Invalid or structurally incomplete sources are not
  activated.
- `tests/test_telegram_onboarding_handler.py` covers the happy path, selected
  laboratory, invalid URL, incomplete preview, interruption/resume, repeat
  operation and two-chat isolation. `tests/test_telegram_multi_user_runtime.py`
  confirms unknown chats can enter only via `/start`.
- This is a locally verified application flow, not a claim that a real Telegram
  bot or Google OAuth connection has been run. Wiring the legacy polling
  transport to the multi-user dispatcher and a disposable-provider sandbox are
  operational work, not a reason to bypass the ownership boundary.

### 2026-09-20 — P1.4 complete locally: privacy-minimised counters

- `ProductAnalyticsStore` records only idempotent, allow-listed lifecycle
  milestones (registration, source, attendance, first plan, weekly activity,
  referral/payment stage). It cannot store schedule or message content.
- The multi-user router records registration only when an unknown chat enters
  through the authorised `/start` boundary. `TelegramOnboardingHandler` records
  source connection, attendance completion and first plan only after the
  corresponding application step succeeds. Repeated commands remain
  idempotent, and analytics storage failures do not block account creation or
  onboarding.
- The legacy polling composition retains the same counters for its existing
  single-chat flow. Payment/referral entries are only provider-independent
  funnel milestones; billing, subscriptions and rewards remain P2 domain work.
- The optional n8n compose service no longer has a public port binding or
  default `admin/changeme` credentials; protected environment values are now
  required before it can start.
- `UserStateBackupService` writes a manifest-hashed archive of only exported
  user operational state. Restore validates user ownership and integrity and
  refuses implicit overwrite; OAuth, `.env`, databases and source ICS files
  are outside its allow-list.
- Synthetic analytics and onboarding tests pass as part of the full local
  suite (**527 passed, 2 subtests passed**). No production Telegram, analytics
  export, payment or external provider was used.

### 2026-09-20 — P1.5 complete locally: runtime recovery operations

- `scripts/runtime_healthcheck.py` validates required Telegram configuration,
  the private-chat identifier, source URL policy and writable runtime storage
  without echoing credentials or rejected values.
- `scripts/user_state_backup.py` provides a reproducible operator command over
  `UserStateBackupService`. It backs up only allow-listed state with a hash
  manifest, restores only the same opaque user, refuses implicit overwrite and
  emits categorical output without state contents. Subprocess tests perform a
  synthetic backup/restore drill and confirm that a token file is excluded.
- `PRODUCT_RUNTIME.md` and the closed-beta runbook now contain concrete backup
  and disposable-restore commands. The Calendar retry description matches the
  P1.3 policy: verify first, at most three attempts for temporary failures, no
  retry for authorization or validation failures.
- This is an operationally testable local boundary, not a deployment claim.
  The full synthetic suite is **531 passed, 2 subtests passed**. No bot, live
  TPU, Calendar, OAuth, n8n or production backup was run.

### 2026-09-18 — P1.3 Calendar projection implemented locally; sandbox pending

- The preparation Calendar projector now records a pending write in the
  per-user `DraftOperationStore` before mutation. An ambiguous response is
  followed by a strict read of the owned event before any bounded retry;
  restart reuses the same operation and performs a no-op when the projection
  already matches. Temporary failures may retry up to three times; auth,
  validation and other permanent failures stop with a credential-safe message.
- Existing events require the app's private ownership marker (or a persisted
  legacy ID plus its exact block marker). A saved provider ID is reread and
  ownership checked before reuse. Create/update/no-op is differential, and a
  manual move or deletion is persisted as an override. Rollback checks remote
  ownership and placement before deleting events created by the current run;
  it restores a previous block only when this run actually updated it and the
  remote still matches that update. External events are never selected by UID
  alone for mutation.
- Draft confirmation and explicit cleanup now reread ownership before any
  mutation. Health repair rereads each candidate by provider ID and deletes
  only a still-owned preparation; a title, UID or legacy description alone is
  insufficient deletion evidence. Ambiguous DELETE is verified before retry.
- University event projection now has a per-user atomic Calendar checkpoint
  wired into the Telegram application composition and included in portable
  state export/migration. Work lesson projection uses its existing per-user
  state file for pending writes and manual overrides. Both verify an ambiguous
  insert/update before retry, reject unowned same-UID events, and preserve
  manual moves/deletions after restart. Confirmed source cancellation deletes
  only a still-owned, unmoved projection after an exact read; ambiguous delete
  is verified before retry. Work preparation cleanup remains with its owning
  draft operation rather than source-absence inference. AlfaCRM is read-only.
- The separate weekly-plan and legacy ICS projection components now accept
  `CalendarProjectionState` for durable checkpoints, use calendar-scoped reads,
  reject unowned events and preserve manual overrides. Omitted weekly items
  remain for explicit source-aware cleanup. These components are not part of
  the current Telegram polling composition; a future runtime consumer must
  supply a per-user state path before provider writes.
- The active Telegram MVP cleanup paths (`/system_delete`, retained preparation
  retirement and Calendar integrity repair) now reread by provider ID and
  require matching private block/operation ownership before delete. A manual
  move is checkpointed and retained. The older generic application service and
  `scripts/prep_blocks.py` remain legacy/demo compositions outside the Telegram
  MVP runtime; they must not be used for beta Calendar writes until migrated to
  the same per-user projection state.
- Synthetic recovery, ownership, rollback, retry and restart tests pass. The
  full local suite is **525 passed, 2 subtests passed**. This does not establish
  Google Calendar provider behaviour. The disposable sandbox checklist remains
  the P1.3 external gate; no real Calendar write was performed.

## P2 — after first paid users

1. Feedback/adaptive estimates with confirmation-only proposal application.
2. Natural text → `ProposedCommand` → preview/confirm; voice only after provider, cost and privacy boundary are chosen.
3. Projects, personal events and tutoring as inputs to the same scheduler/runtime.
4. Referral/payment-provider-independent domain: `ReferralCode`, `ReferralAttribution`, `ReferralConversion`, `ReferralReward`, `Subscription`, `Payment`, `DiscountCredit`; reward only after activation and first successful payment. No payment provider in this phase unless selected separately.
5. Subscription/referral Telegram surfaces and minimal analytics funnel.

### 2026-09-20 — P2.1 complete locally: confirmed feedback adaptation

- Preparation feedback records completion/carryover facts but no longer runs
  a replan automatically. Partial/skipped feedback creates a durable inert
  proposal; only `/feedback_apply` may replan an existing draft, while
  `/feedback_reject` clears the proposal without changing the plan.
- Completed feedback with a material actual/planned duration difference can
  propose a bounded future estimate for the matching lecture, practical or
  lab type. Applying it updates the per-user planning profile for future
  drafts; it never edits the current Calendar projection. Comments are not
  copied into the proposal.
- Proposal state survives a process restart. Confirmed operations remain
  unchanged unless the user takes an explicit follow-up action. Synthetic
  tests cover apply, reject, restart recovery, profile persistence boundary
  and the Telegram commands. The full suite is **535 passed, 2 subtests
  passed**.
- Live Telegram text behaviour and provider-visible Calendar invariance remain
  manual checks in `docs/P2_MANUAL_ACCEPTANCE_CHECKLIST.md`; no external
  service was run.

### 2026-09-20 — P2.2 complete locally: natural text proposals

- `TelegramNaturalTextProposalFlow` converts only interpreter-supported text
  into a validated `ProposedCommand` preview. It performs no mutation during
  interpretation, binds callbacks to the owning chat and executes a proposal
  once only after its exact confirm callback. Reject and stale callbacks are
  non-mutating.
- The polling bot can route plain text and `nl:*` callbacks through this
  boundary when a per-user flow is injected. Unknown/destructive language
  remains a clarification response; raw provider errors and the Telegram chat
  identifier are not included in proposal output or approval metadata.
- `PerUserNaturalCommandExecutor` atomically persists confirmed tasks and
  personal events under the router-owned user directory. Command IDs make a
  repeated execution idempotent. Personal events remain internal operational
  state; this flow performs no Calendar write and cannot create a TPU group
  event. `NaturalTextProposalStore` restores a validated pending proposal after
  restart without storing the original raw chat message.
- The multi-user onboarding handler accepts an injected per-user flow composed
  only from its authenticated `UserAccount` and state directory. Cross-chat
  callbacks cannot execute another user's proposal. Natural command/proposal
  files are included in the existing portable-state backup and migration
  allow-lists. The legacy public polling composition is still single-chat and
  does not enable this flow automatically.
- Voice remains deferred until provider, cost, consent and retention rules are
  selected.
- Synthetic interpreter, proposal persistence, restart, per-user isolation,
  idempotent execution, rejection and bot routing tests pass. Full suite:
  **541 passed, 2 subtests passed**. No external model, Telegram bot or
  Calendar write was used.

## P3 — future

- Optional Obsidian adapter and richer knowledge links.
- Other universities/ICS/API providers beyond hardened TPU abstraction.
- B2B tenancy/admin/web/mobile clients, billing provider, legal/security review and sales operations.

## Acceptance rule

No mock-only success is called production integration. The MVP is ready only when the P0 synthetic E2E path passes, a controlled live sandbox validates provider boundaries, and no source change can silently cancel, duplicate or overwrite user data.

## Implementation log

### 2026-09-16 — P0.1 complete

- Unconfigured attendance remains `EXPECTED`; saved positive choices promote
  attendance to `CONFIRMED`; preparation stays gated to `CONFIRMED/MOVED`.

### 2026-09-16 — P0.2 implemented locally; sandbox validation pending

- `UniversitySnapshotStore` now atomically persists per-user verified source
  snapshots and `PersonalUniversityEvent` state under the user data directory.
- Runtime `/update_all` reconciles a downloaded source snapshot before it
  projects classes.  UID regeneration/moves preserve local identity;
  ambiguous replacements become `NEEDS_REVIEW`; disappearance is retained
  through the existing grace logic.
- The narrower `/update_schedule` command now runs the same local
  reconciliation callback before reporting success, so it cannot leave a
  known stale personal projection after a successful source download.
- `/schedule_review` now renders only persisted, source-verified ambiguous
  replacement candidates. A user confirmation retains the personal event ID,
  changes it to `MOVED`, and defers Calendar projection to `/update_all`.
- Tests cover persistence, idempotency, delayed attendance confirmation, move,
  UID regeneration, location change, disappearance/reappearance, ambiguity,
  stale selection, rejected empty input and a snapshot outside the planning
  horizon. The runtime rejects a structurally valid snapshot with no event in
  the current/next planning window before it replaces verified state. An ICS
  alone still cannot distinguish a holiday from a truncated-but-well-formed
  response that happens to contain an in-horizon event; that is a sandbox
  monitoring concern, not an excuse for silent cancellation.

### 2026-09-16 — P0.3 implemented locally; sandbox validation pending

- `WeeklyPlanPipeline` now returns structured `INFEASIBLE` instead of selecting
  an incomplete candidate.  It includes capacity deficit, unplaced IDs, hard
  constraint facts and non-destructive alternatives; the MVP pipeline does not
  project a partial plan.
- The production Telegram workflow converts proven `NO_AVAILABLE_SLOT`
  preparation requirements into a visible `INFEASIBLE` report with a
  conservative missing-minute total, hard-constraint facts and safe
  alternatives. A visible manual-conflict proposal remains a separate
  conflicting state, never a free placement.
- If a full fallback block cannot finish before its lesson, neither the
  university nor work planner creates a post-lesson marker; the requirement is
  reported unplaced instead.

### 2026-09-16 — P0.4 implemented locally; sandbox validation pending

- The actual Telegram `/update_all` sequence is source refresh → durable TPU
  reconciliation → attendance/overrides → Calendar availability → shared
  study/work preparation calculation → differential projection → reread and
  integrity report. It is protected by the durable inter-process lock in
  `UpdateAllWorkflow`; a second run receives “Обновление уже выполняется”.
- The compact report has at most three groups plus “Разобрать”, “Подробности”
  and “Повторить проверку”; stale callbacks cannot initiate a new run.
- Existing draft/operation stores remain the checkpoint and rollback
  mechanism. The legacy projection stages drafts during `/update_all`; the
  existing explicit preparation confirm/rollback controls remain available.
  Production behaviour still requires the disposable sandbox check.

### 2026-09-16 — P0.5 implemented locally; sandbox validation pending

- `tests/test_mvp_acceptance.py` exercises a credential-free path from an
  explicit lab choice through durable move reconciliation, plan approval and
  an idempotent Calendar projection.
- `docs/P0_MVP_SANDBOX_CHECKLIST.md` defines a disposable-account manual
  verification of the provider boundaries, idempotence, manual overrides,
  source failure and secret-safe reports. It intentionally does not start a
  real bot or access a real account.

### P0 completion boundary

All P0 implementation and synthetic acceptance work is complete in the local
repository. The only remaining P0 gate is an authorised manual sandbox run
against disposable TPU/Telegram/Google Calendar accounts. It cannot be run
without external credentials and explicit permission, and must not be called
production readiness until then.
