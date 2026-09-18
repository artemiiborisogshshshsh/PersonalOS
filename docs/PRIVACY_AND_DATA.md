# Privacy and data handling

This is the product's technical data-handling policy draft. Obtain legal review
for the jurisdiction and final public privacy notice before selling the app.

## Data the application owns

- attendance choices, planning settings and sleep schedule;
- source references, never raw password values in product state;
- system-owned calendar draft operation IDs, versions, hashes and snapshots;
- user feedback supplied through Telegram.

Google Calendar events are changed only when they contain the application's
ownership marker. User-created events are treated as conflicts and are not
deleted by rollback.

## User controls

`UserDataLifecycleService` exports only an explicit allow-list of one user's
application-owned JSON state and deletes it only after an explicit confirmation
flag. It rejects path traversal and symlink escapes and never touches another
user's directory. Google OAuth tokens are excluded from the export and must be
revoked via Google/secret-vault controls.

## Security requirements

- Keep `.env`, OAuth credentials and tokens out of Git and logs.
- Use per-user token storage or a platform secret vault in production.
- Rotate credentials if they were ever committed or shared.
- Encrypt backups and restrict access to the runtime data volume.
