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

Run these checks only after a per-user deterministic executor has been wired.

1. Send a supported task phrase and verify that only a preview with confirm and
   reject controls appears. Confirm that no task/event exists yet.
2. Press reject and verify no state or Calendar change. Reusing the old button
   must return a stale-proposal response.
3. Create another proposal, restart the bot, and record the current behaviour.
   Production enablement requires an explicit decision on whether pending
   proposals should survive restart; the current local flow is in-memory.
4. Press confirm once and verify exactly one internal per-user object is
   created. Press the same button again and verify no duplicate is created.
5. Send destructive or ambiguous text such as an unqualified delete/move.
   Verify it asks for clarification and creates no proposal.
6. Verify another Telegram chat cannot confirm or reject the proposal.

Do not enable voice during this checklist.
