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
    if not api_key or not valid_voice(voice):
        raise ValueError('Invalid voice request')
    response = requests.get(base_url + '/getFile', params={'file_id': voice['file_id']},
                            timeout=10, allow_redirects=False)
    if response.status_code != 200:
        raise ValueError('Telegram file metadata unavailable')
    payload = response.json()
    path = payload.get('result', {}).get('file_path', '')
    if (not payload.get('ok') or not re.fullmatch(r'[A-Za-z0-9_./-]+', path)
            or path.startswith('/') or '..' in path):
        raise ValueError('Invalid Telegram file path')
    url = base_url.replace('/bot', '/file/bot', 1) + '/' + path
    audio = bytearray()
    with requests.get(url, stream=True, timeout=12, allow_redirects=False) as file:
        if file.status_code != 200:
            raise ValueError('Telegram file unavailable')
        for chunk in file.iter_content(64 * 1024):
            audio.extend(chunk)
            if len(audio) > MAX_BYTES:
                raise ValueError('Voice file too large')
    if not audio.startswith(b'OggS') or b'OpusHead' not in audio[:256]:
        raise ValueError('Expected Telegram OGG/Opus')
    result = requests.post(
        ENDPOINT, headers={'Authorization': 'Bearer ' + api_key},
        files={'file': ('voice.ogg', bytes(audio), 'audio/ogg')},
        data={'model': MODEL, 'language': 'ru', 'response_format': 'json',
              'temperature': '0', 'prompt': 'Личные планы, название события, дата, время начала, длительность.'},
        timeout=(5, 20), allow_redirects=False,
    )
    if result.status_code == 429:
        raise VoiceQuotaExhausted
    if result.status_code != 200:
        raise ValueError('Voice transcription unavailable')
    text = result.json().get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise ValueError('No usable transcription')
    return text.strip()
