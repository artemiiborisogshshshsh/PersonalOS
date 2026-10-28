# Local operator access maintenance

These commands prepare and manage free beta access in the shared local ledger.
They do not take money, create payment records, award referral bonuses, contact
Telegram or Google, or deploy a release. The payment provider remains unselected.

Use the real application's configured `access_billing.sqlite3`, outside deletable
per-user planning state. The ledger must already exist, have mode `0600`, and use
a path without symlinks. Run as the local account that owns the service data.
Select the opaque `user-…` account ID from the trusted local user registry; never
guess a Telegram chat ID or create an account to work around an unknown-user error.

## Review, then apply an extension

Replace the example path and account ID with the verified local values. The
commands below are examples; release preparation does not execute a live grant.

```sh
python scripts/access_admin.py --ledger /absolute/private/data/access_billing.sqlite3 status user-0123456789abcdef01234567

python scripts/access_admin.py --ledger /absolute/private/data/access_billing.sqlite3 extend user-0123456789abcdef01234567 --days 21 --request-id beta-extension-2026-10-27-001 --reason 'Free invited beta continuation'
```

The second command is a read-only preview. Check the opaque user ID, current
expiry, proposed `beta_through`, days, reason and request ID. Preview and status
do not migrate, create, or change the ledger. A missing ledger or unknown user is
an error. Extension length must be a whole number between 1 and 365 days.

When the reviewed operation is intended, append `--apply` to the same `extend`
command. Apply recomputes against the current committed expiry, so a later apply
or another intervening grant can produce a different proposed date; inspect the
returned audit record. The expiry advances from the later of now, trial end,
paid access end, and existing free beta end. It never resets the trial.

Keep the request ID stable when retrying an uncertain result. A retry with the
same user, days and reason returns the original grant with `duplicate: true`
without adding days. Reusing that request ID with different parameters fails.
Use a new request ID only for a genuinely separate extension.

Reasons are durable and included in that user's export. Record only a short
administrative reason. Never paste passwords, bot/OAuth tokens, signing secrets,
payment credentials, or another user's personal information into the reason or
request ID. CLI failure output intentionally omits supplied values and raw SQL.

## Verify the result and resume safely

Run `status` again. Free extension is reported separately as `beta_through` and
`beta_active`; `paid_through`, payment history, first-payment markers and bonus
balances remain unchanged. The user sees «Бесплатное продление беты до», and trial
ending warnings are suppressed while beta access is active.

An expired account still needs a fresh `/weekly_preview` and confirmation before
automatic updates resume. A local grant does not clear that existing barrier or
replay overdue reminders or saved pending operations. Saved plans and exports
remain available. This CLI does not message the user.

## Back up the whole ledger

Before operational changes, stop the bot and administrative writers, then take a
verified private beta-state backup with `scripts/beta_state_backup.py` following
[the backup and restore rehearsal runbook](BETA_STATE_BACKUP.md). This includes
the shared ledger and its extension audit. A per-user JSON export or planning-state backup cannot
restore billing safely: it omits shared referral relationships, payment
idempotency, and administrative extension audit. Retain and restore the full
shared SQLite ledger according to that runbook. Never repair access by
deleting the ledger, resetting trial dates, inserting synthetic payments, or
editing bonus balances.

The first writable application open safely migrates older ledgers by adding
`beta_through` and an extension audit table under a SQLite write lock. Repeated
and concurrent opens preserve existing trial, payment, referral and bonus state.
Read-only previews can inspect the previous schema before that migration.
