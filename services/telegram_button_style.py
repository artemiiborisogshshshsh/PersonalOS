"""Visual defaults for button labels at the Telegram delivery boundary."""

import re


_NAVIGATION_ARROWS = dict.fromkeys('←‹«', '⬅️') | dict.fromkeys('→›»', '➡️')
_FORWARD_LABELS = ('далее', 'дальше', 'вперёд', 'следующ')
_ATTENDANCE_MARKS = {'☑': '☑️', '☐': '⬜'}
_LEADING_SUCCESS = '✅✓✔☑'
_LEADING_DANGER = '❌✖❎🚫⛔🗑'
# Unicode Emoji_Presentation ranges. VS16 and keycaps are handled separately.
_EMOJI_PRESENTATION = re.compile(
    r'^[\u231a-\u231b\u23e9-\u23ec\u23f0\u23f3\u25fd-\u25fe\u2614-\u2615\u2648-\u2653'
    r'\u267f\u2693\u26a1\u26aa-\u26ab\u26bd-\u26be\u26c4-\u26c5\u26ce\u26d4\u26ea'
    r'\u26f2-\u26f3\u26f5\u26fa\u26fd\u2705\u270a-\u270b\u2728\u274c\u274e\u2753-\u2755'
    r'\u2757\u2795-\u2797\u27b0\u27bf\u2b1b-\u2b1c\u2b50\u2b55'
    r'\U0001f004\U0001f0cf\U0001f18e\U0001f191-\U0001f19a\U0001f1e6-\U0001f1ff'
    r'\U0001f201\U0001f21a\U0001f22f\U0001f232-\U0001f236\U0001f238-\U0001f23a\U0001f250-\U0001f251'
    r'\U0001f300-\U0001f320\U0001f32d-\U0001f335\U0001f337-\U0001f37c\U0001f37e-\U0001f393'
    r'\U0001f3a0-\U0001f3ca\U0001f3cf-\U0001f3d3\U0001f3e0-\U0001f3f0\U0001f3f4\U0001f3f8-\U0001f43e'
    r'\U0001f440\U0001f442-\U0001f4fc\U0001f4ff-\U0001f53d\U0001f54b-\U0001f54e\U0001f550-\U0001f567'
    r'\U0001f57a\U0001f595-\U0001f596\U0001f5a4\U0001f5fb-\U0001f64f\U0001f680-\U0001f6c5'
    r'\U0001f6cc\U0001f6d0-\U0001f6d2\U0001f6d5-\U0001f6d8\U0001f6dc-\U0001f6df\U0001f6eb-\U0001f6ec'
    r'\U0001f6f4-\U0001f6fc\U0001f7e0-\U0001f7eb\U0001f7f0\U0001f90c-\U0001f93a\U0001f93c-\U0001f945'
    r'\U0001f947-\U0001f9ff\U0001fa70-\U0001fa7c\U0001fa80-\U0001fa8a\U0001fa8e-\U0001fac6'
    r'\U0001fac8\U0001facd-\U0001fadc\U0001fadf-\U0001faea\U0001faef-\U0001faf8]'
)
_KEYCAP = re.compile(r'^[0-9#*]\ufe0f?\u20e3')
_TIME = re.compile(r'^(?:\d{1,2}:\d{2}\b|\d+\s*(?:мин|час)\b)')
_DATE = re.compile(r'^(?:\d{1,2}\.\d{1,2}\b|сегодня\b|завтра\b)')

_LABEL_RULES = (
    (('удал', 'сброс', 'очист', 'да, сброс', 'подтвердить удаление'), '🗑️', 'danger'),
    (('отмен', 'отклон', 'отключ', 'отказ', 'откат', 'оставить преж', 'оставить без', 'оставить сейчас'),
     '❌', 'danger'),
    (('не посещ', 'не был', 'пропуст'), '🚫', 'danger'),
    (('посещаю', 'посещать'), '👤', 'success'),
    (('назад', 'вернуться'), '⬅️', 'primary'),
    (_FORWARD_LABELS, '➡️', 'primary'),
    (('подтверд', 'примен', 'сохранить', 'записать', 'опубликовать', 'завершить', 'выполнить',
      'принять', 'создать', 'отправить в', 'восстановить', 'возобновить', 'добавить расписание'), '✅', 'success'),
    (('мой план', 'проверить план', 'показать план', 'предпросмотр плана', 'расписание'), '📅', 'primary'),
    (('задания', 'задачи', 'список задач', 'список заданий', 'мои задачи'), '📋', 'primary'),
    (('материалы', 'сохранённые материалы'), '📚', 'primary'),
    (('настройки', 'окно и лимиты'), '⚙️', 'primary'),
    (('посещаемость', 'открыть посещаемость'), '👥', 'primary'),
    (('главное меню',), '🏠', 'primary'),
    (('помощь',), '❔', 'primary'),
    (('пригласить',), '👋', 'primary'),
    (('добавить',), '➕', 'primary'),
    (('изменить', 'исправить', 'выбрать'), '✏️', 'primary'),
    (('повторить', 'обновить'), '🔄', 'primary'),
    (('режим сна',), '🌙', 'primary'),
    (('время на дорогу',), '🚶', 'primary'),
    (('ввести голосом',), '🎙️', 'primary'),
)


def _appearance(label, button, leading_icon):
    if label[0] in _LEADING_SUCCESS:
        return '✅', 'success'
    if label[0] in _LEADING_DANGER:
        return '❌', 'danger'
    if label[0] in _NAVIGATION_ARROWS or label[0] in '⬅➡':
        return _NAVIGATION_ARROWS.get(label[0], label[0]), 'primary'
    if button.get('url') or button.get('login_url') or button.get('web_app'):
        return '🔗', 'primary'
    if leading_icon:
        label = label.partition(' ')[2].lstrip()
    label = label.casefold()
    if re.match(r'^нет\b', label):
        return '❌', 'danger'
    if _TIME.match(label):
        return '🕒', 'primary'
    if _DATE.match(label):
        return '📅', 'primary'
    for prefixes, icon, style in _LABEL_RULES:
        if label.startswith(prefixes):
            return icon, style
    if re.match(r'^(?:да|был|была)\b', label):
        return '✅', 'success'
    return '🔹', 'primary'


def _style_button(button):
    if not isinstance(button, dict):
        return button
    result = dict(button)
    text = button.get('text')
    if not isinstance(text, str) or not (label := text.lstrip()):
        return result
    lead = label[0]
    leading_icon = _EMOJI_PRESENTATION.match(label) or label[1:2] == '\ufe0f' or _KEYCAP.match(label)
    icon, style = _appearance(label, button, leading_icon)
    index = len(text) - len(label)
    callback = button.get('callback_data')
    forward_label = label.casefold().startswith(_FORWARD_LABELS)
    if lead in _NAVIGATION_ARROWS:
        result['text'] = f'{text[:index]}{icon}{label[1:]}'
    elif forward_label and label.rstrip()[-1] in '→›»':
        end = len(text.rstrip()) - 1
        result['text'] = f'{text[:end]}➡️{text[end + 1:]}'
    elif isinstance(callback, str) and callback.startswith('ate:') and lead in _ATTENDANCE_MARKS and label[1:2] == ' ':
        result['text'] = f'{text[:index]}{_ATTENDANCE_MARKS[lead]}{label[1:]}'
    elif isinstance(callback, str) and callback.startswith('nt:done:') and label.startswith('✓ '):
        result['text'] = f'{text[:index]}✅{label[1:]}'
    elif (not leading_icon and not button.get('icon_custom_emoji_id')
          and not (forward_label and label.rstrip().endswith('➡️'))):
        result['text'] = f'{icon} {text}'
    if button.get('style') not in ('success', 'primary', 'danger'):
        result['style'] = style
    return result


def style_inline_keyboard(buttons):
    """Copy styled inline rows without changing their actions or order."""
    if not isinstance(buttons, (list, tuple)):
        return buttons
    return [[_style_button(button) for button in row] if isinstance(row, (list, tuple)) else row
            for row in buttons]
