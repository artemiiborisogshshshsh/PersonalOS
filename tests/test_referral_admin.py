import csv
from datetime import datetime, timezone
import hashlib
import io
import os
import sqlite3
from pathlib import Path

import pytest

from scripts import referral_admin
from services.access_billing import AccessBillingStore


NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def seed_ledger(path: Path):
    store = AccessBillingStore(path)
    leader1, leader2 = 'operator-a', 'operator-b'
    code1 = store.referral_code(leader1)
    code2 = store.referral_code(leader2)
    for user in ('a-friend-1', 'a-friend-2'):
        store.bind_referral(user, code1, NOW)
    store.start_trial('a-friend-1', NOW)
    store.bind_referral('b-friend-1', code2, NOW)
    return store, code1, code2


def run_cli(capsys, *args):
    result = referral_admin.main([*map(str, args)])
    out, err = capsys.readouterr()
    return result, out, err


def test_report_search_labels_and_unassigned_are_aggregate_only(tmp_path, capsys):
    ledger = tmp_path / 'access.sqlite3'
    store, code1, code2 = seed_ledger(ledger)
    assert store.set_referral_label(code1, 'Анна', 'Осень') == {
        'code': code1, 'leader': 'Анна', 'group': 'Осень'}

    status, out, err = run_cli(capsys, '--ledger', ledger, 'report')
    assert status == 0 and not err
    assert 'Всего приглашений: 3' in out
    assert code1 in out and code2 in out and 'Анна' in out and 'Осень' in out
    assert 'a-friend' not in out and 'operator-' not in out

    status, out, err = run_cli(capsys, '--ledger', ledger, 'search', '--leader', 'АНН')
    assert status == 0 and not err and code1 in out and code2 not in out
    status, out, err = run_cli(capsys, '--ledger', ledger, 'unassigned')
    assert status == 0 and not err and code2 in out and code1 not in out
    status, out, err = run_cli(capsys, '--ledger', ledger, 'labels')
    assert status == 0 and not err and code1 in out and code2 not in out


def test_label_and_clearlabel_are_preview_only_until_apply(tmp_path, capsys):
    ledger = tmp_path / 'access.sqlite3'
    store, code, _ = seed_ledger(ledger)
    before_report = store.referral_report(NOW)
    status, out, err = run_cli(capsys, '--ledger', ledger, 'label', code,
                               '--leader', 'Лидер', '--group', 'Группа')
    assert status == 0 and not err and 'preview' in out
    assert next(row for row in store.referral_report(NOW)['codes'] if row['code'] == code)['leader'] == ''
    assert before_report == store.referral_report(NOW)

    status, out, err = run_cli(capsys, '--ledger', ledger, 'label', code,
                               '--leader', 'Лидер', '--group', 'Группа', '--apply')
    assert status == 0 and not err and 'applied' in out
    assert next(row for row in store.referral_report(NOW)['codes'] if row['code'] == code)['leader'] == 'Лидер'

    status, out, err = run_cli(capsys, '--ledger', ledger, 'clearlabel', code)
    assert status == 0 and not err and 'preview' in out
    assert next(row for row in store.referral_report(NOW)['codes'] if row['code'] == code)['group'] == 'Группа'
    status, out, err = run_cli(capsys, '--ledger', ledger, 'clearlabel', code, '--apply')
    assert status == 0 and not err and 'applied' in out
    assert store.preview_referral_label(code, '', '') == {'code': code, 'leader': '', 'group': ''}


def test_label_preview_rejects_unknown_code_without_creating_user(tmp_path, capsys):
    ledger = tmp_path / 'access.sqlite3'
    store, _code, _ = seed_ledger(ledger)
    before = store.referral_report(NOW)
    status, out, err = run_cli(capsys, '--ledger', ledger, 'label', 'unknown',
                               '--leader', 'X', '--group', 'Y', '--apply')
    assert status == 1 and not out and 'FAILED' in err
    assert store.referral_report(NOW) == before


def test_empty_report_and_empty_export_are_well_formed(tmp_path, capsys):
    ledger = tmp_path / 'access.sqlite3'
    AccessBillingStore(ledger)
    status, out, err = run_cli(capsys, '--ledger', ledger, 'report')
    assert status == 0 and not err and 'Пока нет кодов' in out and 'Пока нет групп' in out
    codes, groups = tmp_path / 'codes.csv', tmp_path / 'groups.csv'
    status, out, err = run_cli(capsys, '--ledger', ledger, 'export', '--codes', codes, '--groups', groups)
    assert status == 0 and not err and codes.is_file() and groups.is_file()
    assert codes.read_bytes().startswith(b'\xef\xbb\xbf')
    assert groups.read_bytes().startswith(b'\xef\xbb\xbf')
    assert stat_mode(codes) == stat_mode(groups) == 0o600
    assert list(csv.reader(io.StringIO(codes.read_text(encoding='utf-8-sig'))))[1:] == []
    assert list(csv.reader(io.StringIO(groups.read_text(encoding='utf-8-sig'))))[1:] == []


def stat_mode(path):
    return path.stat().st_mode & 0o777


@pytest.mark.parametrize('value', ['=SUM(1,1)', '+cmd', '-1+2', '@link', '  \t=1+1', '\r=2+2',
                                  '\tplain', '\u00a0=1+1', '\x1b=2+2'])
def test_csv_formula_and_control_prefixes_are_escaped(value):
    assert referral_admin._safe_csv_cell(value).startswith("'")


def test_export_is_private_no_overwrite_and_uses_formula_safe_cells(tmp_path, capsys):
    ledger = tmp_path / 'access.sqlite3'
    store, code, _ = seed_ledger(ledger)
    store.set_referral_label(code, '=HYPERLINK("bad")', '+leader')
    codes, groups = tmp_path / 'codes.csv', tmp_path / 'groups.csv'
    args = ('--ledger', ledger, 'export', '--codes', codes, '--groups', groups)
    status, _out, err = run_cli(capsys, *args)
    assert status == 0 and not err
    data = codes.read_text(encoding='utf-8-sig')
    assert "'=HYPERLINK" in data and "'+leader" in data
    original = codes.read_bytes()
    status, _out, err = run_cli(capsys, *args)
    assert status == 1 and 'FAILED' in err
    assert codes.read_bytes() == original


def test_export_prevalidates_distinct_targets_and_rolls_back_partial_publish(tmp_path, monkeypatch):
    one = tmp_path / 'one.csv'
    with pytest.raises(ValueError):
        referral_admin._write_csv_pair(one, b'one', one, b'two')
    assert not one.exists()

    first, second = tmp_path / 'first.csv', tmp_path / 'second.csv'
    real_link = os.link
    calls = 0

    def fail_second(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('simulated second publication error')
        return real_link(source, destination)

    monkeypatch.setattr(referral_admin.os, 'link', fail_second)
    with pytest.raises(OSError):
        referral_admin._write_csv_pair(first, b'one', second, b'two')
    assert not first.exists() and not second.exists()
    assert not list(tmp_path.glob('.referral-export-*'))


def test_read_only_old_schema_report_preserves_database_bytes(tmp_path, capsys):
    ledger = tmp_path / 'legacy.sqlite3'
    store, code, _ = seed_ledger(ledger)
    # Simulate an older ledger: remove only the optional labels table.
    with sqlite3.connect(ledger) as db:
        db.execute('DROP TABLE access_referral_labels')
    ledger.chmod(0o600)
    before = hashlib.sha256(ledger.read_bytes()).digest()
    status, out, err = run_cli(capsys, '--ledger', ledger, 'report')
    assert status == 0 and not err and code in out
    assert hashlib.sha256(ledger.read_bytes()).digest() == before


def test_export_rejects_existing_symlink_and_missing_parent(tmp_path, capsys):
    ledger = tmp_path / 'access.sqlite3'
    AccessBillingStore(ledger)
    target = tmp_path / 'target.csv'
    target.write_text('leave me')
    link = tmp_path / 'link.csv'
    link.symlink_to(target)
    status, _out, err = run_cli(capsys, '--ledger', ledger, 'export', '--codes', link,
                                '--groups', tmp_path / 'groups.csv')
    assert status == 1 and 'FAILED' in err and target.read_text() == 'leave me'
    status, _out, err = run_cli(capsys, '--ledger', ledger, 'export', '--codes', tmp_path / 'absent' / 'codes.csv',
                                '--groups', tmp_path / 'groups.csv')
    assert status == 1 and 'FAILED' in err
