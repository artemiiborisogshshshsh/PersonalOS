#!/usr/bin/env python3
"""Local-only referral group reporting and label maintenance."""

from __future__ import annotations

import argparse
import csv
import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.access_billing import AccessBillingStore  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True,
                        help='Existing private access_billing.sqlite3; no ledger is created')
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('report', help='Show lifetime referral totals and labeled groups')
    search = actions.add_parser('search', help='Find codes by leader or group label')
    search.add_argument('--leader')
    search.add_argument('--group')
    search.add_argument('--unassigned', action='store_true',
                        help='Show rows missing either a leader or group label')
    actions.add_parser('unassigned', help='List codes missing a leader or group label')
    actions.add_parser('labels', help='List codes with a leader or group label')
    label = actions.add_parser('label', help='Preview a label change; write only with --apply')
    label.add_argument('code')
    label.add_argument('--leader', required=True)
    label.add_argument('--group', required=True)
    label.add_argument('--apply', action='store_true')
    clear = actions.add_parser('clearlabel', help='Preview clearing labels; write only with --apply')
    clear.add_argument('code')
    clear.add_argument('--apply', action='store_true')
    export = actions.add_parser('export', help='Write private, no-overwrite CSV snapshots')
    export.add_argument('--codes', type=Path, required=True)
    export.add_argument('--groups', type=Path, required=True)
    return parser.parse_args(argv)


def _text(value):
    return '' if value is None else str(value)


def _normal(value):
    return _text(value).casefold()


def _safe_csv_cell(value):
    """Keep spreadsheet software from evaluating imported text as a formula."""
    value = _text(value)
    probe = value.lstrip()
    while probe and unicodedata.category(probe[0]).startswith('C'):
        probe = probe[1:]
    if (probe[:1] in ('=', '+', '-', '@')
            or value[:1] and (value[0].isspace() or unicodedata.category(value[0]).startswith('C'))):
        return "'" + value
    return value


def _csv_bytes(headers, rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\r\n')
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_safe_csv_cell(value) for value in row])
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def _check_export_path(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if path.exists() or path.is_symlink():
        raise ValueError('Export destination already exists')
    parent = path.parent
    if not parent.is_dir():
        raise ValueError('Export destination folder does not exist')
    # Reject symlinks in the destination path so validation and publication
    # cannot be redirected through a linked folder.
    current = parent
    while current != current.parent:
        if current.is_symlink():
            raise ValueError('Export path contains a symlink')
        current = current.parent
    return path


def _write_csv_pair(first: Path, first_bytes: bytes, second: Path, second_bytes: bytes):
    """Prepare both files privately, publish without overwrite, roll back a partial pair."""
    first = _check_export_path(first)
    second = _check_export_path(second)
    if first == second:
        raise ValueError('CSV destinations must be different')
    temporaries = []
    published = []
    try:
        for target, data in ((first, first_bytes), (second, second_bytes)):
            fd, name = tempfile.mkstemp(prefix='.referral-export-', dir=target.parent)
            temp = Path(name)
            temporaries.append((temp, target))
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        # Hard links are atomic and fail if a destination appeared after the
        # initial check. The files remain on the same filesystem as targets.
        for temp, target in temporaries:
            info = temp.stat()
            os.link(temp, target)
            published.append((target, info.st_dev, info.st_ino))
        for temp, _target in temporaries:
            temp.unlink()
    except BaseException:
        for target, device, inode in reversed(published):
            try:
                current = target.stat(follow_symlinks=False)
                if current.st_dev == device and current.st_ino == inode:
                    target.unlink()
            except OSError:
                pass
        raise
    finally:
        for temp, _target in temporaries:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass


def _filtered(report, *, leader=None, group=None, unassigned=False):
    rows = report.get('codes', [])
    wanted_leader = _normal(leader) if leader is not None else None
    wanted_group = _normal(group) if group is not None else None
    result = []
    for row in rows:
        row_leader = _normal(row.get('leader'))
        row_group = _normal(row.get('group'))
        if wanted_leader is not None and wanted_leader not in row_leader:
            continue
        if wanted_group is not None and wanted_group not in row_group:
            continue
        if unassigned and row_leader and row_group:
            continue
        result.append(row)
    return result


def _print_report(report):
    totals = report.get('totals', {})
    print('Реферальная статистика (срок жизни журнала)')
    print(f"Сформировано: {report.get('generated_at', '—')}")
    print('Всего приглашений: {invited}; первая публикация: {published}; конверсия: {conversion}%'.format(
        invited=totals.get('invited', 0), published=totals.get('published', 0),
        conversion=totals.get('conversion_percent', 0)))
    print('\nКоды:')
    codes = report.get('codes', [])
    if not codes:
        print('  Пока нет кодов.')
    for row in codes:
        print('  {code} | лидер: {leader} | группа: {group} | приглашено: {invited} | '
              'опубликовано: {published} | конверсия: {conversion_percent}%'.format(
                  code=row.get('code', '—'), leader=row.get('leader') or '—',
                  group=row.get('group') or '—', invited=row.get('invited', 0),
                  published=row.get('published', 0), conversion_percent=row.get('conversion_percent', 0)))
    print('\nГруппы:')
    groups = report.get('groups', [])
    if not groups:
        print('  Пока нет групп.')
    for row in groups:
        print('  {group} | приглашено: {invited} | опубликовано: {published} | конверсия: {conversion_percent}%'.format(
            group=row.get('group') or '—', invited=row.get('invited', 0),
            published=row.get('published', 0), conversion_percent=row.get('conversion_percent', 0)))


def _print_rows(rows):
    if not rows:
        print('Совпадений нет.')
        return
    for row in rows:
        print('{code} | лидер: {leader} | группа: {group} | приглашено: {invited} | опубликовано: {published} | {conversion}%'.format(
            code=row.get('code', '—'), leader=row.get('leader') or '—', group=row.get('group') or '—',
            invited=row.get('invited', 0), published=row.get('published', 0),
            conversion=row.get('conversion_percent', 0)))


def _safe_error():
    print('REFERRAL ADMIN FAILED: invalid request, unknown code, unsafe ledger/export path, or storage failure',
          file=sys.stderr)


def _print_label(mode, result):
    print('{mode}: {code} | лидер: {leader} | группа: {group}'.format(
        mode=mode, code=result['code'], leader=result.get('leader') or '—',
        group=result.get('group') or '—'))


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        reader = AccessBillingStore(args.ledger, read_only=True)
        report = reader.referral_report()
        if args.action == 'report':
            _print_report(report)
        elif args.action in {'search', 'unassigned', 'labels'}:
            rows = _filtered(report,
                leader=getattr(args, 'leader', None), group=getattr(args, 'group', None),
                unassigned=args.action == 'unassigned' or getattr(args, 'unassigned', False))
            if args.action == 'labels':
                rows = [row for row in rows if row.get('leader') or row.get('group')]
            _print_rows(rows)
        elif args.action == 'label':
            preview = reader.preview_referral_label(args.code, args.leader, args.group)
            result = preview
            mode = 'preview'
            if args.apply:
                result = AccessBillingStore(args.ledger).set_referral_label(args.code, args.leader, args.group)
                mode = 'applied'
            _print_label(mode, result)
        elif args.action == 'clearlabel':
            preview = reader.preview_referral_label(args.code, '', '')
            result = preview
            mode = 'preview'
            if args.apply:
                result = AccessBillingStore(args.ledger).clear_referral_label(args.code)
                mode = 'applied'
            _print_label(mode, result)
        elif args.action == 'export':
            codes = _csv_bytes(
                ['code', 'leader', 'group', 'invited', 'published', 'conversion_percent', 'generated_at'],
                [[row.get('code'), row.get('leader'), row.get('group'), row.get('invited'),
                  row.get('published'), row.get('conversion_percent'), report.get('generated_at')]
                 for row in report.get('codes', [])])
            groups = _csv_bytes(
                ['group', 'invited', 'published', 'conversion_percent', 'generated_at'],
                [[row.get('group'), row.get('invited'), row.get('published'),
                  row.get('conversion_percent'), report.get('generated_at')]
                 for row in report.get('groups', [])])
            _write_csv_pair(args.codes, codes, args.groups, groups)
            print(f'Экспорт готов: {args.codes} и {args.groups} (файлы доступны только владельцу).')
    except (OSError, sqlite3.Error, ValueError):
        _safe_error()
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
