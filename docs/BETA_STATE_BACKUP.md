# Invited-bot state backup and isolated restore

This is an operator-only recovery archive containing **all users**, invitation grants,
registries, polling inbox and cursor, recovery journals, access runtime warnings and
resume barriers, release-delivery receipts, and the shared access/billing ledger.
It is separate from a per-user privacy export. Treat the archive as private user data;
it is not encrypted. Store it only on private storage with an appropriate retention
policy. The script creates archives and restored files with mode `0600`, and restored
directories with mode `0700`.

## Offline backup

1. Stop the invited bot and its OAuth callback server. Stop all other state writers,
   including invite/revoke, access-extension, payment/ledger, and maintenance tools.
   Keep them stopped until the backup command returns. The `.pilot.lock` check
   refuses a running bot and holds the same exclusive instance lock throughout the
   snapshot. Other operator tools must also remain stopped: the instance lock
   cannot prevent writes by tools that do not acquire it.
2. Use the exact `PERSONAL_OS_BETA_DIR`, never the repository, a user's inner state
   directory, or `PERSONAL_OS_GOOGLE_DIR`. Keep Google tokens, OAuth client files,
   `.env` files and encryption/private keys outside this root.
3. Choose a new archive filename outside the state root, in an existing private
   backup directory, then run:

   ```sh
   python3 scripts/beta_state_backup.py backup \
     --state-dir /absolute/private/beta-state \
     --archive /absolute/private/backups/beta-before-update.zip
   ```

The service inventories the complete operational root and snapshots each SQLite
file with SQLite's backup API, including committed WAL data and all tables. It
checks SQLite integrity and hashes the snapshot bytes. Other files, including JSON,
are preserved byte for byte. A second inventory detects changes during backup.
It publishes the completed, fsynced archive atomically without overwriting an
existing path. An error does not publish a partial archive. Keep the source stopped
while investigating a failure; the command prints only categorical errors.

## Isolated restore rehearsal

Choose a **nonexistent** destination under an existing private parent. Existing
empty directories are also rejected. Keep the source bot stopped while comparing
operational state, and do not configure or start a second bot against the copy.

```sh
python3 scripts/beta_state_backup.py restore \
  --archive /absolute/private/backups/beta-before-update.zip \
  --destination /absolute/private/rehearsals/beta-restored
```

Restore verifies the format version, full manifest inventory, sizes and SHA-256
hashes in private staging before creating the destination. It rejects traversal,
absolute paths, symlinks, duplicate entries/manifest keys, unmanifested entries,
conflicting file/directory paths, credentials and oversized archives. Version 1
limits are 10,000 state files, 256 MiB per file, 1 GiB total uncompressed data, and
4 MiB for the manifest. Archives made by other tools are not a supported format.
Hashes detect corruption; they do not authenticate an archive's origin. Restore only
archives from trusted operator storage.

For acceptance, compare the manifest inventory with the source operational files;
verify all ordinary file bytes and a read-only logical comparison of the SQLite
snapshot. Check invitation enabled/generation values, polling pending updates and
offset, trial dates, paid-through dates, referral and bonus records, payment and
extension idempotency keys, warning receipts, `resume_required`, and pending recovery
journals. Do not exercise idempotency APIs against a live ledger: synthetic tests
already verify replay behavior; use only the isolated copy if additional rehearsal
is needed.

Restore does not import application services, start polling, call Telegram or
Google, grant access, renew a trial, replay journals, clear warnings, or clear a
resume barrier. Existing application recovery and explicit confirmation rules still
apply when an operator later starts a chosen state root. This command does not
switch production roots or authorize deployment. A live cutover requires a separate
operator decision after rehearsal.

## Scope and excluded material

The manifest lists every included relative filename, byte size and SHA-256 digest.
No user IDs, state values or paths are printed by the CLI. All regular operational
files are included without a per-user allowlist, so future ledger tables and
application journals survive without changing this service.

The known `google_oauth_pending/` subtree is intentionally omitted: it contains
short-lived OAuth link verifiers. In-flight Google connect links are invalid after
restore and must be recreated with `/connect_google`. Existing Google credentials
live in the separate `PERSONAL_OS_GOOGLE_DIR`; this command neither reads nor backs
up that directory. Recover those credentials through the operator's separate secret
management process, or reconnect users after choosing a restored root.

Lock files, hidden atomic-write temporary files, `*.tmp`, Python `tmpXXXXXXXX`
files, Python/pytest caches, `cache/` and `caches/` directories, and SQLite
`-wal`/`-shm`/`-journal` sidecars are omitted. SQLite's backup API incorporates durable
database content; copying sidecars into the restored root would be unsafe. Pending
application recovery journals such as `update_all.json` and
`class-calendar-recovery.json` remain included.

Symbolic links and special files anywhere in the scanned operational tree cause
failure. Known credential/key/token filenames and Google/OAuth paths outside the
one excluded pending subtree also cause failure, including `.env`, `token.json`,
`credentials.json`, `*-keyfile.*`, and private-key formats. This is a filename guard,
not a secret detector: an arbitrarily renamed secret could evade it. Keep the state
root exclusively for application operational state and review its inventory before
backup; never copy credentials into it.

Validation: `python3 -m pytest -q tests/test_beta_state_backup.py`. The suite uses
synthetic state and local SQLite only, with no providers or deployment actions.
