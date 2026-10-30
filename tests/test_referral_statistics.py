"""Lifetime referral aggregates and internal labels on the existing access ledger."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from services.access_billing import AccessBillingError, AccessBillingStore
from tests.test_access_billing import old_ledger, pay


NOW = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return AccessBillingStore(tmp_path / 'shared' / 'access.sqlite3')


def owner(store, name, code):
    store.ensure_user(name, NOW)
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE access_users SET referral_code = ? WHERE user_id = ?', (code, name))
    return code


def invite(store, owner_code, name, *, published=False):
    store.bind_referral(name, owner_code, NOW)
    if published:
        store.start_trial(name, NOW)


def test_counts_unique_bound_users_and_first_publication_not_payment_or_beta(store):
    code = owner(store, 'owner-sensitive', 'code-owner')
    invite(store, code, 'published-friend', published=True)
    invite(store, code, 'paid-but-unpublished-friend')
    invite(store, code, 'beta-but-unpublished-friend')
    pay(store, 'paid-but-unpublished-friend', 'verified-first-payment', now=NOW)
    store.grant_beta_extension('beta-but-unpublished-friend', 10, 'manual-grant', 'Free beta', NOW)
    before = store.referral_statistics('owner-sensitive')
    assert before == {'invited': 3, 'published': 1}
    for index in range(5):
        store.start_trial('published-friend', NOW + timedelta(days=index))
        with pytest.raises(AccessBillingError):
            store.bind_referral('published-friend', code, NOW)
    assert store.referral_statistics('owner-sensitive') == before
    store.start_trial('paid-but-unpublished-friend', NOW + timedelta(days=1))
    assert store.referral_statistics('owner-sensitive') == {'invited': 3, 'published': 2}
    # Publication does not award a second payment bonus or consume balances.
    assert store.known_status('owner-sensitive', NOW)['bonus_points'] == 25
    assert store.known_status('paid-but-unpublished-friend', NOW)['bonus_points'] == 10


def test_report_aggregates_duplicate_groups_includes_zero_codes_and_hides_users(store):
    codes = {key: owner(store, f'private-owner-{key}', f'code-{key}') for key in 'ABCD'}
    for index in range(3):
        invite(store, codes['A'], f'private-invite-A-{index}', published=index < 2)
    for index in range(2):
        invite(store, codes['B'], f'private-invite-B-{index}', published=index == 0)
    invite(store, codes['C'], 'private-invite-C-0')
    store.set_referral_label(codes['A'], 'Староста 1', 'Группа 101')
    store.set_referral_label(codes['B'], 'Староста 2', 'Группа 101')
    store.set_referral_label(codes['C'], 'Староста 3', 'Группа 202')
    report = store.referral_report(NOW)
    assert set(report) == {'generated_at', 'totals', 'codes', 'groups'}
    assert report['generated_at'] == NOW.isoformat()
    assert report['totals'] == {'invited': 6, 'published': 3, 'conversion_percent': 50.0}
    assert len(report['codes']) == 10  # Every existing code owner, including invited users.
    assert [row['code'] for row in report['codes'][:3]] == ['code-A', 'code-B', 'code-C']
    by_code = {row['code']: row for row in report['codes']}
    assert by_code['code-A'] == {'code': 'code-A', 'leader': 'Староста 1', 'group': 'Группа 101',
                                  'invited': 3, 'published': 2, 'conversion_percent': 66.67}
    assert by_code['code-D'] == {'code': 'code-D', 'leader': '', 'group': '',
                                  'invited': 0, 'published': 0, 'conversion_percent': 0.0}
    assert report['groups'] == [
        {'group': 'Группа 101', 'invited': 5, 'published': 3, 'conversion_percent': 60.0},
        {'group': 'Группа 202', 'invited': 1, 'published': 0, 'conversion_percent': 0.0},
        {'group': '', 'invited': 0, 'published': 0, 'conversion_percent': 0.0},
    ]
    assert 'private-owner' not in json.dumps(report) and 'private-invite' not in json.dumps(report)
    assert all(set(row) == {'code', 'leader', 'group', 'invited', 'published', 'conversion_percent'}
               for row in report['codes'])
    assert all(isinstance(row['conversion_percent'], float) for row in report['codes'])
    # `now` timestamps the lifetime report; it does not invent cohort history.
    assert store.referral_report(NOW + timedelta(days=365))['totals'] == report['totals']


def test_report_ties_are_ordered_by_published_then_code_and_groups_by_name(store):
    a = owner(store, 'owner-a', 'a-code')
    b = owner(store, 'owner-b', 'b-code')
    c = owner(store, 'owner-c', 'c-code')
    invite(store, a, 'friend-a')
    invite(store, b, 'friend-b', published=True)
    invite(store, c, 'friend-c', published=True)
    store.set_referral_label(a, '', 'A')
    store.set_referral_label(b, '', 'B')
    store.set_referral_label(c, '', 'C')
    report = store.referral_report(NOW)
    assert [row['code'] for row in report['codes'][:3]] == ['b-code', 'c-code', 'a-code']
    assert [row['group'] for row in report['groups'][:3]] == ['B', 'C', 'A']
    zero_codes = [row['code'] for row in report['codes'] if not row['invited']]
    assert zero_codes == sorted(zero_codes)


def test_label_preview_crud_restart_and_clear_keep_referral_bindings(store):
    code = owner(store, 'owner', 'opaque-code')
    invite(store, code, 'friend', published=True)
    before = store.path.read_bytes()
    label = {'code': code, 'leader': 'Мария Иванова', 'group': '41736'}
    assert store.preview_referral_label(code, label['leader'], label['group']) == label
    assert store.path.read_bytes() == before
    assert store.set_referral_label(code, label['leader'], label['group']) == label
    restarted = AccessBillingStore(store.path)
    row = next(row for row in restarted.referral_report(NOW)['codes'] if row['code'] == code)
    assert {key: row[key] for key in ('code', 'leader', 'group')} == label
    assert restarted.set_referral_label(code, 'Новый староста', 'Другая группа') == {
        'code': code, 'leader': 'Новый староста', 'group': 'Другая группа'}
    assert restarted.referral_statistics('owner') == {'invited': 1, 'published': 1}
    assert restarted.clear_referral_label(code) == {'code': code, 'leader': '', 'group': ''}
    assert restarted.clear_referral_label(code) == {'code': code, 'leader': '', 'group': ''}
    row = next(row for row in restarted.referral_report(NOW)['codes'] if row['code'] == code)
    assert row['leader'] == row['group'] == ''
    assert restarted.referral_code('owner') == code
    assert restarted.known_status('friend', NOW)['referral_bound']


def test_user_exports_never_include_internal_labels_codes_or_other_identities(store):
    code = owner(store, 'private-owner', 'private-code')
    invite(store, code, 'friend')
    before_owner = store.export_user('private-owner', NOW)
    before_friend = store.export_user('friend', NOW)
    store.set_referral_label(code, 'Internal leader annotation', 'Internal group annotation')
    assert store.export_user('private-owner', NOW) == before_owner
    assert store.export_user('friend', NOW) == before_friend
    assert 'Internal' not in json.dumps(store.export_user('private-owner', NOW))
    assert 'private-owner' not in json.dumps(store.export_user('friend', NOW))
    assert code not in json.dumps(store.export_user('private-owner', NOW))


def test_read_only_stats_report_and_label_preview_change_no_bytes_or_permissions(store):
    code = owner(store, 'owner', 'existing-code')
    invite(store, code, 'friend', published=True)
    store.set_referral_label(code, 'Leader', 'Group')
    before = store.path.read_bytes(), store.path.stat().st_mode, set(store.path.parent.iterdir())
    reader = AccessBillingStore(store.path, read_only=True)
    assert reader.referral_statistics('owner') == {'invited': 1, 'published': 1}
    assert reader.referral_report(NOW)['totals']['invited'] == 1
    assert reader.preview_referral_label(code, 'Proposed', 'Group') == {
        'code': code, 'leader': 'Proposed', 'group': 'Group'}
    with pytest.raises(AccessBillingError, match='read-only'):
        reader.set_referral_label(code, 'New', 'Group')
    with pytest.raises(AccessBillingError, match='read-only'):
        reader.clear_referral_label(code)
    assert (store.path.read_bytes(), store.path.stat().st_mode, set(store.path.parent.iterdir())) == before


def test_old_schema_reader_uses_empty_labels_without_migrating_and_writer_preserves_history(tmp_path):
    path = tmp_path / 'old.sqlite3'
    old_ledger(path)
    before = path.read_bytes()
    reader = AccessBillingStore(path, read_only=True)
    assert reader.referral_statistics('old') == {'invited': 0, 'published': 0}
    assert reader.referral_report(NOW)['codes'] == [{
        'code': 'opaque-old-code', 'leader': '', 'group': '', 'invited': 0, 'published': 0,
        'conversion_percent': 0.0}]
    assert reader.preview_referral_label('opaque-old-code', 'Leader', 'Group')['group'] == 'Group'
    assert path.read_bytes() == before
    with ThreadPoolExecutor(max_workers=4) as executor:
        stores = list(executor.map(lambda _: AccessBillingStore(path), range(8)))
    writer = stores[0]
    assert writer.set_referral_label('opaque-old-code', 'Leader', 'Group')['leader'] == 'Leader'
    status = writer.known_status('old', NOW)
    assert status['trial_started'] and status['bonus_points'] == 25
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT balance_version FROM access_users WHERE user_id = ?', ('old',)).fetchone() == (4,)
        assert db.execute('SELECT COUNT(*) FROM access_referral_labels').fetchone() == (1,)


def test_unknown_codes_and_users_never_create_accounts_or_labels(store):
    store.ensure_user('existing', NOW)
    before = store.path.read_bytes()
    for operation in (
        lambda: store.referral_statistics('typo'),
        lambda: store.preview_referral_label('unknown', 'Leader', 'Group'),
        lambda: store.set_referral_label('unknown', 'Leader', 'Group'),
        lambda: store.clear_referral_label('unknown'),
    ):
        with pytest.raises(AccessBillingError):
            operation()
    assert store.path.read_bytes() == before
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM access_users').fetchone() == (1,)
        assert db.execute('SELECT COUNT(*) FROM access_referral_labels').fetchone() == (0,)


@pytest.mark.parametrize('bad', ['bad\nlabel', 'bad\tlabel', 'bad\rlabel', 'bad\x7flabel',
                                  'bad\x85label', 'bad\u202elabel', 'x' * 201, ' leading', 'trailing ', None, 12])
@pytest.mark.parametrize('field', ['leader', 'group'])
def test_labels_reject_control_characters_unbounded_and_invalid_values_atomically(store, bad, field):
    code = owner(store, 'owner', 'existing-code')
    store.set_referral_label(code, 'Original leader', 'Original group')
    before = store.path.read_bytes()
    values = {'leader': 'Valid', 'group': 'Valid'}
    values[field] = bad
    for method in (store.preview_referral_label, store.set_referral_label):
        with pytest.raises(AccessBillingError):
            method(code, **values)
    assert store.path.read_bytes() == before


def test_empty_labels_and_exact_200_character_unicode_labels_are_supported(store):
    code = owner(store, 'owner', 'existing-code')
    assert store.set_referral_label(code, '', '') == {'code': code, 'leader': '', 'group': ''}
    assert store.set_referral_label(code, 'И' * 200, 'Г' * 200)['leader'] == 'И' * 200


def test_concurrent_first_publications_are_counted_once_after_restart(store):
    code = owner(store, 'owner', 'existing-code')
    invite(store, code, 'friend')
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: AccessBillingStore(store.path).start_trial('friend', NOW), range(8)))
    restarted = AccessBillingStore(store.path, read_only=True)
    assert restarted.referral_statistics('owner') == {'invited': 1, 'published': 1}
    assert restarted.referral_report(NOW)['totals'] == {'invited': 1, 'published': 1, 'conversion_percent': 100.0}


def test_empty_report_is_read_only_and_has_zero_conversion(store):
    before = store.path.read_bytes()
    assert store.referral_report(NOW) == {'generated_at': NOW.isoformat(),
        'totals': {'invited': 0, 'published': 0, 'conversion_percent': 0.0}, 'codes': [], 'groups': []}
    assert store.path.read_bytes() == before
