# Два бота: staging и production

Этот порядок отделяет тестирование версии от рабочего polling-процесса. Git
ветка сама по себе не изолирует токены, OAuth-аккаунты, Calendar или state.
Production и staging должны быть двумя отдельными чистыми checkout/worktree,
двумя invite-only bot tokens, двумя state-каталогами, двумя Google-каталогами и
разными disposable Google/Telegram accounts. Для каждого bot token работает
только один polling-процесс. Гайд описывает целевую настройку: он не означает,
что новые боты, аккаунты, Keychain entries или branch rules уже созданы.

Production и staging — постоянные ветки `codex/production` и `codex/staging`.
В текущем setup GitHub-ветка `codex/production` создана от опубликованного
`origin/main` на `afbb7d8`; локальная `codex/staging` основана на подготовленной
работе, её публикация ещё впереди. `main` не изменяйте. Production baseline сам
по себе не доказывает готовность для реальных пользователей. Staging должен
включать пять Markdown-переименований из
`origin/main`. Текущая staging working tree может содержать незакоммиченные
изменения: перед приёмкой зафиксируйте только подготовленный результат в commit
и тестируйте чистый точный SHA. Не переносите секреты или runtime state между
checkout; не подставляйте вместо подготовленного SHA догадку.

Для отдельного production worktree используйте уже созданную локальную ветку;
если она уже прикреплена к worktree, продолжайте в существующем каталоге:

```bash
set -eu
git fetch origin main
test "$(git rev-parse --short=7 origin/main)" = afbb7d8
git worktree add ../personalos-production codex/production
git -C ../personalos-production status --short
git -C ../personalos-production rev-parse HEAD
```

Проверьте staging worktree, интегрируйте опубликованный `origin/main`, если это
ещё не сделано, и разрешите конфликты пяти переименований. Не выполняйте merge
из dirty staging tree: сначала зафиксируйте только проверенные подготовленные
изменения и убедитесь, что рабочая копия чистая.

```bash
set -eu
test "$(git branch --show-current)" = codex/staging
git branch --show-current
git status --short
git rev-parse HEAD
test -z "$(git status --porcelain)"
git fetch origin main
test "$(git rev-parse --short=7 origin/main)" = afbb7d8
git merge origin/main
git status --short
git diff --check
```

Если `origin/main` продвинулся после подтверждения baseline, оператор должен
сначала сверить новый SHA и повторно оценить интеграцию. Не обновляйте production
при push в staging. Сначала заведите короткую ветку
`codex/feature/<name>` от staging и откройте PR в `codex/staging`; не отправляйте
feature PR напрямую в production или `main`.

## Раздельные процессы и секреты

Создайте отдельное виртуальное окружение в каждом checkout. Для production
установите runtime manifest; для staging — manifest тестов, который включает
runtime-зависимости. Для Mac можно хранить bot tokens в Keychain;
создайте там запись сервиса `PersonalOS/production/telegram-bot` с account
`personalos-production` и запись сервиса `PersonalOS/staging/telegram-bot` с
account `personalos-staging`. Пример получает токен в память процесса и не
вписывает его значение в команду. На Linux используйте уже настроенный secret
manager; готовой службы или передачи credentials в репозитории нет.

**Production-команды ниже применяются только после первой проверенной staging
promotion, когда manifests попали в `codex/production`.** Исходный baseline
`afbb7d8` не содержит `requirements-closed-beta*.txt`: до promotion production
ветка является только снимком кода. Не устанавливайте из неё environment и не
запускайте production bot.

Откройте отдельный терминал для каждой среды. Задайте отличающиеся абсолютные
приватные пути вне обоих checkout и загрузите правильный token для этой среды:

```bash
# Терминал production; пути заменить на реальные приватные каталоги.
set -eu
cd "/path/to/personalos-production"
test -f scripts/invited_beta_bot.py
test -f requirements-closed-beta.txt
export BETA_VENV_DIR="/absolute/private/personalos/production/venv"
export PERSONAL_OS_BETA_DIR="/absolute/private/personalos/production/beta"
export PERSONAL_OS_GOOGLE_DIR="/absolute/private/personalos/production/google"
export TELEGRAM_BOT_TOKEN="$(security find-generic-password -s 'PersonalOS/production/telegram-bot' -a 'personalos-production' -w)"
umask 077
mkdir -p "$(dirname "$BETA_VENV_DIR")"
python3.12 -m venv "$BETA_VENV_DIR"
"$BETA_VENV_DIR/bin/python" -m pip install -r requirements-closed-beta.txt
export PATH="$BETA_VENV_DIR/bin:$PATH"
```

```bash
# Отдельный терминал staging.
set -eu
cd "/path/to/personalos-staging"
test -f scripts/invited_beta_bot.py
test -f requirements-closed-beta-test.txt
export BETA_VENV_DIR="/absolute/private/personalos/staging/venv"
export PERSONAL_OS_BETA_DIR="/absolute/private/personalos/staging/beta"
export PERSONAL_OS_GOOGLE_DIR="/absolute/private/personalos/staging/google"
export TELEGRAM_BOT_TOKEN="$(security find-generic-password -s 'PersonalOS/staging/telegram-bot' -a 'personalos-staging' -w)"
umask 077
mkdir -p "$(dirname "$BETA_VENV_DIR")"
python3.12 -m venv "$BETA_VENV_DIR"
"$BETA_VENV_DIR/bin/python" -m pip install -r requirements-closed-beta-test.txt
export PATH="$BETA_VENV_DIR/bin:$PATH"
```

Приглашайте в staging только тестовые private chat IDs. OAuth проходите заново
для отдельных staging Google accounts и файлов токенов; production credentials
или state не копируйте в staging. Для обоих checkout выполните из корня проекта:

```bash
set -eu
umask 077
mkdir -p "$PERSONAL_OS_BETA_DIR" "$PERSONAL_OS_GOOGLE_DIR"
PYTHONDONTWRITEBYTECODE=1 python3 scripts/invited_beta_bot.py check
PYTHONDONTWRITEBYTECODE=1 python3 scripts/invited_beta_bot.py run
```

`check` проверяет только локальные настройки, `run` запускает polling. Сначала
запускайте staging и проверяйте его disposable accounts; production работает
только в своем checkout с собственным token/state/Google каталогом. Не запускайте
один token в обоих терминалах. Не выполняйте `pull`, checkout или restart в
production worktree как реакцию на staging push.

## Проверка и promotion

Каждый PR сначала направляется в staging. До ручной проверки CI тестирует
кандидат. Тестируйте после merge все изменения на точном SHA staging: запишите
`git rev-parse HEAD`, выполните локальный preflight из
[quick-start](CLOSED_TEST_QUICKSTART.md), затем проведите ручную sandbox-приёмку
двух тестовых аккаунтов на этом же SHA. Если staging изменился, SHA меняется и
приёмку повторяют. После теста не добавляйте коммиты в принятый кандидат.

Затем откройте PR `codex/staging` → `codex/production`. Ревьюер проверяет, что
production включает проверенный кандидат, CI прошёл на итоговом commit SHA,
ручная sandbox-приёмка относится к тому же SHA, и отдельно принимает решение о
релизе. После merge запишите фактический production SHA и дождитесь
`Closed beta tests` для этого SHA. Если SHA отличается от проверенного staging
commit, запустите staging/test bot на точном production SHA с test token/state,
повторите preflight и короткую sandbox smoke-проверку до production release.
Для этого остановите текущий staging poller и откройте detached worktree на
`RELEASE_SHA`; используйте только staging environment, token, state, Google
tokens и тестовые аккаунты:

```bash
set -eu
RELEASE_SHA="$(git -C /path/to/personalos-production rev-parse HEAD)"
test -n "$RELEASE_SHA"
git worktree add --detach ../personalos-release-smoke "$RELEASE_SHA"
cd ../personalos-release-smoke
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
PYTHONDONTWRITEBYTECODE=1 python3 scripts/invited_beta_bot.py check
PYTHONDONTWRITEBYTECODE=1 python3 scripts/invited_beta_bot.py run
```

Не запускайте production bot, пока CI, commit review и smoke/acceptance gates не
относятся к этому commit. Не считайте имя ветки или зелёный статус старого commit
достаточным доказательством.

Локальный `.github/workflows/closed-beta.yml` определяет job/status check с
именем `Closed beta tests` для push в staging/production и PR в staging/production.
После публикации workflow дождитесь первого GitHub run и добавьте это имя в
required checks. Настройте для обеих постоянных веток PR review, запрет
force-push и удаления; отдельно подтвердите реальные branch rules в GitHub,
поскольку наличие workflow само по себе их не включает. Оставьте `main` без
прямых push.

## Запуск релиза и rollback

Для production разрешён только явный операторский запуск на SHA, принятом PR и
проверенном в staging:

1. Остановите production bot через Ctrl-C и дождитесь выхода poller.
2. Сделайте остановленную полную копию production beta-state и отдельную копию
   Google-token directory по [quick-start backup procedure](CLOSED_TEST_QUICKSTART.md).
3. Сверьте release SHA с проверенным SHA, обновите только production checkout,
   загрузите production environment и выполните `invited_beta_bot.py check`.
4. Запустите один production `invited_beta_bot.py run`. Не запускайте второй
   poller и не меняйте тестовый staging процесс.

Полная копия возвращает только локальные файлы и не отменяет уже записанные
или частично записанные события Google Calendar.

Если нужен rollback, остановите production и запишите текущий и предыдущий SHA.
Восстановите остановленные полные копии beta-state и Google tokens в новые
приватные каталоги, затем запускайте предыдущий точный SHA только с этими
совместимыми state/token paths и прежним production bot token. Не понижайте код
автоматически поверх изменённой схемы и не объединяйте backup с текущими данными.
Сначала оператор проверяет совместимость; при сомнении сохраняйте обе копии и
остановитесь для ручной проверки. Перед запуском старого runtime оператор должен
сверить удалённые проекции с локальными журналами и backup, разобрать uncertain
writes и вручную согласовать расхождения. Не удаляйте Calendar events как часть
автоматического rollback.

Общий class-calendar recovery рассчитан только на неизменённый подтверждённый
plan из занятий без active/unknown подготовки. Смешанный plan, изменившийся
источник, неясная ownership-привязка или ошибка чтения требуют остановки и
оператора; recovery не является общим мигратором. Synthetic проверки и история
promotion описаны в [runbook](CLOSED_BETA_RUNBOOK.md) и
[manual acceptance checklist](P2_MANUAL_ACCEPTANCE_CHECKLIST.md); production
провайдеры и rollback на реальном аккаунте требуют отдельной sandbox-приёмки.
