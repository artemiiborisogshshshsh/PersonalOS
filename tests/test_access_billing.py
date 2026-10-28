"""Real local transactions, without a payment provider or settlement transport."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import stat
import sqlite3

import pytest

from services.access_billing import AccessBillingError, AccessBillingStore, StaleQuoteError


NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return AccessBillingStore(tmp_path / "shared" / "access.sqlite3")


def pay(store, user, payment_id, *, now=NOW, price=10000, quote=None):
    quote = quote or store.quote(user, price, "RUB", 30, now)
    return store.record_verified_payment(
        user, provider="trusted-test-adapter", payment_id=payment_id,
        quote_id=quote["quote_id"], amount_minor=quote["amount_minor"],
        currency=quote["currency"], now=now)


def referred_friend(store, inviter, friend, payment_id, *, now=NOW):
    store.bind_referral(friend, store.referral_code(inviter), now)
    return pay(store, friend, payment_id, now=now)


def test_ensure_does_not_start_trial_or_expire_legacy_account(store):
    status = store.ensure_user("legacy", NOW)
    assert not status["trial_started"] and not status["access_allowed"]
    assert status["access_expires_at"] is None
    assert store.status("legacy", NOW + timedelta(days=200))["trial_started_at"] is None


def test_trial_exact_21_days_utc_boundary_and_never_reset(store):
    tomsk = timezone(timedelta(hours=7))
    started = store.start_trial("one", NOW.astimezone(tomsk))
    expiry = NOW + timedelta(days=21)
    assert started["trial_started_at"] == NOW.isoformat()
    assert started["trial_ends_at"] == expiry.isoformat()
    assert store.status("one", expiry - timedelta(microseconds=1))["access_allowed"]
    assert not store.status("one", expiry)["access_allowed"]
    assert store.start_trial("one", expiry + timedelta(days=50))["trial_ends_at"] == expiry.isoformat()
    restarted = AccessBillingStore(store.path)
    assert restarted.start_trial("one", expiry + timedelta(days=500))["trial_ends_at"] == expiry.isoformat()


def test_referral_code_opaque_persistent_and_binding_once_before_publication(store):
    code = store.referral_code("inviter-sensitive-id")
    assert len(code) >= 32 and "inviter-sensitive-id" not in code
    assert code == AccessBillingStore(store.path).referral_code("inviter-sensitive-id")
    for user, candidate in [("inviter-sensitive-id", code), ("new", "unknown")]:
        with pytest.raises(AccessBillingError):
            store.bind_referral(user, candidate, NOW)
    store.bind_referral("friend", code, NOW)
    assert store.status("friend", NOW)["referral_bound"]
    with pytest.raises(AccessBillingError):
        store.bind_referral("friend", code, NOW)
    store.start_trial("published", NOW)
    with pytest.raises(AccessBillingError):
        store.bind_referral("published", code, NOW)
    pay(store, "already-paid", "old")
    with pytest.raises(AccessBillingError):
        store.bind_referral("already-paid", code, NOW)


def test_bonuses_only_after_first_matching_success_and_not_used_on_same_payment(store):
    store.bind_referral("friend", store.referral_code("inviter"), NOW)
    quote = store.quote("friend", 10000, "RUB", 30, NOW)
    assert quote["discount_points"] == 0
    assert store.status("inviter", NOW)["bonus_points"] == 0
    with pytest.raises(AccessBillingError):
        store.record_verified_payment("friend", provider="trusted-test-adapter", payment_id="bad",
                                      quote_id=quote["quote_id"], amount_minor=1, currency="RUB", now=NOW)
    assert store.status("friend", NOW)["bonus_points"] == 0
    payment = pay(store, "friend", "first", quote=quote)
    assert payment["amount_minor"] == 10000
    assert store.status("friend", NOW)["bonus_points"] == 10
    assert store.status("inviter", NOW)["bonus_points"] == 25
    next_payment = pay(store, "friend", "second")
    assert next_payment["discount_points"] == 10
    assert next_payment["amount_minor"] == 9000
    assert store.status("friend", NOW)["bonus_points"] == 0
    assert store.status("inviter", NOW)["bonus_points"] == 25


def test_bonus_sum_50_cap_and_remainder_carries_without_expiration(store):
    for index in range(3):
        referred_friend(store, "inviter", f"friend-{index}", f"payment-{index}")
    assert store.status("inviter", NOW)["bonus_points"] == 75
    later = NOW + timedelta(days=3650)
    first = pay(store, "inviter", "spend50", now=later)
    assert first["discount_points"] == 50 and first["amount_minor"] == 5000
    assert store.status("inviter", later)["bonus_points"] == 25
    second = pay(store, "inviter", "spend25", now=later)
    assert second["discount_points"] == 25 and second["amount_minor"] == 7500
    assert store.status("inviter", later)["bonus_points"] == 0


def test_fractional_minor_unit_discount_rounds_down_deterministically(store):
    referred_friend(store, "inviter", "friend", "first")
    quote = store.quote("inviter", 103, "RUB", 30, NOW)
    assert quote["discount_points"] == 25
    assert quote["discount_minor"] == 25 and quote["amount_minor"] == 78
    assert pay(store, "inviter", "fractional", quote=quote)["amount_minor"] == 78


def test_tiny_price_cannot_consume_points_for_zero_money_discount(store):
    referred_friend(store, "inviter", "friend", "first")
    with pytest.raises(AccessBillingError):
        store.quote("inviter", 1, "RUB", 30, NOW)
    assert store.status("inviter", NOW)["bonus_points"] == 25
    assert store.export_user("inviter", NOW)["quotes"] == []


def test_quotes_immutable_wrong_user_and_tampered_amount_currency_rejected(store):
    quote = store.quote("one", 10000, "RUB", 30, NOW)
    quote["amount_minor"] = 1
    with pytest.raises(AccessBillingError):
        pay(store, "one", "tampered", quote=quote)
    quote["amount_minor"] = 10000
    with pytest.raises(AccessBillingError):
        pay(store, "other", "stolen", quote=quote)
    with pytest.raises(AccessBillingError):
        store.record_verified_payment("one", provider="p", payment_id="wrong-currency",
                                      quote_id=quote["quote_id"], amount_minor=10000, currency="USD", now=NOW)
    assert pay(store, "one", "valid", quote=quote)["price_minor"] == 10000
    with pytest.raises(AccessBillingError):
        pay(store, "one", "different-id", quote=quote)


def test_multiple_quotes_cannot_overspend_and_stale_does_not_consume(store):
    referred_friend(store, "inviter", "friend", "award")
    quotes = [store.quote("inviter", 10000, "RUB", 30, NOW) for _ in range(2)]
    assert store.validate_quote("inviter", quotes[0]["quote_id"], NOW) == quotes[0]
    pay(store, "inviter", "first", quote=quotes[0])
    with pytest.raises(AccessBillingError):
        store.validate_quote("inviter", quotes[0]["quote_id"], NOW)
    with pytest.raises(StaleQuoteError):
        store.validate_quote("inviter", quotes[1]["quote_id"], NOW)
    with pytest.raises(StaleQuoteError):
        pay(store, "inviter", "second", quote=quotes[1])
    assert store.status("inviter", NOW)["bonus_points"] == 0
    assert len(store.export_user("inviter", NOW)["payments"]) == 1


def test_abandoned_or_failed_quote_leaves_all_points_available(store):
    referred_friend(store, "inviter", "friend", "award")
    abandoned = store.quote("inviter", 10000, "RUB", 30, NOW)
    with pytest.raises(AccessBillingError):
        store.record_verified_payment("inviter", provider="p", payment_id="failed",
                                      quote_id=abandoned["quote_id"], amount_minor=9999, currency="RUB", now=NOW)
    assert store.status("inviter", NOW)["bonus_points"] == 25
    replacement = store.quote("inviter", 10000, "RUB", 30, NOW)
    assert replacement["discount_points"] == 25
    assert pay(store, "inviter", "valid", quote=replacement)["amount_minor"] == 7500


def test_repeated_and_concurrent_payment_only_renews_and_awards_once(store):
    store.bind_referral("friend", store.referral_code("inviter"), NOW)
    quote = store.quote("friend", 10000, "RUB", 30, NOW)
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: pay(AccessBillingStore(store.path), "friend", "same-id", quote=quote), range(16)))
    assert sum(not row["duplicate"] for row in results) == 1
    assert store.status("friend", NOW)["bonus_points"] == 10
    assert store.status("inviter", NOW)["bonus_points"] == 25
    assert store.status("friend", NOW)["paid_through"] == (NOW + timedelta(days=30)).isoformat()
    assert pay(store, "friend", "same-id", now=NOW + timedelta(days=10), quote=quote)["duplicate"]


def test_same_payment_id_cannot_cross_user_even_concurrently(store):
    quotes = {user: store.quote(user, 10000, "RUB", 30, NOW) for user in ("one", "two")}
    def settle(user):
        try:
            return pay(store, user, "global-id", quote=quotes[user])
        except AccessBillingError:
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(settle, quotes))
    assert sum(row is not None for row in results) == 1
    assert sum(len(store.export_user(user, NOW)["payments"]) for user in quotes) == 1


def test_concurrent_friend_payments_do_not_lose_inviter_rewards(store):
    code = store.referral_code("inviter")
    for index in range(12):
        store.bind_referral(f"friend-{index}", code, NOW)
    with ThreadPoolExecutor(max_workers=6) as workers:
        list(workers.map(lambda index: pay(store, f"friend-{index}", f"p-{index}"), range(12)))
    assert store.status("inviter", NOW)["bonus_points"] == 300


def test_transaction_failure_rolls_back_payment_access_and_both_rewards(store):
    store.bind_referral("friend", store.referral_code("inviter"), NOW)
    quote = store.quote("friend", 10000, "RUB", 30, NOW)
    with sqlite3.connect(store.path) as db:
        db.execute("""CREATE TRIGGER fail_second_reward BEFORE INSERT ON access_bonus_events
                      WHEN NEW.reason = 'own_first_payment_referral'
                      BEGIN SELECT RAISE(ABORT, 'synthetic disk failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        pay(store, "friend", "retryable", quote=quote)
    assert store.status("friend", NOW)["first_payment_at"] is None
    assert store.status("friend", NOW)["paid_through"] is None
    assert store.status("inviter", NOW)["bonus_points"] == 0
    assert store.export_user("friend", NOW)["payments"] == []
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER fail_second_reward")
    assert not pay(store, "friend", "retryable", quote=quote)["duplicate"]


def test_renewal_uses_current_expiry_or_now_and_replay_does_not_reexpire(store):
    store.start_trial("one", NOW)
    quote = store.quote("one", 10000, "RUB", 30, NOW)
    initial = pay(store, "one", "during-trial", quote=quote)
    assert initial["paid_through"] == (NOW + timedelta(days=51)).isoformat()
    expired = NOW + timedelta(days=80)
    assert not store.status("one", expired)["access_allowed"]
    renewed = pay(store, "one", "renewal", now=expired)
    assert renewed["paid_through"] == (expired + timedelta(days=30)).isoformat()
    pay(store, "one", "during-trial", quote=quote, now=expired)
    assert store.status("one", expired)["paid_through"] == renewed["paid_through"]
    assert store.status("one", expired)["access_allowed"]


def test_export_contains_only_own_data_without_referral_internals(store):
    referred_friend(store, "private-inviter-identity", "friend", "friend-payment")
    pay(store, "private-inviter-identity", "private-inviter-payment")
    exported = store.export_user("friend", NOW)
    serialized = json.dumps(exported)
    assert "private-inviter" not in serialized
    assert store.referral_code("friend") not in serialized
    assert store.referral_code("private-inviter-identity") not in serialized
    assert "inviter_id" not in serialized and "balance_version" not in serialized
    assert exported["status"]["bonus_points"] == 10
    assert len(exported["payments"]) == 1 and len(exported["bonus_events"]) == 1


@pytest.mark.parametrize("price,currency,period", [(-1, "RUB", 30), (0, "RUB", 30),
    (1.1, "RUB", 30), (True, "RUB", 30), (100, "rub", 30), (100, "RUB", 0),
    (100, "RUB", -1), (100, "RUB", True), (100, "RUB", 2**63)])
def test_invalid_quotes_rejected_without_mutation(store, price, currency, period):
    with pytest.raises(AccessBillingError):
        store.quote("one", price, currency, period, NOW)
    assert store.export_user("one", NOW)["quotes"] == []


def test_naive_clock_rejected(store):
    with pytest.raises(AccessBillingError):
        store.start_trial("one", NOW.replace(tzinfo=None))


def test_private_backup_reopens_with_trial_balance_and_payment_idempotency(store, tmp_path):
    store.start_trial("inviter", NOW)
    store.bind_referral("friend", store.referral_code("inviter"), NOW)
    quote = store.quote("friend", 10000, "RUB", 30, NOW)
    pay(store, "friend", "paid-once", quote=quote)
    target = tmp_path / "operator-backups" / "ledger.sqlite3"
    assert store.backup_to(target) == target
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    restored = AccessBillingStore(target)
    assert restored.status("inviter", NOW) == store.status("inviter", NOW)
    assert restored.status("friend", NOW) == store.status("friend", NOW)
    assert restored.referral_code("inviter") == store.referral_code("inviter")
    assert pay(restored, "friend", "paid-once", quote=quote)["duplicate"]
    assert restored.status("inviter", NOW)["bonus_points"] == 25
    assert restored.export_user("friend", NOW) == store.export_user("friend", NOW)


def test_backup_never_clobbers_and_rejects_target_or_parent_symlink(store, tmp_path):
    existing = tmp_path / "existing.sqlite3"
    existing.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        store.backup_to(existing)
    assert existing.read_bytes() == b"preserve"
    link = tmp_path / "link.sqlite3"
    link.symlink_to(existing)
    with pytest.raises(AccessBillingError):
        store.backup_to(link)
    directory = tmp_path / "directory-link"
    directory.symlink_to(store.path.parent, target_is_directory=True)
    with pytest.raises(AccessBillingError):
        store.backup_to(directory / "new.sqlite3")
    assert not (store.path.parent / "new.sqlite3").exists()


def test_ledger_itself_is_private_and_symlink_is_rejected(store, tmp_path):
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    link = tmp_path / "ledger-link.sqlite3"
    link.symlink_to(store.path)
    with pytest.raises(AccessBillingError):
        AccessBillingStore(link)


def test_beta_extension_separate_from_payment_trial_and_referral_rewards(store):
    store.bind_referral("friend", store.referral_code("inviter"), NOW)
    trial = store.start_trial("friend", NOW)
    before = store.export_user("friend", NOW)
    extension = store.grant_beta_extension("friend", 14, "grant-1", "Free beta continuation", NOW)
    status = store.status("friend", NOW)
    assert extension["beta_through"] == (NOW + timedelta(days=35)).isoformat()
    assert extension["previous_expires_at"] == trial["trial_ends_at"]
    assert status["beta_active"] and status["access_allowed"]
    assert status["access_expires_at"] == status["beta_through"]
    assert status["trial_started_at"] == trial["trial_started_at"]
    assert status["trial_ends_at"] == trial["trial_ends_at"]
    assert status["paid_through"] is None and status["first_payment_at"] is None
    assert status["bonus_points"] == 0
    assert store.status("inviter", NOW)["bonus_points"] == 0
    after = store.export_user("friend", NOW)
    for key in ("payments", "bonus_events", "quotes"):
        assert after[key] == before[key]
    assert after["beta_extensions"] == [{key: value for key, value in extension.items() if key != "duplicate"}]
    assert store.export_user("inviter", NOW)["beta_extensions"] == []


def test_beta_extension_renewal_from_now_after_expiry_and_exact_boundary(store):
    trial = store.start_trial("one", NOW)
    renewed_at = NOW + timedelta(days=40)
    extension = store.grant_beta_extension("one", 5, "renew-expired", "Continue beta", renewed_at)
    expiry = renewed_at + timedelta(days=5)
    assert extension["beta_through"] == expiry.isoformat()
    assert store.status("one", expiry - timedelta(microseconds=1))["access_allowed"]
    assert not store.status("one", expiry)["access_allowed"]
    assert not store.status("one", expiry)["beta_active"]
    assert store.status("one", expiry)["trial_ends_at"] == trial["trial_ends_at"]


def test_beta_extension_stacks_on_later_paid_or_beta_expiry(store):
    paid = pay(store, "one", "payment")
    first = store.grant_beta_extension("one", 5, "free-one", "Continue beta", NOW)
    assert datetime.fromisoformat(first["beta_through"]) == datetime.fromisoformat(paid["paid_through"]) + timedelta(days=5)
    second = store.grant_beta_extension("one", 7, "free-two", "Continue beta", NOW)
    assert datetime.fromisoformat(second["beta_through"]) == datetime.fromisoformat(first["beta_through"]) + timedelta(days=7)
    assert store.status("one", NOW)["paid_through"] == paid["paid_through"]


def test_beta_extension_idempotent_under_concurrent_retries_and_mismatch_rejected(store):
    store.ensure_user("one", NOW)
    store.ensure_user("two", NOW)
    with ThreadPoolExecutor(max_workers=6) as workers:
        grants = list(workers.map(lambda _: AccessBillingStore(store.path).grant_beta_extension(
            "one", 10, "same-request", "Free beta", NOW), range(12)))
    assert sum(not row["duplicate"] for row in grants) == 1
    assert store.status("one", NOW)["beta_through"] == (NOW + timedelta(days=10)).isoformat()
    assert len(store.export_user("one", NOW)["beta_extensions"]) == 1
    duplicate = store.grant_beta_extension("one", 10, "same-request", "Free beta", NOW + timedelta(days=99))
    assert duplicate["duplicate"] and duplicate["granted_at"] == NOW.isoformat()
    for user, days, reason in [("two", 10, "Free beta"), ("one", 11, "Free beta"), ("one", 10, "Changed")]:
        with pytest.raises(AccessBillingError):
            store.grant_beta_extension(user, days, "same-request", reason, NOW)
    assert len(store.export_user("one", NOW)["beta_extensions"]) == 1


def test_beta_extension_failed_status_update_rolls_back_audit_and_retries_cleanly(store):
    store.start_trial("one", NOW)
    before = store.export_user("one", NOW)
    with sqlite3.connect(store.path) as db:
        db.execute("""CREATE TRIGGER fail_beta BEFORE UPDATE OF beta_through ON access_users
                      BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.grant_beta_extension("one", 10, "retryable-grant", "Free beta", NOW)
    assert store.export_user("one", NOW) == before
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER fail_beta")
    assert not store.grant_beta_extension("one", 10, "retryable-grant", "Free beta", NOW)["duplicate"]


def test_distinct_concurrent_beta_grants_add_all_days_without_lost_updates(store):
    store.ensure_user("one", NOW)
    with ThreadPoolExecutor(max_workers=6) as workers:
        list(workers.map(lambda index: store.grant_beta_extension(
            "one", 1, f"distinct-{index}", "Free beta", NOW), range(12)))
    assert store.status("one", NOW)["beta_through"] == (NOW + timedelta(days=12)).isoformat()
    assert len(store.export_user("one", NOW)["beta_extensions"]) == 12


@pytest.mark.parametrize("days,request_id,reason", [(0, "id", "Reason"), (-1, "id", "Reason"),
    (366, "id", "Reason"), (True, "id", "Reason"), (1.5, "id", "Reason"), (1, "", "Reason"),
    (1, "id", ""), (1, "id", "bad\nreason"), (1, "id", "x" * 501)])
def test_invalid_extension_rejected_without_audit(store, days, request_id, reason):
    store.ensure_user("one", NOW)
    before = store.export_user("one", NOW)
    with pytest.raises(AccessBillingError):
        store.grant_beta_extension("one", days, request_id, reason, NOW)
    assert store.export_user("one", NOW) == before


def test_extension_unknown_user_does_not_create_account(store):
    with pytest.raises(AccessBillingError):
        store.grant_beta_extension("typo", 10, "id", "Reason", NOW)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM access_users").fetchone()[0] == 0


def test_read_only_status_and_extension_preview_change_no_bytes_or_permissions(store):
    store.start_trial("one", NOW)
    before = store.path.read_bytes(), store.path.stat().st_mode
    reader = AccessBillingStore(store.path, read_only=True)
    assert reader.known_status("one", NOW)["trial_started"]
    preview = reader.preview_beta_extension("one", 10, "planned", "Free beta", NOW)
    assert preview["beta_through"] == (NOW + timedelta(days=31)).isoformat()
    assert (store.path.read_bytes(), store.path.stat().st_mode) == before
    with pytest.raises(AccessBillingError):
        reader.grant_beta_extension("one", 10, "planned", "Free beta", NOW)
    assert store.export_user("one", NOW)["beta_extensions"] == []


def old_ledger(path):
    """Previous deployed schema, with already-used trial/payment/bonus markers."""
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE access_users (
            user_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, trial_started_at TEXT,
            trial_ends_at TEXT, paid_through TEXT, first_payment_at TEXT,
            bonus_points INTEGER NOT NULL DEFAULT 0, balance_version INTEGER NOT NULL DEFAULT 0,
            referral_code TEXT NOT NULL UNIQUE, inviter_id TEXT REFERENCES access_users(user_id))""")
        db.execute("INSERT INTO access_users VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   ("old", NOW.isoformat(), NOW.isoformat(), (NOW + timedelta(days=21)).isoformat(),
                    (NOW + timedelta(days=51)).isoformat(), NOW.isoformat(), 25, 4, "opaque-old-code", None))
    path.chmod(0o600)


def test_old_schema_preview_unchanged_and_concurrent_migration_preserves_history(tmp_path):
    path = tmp_path / "old.sqlite3"
    old_ledger(path)
    original = path.read_bytes()
    reader = AccessBillingStore(path, read_only=True)
    before = reader.known_status("old", NOW)
    assert before["beta_through"] is None and not before["beta_active"]
    assert reader.preview_beta_extension("old", 5, "new-request", "Free beta", NOW)["beta_through"] == (NOW + timedelta(days=56)).isoformat()
    assert path.read_bytes() == original
    with ThreadPoolExecutor(max_workers=6) as workers:
        statuses = list(workers.map(lambda _: AccessBillingStore(path).known_status("old", NOW), range(12)))
    assert all(status == before for status in statuses)
    reopened = AccessBillingStore(path)
    assert reopened.status("old", NOW) == before
    with sqlite3.connect(path) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(access_users)")]
        assert columns.count("beta_through") == 1
        assert db.execute("SELECT balance_version, referral_code FROM access_users WHERE user_id = 'old'").fetchone() == (4, "opaque-old-code")
    extension = reopened.grant_beta_extension("old", 5, "new-request", "Free beta", NOW)
    assert extension["beta_through"] == (NOW + timedelta(days=56)).isoformat()
