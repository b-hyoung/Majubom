"""Restore reviewed installation settings and exact public model assets on a new laptop."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SNAPSHOT = PROJECT / 'config/installation-20261103'


def restore_settings(destination):
    destination = Path(destination)
    for source in SNAPSHOT.glob('*.json'):
        if source.name == 'manifest.json':
            continue
        target = destination / source.name
        if target.exists():
            print('Preserved existing settings:', target.name)
            continue
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        print('Restored provisional settings:', target.name)


def prepare_models():
    assets = json.loads((SNAPSHOT / 'manifest.json').read_text(encoding='utf-8'))
    for name, spec in assets['mediapipe'].items():
        target = PROJECT / 'checkpoints' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_suffix('.download')
            with urllib.request.urlopen(spec['url'], timeout=120) as response:
                data = response.read(64 * 1024**2 + 1)
            if hashlib.sha256(data).hexdigest() != spec['sha256']:
                raise ValueError('Downloaded model checksum mismatch: ' + name)
            temporary.write_bytes(data)
            temporary.replace(target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != spec['sha256']:
            raise ValueError('Existing model differs; preserved: ' + name)
    clip = PROJECT / 'checkpoints/clip-vit-base-patch16'
    if not clip.exists():
        subprocess.run([sys.executable, '-m', 'bed_pose', 'clip-setup', '--revision',
                        assets['clip_revision']], cwd=PROJECT, check=True)
    from bed_pose.clip_assets import read_assets
    actual = read_assets(clip, verify=True)
    if actual['files'] != assets['clip_files']:
        raise ValueError('CLIP assets differ from the original laptop')


def rebind_guard():
    config = PROJECT / 'checkpoints/person-guard.json'
    if not config.exists():
        return
    value = json.loads(config.read_text(encoding='utf-8'))
    model = PROJECT / 'checkpoints/pose-candidates/balanced_det.onnx'
    if hashlib.sha256(model.read_bytes()).hexdigest() != value['sha256']:
        raise ValueError('Person guard model checksum differs')
    value.update(python_path=str(PROJECT.parent / '.venv-pose/Scripts/python.exe'),
                 detector_model=str(model),
                 cuda_dll_dir=str(PROJECT.parent / '.venv-training/Lib/site-packages/torch/lib'))
    config.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', action='store_true')
    parser.add_argument('--settings', action='store_true')
    parser.add_argument('--rebind-guard', action='store_true')
    args = parser.parse_args()
    if args.models:
        prepare_models()
    if args.settings:
        restore_settings(PROJECT / 'recordings/live/.monitor')
    if args.rebind_guard:
        rebind_guard()
    if not any((args.models, args.settings, args.rebind_guard)):
        parser.error('Choose --models, --settings and/or --rebind-guard')
