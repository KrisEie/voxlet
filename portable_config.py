"""Machine-independent paths. Never import settings or voices from another install."""
import hashlib
import json
import os
from pathlib import Path
import sys


def application_root():
    return Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent


def load_paths(root):
    root = Path(root).resolve()
    os.environ['HF_HOME'] = str(root / '.cache' / 'huggingface')
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    paths = dict(root=str(root), voices=[], cuda_dirs=[], asr_device='cpu',
                 worker_python=str(root / '.runtime' / 'speech' / 'Scripts' / 'python.exe'),
                 qwen_python=str(root / '.runtime' / 'qwen' / 'Scripts' / 'python.exe'),
                 multilingual_python=str(root / '.runtime' / 'chatterbox' / 'Scripts' / 'python.exe'))
    config = root / 'runtime.json'
    if config.exists():
        paths.update(json.loads(config.read_text(encoding='utf-8')))
    paths['root'] = str(root)
    paths['voices'] = []  # Personal voices are created locally, never shipped in configuration.
    return paths


def instance_suffix():
    return hashlib.sha256(str(application_root()).casefold().encode()).hexdigest()[:12]
