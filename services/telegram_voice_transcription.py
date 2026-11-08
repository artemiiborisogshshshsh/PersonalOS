"""Bounded Telegram voice download and Russian transcription through Groq."""
import os
import re

import requests

MODEL = 'whisper-large-v3-turbo'
ENDPOINT = 'https://api.groq.com/openai/v1/audio/transcriptions'
MAX_SECONDS = 60
MAX_BYTES = 2 * 1024 * 1024
DAILY_LIMIT = 10


class VoiceQuotaExhausted(Exception):
    """The Groq organization reached its rate limit."""


class VoiceFailure(Exception):
    """Safe categorized voice processing failure."""
    def __init__(self, category, stage, status=None):
        super().__init__()
        self.category, self.stage, self.status = category, stage, status


def groq_free_api_key():
    """Only opt in with a key whose organization is confirmed to be on Free."""
    if os.environ.get('PERSONAL_OS_GROQ_FREE_TIER') != '1':
        return ''
    return (os.environ.get('Groq_API_KEY', '').strip()
            or os.environ.get('GROQ_API_KEY', '').strip())


def valid_voice(voice):
    return (isinstance(voice, dict)
            and isinstance(voice.get('file_id'), str) and 0 < len(voice['file_id']) <= 512
            and type(voice.get('duration')) is int and 0 <= voice['duration'] <= MAX_SECONDS
            and type(voice.get('file_size', 1)) is int and 0 < voice.get('file_size', 1) <= MAX_BYTES)


def transcribe_voice(base_url, voice, api_key):
    if not api_key:
        raise VoiceFailure('temporary', 'groq_request')
    if not valid_voice(voice):
        raise VoiceFailure('invalid', 'audio_validation')
    try:
        response = requests.get(base_url + '/getFile', params={'file_id': voice['file_id']},
                                timeout=10, allow_redirects=False)
    except (requests.RequestException, ValueError) as error:
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        raise VoiceFailure('temporary', 'telegram_metadata', status) from None
    if response.status_code != 200:
        raise VoiceFailure('temporary', 'telegram_metadata', response.status_code)
    try:
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise VoiceFailure('temporary', 'telegram_metadata', response.status_code) from None
    result = payload.get('result') if isinstance(payload, dict) else None
    path = result.get('file_path', '') if isinstance(result, dict) else ''
    if (not isinstance(payload, dict) or payload.get('ok') is not True
            or not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+', path)
            or path.startswith('/') or '..' in path):
        raise VoiceFailure('temporary', 'telegram_metadata', response.status_code) from None
    url = base_url.replace('/bot', '/file/bot', 1) + '/' + path
    audio = bytearray()
    download_status = None
    try:
        with requests.get(url, stream=True, timeout=12, allow_redirects=False) as file:
            download_status = file.status_code
            if file.status_code != 200:
                raise VoiceFailure('temporary', 'telegram_download', file.status_code)
            for chunk in file.iter_content(64 * 1024):
                audio.extend(chunk)
                if len(audio) > MAX_BYTES:
                    raise VoiceFailure('invalid', 'audio_validation')
    except VoiceFailure:
        raise
    except (requests.RequestException, ValueError) as error:
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        if status is None:
            status = download_status
        raise VoiceFailure('temporary', 'telegram_download', status) from None
    if not audio.startswith(b'OggS') or b'OpusHead' not in audio[:256]:
        raise VoiceFailure('invalid', 'audio_validation')

    try:
        result = requests.post(
            ENDPOINT, headers={'Authorization': 'Bearer ' + api_key},
            files={'file': ('voice.ogg', bytes(audio), 'audio/ogg')},
            data={'model': MODEL, 'language': 'ru', 'response_format': 'json',
                  'temperature': '0', 'prompt': 'Личные планы, название события, дата, время начала, длительность.'},
            timeout=(5, 20), allow_redirects=False,
        )
    except requests.RequestException as error:
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        raise VoiceFailure('temporary', 'groq_request', status) from None
    if result.status_code == 429:
        raise VoiceQuotaExhausted
    if result.status_code != 200:
        raise VoiceFailure('temporary', 'groq_response', result.status_code)
    try:
        payload = result.json()
    except (requests.RequestException, ValueError):
        raise VoiceFailure('temporary', 'groq_response', result.status_code) from None
    if not isinstance(payload, dict) or not isinstance(payload.get('text'), str):
        raise VoiceFailure('temporary', 'groq_response', result.status_code)
    text = payload['text']
    if not text.strip() or len(text) > 1000:
        raise VoiceFailure('invalid', 'audio_validation', result.status_code)
    return text.strip()
