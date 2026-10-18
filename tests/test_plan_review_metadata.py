from copy import deepcopy
from datetime import timedelta

from services.published_plan_updates import decode
from tests.test_published_plan_updates import setup
from tests.test_closed_beta_runtime import NOW


def _preview(tmp_path, transform):
    app, _, _, _ = setup(tmp_path)
    operation = next(iter(app.operations.load_all().values()))
    uid = next(uid for uid, row in operation.published_plan['rows'].items()
               if row['kind'] == 'preparation')
    old = deepcopy(operation.published_plan['rows'][uid])
    target = deepcopy(old)
    transform(target['data'])
    payload = {
        'signature': 'changed-signature',
        'operation': {'version': operation.version + 1},
        'rows': {uid: target},
    }
    before = deepcopy(payload)
    app.updates.build = lambda *_args: payload
    app.updates.guard = lambda *_args: {}

    result = app.updates.preview(operation, app.profile.load(), NOW, [],
                                 'changed-signature', [])
    assert payload == before
    return result['text']


def _restamp_description(data):
    visible, marker, _internal = data['description'].partition('\n\nAI Calendar Operation:')
    assert marker
    data['description'] = (
        visible + '\n\nAI Calendar Operation: operation\n'
        'AI Calendar Block: block\nAI Calendar Source: source\n'
        'AI Calendar Version: 2\nAI Calendar Hash: new-hash\nStatus: confirmed'
    )


def test_metadata_only_preparation_update_is_described_without_fake_move(tmp_path):
    text = _preview(tmp_path, _restamp_description)

    assert 'Обновить служебную информацию: Подготовка:' in text
    assert '(время без изменений).' in text
    assert '↔ Изменить:' not in text
    assert 'AI Calendar Hash' not in text


def test_reallocated_preparation_keeps_detailed_move_preview(tmp_path):
    def reallocate(data):
        start = decode(data).dtstart
        data['dtstart'] = (start + timedelta(minutes=30)).isoformat()
        data['dtend'] = (decode(data).dtend + timedelta(minutes=30)).isoformat()
        _restamp_description(data)

    text = _preview(tmp_path, reallocate)

    assert '↔ Изменить: Подготовка:' in text
    assert 'Обновить служебную информацию:' not in text


def test_human_description_change_keeps_detailed_description_preview(tmp_path):
    def edit_description(data):
        visible, marker, internal = data['description'].partition('\n\nAI Calendar Operation:')
        assert marker
        data['description'] = visible + '\nДополнительная информация' + marker + internal

    text = _preview(tmp_path, edit_description)

    assert '↔ Изменить: Подготовка:' in text
    assert 'Описание:' in text
    assert 'Дополнительная информация' in text
