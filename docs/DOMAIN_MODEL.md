# Доменная модель Personal OS

## Основные принципы

1. **Чистая предметная область**: никаких отсылок к конкретным технологиям (Google Calendar, Telegram, Obsidian и т.д.)
2. **Разделение знаний и состояния**:
   - Persistent Knowledge (хранится в Obsidian): неизменяемая или медленно меняющаяся информация о мире
   - Operational State (хранится в SQLite): быстро меняющаяся информация о текущем состоянии системы
3. **Явные зависимости**:关系显式建模,避免隐式耦合
4. **Value Objects for shared concepts**: используем объекты-значения для общих атрибутов (время, место, участники)
5. **Избегаем преждевременного наследования**: предпочитаем композицию, пока наследование не демонстрирует четкую выгоду

## Core Entities

### 1. AcademicEvent (базовый концепт)
*Абстракция для любого расписанного академического события*
- `event_id: UUID` - уникальный идентификатор в рамках Personal OS
- `scheduled_occurrence: ScheduledOccurrence` - когда и где происходит (value object)
- `title: string` - краткое описание события
- `description: string` - подробное описание
- `source: Source` - происхождение события (университет, ввод пользователя и т.д.)
- `external_uid: Optional[string]` - идентификатор во внешней системе (если применимо)
- `version: int` - версия события для отслеживания изменений

### 2. ScheduledOccurrence (value object)
*Когда и где происходит событие*
- `starts_at: DateTime` - начало события (с timezone)
- `ends_at: DateTime` - конец события
- `timezone: string` - IANA timezone identifier
- `location: Optional[string]` - аудитория, кабинет или онлайн-ссылка
- `participants: List[Participant]` - список участников (преподаватели, студенты и т.д.)

### 3. Participant (value object)
*Участник события*
- `participant_id: UUID` - уникальный идентификатор участника
- `name: string` - имя участника
- `role: string` - роль (преподаватель, студент, ассистент и т.д.)
- `contact_info: Optional[ContactInfo]` - контактная информация (по желанию)

### 4. ContactInfo (value object)
*Контактная информация участника*
- `email: Optional[string]`
- `phone: Optional[string]`
- `messenger: Optional[MessengerType]` - Telegram, WhatsApp и т.д.
- `handle: Optional[string]` - идентификатор в мессенджере

### 5. Source
*Происхождение события или данных*
- `UNIVERSITY_OFFICIAL` - официальное расписание университета
- `USER_INPUT` - ручной ввод пользователем
- `SYSTEM_GENERATED` - сгенерировано системой (напоминания, блоки подготовки)
- `EXTERNAL_IMPORT` - импорт из внешней системы
- `AI_SUGGESTION` - предложено ИИ-ассистентом

### 6. UniversitySession
*Конкретное академическое событие в университете (лекция, лабораторная, семинар)*
Наследует концепцию от AcademicEvent через композицию:
- `academic_event: AcademicEvent` - базовые свойства события
- `course_id: UUID` - ссылка на курс
- `session_type: SessionType` - тип занятия (LECTURE, LAB, PRACTICE, SEMINAR)
- `teacher_id: Optional[UUID]` - преподаватель (может быть пустым для самостоятельной работы)
- `group_id: Optional[UUID]` - учебная группа (например, 8И41)
- `subgroup_id: Optional[UUID]` - подгруппа внутри группы
- `is_recurring: bool` - является ли повторяющимся событием
- `recurrence_rule: Optional[RecurrenceRule]` - правило повторения (если применимо)

### 7. TutoringLesson
*Занятие со студентом (репетиторство)*
- `academic_event: AcademicEvent` - базовые свойства события
- `student_id: UUID` - ученик
- `tutor_id: UUID` - репетитор (пользователь системы)
- `subject: string` - предмет занятия
- `modality: LessonModality` - очно, онлайн, выездное
- `travel_requirement: Optional[TravelRequirement]` - требования к дороге
- `preparation_materials: List[UUID]` - ссылки на необходимые материалы
- `homework_assigned: Optional[UUID]` - задача на дом (если выдается)

### 8. Course
*Учебный курс*
- `course_id: UUID`
- `title: string` - название курса
- `code: string` - код курса (например, "ОС")
- `department: string` - кафедра или факультет
- `credits: int` - количество кредитов
- `instructors: Set[UUID]` - преподаватели курса
- `prerequisites: Set[UUID]` - необходимые предварительные курсы
- `core_materials: Set[UUID]` - основные учебные материалы (книги, статьи)
- `description: string` - подробное описание курса

### 9. Task
*Что нужно сделать (рабочая единица)*
- `task_id: UUID`
- `title: string` - краткое описание задачи
- `description: string` - подробное описание того, что нужно сделать
- `status: TaskStatus` - TODO, IN_PROGRESS, BLOCKED, DONE, CANCELLED
- `priority: PriorityLevel` - LOW, MEDIUM, HIGH, URGENT
- `effort_estimate: EffortEstimate` - оценка трудоемкости (value object)
- `difficulty: DifficultyLevel` - оценка сложности задачи
- `due_date: Optional[DateTime]` - крайний срок выполнения
- `belongs_to_course: Optional[UUID]` - связь с курсом (если применимо)
- `belongs_to_project: Optional[UUID]` - связь с проектом (если применимо)
- `belongs_to_student: Optional[UUID]` - для личных задач студента
- `generated_from: Optional[UUID]` - ссылка на источник задачи (UniversitySession, TutoringLesson и т.д.)
- `tags: Set[string]` - метки для категоризации и поиска
- `dependencies: Set[UUID]` - задачи, которые должны быть завершены перед началом этой
- `blocked_by: Set[UUID]` - задачи, которые блокируют выполнение этой задачи
- `created_at: DateTime`
- `updated_at: DateTime`

### 10. EffortEstimate (value object)
*Оценка трудоемкости задачи*
- `value: int` - оценка в минутах
- `source: EstimateSource` - источник оценки
- `confidence: float` - уверенность в оценке (0.0-1.0)
- `basis: Optional[string]` - обоснование оценки (исторические данные, экспертное мнение и т.д.)

### 11. EstimateSource
*Источник оценки трудоемкости*
- `DEFAULT_RULE` - оценка по правилу по умолчанию (на основе типа события)
- `HISTORICAL_DATA` - основано на исторических данных выполнения
- `EXPERT_OPINION` - оценка эксперта (преподаватель, ментор)
- `USER_INPUT` - прямая ввод пользователем
- `AI_PREDICTION` - предсказание ИИ-модели
- `GROUP_CONSENSUS` - согласие группы или команды

### 12. PreparationRequirement
*Что должно быть подготово к конкретному академическому событию*
- `requirement_id: UUID`
- `source_session_id: UUID` - ссылка на UniversitySession или TutoringLesson, к которой относится подготовка
- `course_id: UUID` - курс, к которому относится требование
- `session_type: SessionType` - тип сессии (LECTURE, LAB и т.д.)
- `title: string` - описание того, что нужно подготовить (автоматически генерируется или задается пользователем)
- `description: Optional[string]` - подробное описание требований подготовки
- `deadline: DateTime` - время, к которому подготовка должна быть завершена (обычно начало сессии)
- `minimum_duration_minutes: int` - абсолютный минимум времени на подготовку
- `target_duration_minutes: int` - целевое время на подготовку (по политике)
- `priority: PreparationPriority` - насколько критична подготовка для успешного участия в сессии
- `difficulty: DifficultyLevel` - оценка сложности подготовки материала
- `status: RequirementStatus` - PENDING, IN_PROGRESS, SATISFIED, UNSATISFIED
- `preferred_windows: List[TimeWindow]` - предпочтительные временные окна для подготовки
- `dependencies: Set[UUID]` - другие требования, которые должны быть выполнены сначала
- `confidence: float` - уверенность в оценке времени подготовки (0.0-1.0)
- `created_at: DateTime`
- `updated_at: DateTime`

### 13. TimeWindow (value object)
*Предпочтительное временное окно*
- `starts_at: DateTime`
- `ends_at: DateTime`
- `preference_level: float` - насколько предпочтительно это окно (0.0-1.0)

### 14. ScheduleBlock
*Когда именно выполняется работа (время в календаре)*
- `block_id: UUID`
- `task_id: UUID` - ссылка на задачу, которую выполняем
- `requirement_id: Optional[UUID]` - если блок создан для подготовки
- `block_type: BlockType` - TASK_WORK, PREPARATION, BUFFER, TRAVEL, BREAK, FIXED_COMMITMENT
- `starts_at: DateTime` - начало блока
- `ends_at: DateTime` - конец блока
- `duration_minutes: int` - вычисляется как ends_at - starts_at
- `status: BlockStatus` - PLANNED, IN_PROGRESS, COMPLETED, CANCELLED, SKIPPED
- `source: BlockSource` - кто/что создал этот блок (USER, SCHEDULER, AI_SUGGESTION, SYSTEM)
- `locked: bool` - нельзя ли изменить или переместить этот блок (фиксированные обязательства)
- `generated_by: Optional[UUID]` - если создан системой, ссылка на сущность-планировщик
- `calendar_event_id: Optional[string]` - идентификатор во внешнем календаре (Google Calendar)
- `obsidian_note_section_id: Optional[string]` - идентификатор секции в Obsidian заметке
- `created_at: DateTime`
- `updated_at: DateTime`

### 15. ExecutionRecord
*Факт выполнения работы*
- `execution_id: UUID`
- `schedule_block_id: UUID` - ссылка на блок, который был выполнен
- `actual_started_at: DateTime` - когда действительно начали работу
- `actual_ended_at: DateTime` - когда действительно закончили работу
- `actual_duration_minutes: int` - фактически потраченное время
- `planned_duration_minutes: int` - запланированное duration из ScheduleBlock
- `deviation_minutes: int` - фактически - запланировано
- `outcome: ExecutionOutcome` - COMPLETED, PARTIALLY_COMPLETED, SKIPPED, FAILED
- `efficiency_score: Optional[float]` - отношение фактического к запланированному времени (если применимо)
- `notes: Optional[string]` - комментарии выполнения (препятствия, неожиданности и т.д.)
- `recorded_at: DateTime` - когда запись была создана

### 16. BlockType
*Тип блока расписания*
- `TASK_WORK` - работа над конкретной задачей
- `PREPARATION` - подготовка к академическому событию
- `BUFFER` - время перерыва между делами
- `TRAVEL` - время на дорогу между местами
- `BREAK` - обед, отдых, личное время
- `FIXED_COMMITMENT` - неподвижное обязательство (университет, сон, встречи и т.д.)

### 17. BlockSource
*Источник создания блока*
- `USER` - создан вручную пользователем
- `SCHEDULER` - создан детерминированным планировщиком
- `AI_SUGGESTION` - предложено ИИ-ассистентом
- `SYSTEM` - создан автоматически системой (например, буферное время)

### 18. ExecutionOutcome
*Результат выполнения блока*
- `COMPLETED` - работа полностью выполнена как планировалось
- `PARTIALLY_COMPLETED` - работа выполнена parcialmente
- `SKIPPED` - блок был пропущен намеренно
- `FAILED` - работа не удалась из-за ошибок или препятствий

### 19. PreparationPriority
*Приоритет подготовки*
- `CRITICAL` - без подготовки невозможно участвовать в сессии
- `HIGH` - подготовка значительно повышает эффективность участия
- `MEDIUM` - подготовка полезна, но не критична
- `LOW` - подготовка желательна, но не обязательна
- `OPTIONAL` - подготовка完全可选

### 20. DifficultyLevel
*Уровень сложности*
- `VERY_EASY`
- `EASY`
- `MEDIUM`
- `HARD`
- `VERY_HARD`

### 21. PriorityLevel
*Приоритет задачи*
- `LOW`
- `MEDIUM`
- `HIGH`
- `URGENT`

### 22. TaskStatus
*Статус задачи*
- `TODO`
- `IN_PROGRESS`
- `BLOCKED`
- `DONE`
- `CANCELLED`

### 23. RequirementStatus
*Статус требования подготовки*
- `PENDING` - еще не начато
- `IN_PROGRESS` - в процессе выполнения
- `SATISFIED` - требование удовлетворено (подготовка выполнена)
- `UNSATISFIED` - требование не удовлетворено (подготовка не выполнена или выполнена некачественно)

### 24. SessionType
*Тип университетской сессии*
- `LECTURE` - лекция
- `LAB` - лабораторная работа
- `PRACTICE` - практическое занятие
- `SEMINAR` - семинар
- `EXAM` - экзамен или зачет
- `PROJECT_DEFENSE` - защита проекта
- `COURSEWORK` - курсовая работа

### 25. LessonModality
*Формат занятия*
- `IN_PERSON` - очно
- `ONLINE` - онлайн (видеоконференция)
- `HYBRID` - смешанный формат
- ` FIELD_WORK` - полевые работы
- `SELF_STUDY` - самостоятельное изучение

### 26. TravelRequirement
*Требования к дороге*
- `requires_travel: bool` - нужно ли ехать
- `estimated_travel_time_minutes: int` - estimated время в пути
- `transport_mode: TransportMode` - общественный транспорт, пешком, машина и т.д.
- `departure_buffer_minutes: int` - запас времени перед отъездом
- `arrival_buffer_minutes: int` - запас времени после прибытия

### 27. TransportMode
*Способ передвижения*
- `WALKING`
- `BICYCLE`
- `PUBLIC_TRANSPORT`
- `CAR`
- `TAXI`
- `OTHER`

## Value Objects и Enums перечислены выше как часть сущностей.

## Отношения и ограничения

### Ограничения целостности
1. `UniversitySession.course_id` должно ссылаться на существующий `Course`
2. `TutoringLesson.student_id` и `tutor_id` должны ссылаться на существующих пользователей
3. `Task.belongs_to_course`, если указан, должно ссылаться на существующий `Course`
4. `Task.belongs_to_project`, если указан, должно ссылаться на существующий `Project` (определяется отдельно)
5. `Task.generated_from`, если указан, должно ссылаться на существующий `AcademicEvent` (UniversitySession или TutoringLesson)
6. `PreparationRequirement.source_session_id` должно ссылаться на существующий `AcademicEvent`
7. `PreparationRequirement.course_id` должно ссылаться на существующий `Course`
8. `ScheduleBlock.task_id` должно ссылаться на существующий `Task`
9. `ScheduleBlock.requirement_id`, если указано, должно ссылаться на существующий `PreparationRequirement`
10. `ExecutionRecord.schedule_block_id` должно ссылаться на существующий `ScheduleBlock`
11. Все временные значения должны быть в корректном формате DateTime с timezone
12. Для блоков: `ends_at` должно быть после `starts_at`
13. Для требований подготовки: `deadline` должно быть после `created_at`
14. Оценки времени должны быть неотрицательными целыми числами
15. Уровни уверенности должны быть в диапазоне [0.0, 1.0]

### Бизнес-правила
1. Значение `PreparationRequirement.target_duration_minutes` должно быть >= `minimum_duration_minutes`
2. `ExecutionRecord.actual_duration_minutes` должно быть неотрицательным
3. `ScheduleBlock.duration_minutes` должно соответствовать разнице между `ends_at` и `starts_at`
4. BlockType.FIXED_COMMITMENT должен иметь `locked: true` по умолчанию
5. BlockType.PREPARATION должен иметь ссылку на PreparationRequirement (requirement_id не null)
6. Task со статусом DONE не может иметь незавершенных зависимостей
7. PreparationRequirement со статусом SATISFIED должен иметь связанный ExecutionRecord с outcome COMPLETED или PARTIALLY_COMPLETED
8. UniversitySession с is_recurring: true должно иметь валидное recurrence_rule
9. TutoringLesson modality определяет доступные travel_requirement варианты
10. Course prerequisites должны образовывать ациклический граф

## Исторические замечания и решения

### Отказ от наследования AcademicEvent
Мы сознательно избегаем наследования для UniversitySession и TutoringLesson. Вместо этого используем композицию через поле academic_event: AcademicEvent. Это позволяет:
- Избежать ложного сходства (это разные доменные концепции с разным поведением)
- Легко добавлять уникальные атрибуты без влияния на базовый класс
- Поддерживать четкое разделение ответственности
- Позволять совместное использование общих концепций (время, место, участники) без навязывания иерархии

### Источник истины для событий
AcademicEvent не является источником истины для времени/места - это projection из внешних источников или ввода пользователя. Историческая истина хранится в:
- University source (официальное расписание) -> UniversitySession
- User input -> TutoringLesson или ручные задачи
- System generated -> blokи подготовки, напоминания
- External import -> синхронизация с другими системами

Отдельно хранится operational state (когда/где что происходит) в ScheduleBlock и связанных с ним сущностях.

### Подготовка как отдельная концепция
PreparationRequirement отделен от UniversitySession потому что:
- Правила подготовки могут меняться независимо от расписания
- Одна сессия может иметь несколько требований подготовки (например, теорию и практику)
- Требования подготовки могут быть перенесены или изменены без изменения самого события
- Позволяет моделировать сложные сценарии (подготовка к серии связанных событий)

### Выполнение vs Планирование
ExecutionRecord представляет только фактически выполненную работу и никогда не создается на этапе планирования.Это обеспечивает:
- Чистую разницу между тем, что планировалось и что实际上发生了
- Foundation для adaptive learning и улучшения оценок
- Возможность анализа эффективности планирования
- Предотвращение ложной уверенности в планах