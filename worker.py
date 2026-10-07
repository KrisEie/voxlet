"""Short-lived local audio workers. No model is imported by the desktop UI."""
import json
import os
import sys
import traceback
from pathlib import Path


def run(request):
    task = request['task']
    if task == 'transcribe':
        # Optional CUDA directories come from this installation's runtime.json.
        dll_handles = []
        for directory in request.get('cuda_dirs', []):
            if Path(directory).is_dir():
                dll_handles.append(os.add_dll_directory(directory))
                os.environ['PATH'] = directory + os.pathsep + os.environ['PATH']
        from faster_whisper import WhisperModel
        device = request.get('device', 'cpu')
        try:
            model = WhisperModel(request['model'], device=device,
                                 compute_type='float16' if device == 'cuda' else 'int8',
                                 cpu_threads=4, local_files_only=True)
            segments, info = model.transcribe(
                request['audio'], language=request.get('language') or None,
                task='transcribe', beam_size=5, vad_filter=True,
                vad_parameters={'min_silence_duration_ms': 500},
                condition_on_previous_text=False, hotwords=request.get('vocabulary') or None)
            text = ' '.join(segment.text.strip() for segment in segments).strip()
        except Exception:
            if device != 'cuda':
                raise
            # A machine without working CUDA can still dictate locally.
            model = WhisperModel(request['model'], device='cpu', compute_type='int8',
                                 cpu_threads=4, local_files_only=True)
            segments, info = model.transcribe(request['audio'], language=request.get('language') or None,
                                             beam_size=5, vad_filter=True,
                                             condition_on_previous_text=False, hotwords=request.get('vocabulary') or None)
            text = ' '.join(segment.text.strip() for segment in segments).strip()
            device = 'cpu'
        return {'text': text, 'language': 'no' if info.language in ('no','nb','nn') else info.language, 'device': device}
    if task == 'piper':
        import wave
        import onnxruntime as ort
        from piper import PiperVoice
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        # Piper is intentionally a CPU voice, with no persistent GPU allocation.
        voice = PiperVoice.load(request['model'], use_cuda=False)
        with wave.open(request['output'], 'wb') as audio:
            voice.synthesize_wav(request['text'], audio)
        return {'audio': request['output'], 'engine': 'piper', 'language': 'no'}
    if task == 'piper_stream':
        import time
        import numpy as np
        import soundfile as sf
        from piper import PiperVoice
        started = time.perf_counter()
        voice = PiperVoice.load(request['model'], use_cuda=False)
        duration = 0
        for index, part in enumerate(request['chunks']):
            audio = list(voice.synthesize(part['text']))
            samples = np.concatenate([x.audio_float_array for x in audio])
            rate = audio[0].sample_rate
            path = str(Path(request['events']).parent / f'part-{index:04}.wav')
            sf.write(path, samples, rate, subtype='PCM_16')
            duration += len(samples)/rate
            with open(request['events'], 'a', encoding='utf-8') as events:
                events.write(json.dumps({'kind':'audio','value':dict(part, audio=path, index=index),
                                        'elapsed':time.perf_counter()-started})+'\n')
        return {'engine':'piper','elapsed':time.perf_counter()-started,'audio_seconds':duration}
    raise ValueError('Unknown task')


if __name__ == '__main__':
    source = Path(sys.argv[1])
    destination = source.with_suffix('.result.json')
    try:
        result = run(json.loads(source.read_text(encoding='utf-8')))
        result['ok'] = True
    except Exception as error:
        result = {'ok': False, 'error': str(error), 'traceback': traceback.format_exc()}
    destination.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    sys.exit(0 if result['ok'] else 1)
