"""Explicit CLIP asset preparation; inference itself never downloads files."""
from pathlib import Path
import re
import shutil
import tempfile

from .io_utils import atomic_json, file_hash, load_json

MODEL_ID = 'openai/clip-vit-base-patch16'
DEFAULT_DIRECTORY = Path('checkpoints/clip-vit-base-patch16')
MANIFEST = 'bedpose_clip.json'
FILES = ('config.json', 'preprocessor_config.json', 'tokenizer_config.json',
         'special_tokens_map.json', 'vocab.json', 'merges.txt', 'tokenizer.json', 'pytorch_model.bin')


def validate_manifest(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'model_id', 'revision', 'origin', 'files'}
            or value['version'] != 'clip-assets-0.1' or value['model_id'] != MODEL_ID
            or value['origin'] not in ('huggingface_snapshot', 'local_import')
            or (value['origin'] == 'huggingface_snapshot' and (not isinstance(value['revision'],str) or not re.fullmatch('[0-9a-f]{40}', value['revision'])))
            or (value['origin'] == 'local_import' and value['revision'] is not None)
            or not isinstance(value['files'], dict) or set(value['files']) != set(FILES)):
        raise ValueError('CLIP 모델 구성 파일이 올바르지 않습니다. clip-setup을 다시 실행하세요.')
    if any(not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest) for digest in value['files'].values()):
        raise ValueError('CLIP 자산 체크섬 형식이 올바르지 않습니다.')
    return value


def read_assets(directory, *, verify=False):
    directory = Path(directory).expanduser().resolve()
    value = validate_manifest(load_json(directory/MANIFEST))
    for name, digest in value['files'].items():
        path = directory/name
        if (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
                or not path.is_file() or path.stat().st_size == 0
                or (verify and file_hash(path) != digest)):
            raise ValueError('CLIP 모델 파일 누락 또는 체크섬 불일치: '+name)
    config = load_json(directory/'config.json')
    if not isinstance(config,dict) or any(not isinstance(config.get(k),dict) for k in ('vision_config','text_config')):
        raise ValueError('CLIP 모델 구성이 올바르지 않습니다.')
    vision, text = config.get('vision_config', {}), config.get('text_config', {})
    if (config.get('model_type') != 'clip' or config.get('projection_dim') != 512
            or any(vision.get(k) != v for k, v in {'image_size':224, 'patch_size':16, 'hidden_size':768, 'num_hidden_layers':12}.items())
            or any(text.get(k) != v for k, v in {'hidden_size':512, 'num_hidden_layers':12, 'vocab_size':49408}.items())):
        raise ValueError('지원하는 CLIP ViT-B/16 구성과 다릅니다.')
    return value


def setup_assets(output, *, source=None, revision='main'):
    """Download a resolved immutable snapshot, or prepare a user-supplied folder."""
    output = Path(output).expanduser().resolve()
    if output.exists():raise ValueError('CLIP 저장 폴더가 이미 있습니다. 새 폴더를 지정하세요.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bedpose-clip-', dir=output.parent) as temporary:
        stage = Path(temporary)/'model'
        if source is None:
            try:from huggingface_hub import HfApi, snapshot_download
            except ImportError as exc:raise ValueError('CLIP 설치 필요: python -m pip install -e ".[clip]"') from exc
            resolved = HfApi().model_info(MODEL_ID, revision=revision).sha
            if not isinstance(resolved, str) or not re.fullmatch('[0-9a-f]{40}', resolved):
                raise ValueError('CLIP 모델의 고정 리비전을 확인할 수 없습니다.')
            snapshot_download(MODEL_ID, revision=resolved, local_dir=stage, allow_patterns=list(FILES))
            origin = 'huggingface_snapshot'
        else:
            source = Path(source).expanduser().resolve()
            stage.mkdir()
            for name in FILES:shutil.copyfile(source/name, stage/name)
            resolved, origin = None, 'local_import'
        value = {'version':'clip-assets-0.1', 'model_id':MODEL_ID, 'revision':resolved,
                 'origin':origin, 'files':{name:file_hash(stage/name) for name in FILES}}
        atomic_json(stage/MANIFEST, value)
        read_assets(stage, verify=True)
        # Keep only the explicit asset set, not HF download metadata or partials.
        shutil.rmtree(stage/'.cache', ignore_errors=True)
        stage.rename(output)
    return output, value
