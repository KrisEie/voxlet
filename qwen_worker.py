"""Owned, on-demand Qwen process. Audio is emitted while it is being generated."""
import json
import os
from pathlib import Path
import sys
import time
import traceback
from model_registry import QWEN_BASE, cached_snapshot

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
folder = Path(sys.argv[1])
model = None


def emit(file, event):
    with file.open('a', encoding='utf-8') as output:
        output.write(json.dumps(event, ensure_ascii=False)+'\n')
        output.flush()


while True:
    jobs = sorted(folder.glob('*.request.json'))
    if not jobs:
        time.sleep(.08)
        continue
    source = jobs[0]
    job = json.loads(source.read_text(encoding='utf-8'))
    events = Path(job['events'])
    started = time.perf_counter()
    try:
        if model is None:
            emit(events, {'kind':'status','value':'Loading the English voice engine…'})
            import torch
            import soundfile as sf
            import numpy as np
            from faster_qwen3_tts import FasterQwen3TTS
            torch.set_num_threads(4)
            if not torch.cuda.is_available():
                raise RuntimeError('English voice cloning needs a compatible NVIDIA GPU. Choose Alan (fast) for CPU reading.')
            model = FasterQwen3TTS.from_pretrained(cached_snapshot(QWEN_BASE),
                device='cuda', dtype=torch.float16, attn_implementation='sdpa')
            emit(events, {'kind':'status','value':'Warming up the voice engine for this session…'})
            model.warmup(prefill_len=256)
        first = True
        count = 0
        audio_seconds = 0
        for index, part in enumerate(job['chunks']):
            emit(events, {'kind':'status','value':f'Generating {job["voice"]["name"]} · {index+1}/{len(job["chunks"])}'})
            voice = job['voice']
            for x, sr, timing in model.generate_voice_clone_streaming(
                text=part['text'], language='English', ref_audio=voice['reference_path'],
                ref_text=voice['reference_text'], xvec_only=voice.get('language', 'en')=='no',
                chunk_size=12, max_new_tokens=2048):
                path = Path(job['output_dir']) / f'part-{count:05}.wav'
                sf.write(path, np.asarray(x).reshape(-1), sr, subtype='PCM_16')
                audio_seconds += len(np.asarray(x).reshape(-1))/sr
                emit(events, {'kind':'audio','value':dict(part, audio=str(path), index=index),
                              'first':first, 'elapsed':time.perf_counter()-started})
                first = False
                count += 1
        emit(events, {'kind':'done','value':{'engine':'qwen-stream','elapsed':time.perf_counter()-started,
                                            'audio_seconds':audio_seconds,'parts':len(job['chunks'])}})
    except Exception as error:
        emit(events, {'kind':'error','value':str(error),'traceback':traceback.format_exc()})
        # Failed graph/model setup must not poison the next attempt.
        model = None
    finally:
        source.unlink(missing_ok=True)
