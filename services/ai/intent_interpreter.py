"""
Intent Interpreter Service
Specialized AI service for interpreting user intentions from natural language input.
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from ..application.command import *
from .base_ai_service import BaseAIService, AIServiceResult


class IntentInterpreter(BaseAIService):
    """
    AI service for interpreting user intentions from natural language input.
    Handles parsing of commands, extracting intent and entities.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize Intent Interpreter.

        Args:
            config: Configuration dictionary with keys:
                - model_name: Name of the intent classification model to use
                - confidence_threshold: Minimum confidence for intent acceptance
                - supported_intents: List of intents this interpreter can recognize
        """
        super().__init__(config)
        self.model_name = self.config.get('model_name', 'intent-classifier-v1')
        self.confidence_threshold = self.config.get('confidence_threshold', 0.7)
        self.supported_intents = self.config.get('supported_intents', [
            'create_event',
            'modify_event',
            'delete_event',
            'query_schedule',
            'create_task',
            'modify_task',
            'create_knowledge_item',
            'search_knowledge',
            'schedule_preparation',
            'unknown'
        ])

    async def initialize(self) -> bool:
        """
        Initialize Intent Interpreter.
        In a real implementation, this would load ML models or connect to AI services.

        Returns:
            bool: True if initialization successful
        """
        try:
            # Placeholder for actual model loading or service connection
            # For now, we'll simulate successful initialization
            self._set_initialized(True)
            return True
        except Exception:
            print('Intent interpreter initialization failed')
            self._set_initialized(False)
            return False

    async def health_check(self) -> bool:
        """
        Check if Intent Interpreter is healthy.

        Returns:
            bool: True if healthy, False otherwise
        """
        # In a real implementation, this would check model availability, service health, etc.
        return self._is_initialized

    async def shutdown(self) -> bool:
        """
        Shutdown Intent Interpreter.

        Returns:
            bool: True if shutdown successful
        """
        try:
            # Placeholder for actual cleanup
            self._set_initialized(False)
            return True
        except Exception:
            return False

    async def interpret_intent(self, text: str, context: Optional[Dict[str, Any]] = None) -> AIServiceResult:
        """
        Interpret user intention from natural language text and return a proposed command.

        Args:
            text: Natural language input from user
            context: Optional context information (current time, user preferences, etc.)

        Returns:
            AIServiceResult: Contains proposed command to execute the interpreted intent
        """
        if not self._is_initialized:
            return AIServiceResult(
                success=False,
                error="Intent interpreter not initialized"
            )

        try:
            text_lower = text.lower().strip()

            # Simple intent classification based on keywords
            intent_scores = {}

            # Define keyword patterns for each intent
            intent_patterns = {
                'create_event': [
                    'создать встречу', 'создай встречу', 'запланировать встречу',
                    'запланируй встречу', 'добавить встречу', 'добавь встречу',
                    'назначить встречу', 'назначь встречу',
                ],
                'modify_event': ['изменить', 'перенести', 'изменить время', 'перепланировать'],
                'delete_event': ['удалить', 'отменить встречу', 'убрать из календаря'],
                'query_schedule': ['какое у меня расписание', 'что у меня сегодня', 'показать календарь', 'расписание на'],
                'create_task': ['создать задачу', 'создай задачу', 'добавить задачу',
                                'добавь задачу', 'задача', 'нужно сделать',
                                'требуется', 'следует выполнить'],
                'modify_task': ['изменить задачу', 'обновить задачу', 'пометить как выполненное'],
                'create_knowledge_item': ['запомнить', 'сохранить информацию', 'заметка', 'записать'],
                'search_knowledge': ['найди информацию', 'что ты знаешь о', 'поиск в знаниях', 'расскажи о'],
                'schedule_preparation': ['подготовиться к', 'время на подготовку', 'сколько готовиться к'],
            }

            # A single exact phrase is enough for a deterministic suggestion.
            # Dividing it by the number of synonyms made all normal one-phrase
            # Russian commands fall below 0.7 and therefore ``unknown``.
            for intent, patterns in intent_patterns.items():
                matches = [pattern for pattern in patterns if pattern in text_lower]
                if matches:
                    intent_scores[intent] = (
                        min(0.95, 0.75 + 0.1 * (len(matches) - 1)),
                        max(map(len, matches)),
                    )

            # Find the intent with highest score
            if intent_scores:
                best_intent = max(intent_scores, key=intent_scores.get)
                confidence = intent_scores[best_intent][0]
            else:
                best_intent = 'unknown'
                confidence = 0.0

            # Only actions backed by deterministic commands may become a
            # proposal. Never turn unclear/delete/modify language into a
            # surrogate note that could be approved and mutate data.
            supported = {'create_event', 'create_task', 'create_knowledge_item'}
            if best_intent not in supported:
                return AIServiceResult(
                    success=False,
                    error='Нужно уточнить действие или выбрать конкретное событие.',
                    confidence=confidence,
                )

            # Extract simple entities (placeholder)
            entities = self._extract_entities(text_lower, best_intent)

            # Based on intent and entities, create appropriate command
            command = self._create_command_from_intent(best_intent, entities, text, context)

            return AIServiceResult(
                success=True,
                data=command,  # Return the command object directly in data
                confidence=confidence
            )

        except Exception:
            return AIServiceResult(
                success=False,
                error='Не удалось разобрать команду. Сформулируй её иначе.',
            )

    def _extract_entities(self, text: str, intent: str) -> Dict[str, Any]:
        """
        Extract entities from text based on intent.
        This is a simplified placeholder implementation.

        Args:
            text: Lowercase input text
            intent: Classified intent

        Returns:
            Dictionary of extracted entities
        """
        entities = {}

        # Simple time extraction (very basic)
        import re
        time_patterns = [
            r'(\d{1,2}):(\d{2})',  # HH:MM
            r'(\d{1,2}) часов?',    # X часов
            r'в (\d{1,2}):?(\d{0,2})',  # в X:XX или в X
        ]

        for pattern in time_patterns:
            matches = re.findall(pattern, text)
            if matches:
                entities['time_mentioned'] = matches
                break

        # Simple date extraction
        date_patterns = [
            r'сегодня',
            r'завтра',
            r'послезавтра',
            r'(\d{1,2})[./](\d{1,2})[./](\d{2,4})',  # DD/MM/YYYY или DD.MM.YYYY
        ]

        for pattern in date_patterns:
            if re.search(pattern, text):
                entities['date_mentioned'] = True
                entities['relative_days'] = (
                    2 if pattern == r'послезавтра' else 1 if pattern == r'завтра' else 0
                )
                break

        return entities

    def _create_command_from_intent(self, intent: str, entities: Dict[str, Any],
                                  original_text: str, context: Optional[Dict[str, Any]] = None) -> BaseCommand:
        """
        Create a command object based on interpreted intent and extracted entities.

        Args:
            intent: The interpreted intent
            entities: Extracted entities from the text
            original_text: Original input text
            context: Optional context information

        Returns:
            BaseCommand: A command object representing the action to perform
        """
        # The caller supplies the user's timezone-aware current moment. The
        # fallback remains for isolated domain use only.
        now = (context or {}).get('now')
        now = now if isinstance(now, datetime) else datetime.now()

        # Default values
        default_summary = "New item from voice/input"
        # The confirmed domain fields are sufficient. Do not duplicate the
        # complete raw chat message into persistent task/event descriptions.
        default_description = "Created from confirmed natural-language proposal"

        # Extract time if mentioned
        time_mentioned = entities.get('time_mentioned', [])
        relative_days = entities.get('relative_days', 0)

        # Parse time information (simplified)
        start_time = (now + timedelta(days=relative_days)).replace(
            hour=9, minute=0, second=0, microsecond=0,
        )
        end_time = start_time + timedelta(hours=1)

        if time_mentioned:
            # If we found time mentions, use the first one
            time_match = time_mentioned[0]
            if len(time_match) >= 2:
                try:
                    hour = int(time_match[0])
                    minute = int(time_match[1]) if len(time_match) > 1 else 0
                    start_time = (now + timedelta(days=relative_days)).replace(
                        hour=hour, minute=minute, second=0, microsecond=0,
                    )
                    end_time = start_time + timedelta(hours=1)
                except (ValueError, IndexError):
                    pass  # Keep default times

        # Handle different intents
        if intent == 'create_event':
            # Extract summary from text or use default
            summary = original_text
            # Clean up the summary by removing command words
            for word in ['создать', 'запланировать', 'добавить', 'назначить', 'встречу']:
                summary = summary.replace(word, '').strip()
            if not summary:
                summary = default_summary

            return CreateUniversityEventCommand(
                event_type='other',
                summary=summary,
                description=default_description,
                location='TBD',  # To be determined
                dtstart=start_time,
                dtend=end_time,
                # Natural-language event creation is personal by default;
                # it must never manufacture or alter a TPU group lesson.
                is_group_event=False,
            )

        elif intent == 'create_task':
            # Extract summary from text or use default
            summary = original_text
            # Clean up the summary by removing command words
            for word in ['задача', 'нужно сделать', 'требуется', 'следует выполнить']:
                summary = summary.replace(word, '').strip()
            if not summary:
                summary = default_summary

            return CreateTaskCommand(
                title=summary,
                description=default_description,
                status='todo',
                priority='medium'
            )

        elif intent == 'create_knowledge_item':
            # Extract summary from text or use default
            summary = original_text
            # Clean up the summary by removing command words
            for word in ['запомнить', 'сохранить', 'заметка', 'записать']:
                summary = summary.replace(word, '').strip()
            if not summary:
                summary = default_summary

            return CreateKnowledgeItemCommand(
                title=summary,
                content=default_description,
                note_type='idea',
                status='draft'
            )

        elif intent == 'schedule_preparation':
            # For schedule preparation intent, we might want to suggest preparation time
            # For now, return a generic command or perhaps a scheduling command
            # Since we don't have a specific event to prepare for, we'll return a placeholder
            # In a real implementation, we might look at the calendar to find upcoming events
            return CreateKnowledgeItemCommand(
                title=f"Preparation planning: {original_text}",
                content=f"Planning preparation time based on: {original_text}",
                note_type='idea',
                status='draft'
            )

        elif intent == 'search_knowledge':
            # For search knowledge, we might want to create a knowledge item to save the search results
            # Or we could return a special search command (but we don't have one yet)
            # For now, create a knowledge item to represent the search query
            return CreateKnowledgeItemCommand(
                title=f"Search query: {original_text}",
                content=f"Results for search: {original_text}",
                note_type='reference',
                status='draft'
            )

        elif intent == 'modify_event':
            # For modify event, we would need more specific information
            # For now, return a placeholder knowledge item
            return CreateKnowledgeItemCommand(
                title=f"Event modification request: {original_text}",
                content=f"Request to modify event based on: {original_text}",
                note_type='idea',
                status='draft'
            )

        elif intent == 'delete_event':
            # For delete event, we would need more specific information
            # For now, return a placeholder knowledge item
            return CreateKnowledgeItemCommand(
                title=f"Event deletion request: {original_text}",
                content=f"Request to delete event based on: {original_text}",
                note_type='idea',
                status='draft'
            )

        elif intent == 'modify_task':
            # For modify task, we would need more specific information
            # For now, return a placeholder knowledge item
            return CreateKnowledgeItemCommand(
                title=f"Task modification request: {original_text}",
                content=f"Request to modify task based on: {original_text}",
                note_type='idea',
                status='draft'
            )

        else:
            # For unknown or other intents, return a generic knowledge item
            return CreateKnowledgeItemCommand(
                title=f"Unclear intent ({intent}): {original_text}",
                content=f"Please clarify your request: {original_text}",
                note_type='idea',
                status='draft'
            )
