# Release announcements

Release announcements are opt-in. With announcement settings unset, the bot
does no announcement file, network, or Telegram work, even when its normal
`TELEGRAM_BOT_TOKEN` is configured. The announcement channel is fixed to
`@myaicalendar_updates`. To publish as the existing bot clients chat with, set
`PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT=1` explicitly. The existing bot token
must have permission to publish in the channel. A dedicated announcement token
can be used instead by setting `TELEGRAM_ANNOUNCEMENT_BOT_TOKEN` and leaving the
opt-in flag unset; configuring both choices is rejected.

## Deployment source

The confirmed deployment source is Amvera only; there is no GitHub deploy stage.
GitHub Actions runs CI, so a passing test run must never create a success receipt.
The receipt must be produced by an Amvera-side release operator or orchestrator
only after the actual deployment, smoke checks, and expected artifact match have
been verified. That external producer is not configured, and live activation is
not enabled. This guide does not assume that Amvera provides a native success
callback. Until a matching success receipt exists, the bot remains pending; an
ordinary process startup is not treated as release success.

## Current partial activation

The announcement infrastructure candidate is prepared separately from Update 1
and has passed the full test suite (1,160 tests and 2 subtests). It has not been
deployed. The live Amvera app still reports runtime fingerprint
`7879618b049d99a8`, which matches production base `cf78c3a`.

These nonsecret Run-stage values have been saved in Amvera:

```text
PERSONAL_OS_RELEASE_MANIFEST=/data/releases/current.json
PERSONAL_OS_RELEASE_RECEIPT=/data/releases/current-receipt.json
PERSONAL_OS_ANNOUNCEMENT_DIR=/data/release-announcements
PERSONAL_OS_ANNOUNCEMENT_HEALTH_URL=https://personalos-borisov1artemii1.mia0.amvera.tech/healthz
PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT=1
```

The app has not been restarted and no code has been deployed. No success
receipt exists, and the existing bot's permission to post to
`@myaicalendar_updates` has not been confirmed. Therefore there has been no
announcement attempt or post; these saved settings alone do not activate a
release. Keep them pending until the infrastructure candidate is deployed, its
health fingerprint is verified, and the bot's channel permission is confirmed.

Set these environment variables together:

- `PERSONAL_OS_RELEASE_MANIFEST`: absolute path to a JSON release manifest.
- `PERSONAL_OS_RELEASE_RECEIPT`: absolute path to the deployment success receipt.
- `PERSONAL_OS_ANNOUNCEMENT_DIR`: absolute durable directory for the send ledger.
- `PERSONAL_OS_ANNOUNCEMENT_HEALTH_URL`: public HTTPS URL ending exactly in
  `/healthz`, without query, fragment, or credentials.
- `PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT=1`: explicit opt-in to use the
  existing `TELEGRAM_BOT_TOKEN` for announcements.
- Alternatively, `TELEGRAM_ANNOUNCEMENT_BOT_TOKEN`: a dedicated publisher token.
  Do not set this together with `PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT=1`.

For example, deployment files and the send ledger can live under `/data`:

```text
PERSONAL_OS_RELEASE_MANIFEST=/data/releases/current.json
PERSONAL_OS_RELEASE_RECEIPT=/data/releases/current-receipt.json
PERSONAL_OS_ANNOUNCEMENT_DIR=/data/release-announcements
PERSONAL_OS_ANNOUNCEMENT_HEALTH_URL=https://calendar.example.com/healthz
PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT=1
```

Provision `/data/releases` and `/data/release-announcements` as durable storage
before enabling announcements. Supply the existing `TELEGRAM_BOT_TOKEN` through
the platform's secret manager; do not write its value in a config file or check
it into the repository.

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
