"""Message-attached entry points for the ordinary Telegram user menu."""

MENU = {
    'text': ('Выбери раздел. Предпросмотр плана ничего не меняет в Google Calendar. '
             'Чтобы опубликовать предложенный план, нажми кнопку подтверждения.'),
    'buttons': [
        [{'text': 'Мой план', 'callback_data': 'main:plan'}],
        [{'text': 'Добавить личный план', 'callback_data': 'personal:new'}],
        [{'text': 'Задания и материалы', 'callback_data': 'main:learning'}],
        [{'text': 'Посещаемость', 'callback_data': 'main:attendance'}],
        [{'text': 'Настройки', 'callback_data': 'main:settings'}],
        [{'text': 'Пригласить друзей', 'callback_data': 'main:referral'}],
        [{'text': 'Помощь', 'callback_data': 'main:help'}],
    ],
}


def menu_reply():
    return {'text': MENU['text'], 'buttons': [row[:] for row in MENU['buttons']]}


def with_menu_button(response):
    if not isinstance(response, dict):
        return response
    result = dict(response)
    buttons = [list(row) for row in result.get('buttons', [])]
    if not any(button.get('callback_data') == 'main:open' for row in buttons for button in row):
        buttons.append([{'text': '← Главное меню', 'callback_data': 'main:open'}])
    result['buttons'] = buttons
    return result
