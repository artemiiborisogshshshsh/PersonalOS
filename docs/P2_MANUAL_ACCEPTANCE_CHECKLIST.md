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
