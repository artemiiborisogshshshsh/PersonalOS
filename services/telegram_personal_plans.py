"""Preview and confirm a personal Google Calendar event from text or speech."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

from services.calendar_availability_service import CalendarAvailabilityService
from services.telegram_main_menu import with_menu_button


EXAMPLE = 'Тренировка завтра в 18:30 на 45 минут'
MONTHS = ('января февраля марта апреля мая июня июля августа сентября октября ноября декабря').split()
DATE = r'сегодня|завтра|послезавтра|\d{1,2}\.\d{1,2}(?:\.\d{4})?|\d{1,2} (?:' + '|'.join(MONTHS) + ')'
UNITS = 'ноль один два три четыре пять шесть семь восемь девять'.split()
TEENS = 'десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать'.split()
NUMBER_WORDS = {word: n for n, word in enumerate(UNITS + TEENS)}
for tens, word in enumerate(('двадцать', 'тридцать', 'сорок', 'пятьдесят'), 2):
    NUMBER_WORDS[word] = tens * 10
    NUMBER_WORDS.update({f'{word} {unit}': tens * 10 + n for n, unit in enumerate(UNITS[1:], 1)})
NUMBER_WORDS['час'] = 1
WORD_NUMBER = '|'.join(sorted(NUMBER_WORDS, key=len, reverse=True))
WHEN = re.compile(
    rf'\b(?P<day>{DATE})\s*,?\s+в\s+(?P<hour>\d{{1,2}}|{WORD_NUMBER})'
    rf'(?::(?P<minute>\d{{2}})|\s+(?P<spoken_minute>{WORD_NUMBER}))?'
    r'(?:\s+(?P<period>утра|дня|вечера|ночи))?(?![\w:])', re.I,
)
DURATION = re.compile(
    rf'\s*,?\s+на\s+(?:(?P<minutes>\d{{1,3}}|{WORD_NUMBER})\s*(?:минут(?:у|ы)?|мин\.?)'
    rf'|(?P<hours>\d{{1,2}}|{WORD_NUMBER})\s*час(?:а|ов)?|час|полчаса)\s*[.!]?$', re.I,
)
PREFIX = re.compile(r'^(?:добавь|добавить|создай|создать|запланируй|запиши)\s+(?:личный план|личное событие|план|событие|встречу)\b[\s,:—-]*', re.I)
HOMEWORK_ACTION = re.compile(
    r'^\s*(?:я\s+)?(?:буду\s+(?:делать|выполнять|решать)|собираюсь\s+делать|запланируй|поставь)\s+'
    r'(?:дз|домашнее задание|домашку)\b', re.I)
HOMEWORK_WORD = re.compile(r'\b(?:дз|домашнее задание|домашку|домашка)\b', re.I)


@dataclass(frozen=True)
class PersonalPlan:
    title: str
    start: datetime
    end: datetime


def _number(value):
    return int(value) if value.isdigit() else NUMBER_WORDS[value.casefold()]


def parse_personal_plan(text, now, zone):
    if not isinstance(text, str) or len(text) > 1000:
        raise ValueError('Запиши одно короткое событие.')
    text = PREFIX.sub('', ' '.join(text.split()))
    matches = list(WHEN.finditer(text))
    if len(matches) != 1:
        raise ValueError('Укажи одну дату и одно время начала.')
    match = matches[0]
    day = match['day'].casefold()
    today = now.astimezone(zone).date()
    relative = {'сегодня': 0, 'завтра': 1, 'послезавтра': 2}
    try:
        if day in relative:
            date = today + timedelta(days=relative[day])
        elif ' ' in day:
            number, month = day.split()
            date = today.replace(month=MONTHS.index(month) + 1, day=int(number))
        else:
            number, month, *year = map(int, day.split('.'))
            date = today.replace(day=number, month=month, year=year[0] if year else today.year)
        hour = _number(match['hour'])
        minute = _number(match['minute'] or match['spoken_minute'] or '0')
        if match['period']:
            if not 1 <= hour <= 12:
                raise ValueError
            period = match['period'].casefold()
            hour = hour % 12 + (12 if period in {'дня', 'вечера'} else 0)
        local = datetime(date.year, date.month, date.day, hour, minute)
        start = local.replace(tzinfo=zone)
        if (start.utcoffset() != local.replace(tzinfo=zone, fold=1).utcoffset()
                or start.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != local):
            raise ValueError
    except (ValueError, OverflowError):
        raise ValueError('Проверь дату и время.') from None
    if not now < start <= now + timedelta(days=366):
        raise ValueError('Выбери будущую дату в пределах года.')
    duration = DURATION.search(text)
    minutes = (_number(duration['minutes']) if duration and duration['minutes'] else
               60 * _number(duration['hours']) if duration and duration['hours'] else
               30 if duration and 'полчаса' in duration[0].casefold() else 60)
    if not 1 <= minutes <= 720 or (duration is None and re.search(r'\bна (?:\d|полтор)', text, re.I)):
        raise ValueError('Укажи длительность числом от 1 до 720 минут.')
    tail_end = duration.start() if duration and duration.start() >= match.end() else len(text)
    title = (text[:match.start()] + ' ' + text[match.end():tail_end]).strip(' ,:;—-.!?')
    if not 1 <= len(title) <= 160 or (duration and duration.start() < match.end()):
        raise ValueError('Укажи название события и поставь длительность в конец.')
    end = (start.astimezone(timezone.utc) + timedelta(minutes=minutes)).astimezone(zone)
    return PersonalPlan(title, start, end)


class TelegramPersonalPlans:
    def __init__(self, app):
        self.app = app
        self.clear()

    def clear(self):
        self.awaiting = False
        self.voice_until = None
        self.pending = None
        self.homework_choices = None
        self.homework_selected = None
        self.homework_pending = None

    @property
    def voice(self):
        return self.voice_until is not None and self.app.now() < self.voice_until

    def entry(self, voice=False):
        self.clear()
        self.awaiting = True
        if voice:
            self.voice_until = self.app.now() + timedelta(minutes=10)
        extra = ('Отправь одно голосовое до 60 секунд. Для расшифровки аудио будет передано Groq. '
                 'Можно также отправить текст.\n' if voice else
                 'Напиши название, дату, время и длительность одним сообщением.\n')
        return with_menu_button(self.app.reply(extra + f'Например: «{EXAMPLE}».\n'
            'Для уже сохранённого ДЗ: «Буду делать ДЗ по математике завтра в 18:30».', [
            [{'text': 'Ввести голосом', 'callback_data': 'personal:voice'}]
        ] if not voice and self.app.voice_plans_enabled else []))

    def _buttons(self, token):
        return [[{'text': 'Записать в Google Calendar', 'callback_data': 'personal:save:' + token}],
                [{'text': 'Исправить', 'callback_data': 'personal:new'},
                 {'text': 'Отмена', 'callback_data': 'personal:cancel:' + token}]]

    def propose(self, text):
        self.pending = self.homework_pending = None
        if HOMEWORK_WORD.search(text):
            return self._choose_homework(text)
        if self.homework_selected is not None:
            lesson_id, revision = self.homework_selected
            return self._homework_time(self.app.learning.store.get_lesson(lesson_id), revision, text)
        zone = ZoneInfo(self.app.profile.load().profile.timezone)
        now = self.app.now()
        try:
            plan = parse_personal_plan(text, now, zone)
        except ValueError as error:
            return with_menu_button(self.app.reply(f'{error} Например: «{EXAMPLE}».'))
        self.awaiting = False
        self.voice_until = None
        token = uuid4().hex
        self.pending = (token, plan, now + timedelta(minutes=10), zone.key)
        end = plan.end.strftime('%H:%M') if plan.start.date() == plan.end.date() else plan.end.strftime('%d.%m %H:%M')
        return with_menu_button(self.app.reply(
            f'Личный план: {plan.title}\n{plan.start:%d.%m.%Y %H:%M}–{end} ({zone.key})\n'
            'Проверь детали. Перед записью проверю пересечения в Google Calendar.',
            self._buttons(token)))

    @staticmethod
    def _eligible(lesson):
        return (lesson and lesson['active'] and not lesson['completed']
                and lesson['homework_state'] == 'assigned')

    def _choose_homework(self, text):
        self.homework_selected = self.homework_choices = None
        self.app.learning.refresh()
        cleaned = DURATION.sub('', WHEN.sub(' ', text))
        subject = re.search(r'\bпо\s+(.+)', cleaned, re.I)
        query = re.findall(r'[а-яёa-z0-9]+', subject[1].casefold()) if subject else []
        lessons = [row for row in self.app.learning.store.lessons() if self._eligible(row)]

        def match(course):
            words = re.findall(r'[а-яёa-z0-9]+', course.casefold())
            return all(any(w == q or min(len(w), len(q)) >= 5 and w[:5] == q[:5]
                           for w in words) for q in query)

        lessons = sorted((row for row in lessons if match(row['course'])), key=lambda row: row['start'])
        if not lessons:
            return with_menu_button(self.app.reply(
                'Не нашёл сохранённого незавершённого ДЗ по этому предмету. Выбери пару и добавь задание.',
                [[{'text': 'Открыть задания и материалы', 'callback_data': 'main:learning'}]]))
        if len(lessons) == 1:
            return self._homework_time(lessons[0], lessons[0]['revision'], text)
        token = uuid4().hex[:16]
        self.homework_choices = (token, lessons, text, self.app.now() + timedelta(minutes=10))
        return self._homework_choice_page(0)

    def _homework_choice_page(self, page):
        token, lessons, _, _ = self.homework_choices
        pages = (len(lessons) + 7) // 8
        if not 0 <= page < pages:
            return with_menu_button(self.app.reply('Такой страницы нет.'))
        zone = ZoneInfo(self.app.profile.load().profile.timezone)
        buttons = [[{'text': f"{row['course'][:40]} · {datetime.fromisoformat(row['start']).astimezone(zone):%d.%m %H:%M}",
                     'callback_data': f'personal:hw_pick:{token}:{index}'}]
                   for index, row in enumerate(lessons[page * 8:(page + 1) * 8], page * 8)]
        if pages > 1:
            buttons.append([{'text': label, 'callback_data': f'personal:hw_page:{token}:{target}'}
                            for label, target in [('← Назад', page - 1), ('Далее →', page + 1)]
                            if 0 <= target < pages])
        return with_menu_button(self.app.reply(f'Какое сохранённое ДЗ запланировать? {page + 1}/{pages}', buttons))

    def _homework_time(self, lesson, revision, text):
        if not self._eligible(lesson) or lesson['revision'] != revision:
            self.clear()
            return with_menu_button(self.app.reply('ДЗ изменилось. Выбери его заново.'))
        self.homework_selected = (lesson['id'], revision)
        self.awaiting = True
        minutes = lesson['remaining_minutes'] or lesson['duration_minutes']
        if not WHEN.search(text):
            return with_menu_button(self.app.reply(
                f'Нашёл ДЗ: {lesson["course"]}. На подготовку — {minutes} мин. '
                'Когда начать? Например: «Завтра в 18:30».',
                [[{'text': 'Ввести голосом', 'callback_data': 'personal:hw_voice'}]]
                if self.app.voice_plans_enabled and not self.voice else []))
        zone = ZoneInfo(self.app.profile.load().profile.timezone)
        now = self.app.now()
        try:
            schedule_text = 'Подготовка ' + text
            plan = parse_personal_plan(schedule_text, now, zone)
            if plan.start.minute % 5:
                raise ValueError('Для подготовки выбери минуты, кратные 5, например 18:30.')
            rounded = (minutes + 4) // 5 * 5
            if DURATION.search(text) and plan.end - plan.start != timedelta(minutes=rounded):
                return with_menu_button(self.app.reply(
                    f'Для этого ДЗ сохранено {minutes} мин. Укажи только время начала или измени оценку времени.',
                    self.app.learning._homework_controls(lesson)))
            finish = plan.start + timedelta(minutes=rounded)
        except ValueError as error:
            return with_menu_button(self.app.reply(str(error) + ' Например: «Завтра в 18:30».'))
        self.awaiting = False
        self.voice_until = None
        token = uuid4().hex
        self.homework_pending = (token, lesson['id'], revision, plan.start, now + timedelta(minutes=10), zone.key)
        return with_menu_button(self.app.reply(
            f'Подготовка к сохранённому ДЗ: {lesson["course"]}\n'
            f'{plan.start:%d.%m.%Y %H:%M} — {finish:%d.%m %H:%M} ({zone.key})\n'
            'Задание и материалы останутся привязаны к этой паре. Записать время?', [
                [{'text': 'Записать подготовку', 'callback_data': 'personal:hw_save:' + token}],
                [{'text': 'Отмена', 'callback_data': 'personal:hw_cancel:' + token}],
            ]))

    def _homework_callback(self, data):
        if data == 'personal:hw_voice' and self.homework_selected and self.app.voice_plans_enabled:
            self.voice_until = self.app.now() + timedelta(minutes=10)
            return with_menu_button(self.app.reply(
                'Отправь голосовое до 60 секунд. Аудио будет передано Groq для расшифровки. '
                'Назови дату и время, например «завтра в 18:30».'))
        pick = re.fullmatch(r'personal:hw_(pick|page):([0-9a-f]{16}):([0-9]{1,5})', data)
        if pick and self.homework_choices:
            token, lessons, text, expiry = self.homework_choices
            if pick[2] == token and self.app.now() < expiry:
                index = int(pick[3])
                if pick[1] == 'page':
                    return self._homework_choice_page(index)
                if index < len(lessons):
                    self.homework_choices = None
                    selected = lessons[index]
                    return self._homework_time(self.app.learning.store.get_lesson(selected['id']),
                                               selected['revision'], text)
        if self.homework_pending:
            token, lesson_id, revision, start, expiry, zone = self.homework_pending
            if data == 'personal:hw_cancel:' + token:
                self.clear()
                return with_menu_button(self.app.reply('Запись подготовки отменена.'))
            if data == 'personal:hw_save:' + token and self.app.now() < min(expiry, start):
                lesson = self.app.learning.store.get_lesson(lesson_id)
                if (self._eligible(lesson) and lesson['revision'] == revision
                        and self.app.profile.load().profile.timezone == zone):
                    return self._save_homework_slot(lesson, start)
        return with_menu_button(self.app.reply('Этот выбор ДЗ устарел. Начни заново.'))

    def _save_homework_slot(self, lesson, start):
        from services.published_plan_updates import PlanConflict
        self.homework_pending = None
        self.awaiting = True
        if not self.app.updates:
            return with_menu_button(self.app.reply('Сначала опубликуй учебный план.'))
        try:
            result = self.app.updates.sync_learning(homework_slot=(lesson['id'], start))
        except PlanConflict as error:
            if any(op.projection_pending for op in self.app.operations.load_all().values()):
                return with_menu_button(self.app.reply(str(error) + ' Проверь сохранённое обновление.', [
                    [{'text': 'Проверить план', 'callback_data': 'main:plan'}]
                ]))
            return with_menu_button(self.app.reply(str(error), self.app.learning._homework_controls(lesson)))
        except Exception:
            return with_menu_button(self.app.reply(
                'Запись подготовки не подтверждена. Проверь план перед повтором.',
                [[{'text': 'Проверить план', 'callback_data': 'main:plan'}]]))
        if result and 'Google Calendar обновлён.' not in result:
            return with_menu_button(self.app.reply(result))
        self.clear()
        self.app.learning.refresh()
        return with_menu_button(self.app.reply(
            f'Подготовка к ДЗ «{lesson["course"]}» стоит в Google Calendar на {start:%d.%m.%Y в %H:%M}.'))

    def callback(self, data):
        if data.startswith('personal:hw_'):
            return self._homework_callback(data)
        if data == 'personal:new':
            return self.entry()
        if data == 'personal:voice' and self.app.voice_plans_enabled:
            return self.entry(voice=True)
        if not self.pending:
            return with_menu_button(self.app.reply('Черновик устарел. Открой «Добавить личный план».'))
        token, plan, expiry, zone = self.pending
        if data == 'personal:cancel:' + token:
            self.clear()
            return with_menu_button(self.app.reply('Личный план отменён.'))
        if data != 'personal:save:' + token:
            return with_menu_button(self.app.reply('Кнопка устарела. Открой актуальный черновик.'))
        now = self.app.now()
        if now >= expiry or now >= plan.start or self.app.profile.load().profile.timezone != zone:
            self.clear()
            return with_menu_button(self.app.reply('Время или настройки изменились. Создай новый черновик.'))
        try:
            if not self.app._connected():
                return with_menu_button(self.app.reply('Подключи Google Calendar и повтори подтверждение.', [
                    [{'text': 'Подключить Google Calendar', 'callback_data': 'main:connect_google'}],
                    *self._buttons(token),
                ]))
            adapter = self.app.adapter
            existing = adapter.get_event_by_id('primary', token, strict=True)
            if existing is not None:
                interval = CalendarAvailabilityService._interval(existing)
                if (existing.get('status') == 'cancelled' or interval != (plan.start, plan.end)
                        or existing.get('summary') != plan.title):
                    self.clear()
                    return with_menu_button(self.app.reply('Событие уже изменилось в Google Calendar. Проверь календарь.'))
            else:
                busy = CalendarAvailabilityService(adapter, excluded_calendar_names=()).load(plan.start, plan.end)
                overlaps = [item for item in busy if item.start < plan.end and item.end > plan.start]
                if overlaps:
                    titles = ', '.join(item.title[:65] for item in overlaps[:3])
                    return with_menu_button(self.app.reply(f'В это время уже есть события: {titles}. Выбери другое время.', [
                        [{'text': 'Исправить', 'callback_data': 'personal:new'},
                         {'text': 'Отмена', 'callback_data': 'personal:cancel:' + token}]
                    ]))
                if self.app.now() >= min(expiry, plan.start):
                    self.clear()
                    return with_menu_button(self.app.reply('Время уже изменилось. Создай новый черновик.'))
                adapter.insert_personal_plan(token, plan.title, plan.start, plan.end)
        except Exception:
            return with_menu_button(self.app.reply(
                'Google Calendar не подтвердил запись. Можно повторить подтверждение: бот проверит тот же ID.',
                self._buttons(token)))
        self.clear()
        return with_menu_button(self.app.reply(f'«{plan.title}» записано в основной Google Calendar.', [
            [{'text': 'Добавить ещё', 'callback_data': 'personal:new'}]
        ]))
