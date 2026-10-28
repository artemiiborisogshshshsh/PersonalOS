"""Fixed, actionable provider diagnostics; never expose upstream messages."""
import requests
from googleapiclient.errors import HttpError


def provider_hint(error):
    if type(error).__name__ == 'UniversityScheduleFetchError':
        return 'Не удалось загрузить расписание ТПУ. Последние данные сохранены; повтори запрос позже.'
    if isinstance(error, (TimeoutError, requests.Timeout)):
        return 'Сервис не ответил вовремя. Подожди минуту и повтори запрос.'
    if isinstance(error, requests.ConnectionError):
        return 'Нет связи с внешним сервисом. Повтори запрос через несколько минут.'
    status = getattr(error.resp, 'status', None) if isinstance(error, HttpError) else None
    if getattr(error, 'calendar_unavailable', False):
        status = getattr(error, 'status', None)
    if status == 401:
        return 'Доступ Google истёк. Открой /connect_google, затем /calendar_status.'
    if status == 403:
        return 'Google запретил доступ. Проверь разрешения через /calendar_status; при повторе обратись к оператору.'
    if status in {404, 410}:
        return 'Календарь или событие недоступны. Проверь /calendar_status и /review_calendar; не удаляй подключение.'
    if status == 429:
        return 'Google временно ограничил запросы. Подожди несколько минут и повтори /weekly_preview.'
    if status is not None and 500 <= status <= 599:
        return 'Google Calendar временно недоступен. Повтори /weekly_preview через несколько минут.'
    return ''
