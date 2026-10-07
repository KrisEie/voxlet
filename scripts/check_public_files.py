"""Check Git-tracked files before publication. Never inspect untracked personal data."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DIRS = {'user-data', 'models', '.runtime', '.cache', 'build', 'dist', 'release', 'media-tools'}
FORBIDDEN_NAMES = {'paths.json', 'runtime.json', 'settings.json', 'voices.json', 'update-voice-draft.json', '.env'}
FORBIDDEN_SUFFIXES = {'.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.mp4',
                      '.mov', '.mkv', '.webm', '.exe', '.dll', '.zip', '.bin', '.safetensors', '.pem', '.key'}


def check_file(root, relative):
    path = root / relative
    if set(relative.parts) & FORBIDDEN_DIRS or path.name in FORBIDDEN_NAMES:
        raise ValueError(f'Runtime or personal data: {relative}')
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        raise ValueError(f'Recording, executable, secret or model: {relative}')
    if path.suffix.lower() not in {'.png', '.ico'}:
        content = path.read_text(encoding='utf-8', errors='replace')
        if re.search(r'[A-Za-z]:[/\\]Users[/\\][^/\\\s]+', content, re.I):
            raise ValueError(f'Private machine path: {relative}')
        for prefix in ('gh' + 'p_', 'gh' + 'o_', 'github' + '_pat_', 'hf' + '_'):
            if re.search(re.escape(prefix) + r'[A-Za-z0-9_]{25,}', content):
                raise ValueError(f'Possible credential: {relative}')


def main():
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True)
    paths = [Path(p.decode('utf-8')) for p in result.stdout.split(b'\0') if p]
    for path in paths:
        check_file(ROOT, path)
    print(f'Public-file check passed: {len(paths)} tracked files; no runtime data or recordings.')


if __name__ == '__main__':
    main()
