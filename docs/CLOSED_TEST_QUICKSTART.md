# Быстрый запуск закрытого теста

Здесь сравнены доступные способы запуска и описан короткий путь для двух
одноразовых тестовых аккаунтов под наблюдением оператора. Это подготовка и
приёмка закрытого теста, не публичный production-деплой. Скрипты бота не читают
`.env`. Передавайте секреты через защищённое окружение или secret manager; не
вписывайте токены в команды, историю shell, репозиторий, логи или каталоги данных.
Для раздельного запуска двух bot tokens и promotion через Git branches см.
[двухботовый release workflow](BOT_RELEASE_WORKFLOW.md).

## Выбор способа запуска

| Вариант | Что запускается | Подготовка и ограничения | Когда выбрать |
|---|---|---|---|
| Mac, foreground | `scripts/invited_beta_bot.py run` в открытом терминале | Быстрее всего запустить и удобно наблюдать. Mac должен быть включён и подключён к сети; оператор перезапускает процесс после сбоя или перезагрузки. Остановка — Ctrl-C. | Первый контролируемый тест, когда оператор рядом. |
| Mac, постоянный supervisor | Та же команда под пользовательским LaunchAgent | Может перезапускать процесс после входа или сбоя, но в репозитории нет PersonalOS plist для LaunchAgent и интеграции с хранилищем секретов. Оператору нужно создать и проверить приватную конфигурацию, не сохраняя токен в доступном другим plist. | После проверенного foreground-теста, когда настроены секреты и восстановление. |
| Linux/VPS, supervisor | Та же Python-команда через systemd или другой supervisor | Нужны защищённый хост, постоянное приватное хранилище, защищённая передача окружения, сеть, резервные копии и оператор. Примеры в `DEPLOYMENT.md` относятся к старым скриптам и `.env`; готовой службы invite-only бота там нет. | Если оператор уже подготовил и обслуживает такой хост. |
| Отдельный бот на студента | По одному `scripts/closed_beta_bot.py` на участника | Каждому нужны отдельные bot token, private chat ID, каталог данных и Google token. Процессы и токены изолированы сильнее, но настройку и контроль придётся повторить для каждого. | Если раздельное владение ботом — обязательное условие теста. |
| Контейнер | Образа PersonalOS в репозитории нет | `docker-compose.yml` описывает только необязательный n8n; Dockerfile и сервис бота отсутствуют. Контейнеризация бота станет отдельной работой по развёртыванию. | Сейчас этот путь недоступен. |

**Рекомендация для двух тестовых аккаунтов:** один новый invite-only test bot,
один foreground-процесс на Mac оператора, два приглашённых private chat и два
отдельных Google-аккаунта с собственными OAuth token files. Это самый короткий
путь при сохранении раздельного состояния и токенов пользователей. Оператор
должен присутствовать и остановить процесс, когда тест не контролируется.
Используйте только тестового бота и выделенные календари.

Отдельный `closed_beta_bot.py` остаётся рабочим вариантом для одного чата и
подходит, если каждому участнику нужны собственные бот и процесс.

## Требования и локальная подготовка

Нужен POSIX-хост Mac или Linux. Python 3.12 использовался для текущих локальных
проверок; другие версии в этом прогоне не проверялись. Код использует `fcntl`,
поэтому Windows не поддерживается. Создайте отдельное окружение и поставьте
зависимости закрытого теста до подключения участников. Не устанавливайте
пакеты во время теста. `requirements-closed-beta.txt` закрепляет runtime
пакеты (`requests`, `icalendar`, `google-auth`, `google-auth-oauthlib`,
`google-api-python-client`); test manifest добавляет pytest и прямую зависимость
тестов. Эти manifests закрепляют перечисленные верхнеуровневые пакеты, но не
фиксируют версии всех транзитивных зависимостей.

Задайте `BETA_VENV_DIR` как абсолютный приватный путь вне checkout. Создайте его
родительский каталог и установите тестовые зависимости:

```bash
BETA_VENV_DIR="/absolute/private/path/personalos-venv"
umask 077
mkdir -p "$(dirname "$BETA_VENV_DIR")"
python3.12 -m venv "$BETA_VENV_DIR"
"$BETA_VENV_DIR/bin/python" -m pip install -r requirements-closed-beta-test.txt
source "$BETA_VENV_DIR/bin/activate"
```

До локальной проверки создайте нового тестового бота и загрузите его token из
защищённого источника в переменную окружения. Укажите следующие настройки.
Примеры путей абсолютные, но их нужно заменить реальными приватными каталогами
вне checkout. Chat ID ниже — шаблоны: замените их полученными числовыми ID.

```bash
export TELEGRAM_BOT_TOKEN  # только после загрузки секрета через защищённый источник
export PERSONAL_OS_BETA_DIR="/Users/<operator>/Library/Application Support/PersonalOS/beta"
export PERSONAL_OS_GOOGLE_DIR="/Users/<operator>/Library/Application Support/PersonalOS/google-tokens"
export GOOGLE_CALENDAR_CREDENTIALS_PATH="/absolute/private/path/oauth-client.json"
BETA_ARCHIVE="/absolute/private/backups/user-state.zip"
BETA_STATE_ARCHIVE="/absolute/private/backups/beta-state.tar.gz"
BETA_GOOGLE_ARCHIVE="/absolute/private/backups/beta-google.tar.gz"
BETA_DOMAIN_RESTORE_DIR="/absolute/private/restore/domain-only"
BETA_RESTORE_DIR="/absolute/private/restore/beta-state"
GOOGLE_RESTORE_DIR="/absolute/private/restore/google-tokens"
BETA_CHAT_ID_1="replace-with-numeric-private-chat-id-1"
BETA_CHAT_ID_2="replace-with-numeric-private-chat-id-2"
```

`TELEGRAM_BOT_TOKEN`, `PERSONAL_OS_BETA_DIR` и `PERSONAL_OS_GOOGLE_DIR` должны
быть экспортированы: их читает процесс бота. `GOOGLE_CALENDAR_CREDENTIALS_PATH`
должен быть экспортирован для одноразовой OAuth-команды. Остальные показанные
переменные с путями и chat ID — обычные переменные текущего shell. Chat ID
передаются CLI как аргументы и не являются секретами. Замените примеры путей и
ID своими значениями; не записывайте значение bot token в файл или команду.

Из корня репозитория выполните единый offline preflight. Он проверяет импорты и
CLI, запускает синтетический набор, создаёт каталоги с приватными правами только
если их ещё нет, и вызывает локальный config check. Внешние API не вызываются.
Если каталоги уже существуют, права не меняются; слишком открытые права приведут
к отказу `check`, который нужно устранить отдельно.

```bash
set -eu
export PYTHONDONTWRITEBYTECODE=1
umask 077
mkdir -p "$PERSONAL_OS_BETA_DIR" "$PERSONAL_OS_GOOGLE_DIR"
python3 -c 'import requests, icalendar, google.auth, google_auth_oauthlib, googleapiclient'
python3 scripts/invited_beta_bot.py --help
python3 scripts/closed_beta_bot.py --help
python3 scripts/user_state_backup.py --help
python3 -m pytest -q \
  tests/test_manual_calendar_resolution.py tests/test_manual_calendar_acceptance.py \
  tests/test_published_plan_deletions.py tests/test_published_plan_updates.py \
  tests/test_invited_beta.py tests/test_invited_beta_concurrency.py \
  tests/test_closed_beta_runtime.py tests/test_closed_beta_startup.py \
  tests/test_google_calendar_adapter.py tests/test_user_state_backup.py \
  tests/test_user_data_lifecycle.py tests/test_telegram_schedule_bot.py \
  tests/test_calendar_projection_recovery.py tests/test_personal_projection_recovery.py \
  tests/test_adaptive_preparation_service.py tests/test_preparation_draft_workflow.py \
  tests/test_preparation_calendar_recovery.py \
  tests/test_published_plan_time_boundaries.py tests/test_planning_horizon.py
python3 scripts/invited_beta_bot.py check
```

Синтетические тесты используют подменённые или synthetic providers. `check`
проверяет только локальную конфигурацию и не подтверждает доступ к Telegram,
TPU или Google, а также корректность OAuth.
По отчёту проверки от 9 октября полный прогон дал 958 passed и 2 subtests
passed. После добавления backup allow-list и resume-регрессии отдельный
итоговый прогон по backup/lifecycle/closed-beta/startup/invite-only прошёл
55 тестов. Это локальные synthetic проверки, они не заменяют live-приёмку из
раздела ниже.

## Подключение двух тестовых аккаунтов

Создайте отдельного бота для теста и передайте доступ только числовым private
chat ID участников. Получите ID доверенным способом. Username и ID группы не
заменяют private user ID.

```bash
python3 scripts/invited_beta_bot.py invite "$BETA_CHAT_ID_1"
python3 scripts/invited_beta_bot.py invite "$BETA_CHAT_ID_2"
```

Для каждого участника вычислите opaque user ID и авторизуйте отдельный Google
аккаунт в отдельный token path. Выполняйте OAuth-команду в интерактивной сессии
оператора и проверьте выбранный Google-аккаунт в браузере перед согласием.
Команда авторизации не синхронизирует события; запущенный бот не открывает
интерактивный OAuth.

```bash
BETA_CHAT_ID="$BETA_CHAT_ID_1"
BETA_USER_ID="$(python3 -c 'import hashlib, sys; print("user-" + hashlib.sha256(sys.argv[1].encode("ascii")).hexdigest()[:24])' "$BETA_CHAT_ID")"
GOOGLE_CALENDAR_TOKEN_PATH="$PERSONAL_OS_GOOGLE_DIR/$BETA_USER_ID.json" \
  python3 scripts/authorize_google_calendar.py
chmod 600 "$PERSONAL_OS_GOOGLE_DIR/$BETA_USER_ID.json"
```

Повторите команды для второго ID. Не используйте bot token, Google-аккаунт,
token file или state от реального пользователя. Разные token files сами по себе
не доказывают, что подключены разные Google-аккаунты.

Данные shared runtime имеют такой вид: `$PERSONAL_OS_BETA_DIR/<user-id>/` хранит
`access.json`, `last_update.json` и возможное состояние доставки; внутри
`$PERSONAL_OS_BETA_DIR/<user-id>/state/` лежат `users_registry.json` и
`users/<user-id>/` с пользовательским состоянием. Google tokens лежат отдельно в
`$PERSONAL_OS_GOOGLE_DIR/<user-id>.json`. Для selective backup указывается
каталог `.../<user-id>/state` как data root, а `user_id` команда добавляет сама.

## Запуск, остановка и перезапуск

Повторно проверьте конфигурацию и запустите ровно один polling process с этим
bot token. Во время теста оставьте терминал открытым и не переводите Mac в сон.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/invited_beta_bot.py check
PYTHONDONTWRITEBYTECODE=1 python3 scripts/invited_beta_bot.py run
```

Для штатной остановки нажмите Ctrl-C и дождитесь выхода процесса. Для restart
выполните ту же команду с теми же окружением и каталогами. Блокировка запрещает
использовать один beta directory двумя процессами; не запускайте второй poller
с этим bot token в другом checkout или на другом хосте. После прерванной команды
результат внешней записи может быть неизвестен. Следуйте сообщению бота и
запросите новый preview; не очищайте журналы и не нажимайте старые кнопки.

Для одного isolated участника экспортируйте `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `PERSONAL_OS_DATA_DIR` и `GOOGLE_CALENDAR_TOKEN_PATH`,
предварительно авторизуйте Google, затем выполните:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/closed_beta_bot.py --check
PYTHONDONTWRITEBYTECODE=1 python3 scripts/closed_beta_bot.py
```

`--check` также работает локально без вызовов провайдеров. Остановка — Ctrl-C;
restart использует те же пути. Не запускайте один state directory в двух
процессах.

## Резервная копия и восстановление

### Выборочная копия состояния одного пользователя

Создайте `$BETA_ARCHIVE` с абсолютным путём вне runtime-каталогов. Остановите
общий процесс и дождитесь завершения операторских invite/revoke-команд перед
backup. Источник для этой CLI-команды — именно
`$PERSONAL_OS_BETA_DIR/$BETA_USER_ID/state`; `user_state_backup.py` читает
вложенную `users/$BETA_USER_ID/` и проверяет opaque ID.

```bash
BETA_CHAT_ID="$BETA_CHAT_ID_1"
BETA_USER_ID="$(python3 -c 'import hashlib, sys; print("user-" + hashlib.sha256(sys.argv[1].encode("ascii")).hexdigest()[:24])' "$BETA_CHAT_ID")"
umask 077
mkdir -p "$(dirname "$BETA_ARCHIVE")"
python3 scripts/user_state_backup.py \
  --data-dir "$PERSONAL_OS_BETA_DIR/$BETA_USER_ID/state" \
  --archive "$BETA_ARCHIVE" backup "$BETA_USER_ID"
```

Восстановите архив только в новый пустой приватный каталог; сначала создайте
его с `umask 077`, чтобы файлы не стали доступными другим пользователям.

```bash
umask 077
BETA_CHAT_ID="$BETA_CHAT_ID_1"
BETA_USER_ID="$(python3 -c 'import hashlib, sys; print("user-" + hashlib.sha256(sys.argv[1].encode("ascii")).hexdigest()[:24])' "$BETA_CHAT_ID")"
test ! -e "$BETA_DOMAIN_RESTORE_DIR"
mkdir -p "$BETA_DOMAIN_RESTORE_DIR/$BETA_USER_ID/state"
python3 scripts/user_state_backup.py \
  --data-dir "$BETA_DOMAIN_RESTORE_DIR/$BETA_USER_ID/state" \
  --archive "$BETA_ARCHIVE" restore "$BETA_USER_ID"
```

Выборочная копия не включает приглашение, polling cursor, транспортные файлы
или OAuth. Allow-list включает `class-calendar-recovery.json`, если он есть, и
восстанавливает его только тому же `BETA_USER_ID`; незавершённое подтверждённое
восстановление сможет продолжиться после restart. Не запускайте восстановленный
runtime на выборочной копии как полноценный restore бота.

### Полное восстановление shared runtime

Остановите polling process и все изменения приглашений. Создайте два архива из
одного остановленного snapshot: весь beta state и отдельную папку Google tokens.
Пути архивов должны находиться вне исходных каталогов. Закройте доступ к ним.

```bash
umask 077
mkdir -p "$(dirname "$BETA_STATE_ARCHIVE")" "$(dirname "$BETA_GOOGLE_ARCHIVE")"
tar -czf "$BETA_STATE_ARCHIVE" -C "$PERSONAL_OS_BETA_DIR" .
tar -czf "$BETA_GOOGLE_ARCHIVE" -C "$PERSONAL_OS_GOOGLE_DIR" .
```

Распакуйте копии в два новых пустых приватных каталога. Экспортируйте runtime
пути на восстановленные каталоги, сохраните тот же защищённый bot token и
проверьте, что исходный poller остановлен. Запустите только один экземпляр.

```bash
umask 077
test ! -e "$BETA_RESTORE_DIR"
test ! -e "$GOOGLE_RESTORE_DIR"
mkdir -p "$BETA_RESTORE_DIR" "$GOOGLE_RESTORE_DIR"
tar -xzf "$BETA_STATE_ARCHIVE" -C "$BETA_RESTORE_DIR"
tar -xzf "$BETA_GOOGLE_ARCHIVE" -C "$GOOGLE_RESTORE_DIR"
chmod -R go-rwx "$BETA_RESTORE_DIR" "$GOOGLE_RESTORE_DIR"
export PERSONAL_OS_BETA_DIR="$BETA_RESTORE_DIR"
export PERSONAL_OS_GOOGLE_DIR="$GOOGLE_RESTORE_DIR"
# В новом shell загрузите TELEGRAM_BOT_TOKEN из защищённого источника.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/invited_beta_bot.py check
PYTHONDONTWRITEBYTECODE=1 python3 scripts/invited_beta_bot.py run
```

Не восстанавливайте поверх запущенного процесса и не объединяйте transport state
с пользовательскими данными из разных копий. После restart старые подтверждения
недействительны; запросите новый `/weekly_preview` и сверьте результат.

## Критерии приёмки двух аккаунтов

Проверяйте по одному сценарию. В отчёт заносите только pass/fail, команду или
сценарий и категорию ошибки без секретов. Не копируйте расписания, сообщения,
ID, токены или OAuth-файлы.

1. **Изоляция:** оба приглашённых чата проходят onboarding; неизвестный чат и
   чужие callback не читают и не меняют состояние другого участника.
2. **Preview без записи:** каждый участник подключает публичную страницу TPU,
   задаёт посещаемость и профиль; preview и отмена не меняют Google Calendar.
3. **Подтверждённая публикация:** проверьте свежий preview и подтвердите его.
   Ожидаемые owned events появляются один раз; старые кнопки не создают дубликаты.
4. **Restart и восстановление:** после restart запросите новый preview и
   проверьте сохранённое состояние. Частичные/неоднозначные отказы моделируйте
   только синтетическими тестами, не отказом записи в live-календаре.
5. **Ручное изменение Calendar:** перенос/удаление проверяйте на выделенном
   календаре. Synthetic regressions exercise recovery through the selected
   invite-only application path. Автоматический recovery ограничен только
   подтверждённым планом из пар: любые подготовки, активные или неизвестные
   проекции, неоднозначная ownership-привязка, изменившийся источник или ошибка
   чтения требуют остановки и проверки оператором. История, сохранённые ручные
   переносы и удаления не сбрасываются. Live deleted-calendar drill остаётся
   отдельным контролируемым sandbox-тестом после проверки отчёта и backup.
6. **Backup/restore:** восстановите остановленный snapshot в новые приватные
   каталоги, проверьте ту же тестовую identity, затем остановите восстановленный
   процесс. Не восстанавливайте данные одного участника другому.
7. **Отзыв доступа:** выполните `revoke` и проверьте, что после возврата команды
   новые обращения участника отклоняются, а второй участник продолжает работу.
   Повторно выдавайте доступ только после записи результата; прежние подтверждения
   должны стать недействительными.
8. **Условие остановки:** остановитесь при неоднозначном ответе провайдера,
   недоступном календаре, неожиданном событии, устаревшем журнале или сомнении в
   identity аккаунта. Сохраните состояние для оператора; не очищайте журналы.

Оператор должен вручную проверить полноту экспорта TPU, выбранную Google identity
и фактическое поведение Telegram/Google. Локальные `check` и synthetic tests не
заменяют эту внешнюю приёмку.

## Диагностика

- Если `check` завершился ошибкой, не переходите к провайдерам. Проверьте имена
  окружения, абсолютные пути, права каталогов и отсутствие совпадения/вложенности
  state и Google directories.
- Если Telegram не отвечает, проверьте numeric private chat ID и доступ в
  invite-list. Убедитесь, что с bot token работает только один poller.
- Если Google не авторизован, остановите сценарий. Оператор должен проверить
  выбранный аккаунт и token path через OAuth; не копируйте token в отчёт или state.
- Если экспорт TPU неполный, не подтверждайте запись. Сохраните состояние и
  попросите оператора проверить публичную страницу группы и полноту экспорта.
- При частичной или неоднозначной операции остановите процесс, сохраните state и
  сделайте backup после остановки. Не очищайте журналы и не повторяйте старые
  кнопки.

Отозвать доступ участника можно так:

```bash
python3 scripts/invited_beta_bot.py revoke "$BETA_CHAT_ID_1"
```

Дождитесь возврата команды: она ждёт завершения работы этого участника и не
может отменить уже отправленный внешний запрос. Повторное приглашение выполняйте
только после фиксации результата; старые подтверждения станут недействительными.

## Возможная автоматизация

Сейчас для двух аккаунтов нужны ручное создание бота, доверенная передача chat
ID, две OAuth-сессии, присутствие оператора у терминала и согласованный backup
после остановки. Наиболее полезен небольшой операторский preflight/provisioning
helper: проверить пути и права, получить opaque ID, показать следующие действия
без секретов и остановиться при неполных или повторных привязках. Не автоматизируйте
согласие OAuth, приглашение участников или запись в Calendar на этапе preflight.
Supervisor и контейнер лучше оформлять отдельной задачей после описания защищённой
передачи секретов, постоянного тома, backup, restart и stop.
