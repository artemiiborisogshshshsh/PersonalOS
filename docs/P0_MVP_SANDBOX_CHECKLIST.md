# P0 MVP — manual sandbox checklist

This checklist is for a disposable Telegram chat, a non-production Google
account/calendar and a public TPU schedule chosen by the tester. It is not a
deployment guide and must not be run against a user’s production calendar
until a controlled sandbox run succeeds.

## Before the run

1. Work from a clean copy of the application state directory. Keep a backup of
   that copy; do not point `PERSONAL_OS_DATA_DIR` at existing user data.
2. Set credentials only through the local environment or a protected secret
   store. Do not paste tokens, OAuth JSON, calendar IDs or a private schedule
   URL into this repository, a test fixture or Telegram chat.
3. Use a dedicated test Telegram bot/chat and a dedicated test Google account.
   Confirm its Calendar contains no personal data.
4. Start with `python3 -m pytest -q`. The suite must pass before any manual
   provider check.

## Controlled scenario

1. Configure a TPU group URL and run `/update_schedule`. Confirm the local
   snapshot contains source events but no preparation for unselected courses.
2. Complete attendance selection, including exactly one laboratory option.
   Run `/update_all`; confirm only `CONFIRMED`/`MOVED` personal events receive
   preparation requirements.
3. Use a prepared synthetic changed snapshot (never alter TPU) to exercise a
   UID-preserving move and an ambiguous replacement. Confirm `/schedule_review`
   presents only source-verified options. Selecting one must retain the
   `PersonalUniversityEvent` identity and create no duplicate.
4. Force an occupied test week. Confirm a normal free slot is tried first;
   if one exists before the lesson, the fallback event is visibly marked
   “Конфликт — перенести вручную”. If no full block can finish before its
   lesson, confirm no post-lesson preparation is created and the report says
   the weekly plan is infeasible.
5. Inspect Google Calendar: only app-owned test events may be inserted or
   updated. Run `/update_all` unchanged and confirm no duplicate/create/update
   occurs. Manually move and delete one owned preparation, then repeat; its
   move/deletion must be preserved.
6. Disconnect the test Calendar and make the TPU fetch fail once. Confirm the
   command reports an unverified stage, does not expose credentials and keeps
   the last verified source snapshot and existing projections.

## P1.3 projection and recovery checks

1. In the disposable Calendar, record the provider IDs of one app-owned draft
   and one unrelated user event. Repeat an unchanged projection: verify zero
   creates, updates and deletes; change one draft and verify an in-place
   update with the same provider ID. Inspect private ownership markers.
2. Manually move one owned draft and delete another, then restart the app and
   repeat the projection. Verify both choices remain durable in local
   operation state and neither event is recreated or moved back. Give an
   unrelated event the same visible title; verify it is untouched.
3. In a controlled fault injection, let an insert or update succeed remotely
   while the client receives a timeout. Verify the remote event before retry:
   there must be one owned event and one local operation record after restart.
4. Trigger a temporary 429/5xx failure and verify a bounded retry. Trigger
   401/403 and validation 4xx failures and verify no retry or replacement
   insert. Check bot replies and logs for absence of URLs, tokens and provider
   error bodies.
5. Roll back a run that created one draft and updated one earlier draft.
   Verify only the current run's still-owned create is deleted and only its
   still-matching update is restored. Repeat after a manual move or ownership
   marker removal; the changed event must remain untouched for review.
6. Repeat create/update/no-op and timeout checks for a selected TPU class and
   a read-only AlfaCRM work lesson. Restart between runs. Manually move and
   delete owned projections; verify the per-user checkpoint prevents their
   times or existence from being silently restored. A user event with the same
   UID or title must never be updated or deleted.
7. For verified source cancellation, check an unchanged owned event is
   removed, while a manually moved lesson remains for review. Force an
   ambiguous DELETE response and verify absence before any retry. For the
   separate weekly-plan projector, supply a disposable per-user checkpoint;
   omitted items must remain until an explicit source-aware cleanup.

## Pass criteria and teardown

- The logs, Telegram replies and JSON operation reports contain no secrets.
- No TPU source event, AlfaCRM record, or external personal Calendar event was
  changed.
- Source disappearance is `POSSIBLY_CANCELLED` before any cancellation;
  ambiguous replacement is never auto-applied.
- Restore the disposable state directory from the backup or delete only that
  explicitly created sandbox directory. Remove sandbox credentials from the
  protected secret store when testing ends.

Live provider behaviour remains an external validation result. Passing this
checklist does not authorize deployment or prove production readiness.
