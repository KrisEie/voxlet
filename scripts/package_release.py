"""Fail closed if runtime data or a reference recording enters a release folder."""
import hashlib
from pathlib import Path
import re
import sys
import zipfile

folder=Path(sys.argv[1]).resolve();output=Path(sys.argv[2]).resolve()
files=[]
for path in folder.rglob('*'):
    if not path.is_file():continue
    relative=path.relative_to(folder)
    if set(relative.parts)&{'user-data','models','.runtime','.cache','recovered-samples'}:
        raise RuntimeError(f'Private/runtime directory in release: {relative}')
    if path.suffix.lower() in {'.wav','.mp3','.m4a','.flac','.ogg','.opus','.mp4','.mov','.mkv','.webm','.safetensors'}:
        raise RuntimeError(f'Recording or model file in release: {relative}')
    if path.name in {'paths.json','runtime.json','settings.json','voices.json','update-voice-draft.json'}:
        raise RuntimeError(f'Machine-specific configuration in release: {relative}')
    if path.suffix.lower() in {'.py','.md','.txt','.json','.ps1','.cmd'} and path.stat().st_size<5_000_000:
        text=path.read_text(encoding='utf-8',errors='ignore').lower()
        if re.search(r'[a-z]:[/\\]users[/\\][^/\\\s]+',text):
            raise RuntimeError(f'Private build-machine path in release: {relative}')
    files.append((path,relative))
output.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
    for path,relative in files:archive.write(path,relative.as_posix())
with output.open('rb') as packed:
    digest=hashlib.file_digest(packed,'sha256').hexdigest()
output.with_suffix('.zip.sha256').write_text(f'{digest}  {output.name}\n',encoding='ascii')
print(f'Created {output.name}: {len(files)} files; no recordings or personal configuration.')
