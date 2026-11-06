# Operator checkpoint after an interrupted add-only sync

Ordinary preview keeps interrupted targets immutable. If time or source data
changed during an interruption, it must not silently rewrite that journal.
The operator tool creates a verified partial baseline, retaining the complete
interrupted operation inside `published_plan.recovery_history`. A subsequent
normal preview calculates the missing preparations and still needs its usual
confirmation, or an already-authorized automatic maintenance run.

This tool does not write to Google Calendar. It accepts only an unchanged
published baseline, exactly proven new writes, and unattempted absent
preparations. Attempted-but-absent writes, manual changes, mismatching UID/ID or
ETag, unknown update types, and changed document digests stop recovery.

Before applying, retain a consistent stopped-runtime backup and independently
review the exact expected document digest and counts. Use the private per-user
state and Google roots already configured for the bot. The CLI takes the same
instance lock as the poller. Without `--apply`, it only verifies and reports.

```sh
python3 scripts/checkpoint_partial_plan.py \
  --user-id "$CHECKPOINT_USER_ID" \
  --expected-digest "$CHECKPOINT_DOCUMENT_DIGEST" \
  --old-rows "$CHECKPOINT_OLD_ROWS" \
  --new-classes "$CHECKPOINT_NEW_CLASSES" \
  --new-preparations "$CHECKPOINT_NEW_PREPARATIONS" \
  --absent-preparations "$CHECKPOINT_ABSENT_PREPARATIONS"
```

Add `--apply` only for the reviewed case. Saving uses one private temporary file,
file fsync, atomic replacement, and directory fsync. Other user domains and the
class projection are not rewritten. Every already-written class checkpoint must
already match its verified event. Existing OAuth credentials may refresh normally.

For supervised Amvera startup, an operator may set the nonsecret environment
variable `PERSONAL_OS_OPERATOR_CHECKPOINT` to the following JSON object, or place
the same JSON at `/data/operator_partial_checkpoint.json`. The environment value
takes precedence. Exactly these keys are accepted:

```json
{
  "user_id": "user-000000000000000000000000",
  "expected_digest": "0000000000000000000000000000000000000000000000000000000000000000",
  "old_rows": 23,
  "new_classes": 11,
  "new_preparations": 1,
  "absent_preparations": 10
}
```

The values above are placeholders. Keep the real file on the isolated data
volume, outside git; it contains no OAuth or Telegram credentials. Symlinks,
extra fields, oversized files, and group/world-writable configuration are refused.
The optional startup wrapper runs the bounded repair before the poller. A missing
file leaves startup unchanged. Failure or timeout starts the ordinary bot with
its journals intact. A matching durable recovery receipt makes later starts a
no-op without contacting Google.

Afterward verify the recovery receipt, a fresh preview, normal Calendar sync,
learning revision status, and at least one actual maintenance run. A healthy
HTTP endpoint alone does not establish that Calendar sync succeeded.
