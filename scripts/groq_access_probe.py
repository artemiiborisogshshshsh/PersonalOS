"""Short-lived operator probe. Sends silence and encrypts the provider reply."""
from base64 import b64encode
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import json
import subprocess
import sys
import wave

EXPIRES_AT = datetime(2026, 11, 9, 2, 45, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent
PUBLIC_KEY = ROOT / 'scripts' / 'groq_access_probe_public.pem'


def silence():
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(bytes(32000))
    return buffer.getvalue()


def encrypt(payload):
    return '.'.join(
        b64encode(subprocess.run(
            ['openssl', 'pkeyutl', '-encrypt', '-pubin', '-inkey', str(PUBLIC_KEY),
             '-pkeyopt', 'rsa_padding_mode:oaep', '-pkeyopt', 'rsa_oaep_md:sha256'],
            input=payload[offset:offset + 300], capture_output=True, check=True, timeout=3,
        ).stdout).decode('ascii')
        for offset in range(0, len(payload), 300)
    )


def main():
    if datetime.now(timezone.utc) >= EXPIRES_AT:
        return
    sys.path.insert(0, str(ROOT))
    import requests
    from services.telegram_voice_transcription import ENDPOINT, MODEL, groq_free_api_key

    key = groq_free_api_key()
    if not key:
        print('GROQ ACCESS PROBE unavailable: no Free tier configuration', flush=True)
        return
    request = dict(files={'file': ('probe.wav', silence(), 'audio/wav')},
                   data={'model': MODEL, 'language': 'ru', 'response_format': 'json'},
                   timeout=(3, 8), allow_redirects=False)
    response = requests.post(ENDPOINT, headers={'Authorization': 'Bearer ' + key}, **request)
    print(f'GROQ ACCESS PROBE status {response.status_code}', flush=True)
    if response.status_code != 403:
        return
    data = {
        'headers': {name: response.headers.get(name, '')[:120] for name in
                    ('Content-Type', 'Server', 'Via', 'CF-Mitigated')},
        'body': response.content[:1000].decode('utf-8', errors='replace').replace(key, '[REDACTED]'),
    }
    print('GROQ ACCESS PROBE sealed ' + encrypt(json.dumps(data).encode()), flush=True)
    comparison = requests.post(ENDPOINT, **request)
    print(f'GROQ ACCESS PROBE anonymous status {comparison.status_code}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('GROQ ACCESS PROBE stopped: ' + type(error).__name__, flush=True)
