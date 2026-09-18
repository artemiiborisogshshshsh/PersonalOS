# Product runtime

The application runtime is portable: application code is read-only, while all
user-owned state lives in `PERSONAL_OS_DATA_DIR`.

## Required configuration

Copy `.env.example` to a private deployment secret store and provide:

- `PERSONAL_OS_DATA_DIR` — writable persistent volume;
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` — the bot allow-list;
- `TPU_SCHEDULE_VIEW_URL` — preferred stable TPU group page, for example
  `https://ro-rasp.tpu.ru/gruppa_41736/2026/1/view.html`. Before each refresh
  the bot computes the academic week in Asia/Tomsk, increasing it each Monday.
  Week 1 starts on the Monday of September 1's week (2026: August 31).
  September 9 therefore selects `/2026/2/`, September 14 selects `/2026/3/`.
  Restarting or syncing again does not increment the week. It discovers the current iCal URL in
  memory; the temporary `key` and `sig` are neither saved nor sent to Telegram;
- `UNIVERSITY_SCHEDULE_URL` — optional direct ICS source for other providers;
- Google OAuth paths only through the host's secret storage.

Run `python3 scripts/runtime_healthcheck.py` before starting the bot. It
validates configuration without printing credentials. Start the live process
with `python3 scripts/telegram_schedule_bot.py`; it selects
`TPU_SCHEDULE_VIEW_URL` when provided.

For TPU, `TPU_SCHEDULE_EXPORT_VARIANT=2` is the default and means “На две
недели”, which covers the current and next weekly plan. The app submits the
same public export form that the website presents, then discards the generated
link after downloading the ICS file.

User state is isolated at `PERSONAL_OS_DATA_DIR/users/<user-id>/`. It includes
attendance preferences, planning profile and reversible draft-operation
snapshots. OAuth tokens must not be written into these exports.

## Operations

Use a supervisor (systemd, launchd, Docker/Kubernetes) with restart-on-failure
and persistent mounted `PERSONAL_OS_DATA_DIR`. The running bot checks TPU at
05:00, 12:00, 15:00, 18:00 and 22:00 in the user's planning timezone (default
`Asia/Tomsk`); these hours are stored in the planning profile and can be
changed per user. Saturday's 18:00 pass therefore includes the normal rolling
current-and-next-week planning update. It supports manual refresh
in Telegram. The 05:00 report contains changes and system-known tasks for the
day. Calendar failures are retried safely up to ten times; only system-owned
drafts can be changed or deleted by a retry. Monitor the health check and
retain encrypted backups of the runtime data directory.

## Current deployment boundary

Google OAuth callback hosting and a platform keychain/secret-vault adapter are
deployment-specific infrastructure. They must be supplied before offering a
multi-user public service; source code does not contain a shared token path.
