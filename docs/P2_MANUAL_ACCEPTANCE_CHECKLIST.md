# P2 manual acceptance checklist

Use only a disposable Telegram chat, synthetic schedule and dedicated test
Calendar. Do not paste tokens, OAuth files, Calendar IDs or event contents into
the test report.

## P2.1 feedback adaptation

1. Set preparation durations to `20 60 60`, create and confirm a synthetic
   practical preparation, and record `done 90 4` feedback.
2. Before `/feedback_apply`, verify `/planning_profile` still shows 60 minutes
   for practicals and the existing Calendar event has not moved or changed.
3. Restart the bot, run `/feedback_apply`, and verify the profile now shows 75
   minutes while the existing Calendar event remains unchanged. A newly built
   future practical draft should use 75 minutes.
4. Repeat with another synthetic block, run `/feedback_reject`, restart, and
   verify the profile and Calendar remain unchanged.
5. For a projected **draft**, submit partial feedback. Verify no Calendar write
   occurs before `/feedback_apply`; after applying, only the owned current
   draft may be replaced. Repeat `/feedback_apply` and confirm it is a no-op.
6. Submit partial feedback for a confirmed operation. Applying must keep the
   confirmed Calendar event unchanged and direct the user to a new preview.

Record only pass/fail, command name and a credential-free error category.

## P2.2 natural text proposal boundary

Run these checks after the multi-user dispatcher is connected to a disposable
Telegram sandbox transport.

1. Send a supported task phrase and verify that only a preview with confirm and
   reject controls appears. Confirm that no task/event exists yet.
2. Press reject and verify no state or Calendar change. Reusing the old button
   must return a stale-proposal response.
3. Create another proposal, restart the bot, and verify the same preview can be
   confirmed once after restart without resending the original text.
4. Press confirm once and verify exactly one internal per-user object is
   created and no Calendar write occurs. Press the same button again and verify
   no duplicate is created.
5. Send destructive or ambiguous text such as an unqualified delete/move.
   Verify it asks for clarification and creates no proposal.
6. Verify another Telegram chat cannot confirm or reject the proposal.

Do not enable voice during this checklist.

## P2.3 — confirmed personal events in weekly preview

Run only after a disposable multi-user sandbox composes `PerUserWeeklyPreview`
as the onboarding handler's `weekly_preview` callback, using the directory
supplied by `TelegramUserRouter`. Its base-plan provider must supply only that
user's internal planning inputs and a timezone-aware planning horizon. The
legacy polling composition does not enable this adapter automatically.

1. Set the user's planning timezone and use a synthetic base plan containing a
   flexible task. Propose a personal event inside its available window. Before
   confirmation, `/weekly_preview` must still show the original availability.
2. Reject that proposal: the next preview must remain unchanged. Create and
   confirm another event: the next preview must reserve its fixed interval and
   place the flexible task outside it, or report that no complete plan fits.
3. Repeat the preview and restart the sandbox process. With unchanged inputs,
   the selected times must remain the same and no event may be duplicated.
4. Repeat with a second disposable chat. Neither its preview nor its callbacks
   may expose or change the first user's event.
5. Check events spanning the horizon boundary and events wholly outside it.
   Only the intersecting interval should occupy this preview. Check the user's
   timezone, including older internal records whose timestamps lack an offset.
6. Create overlapping fixed commitments and verify an infeasible response,
   without moving a fixed event or presenting a partial plan as complete.
   Also try an interval off the scheduler's time grid, such as 10:10–11:10.
   It may produce no complete candidate: the response must mention the
   scheduling step, and the stored event must retain its exact times.
7. Verify that preview leaves operational event state and any existing approved
   plan unchanged. Confirm that the dedicated Calendar has no new/changed
   events after proposal, confirmation, preview or restart. Calendar projection
   approval is outside this slice.

These are pending external checks, not evidence of production readiness.
The later project slice supplies scoped project/task storage and preview inputs;
natural-text tasks still need explicit promotion and scheduling estimates.

## P2.3 — tutoring in the same owned preview

Use a disposable sandbox composition and synthetic sessions only. This slice
provides a trusted internal `UserTutoringStore(directory).upsert(session,
confirmed=True)` API; it does not add a Telegram mutation command or connect
an AlfaCRM importer. Bind `directory` from the authenticated router account.

1. Without explicit confirmation, attempt the internal upsert and verify it
   raises `PermissionError` and leaves state/preview unchanged. Then explicitly
   save an approved synthetic 45-minute online session. `/weekly_preview` must
   reserve exactly 45 minutes, using the shared 15-minute grid.
2. Repeat with an offline session, 15-minute travel before/after, material
   preparation and homework review. The same preview must include these
   together with a synthetic personal event and flexible base-plan work;
   preparation ends before the lesson and homework starts after it.
3. Repeat a confirmed upsert with the same ID, request preview twice and restart
   the sandbox. There must be one lesson and stable planned intervals. Confirm
   an update to that ID and verify the next preview reads the new local state.
4. Use a second disposable chat. Verify it cannot see the first chat's tutoring
   state; creation of the second handler must not copy the first user's data.
5. Check lessons/travel crossing both horizon edges. Only intersecting minutes
   should occupy this horizon. A prior lesson's homework may appear within its
   two-day window; future lessons' material preparation must not be pulled in.
   A lesson starting at the horizon boundary with required preparation must
   report no complete draft, rather than silently discard that work.
6. Create a conflict with a personal event and an interval off the 15-minute
   grid. Verify honest no-complete-candidate output and unchanged source times.
7. Back up and restore this synthetic user's state into the same disposable
   identity. Verify tutoring preview survives; restoring into another identity
   must be rejected. Do not overwrite real state for this check.
8. Verify no Calendar changes after storage, repeated previews, restart or
   restore, and no external importer activity. The preview must not approve or
   commit an existing plan or record preparation/homework as completed.

These checks remain pending. Project and tutoring user-facing mutation and
completion flows, and live runtime composition are not declared complete.

## P2.3 — owned projects/tasks with personal events and tutoring

Use only disposable users and synthetic records. This slice exposes trusted
internal `UserProjectStore(directory).upsert_project(...)` and
`upsert_task(...)`, each requiring `confirmed=True`; it does not provide a
Telegram creation or import command. Resolve the directory through the user
router. Global project/task repositories are not a per-user source.

1. Verify that default/false/string confirmations cannot write a project or
   task. Explicitly save an ACTIVE synthetic project and a TODO task with an
   aware creation timestamp and a positive duration estimate. It must appear
   in `/weekly_preview` after saving, without restarting the handler.
2. Add a personal event and a 45-minute tutoring session. Verify all three
   domains appear in the same 15-minute-grid preview without overlapping fixed
   commitments. The underlying records and Calendar must remain unchanged.
3. Add a prerequisite and a higher-priority dependent with a shared deadline.
   The prerequisite must fully finish first. Check a three-task chain and
   verify the operational deadlines are unchanged by derived planning bounds.
4. Confirm a prerequisite's DONE/COMPLETED status. The next preview should omit
   that task and allow its dependent. An INBOX/REVIEW/ARCHIVED or inactive-project
   prerequisite must not be silently treated as completed.
5. Reject a missing local project/dependency, a dependency cycle, an unestimated
   TODO task and invalid optional dates. Previously saved bytes must remain
   intact. No dependency may be resolved from another user's directory.
6. Put a project ON_HOLD or in PLANNING/COMPLETED/CANCELLED. Its tasks must leave
   the preview; standalone TODO/IN_PROGRESS work remains eligible. Check project
   start times and explicit task deadlines, including overdue tasks. Project
   target dates must not silently become task deadlines.
7. Create the same local IDs in a second disposable account with different
   titles. Neither user's preview may expose the other's titles or changes.
   Repeated previews and a restart must yield stable intervals for unchanged
   inputs, and preview must not mark tasks complete or approve/project a plan.
8. Back up and restore this disposable user's project/task state into the same
   identity. Verify references/dependencies and preview survive, while restore
   into another identity remains rejected. Confirm no Calendar changes.

These checks are pending controlled sandbox acceptance. Natural-text tasks now
have a separate explicit `/tasks` → duration → confirm promotion path into the
per-user store; verify it with disposable chats, restart and backup/restore.
The path remains local preview state and is not a migration of the legacy
global project database.

### P2.3 Telegram task promotion

1. Confirm a synthetic task through the existing natural-text preview, then use
   `/tasks`, choose 15/30/60/90 minutes, and verify no project state exists
   until the final confirmation.
2. Restart the handler before confirming; verify exactly one
   `natural-task:<source-id>` task appears in the owner's preview. Repeat the
   callback and verify the record is unchanged.
3. Use a second disposable chat to press the callback; it must receive no
   response and must not create state. Change the intake title before confirm;
   the proposal must become stale without overwriting any task.
4. Back up and restore the same disposable user with a pending proposal, then
   confirm it. Verify the proposal and resulting task survive; do not use live
   Calendar or Telegram transport.
