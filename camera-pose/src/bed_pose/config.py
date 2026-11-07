"""Configuration parsing with a packaged, strict draft contract."""
from importlib.resources import files
from pathlib import Path
from jsonschema import Draft202012Validator
from .io_utils import load_json, strict_loads


def contract_schema(schema_path=None):
    schema = (load_json(schema_path) if schema_path is not None else
              strict_loads(files('bed_pose').joinpath(
                  'schemas/benchmark-v0.1.schema.json').read_text(encoding='utf-8')))
    Draft202012Validator.check_schema(schema)
    return schema


def record_validator(schema_path=None):
    return Draft202012Validator(contract_schema(schema_path))


def load_run_config(path, schema_path=None):
    path = Path(path).resolve()
    config = load_json(path)
    if not isinstance(config, dict):
        raise ValueError('Configuration must be an object')
    config.setdefault('warmup_frames', 0)
    record_validator(schema_path).validate(config)
    if config.get('record_type') != 'run_config':
        raise ValueError('Expected run_config')
    resolved = dict(config)
    for key in ('input_manifest_path', 'output_root', 'adapter_options_path'):
        if config[key] is not None:
            resolved[key] = str((path.parent / config[key]).resolve())
    resolved['assets'] = [dict(asset, path=str((path.parent / asset['path']).resolve()))
                          for asset in config['assets']]
    return resolved
