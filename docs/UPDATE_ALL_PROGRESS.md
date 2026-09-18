# /update_all — implementation checkpoint

`/update_all` is registered and exercised through the real runtime with fake
providers. Restart the bot, then use `/update_all` or `/help`. Production API
behavior has not been verified against a live account in this implementation.

Implemented:

- CalendarIntegrityService now separates read_violations from apply_allowed_fixes.
  Inspection defaults to the current and next calendar weeks. Source absence
  needs explicit verified coverage; distant events are excluded from cleanup.
- preparation_integrity provides read-only expectation/projection validation,
  seven states, local calendar-week horizon, grouped summaries and paginated
  details. Runtime supplies source/rule-derived expectations and rereads Calendar
  to check preparations and source-class projections after publishing.
- Calendar GET/HEAD requests use bounded read retries with connection close,
  exponential backoff and jitter. Writes are never blindly retried there.
  AlfaCRM read queries use the same policy. TLS verification is unchanged.
- Work projection saves each successful lesson checkpoint and preserves absent
  lessons outside explicit verified source coverage. The runtime supplies the
  successfully fetched interval. Work planning uses calendar weeks, not now+14d.
- SharedPreparationWorkflow calculates and validates both halves before writes.
  Journal v2 stores run_id and exact candidates. Publishing uses these candidates
  and previous event IDs without rollback/recalculation; unchanged halves are
  not rewritten. Changed conditions during resume fail closed. Unfinished v1
  journals require inspection, not automatic destructive migration. Old omitted
  blocks are retained and protected, not deleted by absence from a candidate.
- DraftOperation explicitly persists retained_blocks and their Calendar IDs.
  Both workflows restore their ownership after restart. The shared preflight
  protects these intervals on subsequent runs; source absence alone never
  drops them from the local operation history.
- Explicit retirement of retained blocks is checkpointed per confirmed delete,
  retaining retired_blocks for history. Local and Calendar rollback skip retired
  sources. Runtime connects explicit per-event suppression/hide decisions and
  persisted verified AlfaCRM cancellations (rechecked against a fresh fetch). Completed
  sources and blocks outside the supplied horizon are protected.
- AlfaCRM cancellation evidence includes source snapshot and verified range and
  is checkpointed before deletion. A reappearing source clears the evidence.
  Feedback remains preserved. Work planner skips done preparation feedback;
  cancellation skips deleting completed work preparations. TPU cancellation
  authorization still requires verified reconciliation rather than raw absence.

Command and reports:

- Persisted update_all.json records run_id, stage and structured unresolved issues.
  New runs revalidate sources; per-event/shared journals handle pending writes.
- Manual update uses a background thread and edits one status message. Old
  commands, callbacks and background checks share an in-process user RLock.
- Summary shows at most three groups; details paginate five issues, review one
  issue at a time. Old report callbacks cannot start a new sync. Automatic checks
  use the same workflow and suppress unchanged/empty issue reports.
- Removed whole-action retry loops. Source readers own bounded retries; ambiguous
  write failures stop and preserve checkpoints instead of claiming full rollback.
- Planner records structured no-slot reasons. Manual events in the personal study
  calendar remain busy. Work-lesson projections are not preparation candidates.
- Transition preflight orders moves within a scope; dependency cycles and moves
  requiring another scope to be freed first stop without publishing preparations.

Remaining hardening before a product release:

1. Safe migration/recovery UI for old/stale shared journals; automatic cycle handling.
2. Full carryover/adaptive-duration verification and per-event no-op optimisation
   within a changed operation, beyond the covered unchanged-plan no-op.
3. Explicit TPU coverage/cancellation reconciliation instead of raw absence.
4. Rich resolution actions beyond review/retry. `UpdateAllWorkflow` has a
   cross-process advisory lock; a full per-user worker/queue is still needed
   for multi-user deployment.
5. Live read-only audit, broader provider-fault integration tests and externally
   edited projections. Do not claim production verification from mocked tests.

Never read production token/config contents or use live Calendar writes as tests.
