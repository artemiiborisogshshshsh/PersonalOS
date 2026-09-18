from services.user_state_migration import migrate_missing_user_state


def test_migration_copies_only_missing_portable_state(tmp_path):
    legacy = tmp_path / 'checkout-data' / '42'
    target = tmp_path / 'application-support' / '42'
    legacy.mkdir(parents=True)
    target.mkdir(parents=True)
    (legacy / 'attendance_preferences.json').write_text('{"subjects": {}}')
    (legacy / 'work_draft_operations.json').write_text('{"operations": []}')
    (legacy / 'work_planning_state.json').write_text('{"lessons": {}}')
    (legacy / 'update_all.json').write_text('{"version": 1}')
    (legacy / 'google-token.json').write_text('secret')
    (target / 'attendance_preferences.json').write_text('{"subjects": {"new": {}}}')

    migrated = migrate_missing_user_state(legacy, target)

    assert migrated == [
        'work_draft_operations.json', 'work_planning_state.json', 'update_all.json',
    ]
    assert '"new"' in (target / 'attendance_preferences.json').read_text()
    assert not (target / 'google-token.json').exists()
