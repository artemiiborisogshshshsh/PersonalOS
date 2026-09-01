#!/usr/bin/env python3
"""
Demo script showing integration of all three domain services:
University, Project, and Knowledge
"""

from datetime import datetime, timedelta
from services.university_service import UniversityService
from services.project_service import ProjectService
from services.knowledge_service import KnowledgeService
from planning_engine import PlanningEngine
from models import ProjectStatus, TaskPriority, TaskStatus, PersonalUniversityEvent, PersonalEventState


def demo_integration():
    """Demonstrate how all three domain services work together."""
    print("=== Personal OS AI Calendar: All Domain Services Integration ===\n")

    # Initialize all services
    planning_engine = PlanningEngine()
    uni_service = UniversityService(planning_engine)
    project_service = ProjectService(planning_engine)
    knowledge_service = KnowledgeService(planning_engine)

    print("1. Creating a university course with related projects and knowledge...\n")

    # Create a university course (project)
    course_project = project_service.create_project(
        name="Химическая физика: Квантовая механика",
        description="Курс по применению квантовой механики в химической физике",
        status=ProjectStatus.ACTIVE,
        start_date=datetime(2026, 9, 1),
        target_date=datetime(2026, 12, 20),
        tags={"university", "course", "chemistry", "quantum"},
        metadata={"credits": 4, "level": "graduate"}
    )

    print(f"   Created course project: {course_project.name}")
    print(f"   ID: {course_project.id}")
    print(f"   Credits: {course_project.metadata.get('credits')}")

    # Create university events for this course
    lecture1 = uni_service.create_lecture(
        uid="lecture-qm-001",
        summary="Введение в квантовую механику",
        description="Основные принципы квантовой механики: волново-частичный дуализм, принцип неопределенности",
        location="Аудитория ФИЗ-301",
        dtstart=datetime(2026, 9, 5, 10, 0),
        dtend=datetime(2026, 9, 5, 11, 30),
        is_group_event=True
    )

    lecture2 = uni_service.create_lecture(
        uid="lecture-qm-002",
        summary="Шреденгерово уравнение",
        description="Временнонезависимое и время-зависимое шреденгерово уравнение",
        location="Аудитория ФИЗ-301",
        dtstart=datetime(2026, 9, 12, 10, 0),
        dtend=datetime(2026, 9, 12, 11, 30),
        is_group_event=True
    )

    lab1 = uni_service.create_lab(
        uid="lab-qm-001",
        summary="Расчет спектров атомов водорода",
        description="Вычисление энергетических уровней атома водорода utilizando kвантовой механики",
        location="Компьютерный класс 205",
        dtstart=datetime(2026, 9, 7, 14, 0),
        dtend=datetime(2026, 9, 7, 17, 0),
        is_group_event=True
    )

    print(f"   Created university events: {len([lecture1, lecture2, lab1])} events")

    print("\n2. Creating related knowledge base for the course...\n")

    # Create knowledge base for the course
    course_kb = knowledge_service.create_knowledge_base(
        name="Квантовая механика для химиков",
        description="База знаний по квантовой механике с фокусом на химические приложения",
        tags={"kвантовая механика", "химия", "lecture notes", "formulas"},
        metadata={"course_id": course_project.id, "university_course": "Химическая физика"}
    )

    print(f"   Created knowledge base: {course_kb.name}")

    # Create knowledge items from lectures
    lecture1_note = knowledge_service.create_learning_note(
        title="Лекция 1: Введение в квантовую механику",
        content="""Основные принципы квантовой механики:

1. Волново-частичный дуализм - частицы проявляют свойства волн и частиц
2. Принцип неопределенности Гейзенберга - невозможно одновременно точно знать координу и импульс частицы
3. Принцип суперпозиции - квантовая система может находиться в нескольких состояниях одновременно
4. Принцип измерения - акт измерения изменяет состояние квантовой системы

Математический аппарат:
- Волновая функция Ψ(x,t) описывает состояние квантовой системы
- Плотность вероятности |Ψ(x,t)|² дает вероятность нахождения частицы в точке x в момент времени t
- Операторы в квантовой механике соответствуют измеримым величинам""",
        author="Препод. Смирнов И.И.",
        tags={"kвантовая механика", "введение", "основы"},
        project_id=course_project.id
    )

    lecture2_note = knowledge_service.create_learning_note(
        title="Лекция 2: Шреденгерово уравнение",
        content="""Шреденгерово уравнение - фундаментальное уравнение квантовой механики:

Время-зависимое шреденгерово уравнение:
iħ ∂Ψ/∂t = ĤΟ

Где:
- i - мнимая единица
- ħ - приведенная постоянная Планка (h/2π)
- Ψ - волновая функция
- Ĥ - оператор Гамильтона (энергия системы)

Временнонезависимое шреденгерово уравнение:
Ĥψ = Eψ

Где:
- ψ - пространственная часть волновой функции
- E - собственная энергия (собственное значение оператора Гамильтона)

Применение в химии:
- Расчет электронной структуры молекул
- Предсказание спектральных свойств
- Понимание химической связи через орбитали""",
        author="Препод. Смирнов И.И.",
        tags={"kвантовая механика", "шреденгерово", "уравнение"},
        project_id=course_project.id
    )

    # Link the knowledge items
    knowledge_service.add_knowledge_item_link(lecture1_note, lecture2_note.id)
    knowledge_service.add_knowledge_item_link(lecture2_note, lecture1_note.id)

    # Add to knowledge base
    knowledge_service.add_knowledge_item_to_base(course_kb, lecture1_note.id)
    knowledge_service.add_knowledge_item_to_base(course_kb, lecture2_note.id)

    print(f"   Created {2} learning notes from lectures")
    print(f"   Added notes to knowledge base: {len(course_kb.items)} items")

    print("\n3. Creating related tasks and assignments...\n")

    # Create tasks for the course project
    task1 = project_service.create_task(
        title="Решить задачник по лекции 1",
        description="Решить задачи 1-5 из задачника по введению в квантовую механику",
        project_id=course_project.id,
        priority=TaskPriority.HIGH,
        status=TaskStatus.INBOX,
        due_date=datetime(2026, 9, 8, 20, 0),
        estimated_hours=3.0,
        assignee="Студент Иванов",
        tags={"homework", "lecture1", "problems"},
        dependencies=[]
    )

    task2 = project_service.create_task(
        title="Подготовиться к лабораторной работе",
        description="Изучить теоретическую часть лабораторной работы по спектрам атома водорода",
        project_id=course_project.id,
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.INBOX,
        due_date=datetime(2026, 9, 6, 12, 0),
        estimated_hours=2.0,
        assignee="Студент Иванов",
        tags={"preparation", "lab1", "theory"},
        dependencies=[]
    )

    task3 = project_service.create_task(
        title="Написать отчет по лабораторной работе",
        description="Подготовить письменный отчет по результатам лабораторной работы",
        project_id=course_project.id,
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.INBOX,
        due_date=datetime(2026, 9, 10, 18, 0),
        estimated_hours=4.0,
        assignee="Студент Иванов",
        tags={"report", "lab1", "writeup"},
        dependencies=[task2.id]  # Report depends on lab preparation
    )

    task4 = project_service.create_task(
        title="Готовиться к следующей лекции",
        description="Препрочитать материал о шреденгерово уравнении перед лекцией",
        project_id=course_project.id,
        priority=TaskPriority.LOW,
        status=TaskStatus.INBOX,
        due_date=datetime(2026, 9, 11, 20, 0),
        estimated_hours=1.5,
        assignee="Студент Иванов",
        tags={"preparation", "lecture2", "reading"},
        dependencies=[]
    )

    tasks = [task1, task2, task3, task4]
    print(f"   Created {len(tasks)} course-related tasks")

    print("\n4. Establishing connections between domains...\n")

    # Link knowledge items to tasks
    knowledge_service.add_knowledge_item_link(lecture1_note, task1.id)  # Lecture 1 note related to task 1
    knowledge_service.add_knowledge_item_link(lecture2_note, task4.id)  # Lecture 2 note related to task 4

    # Link tasks to university events (preparation for lectures/labs)
    project_service.add_task_dependency(task2, lecture1.uid)  # Lab prep depends on attending lecture 1
    project_service.add_task_dependency(task4, lecture2.uid)  # Lecture 2 prep depends on... well, it's just timed around it

    # Create preparation blocks for university events
    personal_lecture1 = PersonalUniversityEvent(
        id=lecture1.uid,
        title=lecture1.summary,
        description=lecture1.description,
        start_time=lecture1.dtstart,
        end_time=lecture1.dtend,
        university_event_uid=lecture1.uid,
        state=PersonalEventState.CONFIRMED
    )
    prep_for_lecture1 = uni_service.create_preparation_from_event(personal_lecture1, 45)  # 45 min prep
    personal_lab1 = PersonalUniversityEvent(
        id=lab1.uid,
        title=lab1.summary,
        description=lab1.description,
        start_time=lab1.dtstart,
        end_time=lab1.dtend,
        university_event_uid=lab1.uid,
        state=PersonalEventState.CONFIRMED
    )
    prep_for_lab1 = uni_service.create_preparation_from_event(personal_lab1, 60)         # 60 min prep
    personal_lecture2 = PersonalUniversityEvent(
        id=lecture2.uid,
        title=lecture2.summary,
        description=lecture2.description,
        start_time=lecture2.dtstart,
        end_time=lecture2.dtend,
        university_event_uid=lecture2.uid,
        state=PersonalEventState.CONFIRMED
    )
    prep_for_lecture2 = uni_service.create_preparation_from_event(personal_lecture2, 45)  # 45 min prep

    preparation_blocks = [prep_for_lecture1, prep_for_lab1, prep_for_lecture2]
    print(f"   Created {len(preparation_blocks)} preparation blocks for university events")

    print("\n5. Scheduling all activities using the planning engine...\n")

    # Schedule preparation blocks (university domain)
    uni_start = datetime(2026, 9, 1, 8, 0)
    uni_end = datetime(2026, 9, 10, 20, 0)

    uni_schedule = uni_service.schedule_preparation_blocks(
        preparation_blocks, uni_start, uni_end, 30
    )

    # Schedule tasks (project domain)
    project_start = datetime(2026, 9, 1, 8, 0)
    project_end = datetime(2026, 9, 15, 20, 0)

    project_schedule = project_service.schedule_tasks(
        tasks, project_start, project_end, 60
    )

    # Schedule knowledge learning/review (knowledge domain)
    knowledge_items = [lecture1_note, lecture2_note]
    knowledge_start = datetime(2026, 9, 1, 19, 0)  # Evening study sessions
    knowledge_end = datetime(2026, 9, 15, 22, 0)

    knowledge_schedule = knowledge_service.schedule_learning_tasks(
        knowledge_items, knowledge_start, knowledge_end, 60, 3  # 3-day review interval
    )

    print(f"   University preparation blocks scheduled: {len(uni_schedule.get_scheduled_items())}")
    print(f"   Project tasks scheduled: {len(project_schedule.get_scheduled_items())}")
    print(f"   Knowledge learning tasks scheduled: {len(knowledge_schedule.get_scheduled_items())}")

    print("\n6. Showing integrated view of student's schedule...\n")

    # Combine all scheduled items for a unified view
    all_scheduled = []

    # Add university preparations
    for item in uni_schedule.get_scheduled_items():
        item_slots = [slot for slot in uni_schedule.slots if slot.scheduled_item_id == item.id]
        if item_slots:
            start_time = min(slot.start for slot in item_slots)
            end_time = max(slot.end for slot in item_slots)
            all_scheduled.append({
                'time': start_time,
                'end_time': end_time,
                'title': f"Универ: {item.title}",
                'type': 'university',
                'duration': (end_time - start_time).total_seconds() / 3600
            })

    # Add project tasks
    for item in project_schedule.get_scheduled_items():
        item_slots = [slot for slot in project_schedule.slots if slot.scheduled_item_id == item.id]
        if item_slots:
            start_time = min(slot.start for slot in item_slots)
            end_time = max(slot.end for slot in item_slots)
            all_scheduled.append({
                'time': start_time,
                'end_time': end_time,
                'title': f"Проект: {item.title}",
                'type': 'project',
                'duration': (end_time - start_time).total_seconds() / 3600
            })

    # Add knowledge activities
    for item in knowledge_schedule.get_scheduled_items():
        item_slots = [slot for slot in knowledge_schedule.slots if slot.scheduled_item_id == item.id]
        if item_slots:
            start_time = min(slot.start for slot in item_slots)
            end_time = max(slot.end for slot in item_slots)
            # Extract original knowledge item ID
            kb_item_id = item.id.replace("learn-", "")
            all_scheduled.append({
                'time': start_time,
                'end_time': end_time,
                'title': f"Знания: {item.title}",
                'type': 'knowledge',
                'duration': (end_time - start_time).total_seconds() / 3600
            })

    # Sort by time
    all_scheduled.sort(key=lambda x: x['time'])

    print("   Integrated weekly schedule (Sep 1-10):")
    current_date = None
    for scheduled in all_scheduled[:15]:  # Show first 15 items
        scheduled_date = scheduled['time'].date()
        if current_date != scheduled_date:
            current_date = scheduled_date
            print(f"     {current_date.strftime('%d.%m (%A)')}:")

        time_str = scheduled['time'].strftime('%H:%M')
        end_time_str = scheduled['end_time'].strftime('%H:%M')
        print(f"       {time_str}-{end_time_str} [{scheduled['type']}] {scheduled['title']} ({scheduled['duration']:.1f}h)")

    if len(all_scheduled) > 15:
        print(f"       ... и ещё {len(all_scheduled) - 15} записей")

    print("\n7. Demonstrating cross-domain queries...\n")

    # Find all knowledge related to a specific lecture
    lecture1_related = knowledge_service.suggest_related_items(lecture1_note, knowledge_items)
    print(f"   Knowledge related to '{lecture1_note.title}':")
    for related in lecture1_related:
        print(f"     - {related.title}")

    # Find all tasks related to a knowledge item
    print(f"\n   Tasks that could benefit from '{lecture1_note.title}':")
    for task in tasks:
        if lecture1_note.id in task.tags or any(tag in lecture1_note.title.lower() for tag in task.tags):
            print(f"     - {task.title}")

    # Show project progress
    completed_tasks = project_service.get_tasks_by_status(tasks, TaskStatus.COMPLETED)
    print(f"\n   Project progress: {len(completed_tasks)}/{len(tasks)} tasks completed")

    print("\n✓ All domain services integration demonstrated successfully!")
    print("\nKey integration points:")
    print("  • University events → Preparation blocks → Personal study time")
    print("  • Course projects → Tasks → Knowledge application")
    print("  • Lecture materials → Knowledge items → Study/review schedule")
    print("  • Cross-tagging and linking enable discovery across domains")
    print("  • Unified planning engine schedules activities from all domains")


if __name__ == "__main__":
    demo_integration()