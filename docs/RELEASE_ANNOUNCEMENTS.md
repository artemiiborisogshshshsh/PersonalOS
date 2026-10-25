# Release announcements

Announcements remain opt-in. The existing local receipt mode is unchanged.
The optional PERSONAL_OS_RELEASE_CONFIRMATION=github mode replaces the
operator-written receipt with a read-only GitHub observer, while the repository
workflow records confirmation metadata only after Amvera serves the expected
artifact. No Amvera API, additional secret, or new public application endpoint
is used.

## Confirmation flow

When releases/current.json changes on main, or on manual dispatch from main,
the GitHub workflow checks out the exact event SHA. If the manifest is still a
draft, it exits without creating a deployment record. For a released manifest,
the workflow creates a GitHub deployment record for that SHA in the
amvera-production environment. The record payload binds the release ID, the
artifact fingerprint, and a SHA-256 digest of the manifest id, text, and status.
It then polls the fixed Amvera health URL for up to 15 minutes without following
redirects. Two consecutive responses, 15 seconds apart, must report the exact
artifact fingerprint before the workflow marks the deployment successful. Any
failed request, non-200 response, redirect, or mismatching fingerprint resets
the consecutive-match count. Timeout records failure.

This confirmation is supported for the current single Amvera replica. A
load-balanced public health URL cannot prove that every replica has finished
rolling out the artifact. Disable or replace this confirmation gate before
scaling to multiple replicas; do not treat one matching replica as a complete
rollout confirmation.

If the 15-minute confirmation times out, the workflow records failure. After
checking the Amvera deployment, an operator can use manual dispatch from main
to retry confirmation for the exact main SHA.

This is confirmation, not deployment. Amvera remains responsible for deploying
the repository. A passing CI job or a bot restart does not create confirmation.
The workflow uses GitHub's built-in GITHUB_TOKEN with only repository read and
deployment write permissions; no separate GitHub credential is configured.

The bot's GitHub mode checks the public repository's deployment records no more
than once every five minutes, with the check interval persisted under the
announcement state directory across restarts. It accepts only a deployment in
the fixed repository and environment, for a 40-character source SHA, created
by github-actions[bot], with the matching release ID, manifest digest, and
artifact fingerprint in its payload, and with latest status success. The
workflow is restricted to the default main ref. GitHub Actions bot identity is
a repository-wide trust boundary: any repository workflow able to create a
matching deployment record is trusted by this observer.

The manifest omits revision by default because a commit cannot contain its own
SHA. The successful GitHub deployment's exact source SHA becomes the release
revision passed to the unchanged durable announcement core. If a manifest does
specify revision, it must equal that deployment SHA. The content digest binds
the manifest fields independently of the commit SHA, and the runtime
fingerprint binds the deployed application files.

## Configuration

The channel is fixed to @myaicalendar_updates. Publishing requires either the
explicit existing-bot opt-in or a dedicated announcement token. The current
Amvera deployment already has the main-bot opt-in and state/health settings;
enable GitHub confirmation by setting:

~~~text
PERSONAL_OS_RELEASE_CONFIRMATION=github
~~~

In GitHub mode, the manifest defaults to the bundled releases/current.json.
PERSONAL_OS_RELEASE_MANIFEST may instead name a relative path beneath the
deployed code root; absolute paths, traversal, and symlinked paths are rejected.
The announcement state directory and exact configured Amvera health URL remain
required. PERSONAL_OS_RELEASE_RECEIPT is not required in this mode. Keep the
existing PERSONAL_OS_ANNOUNCEMENT_USE_MAIN_BOT=1 only if the bot has permission
to publish to the channel; otherwise configure a dedicated
TELEGRAM_ANNOUNCEMENT_BOT_TOKEN and remove the main-bot opt-in. Do not set both.

The bundled source manifest is intentionally inert:

~~~json
{
  "id": "update-1",
  "text": "Release announcement awaiting an approved release.",
  "status": "draft"
}
~~~

This draft does not announce Update 1. A release owner must approve the release
text and change the manifest to released in a main commit. Do not add revision
unless it is already known to match the exact source SHA that will be confirmed.
The workflow and observer never manufacture or auto-release a draft.

## Local receipt mode

When PERSONAL_OS_RELEASE_CONFIRMATION is unset, the backward-compatible local
receipt mode still requires absolute paths for PERSONAL_OS_RELEASE_MANIFEST,
PERSONAL_OS_RELEASE_RECEIPT, and PERSONAL_OS_ANNOUNCEMENT_DIR, plus the HTTPS
health URL and publisher configuration. Its success receipt is written by an
external deployment operator only after deployment and smoke checks succeed:

~~~json
{"status":"success","release_id":"update-2","revision":"40-lowercase-hex-characters","artifact_fingerprint":"64-lowercase-hex-characters"}
~~~

Write local receipts atomically: write the complete JSON to a temporary file in
the same directory, flush it, then rename it to the configured path. Never
expose a partial receipt or create a success receipt before deployment checks.

## Durable delivery and recovery

Both modes pass only verified release identity to the same durable announcement
ledger. A missing confirmation remains pending and can be noticed on later
polls. A successful send, definitive Telegram rejection, ambiguous outcome, or
blocked condition ends checks for that process. Uncertain sends are never
retried automatically.

Back up /data/release-announcements as durable operational state. Do not delete
the ledger when restarting the bot, rolling back a deployment, or cleaning up
release files; retain it through rollbacks so a release cannot be announced
twice. If an outcome is uncertain, inspect the actual announcement channel
first and reconcile with the ledger. Never blindly delete the ledger or rename
the release ID to force another send.

Run python scripts/announce_release.py for one explicit delivery attempt. It
prints only fixed status text and, where available, a numeric message ID or
Telegram error code. It returns success only for sent or already_sent.
The --fingerprint option prints the local artifact fingerprint without network
access.
