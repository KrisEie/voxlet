"""Owned multilingual voice-cloning worker; models stay outside the UI process."""
import json
import os
from pathlib import Path
import sys
import time
import traceback
from reference_audio import prepare_reference
from model_registry import CHATTERBOX, cached_snapshot
os.environ['HF_HUB_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
folder=Path(sys.argv[1]);model=None;reference_stamp=None

def emit(path,event):
    with path.open('a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n');f.flush()

while True:
    jobs=sorted(folder.glob('*.request.json'))
    if not jobs:time.sleep(.08);continue
    source=jobs[0];job=json.loads(source.read_text(encoding='utf-8'));events=Path(job['events']);started=time.perf_counter()
    try:
        if model is None:
            emit(events,dict(kind='status',value='Loading the Norwegian voice engine…'))
            import torch
            import numpy as np
            import soundfile as sf
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
            torch.set_num_threads(4)
            if not torch.cuda.is_available():
                raise RuntimeError('This cloning setup needs a compatible NVIDIA GPU. Choose Norwegian (fast) for CPU reading.')
            torch.backends.cuda.matmul.allow_tf32=True
            torch.backends.cudnn.allow_tf32=True
            model=ChatterboxMultilingualTTS.from_local(cached_snapshot(CHATTERBOX),device='cuda',t3_model='v3')
        voice=job['voice'];audio_seconds=0
        calmer=job.get('delivery')=='calmer'
        exaggeration=.25 if calmer else .5
        temperature=.65 if calmer else .8
        # Prepare reference conditioning once; subsequent sentences reuse it.
        stamp=(voice['reference_path'],Path(voice['reference_path']).stat().st_mtime_ns)
        if stamp!=reference_stamp:
            clean_reference=Path(job['output_dir'])/'conditioning.wav'
            prepare_reference(voice['reference_path'],clean_reference)
            model.prepare_conditionals(str(clean_reference),exaggeration=exaggeration);reference_stamp=stamp
        for index,part in enumerate(job['chunks']):
            emit(events,dict(kind='status',value=f'Generating {voice["name"]} in Norwegian · {index+1}/{len(job["chunks"])}'))
            wav=model.generate(part['text'],language_id=job['language'],exaggeration=exaggeration,cfg_weight=.5,temperature=temperature)
            x=wav.detach().float().cpu().numpy().reshape(-1)
            path=Path(job['output_dir'])/f'part-{index:04}.wav'
            sf.write(path,x,model.sr,subtype='PCM_16');audio_seconds+=len(x)/model.sr
            emit(events,dict(kind='audio',value=dict(part,audio=str(path),index=index),elapsed=time.perf_counter()-started))
        emit(events,dict(kind='done',value=dict(engine='chatterbox-v3',elapsed=time.perf_counter()-started,audio_seconds=audio_seconds)))
    except Exception as error:
        emit(events,dict(kind='error',value=str(error),traceback=traceback.format_exc()));model=None;reference_stamp=None
    finally:source.unlink(missing_ok=True)
