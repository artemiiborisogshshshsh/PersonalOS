"""Recoverable backups for allow-listed user operational state only."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile
import json
import os

from services.user_data_lifecycle import EXPORTABLE_STATE_FILES, UserDataLifecycleService


class UserStateBackupService:
    MANIFEST = 'manifest.json'

    def __init__(self, data_root: Path):
        self.lifecycle = UserDataLifecycleService(data_root)

    def backup(self, user_id: str, archive_path: Path) -> dict:
        exported = self.lifecycle.export(user_id)
        files = exported['files']
        manifest = {'version': 1, 'user_id': user_id,
                    'files': {name: sha256(text.encode()).hexdigest() for name, text in files.items()}}
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(dir=archive_path.parent, prefix='.backup-', suffix='.zip', delete=False) as output:
                temporary = Path(output.name)
            with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
                archive.writestr(self.MANIFEST, json.dumps(manifest, ensure_ascii=False))
                for name, text in files.items(): archive.writestr('state/' + name, text)
            os.replace(temporary, archive_path)
        finally:
            if temporary is not None and temporary.exists(): temporary.unlink()
        return manifest

    def restore(self, user_id: str, archive_path: Path, *, overwrite: bool = False) -> dict:
        target = self.lifecycle._directory(user_id)
        with ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read(self.MANIFEST))
            if manifest.get('version') != 1 or manifest.get('user_id') != user_id:
                raise ValueError('Backup does not belong to this user')
            entries = manifest.get('files', {})
            if not set(entries).issubset(EXPORTABLE_STATE_FILES):
                raise ValueError('Backup contains unsupported state file')
            payload = {}
            for name, digest in entries.items():
                text = archive.read('state/' + name).decode('utf-8')
                if sha256(text.encode()).hexdigest() != digest: raise ValueError('Backup integrity check failed')
                payload[name] = text
        target.mkdir(parents=True, exist_ok=True)
        if any((target / name).is_symlink() for name in payload):
            raise ValueError('Refusing to restore through a symbolic link')
        if not overwrite and any((target / name).exists() for name in payload):
            raise FileExistsError('Refusing to overwrite existing user state')
        for name, text in payload.items():
            (target / name).write_text(text, encoding='utf-8')
        return {'user_id': user_id, 'restored_files': sorted(payload)}
