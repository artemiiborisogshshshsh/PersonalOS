# Проверенный статус PersonalOS — 13.09.2026

Это не roadmap и не обещание production-готовности. Статусы основаны на
локальном чтении кода и `python3 -m pytest -q`: **459 passed, 2 subtests
passed**. Тесты используют синтетические данные и fake adapters; они не
подтверждают live-доступ к Telegram, Google Calendar, TPU или AlfaCRM.

| Этап | Статус | Подтверждённое состояние |
|---|---|---|
| 1. Аудит и conflict fallback | частично завершён | Свободный слот ищется первым; невозможная учебная/рабочая подготовка создаётся только как `manual_conflict is True`, без перемещения исходной пары. Обычный блок, пересекающий такую подготовку, тоже конфликтный. |
| 2. Источники | частично | TPU и read-only AlfaCRM adapters есть. Нераспознанный AlfaCRM response и структурно неполный ICS не заменяют последний снимок. Семантическая полнота live-снимка не доказана. |
| 3. Единый `/update_all` | частично | Источники → пары → общие подготовки → повторное чтение Calendar → integrity report. Есть межпроцессная advisory-блокировка: ручной повтор отвечает «Обновление уже выполняется», автоматический молчит. |
| 4. Idempotency и recovery | частично | Журналы/checkpoints и stable preparation IDs есть; двусмысленная запись не повторяется вслепую. Реальный provider-level recovery не проверен. |
| 5. Capacity и общий план | частично | Профиль, сетка, commitments и общий workflow учёбы/работы есть. Runtime-flow для проектов, личного и tutoring не завершён. |
| 6. Calendar projection/integrity | частично | Health разделяет диагностику и разрешённые исправления; conflict/moved preparations не удаляются автоматически и остаются в отчёте. |
| 7. Telegram | частично | Команды, preview/confirm/rollback и краткий `/update_all` report есть. Полный кнопочный редактор и multi-user UX не подтверждены. |
| 8. Feedback и AI | частично | Feedback хранится; completed work preparation не восстанавливается. Проверяемый AI-adaptation runtime-flow отсутствует. |
| 9. Свободный текст и голос | частично | Детерминированный interpreter распознаёт поддержанные create intents и возвращает отказ для неясных/destructive команд; он пока не подключён к Telegram preview/confirm, voice handler и transcription adapter отсутствуют. |
| 10. Проекты, личное, tutoring | частично | Доменные компоненты существуют, но единый runtime-flow не подтверждён. |
| 11. Obsidian | частично | Marker sections покрыты тестами; vault/onboarding/runtime-конфликты не проверены end-to-end. |
| 12. Автопроверки | частично | Persisted wall-clock schedule и timezone профиля есть; субботний weekly-plan и пользовательская cadence не завершены. |
| 13. Новые пользователи | отсутствует | Runtime всё ещё опирается на один allowed chat и локальные user directories; per-user OAuth/onboarding не готовы. |
| 14. Runtime и продажа | отсутствует | Есть черновые docs/healthcheck, но нет проверенного deployment, backup/restore, migrations, billing или security/legal review. |

## Закрытые регрессии

- Строго boolean `True`, а не строка `"false"`, `1`, `None` или `False`,
  разрешает маркированное конфликтное пересечение.
- Calendar health не сообщает об отсутствии проблем при неисполненных
  исправлениях; он не удаляет автоматически manual-conflict и moved blocks.
- Ручной перенос и удаление preparation сохраняются как исключения и не
  восстанавливаются при `/update_all` в покрытых fake-Calendar сценариях.
- IDs учебной и рабочей подготовки не зависят от флага конфликта.
- Неполный snapshot источника не считается пустым расписанием и не затирает
  последний проверенный снимок.
- TPU-preview использует подменяемые discovery/read adapters; временный export
  key не сохраняется в пользовательском состоянии.
- Export пользовательских данных использует allow-list state-файлов и
  отклоняет symlink escapes; OAuth, произвольные файлы и внешние пути не
  экспортируются в покрытых сценариях.
- Healthcheck и CLI error paths не выводят raw provider exceptions; direct ICS
  и зарегистрированные удалённые источники требуют HTTPS.
- Google Calendar adapter logs and structured sync errors содержат только
  безопасную операцию и HTTP status, без provider response body или URL.
- Telegram adapter также не выводит bot token, chat ID, API response body или
  raw transport exception в покрытых error paths.
- Intent interpreter больше не превращает все одиночные фразы в `unknown` и
  не создаёт placeholder-запись для неясного/удаляющего/переносящего текста.
- Application layer does not persist or log raw provider exception text and
  traceback when command execution fails.
- Рабочие подготовки могут идти подряд только внутри profile-configured
  continuous series; после 120 минут тестируется обязательный отдых.
- Изменение `/deep_work` сбрасывает также cached work workflow: новая серия
  начинает использовать лимит профиля без перезапуска процесса.
- n8n adapter/CLI не выводят raw webhook URL, workflow identifier, provider
  response или exception text в покрытых success/error paths.
- Telegram не показывает в обработанных ошибках raw текст исключения, поэтому
  URL и токены провайдера не попадают в ответ пользователю в покрытых путях.

## Внешние зависимости

Для честного закрытия следующих этапов нужны отдельные решения или доступ:
voice/transcription provider и privacy review, per-user OAuth/onboarding,
read-only audit real TPU/AlfaCRM coverage, live Calendar/Telegram sandbox,
deployment, backup/restore, платежи и legal review. Они не заменяются mock
тестами и в этой итерации не выполнялись.
