"""Local access and discount ledger; payment verification belongs to a future adapter.

``record_verified_payment`` is a trusted application boundary, never a webhook or
Telegram command. A provider adapter must authenticate settlement before calling
it. There is deliberately no provider, checkout, refund or chargeback simulation.
SQLite transactions make a payment, access renewal and both referral credits one
commit. Quotes do not reserve bonuses: a changed balance version invalidates old
quotes, so abandoned/failed payments never spend points.

Before a real provider can capture funds, it needs quote reservation and
settlement/reconciliation design. Even a successful ``validate_quote`` preflight
cannot prevent a later referral credit from changing the balance before capture.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import secrets
import sqlite3
from tempfile import NamedTemporaryFile


TRIAL_DAYS = 21
INVITER_BONUS_POINTS = 25
FRIEND_BONUS_POINTS = 10
MAX_PAYMENT_DISCOUNT_POINTS = 50


class AccessBillingError(ValueError):
    """Invalid input or a rejected ledger operation; no partial mutation occurs."""


class StaleQuoteError(AccessBillingError):
    """The account changed after quoting; request a fresh quote before paying."""


def _now(value: datetime | None) -> datetime:
    value = datetime.now(timezone.utc) if value is None else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AccessBillingError("A timezone-aware datetime is required")
    return value.astimezone(timezone.utc)


def _identifier(value: str, label: str, limit: int = 200) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > limit or any(ord(c) < 32 for c in value)):
        raise AccessBillingError(f"Invalid {label}")
    return value


def _positive_int(value: int, label: str) -> int:
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise AccessBillingError(f"Invalid {label}")
    return value


def _currency(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise AccessBillingError("Currency must be a three-letter uppercase code")
    return value


class AccessBillingStore:
    """One shared durable ledger for all users, backed by a local SQLite file."""

    def __init__(self, path: Path | str, *, read_only: bool = False):
        self.path = Path(path)
        self.read_only = read_only
        if any(parent.is_symlink() for parent in (self.path, *self.path.parents)):
            raise AccessBillingError("Ledger path must not contain symlinks")
        if read_only:
            if not self.path.is_file():
                raise AccessBillingError("An existing ledger is required")
            if self.path.stat().st_mode & 0o077:
                raise AccessBillingError("Ledger must be private (mode 0600)")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self.path.chmod(0o600)
        with self._transaction() as db:
            schema = """
                CREATE TABLE IF NOT EXISTS access_users (
                    user_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    trial_started_at TEXT,
                    trial_ends_at TEXT,
                    paid_through TEXT,
                    beta_through TEXT,
                    first_payment_at TEXT,
                    bonus_points INTEGER NOT NULL DEFAULT 0 CHECK (bonus_points >= 0),
                    balance_version INTEGER NOT NULL DEFAULT 0,
                    referral_code TEXT NOT NULL UNIQUE,
                    inviter_id TEXT REFERENCES access_users(user_id)
                );
                CREATE TABLE IF NOT EXISTS access_quotes (
                    quote_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES access_users(user_id),
                    created_at TEXT NOT NULL,
                    price_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    period_days INTEGER NOT NULL,
                    discount_points INTEGER NOT NULL,
                    discount_minor INTEGER NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    balance_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS access_payments (
                    provider TEXT NOT NULL,
                    payment_id TEXT NOT NULL,
                    quote_id TEXT NOT NULL UNIQUE REFERENCES access_quotes(quote_id),
                    user_id TEXT NOT NULL REFERENCES access_users(user_id),
                    paid_at TEXT NOT NULL,
                    paid_through TEXT NOT NULL,
                    PRIMARY KEY (provider, payment_id)
                );
                CREATE TABLE IF NOT EXISTS access_bonus_events (
                    event_id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES access_users(user_id),
                    points INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS access_beta_extensions (
                    request_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES access_users(user_id),
                    days INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    granted_at TEXT NOT NULL,
                    previous_expires_at TEXT,
                    beta_through TEXT NOT NULL
                );
            """
            # Keep schema creation + old-ledger migration under the same write
            # lock; executescript() would implicitly commit before the ALTER.
            for statement in schema.split(";"):
                if statement.strip():
                    db.execute(statement)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(access_users)")}
            if "beta_through" not in columns:
                db.execute("ALTER TABLE access_users ADD COLUMN beta_through TEXT")

    @contextmanager
    def _transaction(self):
        if self.read_only:
            raise AccessBillingError("Ledger was opened read-only")
        db = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _read_transaction(self):
        uri = self.path.absolute().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN")
            yield db
        finally:
            db.close()

    @staticmethod
    def _ensure(db, user_id: str, now: datetime):
        _identifier(user_id, "user ID")
        db.execute("""INSERT OR IGNORE INTO access_users
                      (user_id, created_at, referral_code) VALUES (?, ?, ?)""",
                   (user_id, now.isoformat(), secrets.token_urlsafe(24)))
        return db.execute("SELECT * FROM access_users WHERE user_id = ?", (user_id,)).fetchone()

    @staticmethod
    def _status(row, now: datetime) -> dict:
        # Read-only operator previews also support the previous schema without
        # migrating it or changing a single ledger byte.
        row = dict(row)
        row.setdefault("beta_through", None)
        expiry = max((datetime.fromisoformat(row[key]) for key in
                      ("trial_ends_at", "paid_through", "beta_through") if row[key]), default=None)
        trial_active = bool(row["trial_ends_at"] and now < datetime.fromisoformat(row["trial_ends_at"]))
        paid_active = bool(row["paid_through"] and now < datetime.fromisoformat(row["paid_through"]))
        beta_active = bool(row["beta_through"] and now < datetime.fromisoformat(row["beta_through"]))
        return {
            "user_id": row["user_id"], "created_at": row["created_at"],
            "trial_started_at": row["trial_started_at"], "trial_ends_at": row["trial_ends_at"],
            "paid_through": row["paid_through"], "first_payment_at": row["first_payment_at"],
            "beta_through": row["beta_through"], "beta_active": beta_active,
            "access_expires_at": expiry.isoformat() if expiry else None,
            "access_allowed": bool(expiry and now < expiry),
            "trial_started": bool(row["trial_started_at"]),
            "trial_active": trial_active, "paid_active": paid_active,
            "bonus_points": row["bonus_points"], "referral_bound": bool(row["inviter_id"]),
        }

    def ensure_user(self, user_id: str, now: datetime | None = None) -> dict:
        now = _now(now)
        with self._transaction() as db:
            return self._status(self._ensure(db, user_id, now), now)

    def status(self, user_id: str, now: datetime | None = None) -> dict:
        return self.ensure_user(user_id, now)

    @staticmethod
    def _known_user(db, user_id: str):
        _identifier(user_id, "user ID")
        row = db.execute("SELECT * FROM access_users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            raise AccessBillingError("Unknown user ID")
        return row

    def known_status(self, user_id: str, now: datetime | None = None) -> dict:
        """Operator read-only lookup; a typo must never create a ledger account."""
        now = _now(now)
        with self._read_transaction() as db:
            return self._status(self._known_user(db, user_id), now)

    @staticmethod
    def _extension_inputs(user_id: str, days: int, request_id: str, reason: str):
        _identifier(user_id, "user ID")
        _positive_int(days, "extension days")
        if days > 365:
            raise AccessBillingError("Extension days must be between 1 and 365")
        _identifier(request_id, "extension request ID")
        _identifier(reason, "extension reason", 500)

    def _extension_preview(self, db, user_id: str, days: int, request_id: str,
                           reason: str, now: datetime) -> dict:
        self._extension_inputs(user_id, days, request_id, reason)
        user = self._known_user(db, user_id)
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'access_beta_extensions'").fetchone()
        previous = (db.execute("SELECT * FROM access_beta_extensions WHERE request_id = ?", (request_id,)).fetchone()
                    if exists else None)
        if previous:
            if previous["user_id"] != user_id or previous["days"] != days or previous["reason"] != reason:
                raise AccessBillingError("Extension request ID was already used with different parameters")
            return {**dict(previous), "duplicate": True}
        status = self._status(user, now)
        previous_expiry = status["access_expires_at"]
        base = max(now, datetime.fromisoformat(previous_expiry)) if previous_expiry else now
        try:
            beta_through = base + timedelta(days=days)
        except OverflowError as exc:
            raise AccessBillingError("Extension exceeds supported dates") from exc
        return {"request_id": request_id, "user_id": user_id, "days": days, "reason": reason,
                "granted_at": now.isoformat(), "previous_expires_at": previous_expiry,
                "beta_through": beta_through.isoformat(), "duplicate": False}

    def preview_beta_extension(self, user_id: str, days: int, request_id: str,
                               reason: str, now: datetime | None = None) -> dict:
        """Validate an operator request without migrating or modifying the ledger."""
        now = _now(now)
        with self._read_transaction() as db:
            return self._extension_preview(db, user_id, days, request_id, reason, now)

    def grant_beta_extension(self, user_id: str, days: int, request_id: str,
                             reason: str, now: datetime | None = None) -> dict:
        """Trusted local admin grant, independent of payments, referrals and trial.

        The reason is exported to this user: record a short administrative reason,
        never credentials or another user's personal details.
        """
        now = _now(now)
        with self._transaction() as db:
            result = self._extension_preview(db, user_id, days, request_id, reason, now)
            if result["duplicate"]:
                return result
            db.execute("""INSERT INTO access_beta_extensions
                          (request_id, user_id, days, reason, granted_at, previous_expires_at, beta_through)
                          VALUES (?, ?, ?, ?, ?, ?, ?)""",
                       tuple(result[key] for key in ("request_id", "user_id", "days", "reason", "granted_at",
                                                     "previous_expires_at", "beta_through")))
            db.execute("UPDATE access_users SET beta_through = ? WHERE user_id = ?", (result["beta_through"], user_id))
            return result

    def start_trial(self, user_id: str, now: datetime | None = None) -> dict:
        """Call only after the first successful publication; repeated calls keep dates."""
        now = _now(now)
        end = now + timedelta(days=TRIAL_DAYS)
        with self._transaction() as db:
            self._ensure(db, user_id, now)
            db.execute("""UPDATE access_users SET trial_started_at = ?, trial_ends_at = ?
                          WHERE user_id = ? AND trial_started_at IS NULL""",
                       (now.isoformat(), end.isoformat(), user_id))
            return self._status(self._ensure(db, user_id, now), now)

    def referral_code(self, user_id: str) -> str:
        with self._transaction() as db:
            return self._ensure(db, user_id, _now(None))["referral_code"]

    def bind_referral(self, user_id: str, code: str, now: datetime | None = None) -> dict:
        now = _now(now)
        _identifier(code, "referral code")
        with self._transaction() as db:
            row = self._ensure(db, user_id, now)
            inviter = db.execute("SELECT user_id FROM access_users WHERE referral_code = ?", (code,)).fetchone()
            if not inviter or inviter["user_id"] == user_id:
                raise AccessBillingError("Invalid referral code")
            if row["inviter_id"] or row["trial_started_at"] or row["first_payment_at"]:
                raise AccessBillingError("Referral can only be bound once before publication or payment")
            db.execute("UPDATE access_users SET inviter_id = ? WHERE user_id = ?", (inviter["user_id"], user_id))
            return self._status(self._ensure(db, user_id, now), now)

    @staticmethod
    def _quote_dict(row) -> dict:
        return {key: row[key] for key in ("quote_id", "user_id", "created_at", "price_minor", "currency",
                                         "period_days", "discount_points", "discount_minor", "amount_minor")}

    def quote(self, user_id: str, price_minor: int, currency: str, period_days: int,
              now: datetime | None = None) -> dict:
        now = _now(now)
        _positive_int(price_minor, "price")
        _positive_int(period_days, "period")
        _currency(currency)
        try:
            now + timedelta(days=period_days)
        except OverflowError as exc:
            raise AccessBillingError("Invalid period") from exc
        with self._transaction() as db:
            user = self._ensure(db, user_id, now)
            points = min(MAX_PAYMENT_DISCOUNT_POINTS, user["bonus_points"])
            # Round savings down to a whole minor unit, never exceeding the cap.
            discount = price_minor * points // 100
            if points and discount == 0:
                raise AccessBillingError("Price is too small to apply bonus points without losing their value")
            quote_id = secrets.token_urlsafe(24)
            db.execute("INSERT INTO access_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (quote_id, user_id, now.isoformat(), price_minor, currency, period_days,
                        points, discount, price_minor - discount, user["balance_version"]))
            return self._quote_dict(db.execute("SELECT * FROM access_quotes WHERE quote_id = ?", (quote_id,)).fetchone())

    def validate_quote(self, user_id: str, quote_id: str, now: datetime | None = None) -> dict:
        """Read-only preflight; does not reserve a quote or authorize capturing funds."""
        _now(now)
        _identifier(user_id, "user ID")
        _identifier(quote_id, "quote ID")
        with self._transaction() as db:
            quote = db.execute("SELECT * FROM access_quotes WHERE quote_id = ?", (quote_id,)).fetchone()
            if not quote or quote["user_id"] != user_id:
                raise AccessBillingError("Quote does not belong to this user")
            if db.execute("SELECT 1 FROM access_payments WHERE quote_id = ?", (quote_id,)).fetchone():
                raise AccessBillingError("Quote was already paid")
            user = db.execute("SELECT * FROM access_users WHERE user_id = ?", (user_id,)).fetchone()
            if user["balance_version"] != quote["balance_version"]:
                raise StaleQuoteError("Account balance changed; request a fresh quote")
            return self._quote_dict(quote)

    @staticmethod
    def _payment_dict(payment, quote, *, duplicate: bool) -> dict:
        return {**AccessBillingStore._quote_dict(quote), "provider": payment["provider"],
                "payment_id": payment["payment_id"], "paid_at": payment["paid_at"],
                "paid_through": payment["paid_through"], "duplicate": duplicate}

    @staticmethod
    def _bonus(db, user_id: str, points: int, reason: str, now: datetime):
        db.execute("""UPDATE access_users SET bonus_points = bonus_points + ?,
                      balance_version = balance_version + 1 WHERE user_id = ?""", (points, user_id))
        if points:
            db.execute("INSERT INTO access_bonus_events (user_id, points, reason, created_at) VALUES (?, ?, ?, ?)",
                       (user_id, points, reason, now.isoformat()))

    def record_verified_payment(self, user_id: str, *, provider: str, payment_id: str,
                                quote_id: str, amount_minor: int, currency: str,
                                now: datetime | None = None) -> dict:
        """Record externally verified success atomically, idempotent by provider + ID.

        The immutable stored quote determines original price, discount and period.
        Replayed IDs must match their original user, quote, amount and currency.
        No failure API is needed: a failed payment leaves the quote and balance alone.
        """
        now = _now(now)
        _identifier(user_id, "user ID")
        _identifier(provider, "provider", 100)
        _identifier(payment_id, "payment ID")
        _identifier(quote_id, "quote ID")
        _positive_int(amount_minor, "amount")
        _currency(currency)
        with self._transaction() as db:
            quote = db.execute("SELECT * FROM access_quotes WHERE quote_id = ?", (quote_id,)).fetchone()
            if not quote or quote["user_id"] != user_id:
                raise AccessBillingError("Quote does not belong to this user")
            if (quote["amount_minor"] != amount_minor or quote["currency"] != currency
                    or quote["price_minor"] - quote["discount_minor"] != amount_minor):
                raise AccessBillingError("Payment does not match the original quote")
            previous = db.execute("SELECT * FROM access_payments WHERE provider = ? AND payment_id = ?",
                                  (provider, payment_id)).fetchone()
            if previous:
                if previous["user_id"] != user_id or previous["quote_id"] != quote_id:
                    raise AccessBillingError("Payment ID was already used")
                return self._payment_dict(previous, quote, duplicate=True)
            if db.execute("SELECT 1 FROM access_payments WHERE quote_id = ?", (quote_id,)).fetchone():
                raise AccessBillingError("Quote was already paid")
            user = self._ensure(db, user_id, now)
            if user["balance_version"] != quote["balance_version"]:
                raise StaleQuoteError("Account balance changed; request a fresh quote")
            current_expiry = max((datetime.fromisoformat(user[key]) for key in
                                  ("trial_ends_at", "paid_through", "beta_through") if user[key]), default=now)
            try:
                paid_through = max(now, current_expiry) + timedelta(days=quote["period_days"])
            except OverflowError as exc:
                raise AccessBillingError("Payment period exceeds supported dates") from exc
            db.execute("INSERT INTO access_payments VALUES (?, ?, ?, ?, ?, ?)",
                       (provider, payment_id, quote_id, user_id, now.isoformat(), paid_through.isoformat()))
            self._bonus(db, user_id, -quote["discount_points"], "payment_discount", now)
            db.execute("""UPDATE access_users SET paid_through = ?,
                          first_payment_at = COALESCE(first_payment_at, ?) WHERE user_id = ?""",
                       (paid_through.isoformat(), now.isoformat(), user_id))
            if user["first_payment_at"] is None and user["inviter_id"]:
                self._bonus(db, user["inviter_id"], INVITER_BONUS_POINTS, "invited_friend_first_payment", now)
                self._bonus(db, user_id, FRIEND_BONUS_POINTS, "own_first_payment_referral", now)
            payment = db.execute("SELECT * FROM access_payments WHERE provider = ? AND payment_id = ?",
                                 (provider, payment_id)).fetchone()
            return self._payment_dict(payment, quote, duplicate=False)

    def export_user(self, user_id: str, now: datetime | None = None) -> dict:
        """Export this user's account and ledger, omitting other users and code internals."""
        now = _now(now)
        with self._transaction() as db:
            user = self._ensure(db, user_id, now)
            quotes = db.execute("SELECT * FROM access_quotes WHERE user_id = ? ORDER BY rowid", (user_id,)).fetchall()
            payments = db.execute("SELECT * FROM access_payments WHERE user_id = ? ORDER BY rowid", (user_id,)).fetchall()
            quotes_by_id = {row["quote_id"]: row for row in quotes}
            events = db.execute("""SELECT points, reason, created_at FROM access_bonus_events
                                   WHERE user_id = ? ORDER BY event_id""", (user_id,)).fetchall()
            extensions = db.execute("SELECT * FROM access_beta_extensions WHERE user_id = ? ORDER BY rowid", (user_id,)).fetchall()
            return {"version": 1, "status": self._status(user, now),
                    "quotes": [self._quote_dict(row) for row in quotes],
                    "payments": [self._payment_dict(row, quotes_by_id[row["quote_id"]], duplicate=False)
                                 for row in payments],
                    "bonus_events": [dict(row) for row in events],
                    "beta_extensions": [dict(row) for row in extensions]}

    def backup_to(self, path: Path | str) -> Path:
        """Write a consistent private full-ledger snapshot without replacing files.

        This operator backup includes all users. It is distinct from a privacy
        export, which cannot restore balances or idempotency safely. Restoration
        requires stopping writers and selecting a verified full-ledger snapshot.
        """
        target = Path(path).absolute()
        if any(parent.is_symlink() for parent in (target, *target.parents)):
            raise AccessBillingError("Backup path must not contain symlinks")
        if target.exists():
            raise FileExistsError("Backup destination already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(dir=target.parent, prefix=".access-backup-", delete=False) as output:
                temporary = Path(output.name)
            with closing(sqlite3.connect(str(self.path), timeout=30)) as source:
                with closing(sqlite3.connect(str(temporary), timeout=30)) as destination:
                    source.backup(destination)
            with temporary.open("rb") as completed:
                os.fsync(completed.fileno())
            # link() publishes only a complete snapshot and fails if target exists,
            # including a symlink inserted while the snapshot was being prepared.
            os.link(temporary, target)
            temporary.unlink()
            temporary = None
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return target
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
