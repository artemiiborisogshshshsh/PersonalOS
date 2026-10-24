# Release announcements

Release announcements are opt-in. With all five settings below unset, the bot
does no announcement file, network, or Telegram work. The announcement channel
is fixed to `@myaicalendar_updates`; configure a dedicated Telegram bot token
that has permission to publish there. The production bot token is never used as
a fallback.

Set these environment variables together:

- `PERSONAL_OS_RELEASE_MANIFEST`: absolute path to a JSON release manifest.
- `PERSONAL_OS_RELEASE_RECEIPT`: absolute path to the deployment success receipt.
- `PERSONAL_OS_ANNOUNCEMENT_DIR`: absolute durable directory for the send ledger.
- `PERSONAL_OS_ANNOUNCEMENT_HEALTH_URL`: public HTTPS URL ending exactly in
  `/healthz`, without query, fragment, or credentials.
- `TELEGRAM_ANNOUNCEMENT_BOT_TOKEN`: dedicated announcement bot token.

For example, deployment files and the send ledger can live under `/data`:

```text
PERSONAL_OS_RELEASE_MANIFEST=/data/releases/current.json
PERSONAL_OS_RELEASE_RECEIPT=/data/releases/current-receipt.json
PERSONAL_OS_ANNOUNCEMENT_DIR=/data/release-announcements
PERSONAL_OS_ANNOUNCEMENT_HEALTH_URL=https://calendar.example.com/healthz
TELEGRAM_ANNOUNCEMENT_BOT_TOKEN=<dedicated token supplied by the secret manager>
```

Provision `/data/releases` and `/data/release-announcements` as durable storage
before enabling announcements. Supply the token through the platform's secret
manager, not a checked-in file.

Manifest format:

```json
{"id":"update-2","revision":"40-lowercase-hex-characters","text":"Release notes","status":"released"}
```

Use `status: "draft"` until the release is ready. Before the external
deployment and smoke checks succeed, do not create a success receipt. The
operator or deployment success hook must then write a JSON receipt such as:

```json
{"status":"success","release_id":"update-2","revision":"40-lowercase-hex-characters","artifact_fingerprint":"64-lowercase-hex-characters"}
```

Create the receipt atomically: write complete JSON to a temporary file in the
same directory, flush it, then rename it to the configured receipt path. Do so
only after deployment and smoke checks succeed; never expose a partial success
receipt to the bot.

With reused `current.json` and `current-receipt.json` paths, publishing a new
manifest can leave the prior release's success receipt in place during
deployment. That well-formed receipt is treated as pending because its release
ID or revision differs; polling continues until the deployment hook atomically
replaces it with the matching success receipt. A mismatched receipt does not
authorize an announcement.

Get the artifact fingerprint from `python scripts/announce_release.py
--fingerprint` for the exact deployed tree. The receipt writer is intentionally
external: this repository does not deploy the service, assert which code is
live, or manufacture successful receipts. The bot also reads the live
`/healthz` response and requires its release fingerprint to match the local
tree before sending.

While polling, the bot checks only after Telegram returns a valid successful
`getUpdates` response. A missing receipt remains pending and can be noticed on
a later poll. A successful send, definitive Telegram rejection, ambiguous
outcome, or blocked/error condition ends checks for that process. The durable
ledger survives restarts; uncertain sends are never retried automatically.
An explicit operator invocation may retry only a definitive Telegram 4xx
rejection after correcting the cause.

Back up `/data/release-announcements` as durable operational state. Do not
delete the ledger when restarting the bot, rolling back a deployment, or
cleaning up release files; retain it through rollbacks so a release cannot be
announced twice. If an outcome is uncertain, inspect the actual announcement
channel first and reconcile with the ledger. Never blindly delete the ledger
or rename the release ID to force another send.

Run `python scripts/announce_release.py` for one explicit validation attempt.
It prints only a fixed status and, where available, a numeric message ID or
Telegram error code. It returns success only for `sent` or `already_sent`.
Drafts, missing receipts, mismatches, invalid settings, failures, and uncertain
delivery return a nonzero exit status.
