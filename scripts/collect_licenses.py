"""Collect notices from installed desktop distributions, without personal files."""
import importlib.metadata as metadata
from pathlib import Path
import shutil
import sys

target=Path(sys.argv[1])/'THIRD_PARTY_LICENSES';target.mkdir(exist_ok=True)
names=['PySide6','PySide6_Essentials','PySide6_Addons','shiboken6','numpy','sounddevice',
       'soundfile','psutil','pywin32','comtypes','langid','cffi','pycparser','pyinstaller']
count=0
for name in names:
    distribution=metadata.distribution(name)
    for relative in distribution.files or []:
        path=Path(str(relative));base=path.name.lower()
        if not (base.startswith('license') or base.startswith('copying')):continue
        source=distribution.locate_file(relative)
        if not source.is_file():continue
        # Distribution metadata may contain ../ paths. Flatten those parts safely.
        safe=Path(*[p for p in path.parts if p not in ('..','.','')])
        output=target/name/safe;output.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,output);count+=1
if count<8:raise RuntimeError('Too few dependency notices found; check the build environment.')
print(f'Collected {count} desktop dependency notices.')
