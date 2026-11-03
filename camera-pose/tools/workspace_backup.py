"""AES-256-GCM encrypted portable workspace backup. Keep its key outside Git."""
import argparse
import base64
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b'BEDPOSE-BACKUP-1\n'
CHUNK = 1024 * 1024
SKIP = {'.git', 'node_modules', '__pycache__', '.pytest_cache', '.venv',
        '.venv-training', '.venv-pose', 'build', 'dist', 'transfer'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def create(root, output, key_file):
    root, output, key_file = map(lambda p: Path(p).resolve(), (root, output, key_file))
    if output.exists() or key_file.exists():
        raise ValueError('Preserve existing backup/key; choose new file names')
    if root in key_file.parents:
        raise ValueError('Key must be outside the workspace')
    output.parent.mkdir(parents=True, exist_ok=True)
    files = []
    for directory, folders, names in os.walk(root):
        folders[:] = sorted(n for n in folders if n not in SKIP and not n.endswith('.egg-info'))
        for name in sorted(names):
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError('Symlink requires explicit review: ' + str(path))
            files.append(path)
    key, nonce = os.urandom(32), os.urandom(12)
    key_file.write_text(base64.b64encode(key).decode() + '\n', encoding='ascii')
    with tempfile.TemporaryDirectory(prefix='bedpose-backup-') as directory:
        zipped = Path(directory) / 'workspace.zip'
        inventory = {}
        with zipfile.ZipFile(zipped, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path in files:
                relative = path.relative_to(root).as_posix()
                inventory[relative] = digest(path)
                archive.write(path, relative)
            archive.writestr('BACKUP-INVENTORY.json', json.dumps(inventory))
        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        cipher.authenticate_additional_data(MAGIC)
        with zipped.open('rb') as source, output.open('wb') as target:
            target.write(MAGIC + nonce)
            while chunk := source.read(CHUNK):
                target.write(cipher.update(chunk))
            target.write(cipher.finalize())
            target.write(cipher.tag)
    metadata = dict(format='AES-256-GCM / ZIP', files=len(files), bytes=output.stat().st_size,
                    sha256=digest(output), key_in_repository=False,
                    excluded_rebuildable_directories=sorted(SKIP),
                    contains_original_images_models_logs_and_runtime_settings=True)
    output.with_suffix('.manifest.json').write_text(json.dumps(metadata, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(metadata))


def restore(backup, key_file, destination):
    backup, destination = Path(backup), Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Restore into a new empty directory to preserve current files')
    key = base64.b64decode(Path(key_file).read_text(encoding='ascii').strip(), validate=True)
    with tempfile.TemporaryDirectory(prefix='bedpose-restore-') as directory:
        zipped = Path(directory) / 'workspace.zip'
        with backup.open('rb') as source:
            if source.read(len(MAGIC)) != MAGIC:
                raise ValueError('Unknown backup format')
            nonce = source.read(12)
            source.seek(-16, 2)
            tag = source.read(16)
            remaining = backup.stat().st_size - len(MAGIC) - 12 - 16
            source.seek(len(MAGIC) + 12)
            cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            cipher.authenticate_additional_data(MAGIC)
            with zipped.open('wb') as target:
                while remaining:
                    chunk = source.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ValueError('Truncated encrypted backup')
                    target.write(cipher.update(chunk)); remaining -= len(chunk)
                target.write(cipher.finalize())
        # Authentication finishes before any file is extracted.
        with zipfile.ZipFile(zipped) as archive:
            inventory = json.loads(archive.read('BACKUP-INVENTORY.json'))
            for name in inventory:
                target = (destination / name).resolve()
                if not target.is_relative_to(destination):
                    raise ValueError('Unsafe archive path')
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != inventory[name]:
                    raise ValueError('Backup file checksum mismatch')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
    print('Restored and verified files:', len(inventory))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create_parser = commands.add_parser('create')
    create_parser.add_argument('--root', type=Path, required=True)
    create_parser.add_argument('--output', type=Path, required=True)
    restore_parser = commands.add_parser('restore')
    restore_parser.add_argument('--backup', type=Path, required=True)
    restore_parser.add_argument('--destination', type=Path, required=True)
    for command in (create_parser, restore_parser):
        command.add_argument('--key-file', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'create':
        create(args.root, args.output, args.key_file)
    else:
        restore(args.backup, args.key_file, args.destination)
