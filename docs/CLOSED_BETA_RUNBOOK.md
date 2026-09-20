# Closed beta runbook

This runbook is for a small invite-only beta. It does not authorize public
deployment, payments, or the use of production personal data for testing.

## Before inviting one student

1. Run `python3 -m pytest -q`; record the passing result.
2. Run `python3 scripts/runtime_healthcheck.py` with secrets supplied only by
   the protected host environment. The command must not print a token or URL.
3. Restore a disposable copy of one user's backup with
   `UserStateBackupService`; verify its manifest and that OAuth, `.env`,
   databases and ICS files are absent.
4. Complete the sandbox checklist in `P0_MVP_SANDBOX_CHECKLIST.md` using a
   dedicated Telegram chat and Calendar. Do not use a student's main calendar.
5. Confirm `docker-compose.yml` is not used with public n8n exposure or
   default credentials. n8n is optional and not part of the MVP critical path.

## Per-user beta acceptance

1. `/start` → timezone → `/connect_tpu <group URL>` → attendance/lab choice.
2. Set sleep/travel preferences, inspect the first preparation plan, then use
   the explicit Calendar preview/confirmation controls.
3. Verify a second `/update_all` makes no unnecessary Calendar writes.
4. Move and delete one owned preparation manually; repeat the update and
   verify that neither action is undone automatically.
5. Trigger a safe source change fixture: verify a move keeps identity,
   ambiguity reaches `/schedule_review`, and disappearance is not silently
   cancelled.

## Incident boundaries

- Pause writes and preserve the last verified snapshot when source, Calendar
  reads, authorization or operation integrity are uncertain.
- Do not inspect or paste secrets into tickets. Keep only safe diagnostic
  category, status and code location.
- Restore only the named user's verified archive into an empty/disposable
  state directory. Restore refuses an implicit overwrite by design.
- A live beta issue is not reproduced against another student's account.

## Metrics and support

Use only lifecycle funnel counters: registered, source connected, attendance
completed, first plan and weekly active. They intentionally exclude schedule
contents, chat messages, feedback comments and raw Telegram IDs. Record
support outcomes separately without copying personal schedule data.

Before reviewing aggregate counts, verify on a disposable synthetic user that
failed source checks and disconnected Calendar checks do not advance the
funnel, while repeated successful steps remain idempotent. Do not export the
event-level analytics file to support or reporting systems.

## Exit criteria

Do not call the beta production-ready until every invited student has a
verified backup/restore drill, source reconciliation has been observed safely,
and no unresolved duplicate, silent cancellation, or user-override regression
remains.
