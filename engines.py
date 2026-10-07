"""Local speech engines with session-scoped, disposable audio."""
import contextlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import uuid

class Cancelled(Exception):
    pass

def text_parts(text, limit=260):
    pieces=[]
    for match in re.finditer(r'[^.!?\n]+(?:[.!?]+["\u201d\u2019]?|\n|$)',text):
        raw=match.group();content=raw.strip()
        if not content:continue
        start=match.start()+len(raw)-len(raw.lstrip())
        while len(content)>limit:
            cut=content.rfind(' ',0,limit)
            if cut<1:cut=limit
            value=content[:cut].strip()
            pieces.append(dict(text=value,char_start=start,char_end=start+len(value)))
            rest=content[cut:];skip=len(rest)-len(rest.lstrip());start+=cut+skip;content=rest.lstrip()
        if content:pieces.append(dict(text=content,char_start=start,char_end=start+len(content)))
    return pieces

_identifier=None
def language_details(text):
    global _identifier
    if not text.strip():return 'en',0
    if _identifier is None:
        from langid.langid import LanguageIdentifier,model
        _identifier=LanguageIdentifier.from_modelstring(model,norm_probs=True)
        _identifier.set_languages([c for c in ['en','nb','nn','no'] if c in _identifier.nb_classes])
    code,confidence=_identifier.classify(text)
    return ('no' if code in ['no','nb','nn'] else 'en'),float(confidence)

def detect_language(text):return language_details(text)[0]

class Engines:
    def __init__(self,root,owner,paths):
        self.root,self.owner,self.paths=root,owner,paths
        self.data=root/'user-data';self.data.mkdir(exist_ok=True)
        self.voice_file=self.data/'voices.json'
        if not self.voice_file.exists():self.voice_file.write_text(json.dumps(paths['voices'],ensure_ascii=False,indent=2),encoding='utf-8')
        self.voices=json.loads(self.voice_file.read_text(encoding='utf-8'))
        self.backend=None;self.multilingual_backend=None
        self.active=0;self.last_use=0;self.lock=threading.RLock();self.idle_seconds=45
        self.jobs=self.data/'streaming';self.jobs.mkdir(exist_ok=True)
        self.temp_root=self.data/'temporary-readings';self.temp_root.mkdir(exist_ok=True)
        self.sessions=set();self.session_active=set();self.pending_delete=set()
        self.accurate=True;self.vocabulary='';self.norwegian_delivery='standard'
        # Restart recovery: only our UUID-named disposable sessions are eligible.
        for p in self.temp_root.iterdir():
            if p.is_dir() and re.fullmatch('[0-9a-f]{32}',p.name):self._delete_folder(p)
        references=self.data/'references'
        if references.exists():
            for p in references.glob('pending-*.wav'):
                if re.fullmatch(r'pending-[0-9a-f]{32}\.wav',p.name):p.unlink(missing_ok=True)

    def _delete_folder(self,path):
        path=Path(path).resolve();parent=self.temp_root.resolve()
        if path.parent!=parent or not re.fullmatch('[0-9a-f]{32}',path.name):raise ValueError('Invalid temporary audio path')
        shutil.rmtree(path,ignore_errors=True)

    def new_session(self):
        path=self.temp_root/uuid.uuid4().hex;path.mkdir();self.sessions.add(str(path));return str(path)

    def cleanup_session(self,session):
        if not session:return
        with self.lock:
            if session in self.session_active:self.pending_delete.add(session);return
            self._delete_folder(session);self.sessions.discard(session);self.pending_delete.discard(session)

    def cleanup_all(self):
        for session in list(self.sessions):self.cleanup_session(session)

    def save_voices(self):
        staged=self.voice_file.with_name('voices-'+uuid.uuid4().hex+'.tmp')
        try:
            staged.write_text(json.dumps(self.voices,ensure_ascii=False,indent=2),encoding='utf-8')
            staged.replace(self.voice_file)
        finally:staged.unlink(missing_ok=True)

    def delete_voice(self,key):
        with self.lock:
            if self.active:raise RuntimeError('Stop reading or recording before deleting a voice.')
            voice=next((v for v in self.voices if v['key']==key),None)
            if voice is None:raise ValueError('This voice is no longer in the library.')
            previous=self.voices
            self.voices=[v for v in self.voices if v['key']!=key]
            try:self.save_voices()
            except Exception:
                self.voices=previous
                raise
            self.cancel_backend()
            cleanup_error=None
            # Only a personal sample created in our own references folder is ours
            # to remove. Uploaded originals, exports and bundled voice assets stay.
            if re.fullmatch(r'own-[0-9a-f]{32}',key) and voice.get('reference_path'):
                reference=Path(voice['reference_path']).resolve()
                owned=self.data.resolve()/'references'/(key+'.wav')
                shared=any(v.get('reference_path') and Path(v['reference_path']).resolve()==reference for v in self.voices)
                if reference==owned and not shared:
                    try:reference.unlink(missing_ok=True)
                    except OSError as error:cleanup_error=str(error)
            return dict(voice=voice,cleanup_error=cleanup_error)

    @contextlib.contextmanager
    def operation(self):
        with self.lock:self.active+=1
        try:yield
        finally:
            with self.lock:self.active-=1;self.last_use=time.monotonic()

    def worker(self,request,cancel):
        if not Path(self.paths['worker_python']).is_file():
            raise RuntimeError('Speech tools are not installed yet. Close Voxlet and run Setup.cmd.')
        with tempfile.TemporaryDirectory(prefix='voxlet-',dir=self.data) as folder:
            source=Path(folder)/'request.json';source.write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
            child=self.owner.spawn([self.paths['worker_python'],self.root/'worker.py',source],cwd=self.root)
            try:
                while True:
                    if cancel.wait(.1):raise Cancelled()
                    if child.poll() is not None:
                        result_file=source.with_suffix('.result.json')
                        if not result_file.exists():raise RuntimeError('Speech engine stopped without returning a result.')
                        result=json.loads(result_file.read_text(encoding='utf-8'))
                        if not result.get('ok'):raise RuntimeError(result.get('error','Speech engine failed.'))
                        return result
            finally:child.terminate()

    def transcribe(self,audio,language,cancel,status):
        with self.operation():
            # The specialised model is biased toward Norwegian on automatic
            # detection. Detect with Turbo first so English samples stay English.
            request=dict(task='transcribe',audio=str(audio),language=language,
                cuda_dirs=self.paths.get('cuda_dirs',[]),device=self.paths.get('asr_device','cpu'),vocabulary=self.vocabulary)
            if language is None and self.accurate:
                status('Detecting the sample language…')
                detected=self.worker(dict(request,model=str(self.root/'models/whisper-turbo')),cancel)
                if detected['language'] not in ('no','nn','nb'):return detected
                language='no';request['language']=language
            model='nb-whisper-large' if self.accurate and language!='en' else 'whisper-turbo'
            status('Transcribing locally with '+('NB-Whisper…' if model=='nb-whisper-large' else 'Whisper Turbo…'))
            return self.worker(dict(request,model=str(self.root/'models'/model)),cancel)

    def read_events(self,path,offset):
        if not path.exists():return [],offset
        with path.open('rb') as source:source.seek(offset);raw=source.read()
        end=raw.rfind(b'\n')+1
        return [json.loads(line) for line in raw[:end].decode('utf-8').splitlines() if line],offset+end

    def speak(self,text,language,voice_key,cancel,status,ready,mode='studio',session=None):
        session=session or self.new_session()
        with self.lock:self.session_active.add(session)
        try:
            with self.operation():return self._speak(text,language,voice_key,cancel,status,ready,session)
        finally:
            with self.lock:
                self.session_active.discard(session)
                if cancel.is_set() or session in self.pending_delete:self.cleanup_session(session)

    def _speak(self,text,language,voice_key,cancel,status,ready,session):
        folder=Path(session);chunks=text_parts(text);events=folder/'events.jsonl';job_id=folder.name
        engine='piper' if voice_key.startswith('piper:') else 'chatterbox' if language=='no' else 'qwen-stream'
        runtime='worker_python' if engine=='piper' else 'multilingual_python' if engine=='chatterbox' else 'qwen_python'
        if not Path(self.paths[runtime]).is_file():
            raise RuntimeError('Run Setup.cmd full to install voice cloning, or choose a fast voice.' if engine!='piper' else 'Run Setup.cmd to install speech tools.')
        voice=next((v for v in self.voices if v['key']==voice_key),None)
        if engine!='piper' and voice is None:raise ValueError('Choose an available voice.')
        child=None;source=folder/'worker.json'
        if engine=='piper':
            model=self.root/'models/piper'/('no/no_NO/talesyntese/medium/no_NO-talesyntese-medium.onnx' if language=='no' else 'en/en_GB/alan/medium/en_GB-alan-medium.onnx')
            status('Preparing fast '+('Norwegian' if language=='no' else 'English')+' speech…')
            source.write_text(json.dumps(dict(task='piper_stream',model=str(model),chunks=chunks,events=str(events)),ensure_ascii=False),encoding='utf-8')
            child=self.owner.spawn([self.paths['worker_python'],self.root/'worker.py',source],cwd=self.root)
        else:
            attr='multilingual_backend' if engine=='chatterbox' else 'backend'
            mailbox=self.jobs/('multilingual' if engine=='chatterbox' else 'english');mailbox.mkdir(exist_ok=True)
            with self.lock:
                process=getattr(self,attr)
                if not process or process.poll() is not None:
                    for abandoned in mailbox.glob('*.request.json'):abandoned.unlink(missing_ok=True)
                    script='chatterbox_worker.py' if engine=='chatterbox' else 'qwen_worker.py'
                    python=self.paths['multilingual_python'] if engine=='chatterbox' else self.paths['qwen_python']
                    process=self.owner.spawn([python,self.root/script,mailbox],cwd=self.root);setattr(self,attr,process)
            staged=mailbox/(job_id+'.tmp')
            staged.write_text(json.dumps(dict(id=job_id,events=str(events),output_dir=str(folder),voice=voice,chunks=chunks,language=language,delivery=self.norwegian_delivery),ensure_ascii=False),encoding='utf-8')
            staged.replace(mailbox/(job_id+'.request.json'));status('Preparing '+voice['name']+'…')
        offset=0;first=None;deadline=time.monotonic()+900
        try:
            while time.monotonic()<deadline:
                if cancel.wait(.05):raise Cancelled()
                rows,offset=self.read_events(events,offset)
                for event in rows:
                    if event['kind']=='status':status(event['value'])
                    elif event['kind']=='audio':
                        if first is None:first=event['elapsed']
                        ready(event['value'])
                    elif event['kind']=='error':raise RuntimeError(event['value'])
                    elif event['kind']=='done':return dict(event['value'],first_audio_seconds=first,cached=False)
                if child and child.poll() is not None:
                    result_path=source.with_suffix('.result.json')
                    if not result_path.exists():raise RuntimeError('Reading stopped before audio was ready.')
                    result=json.loads(result_path.read_text(encoding='utf-8'))
                    if not result.get('ok'):raise RuntimeError(result.get('error','Reading failed.'))
                    # Drain final events after the worker has exited.
                    rows,offset=self.read_events(events,offset)
                    for event in rows:
                        if event['kind']=='audio':
                            if first is None:first=event['elapsed']
                            ready(event['value'])
                    return dict(result,first_audio_seconds=first,cached=False)
                if not child and process.poll() is not None:raise RuntimeError('Voice engine stopped. Try reading again.')
            raise RuntimeError('Reading took too long. Try a shorter passage.')
        finally:
            if child:child.terminate()
            else:
                (mailbox/(job_id+'.request.json')).unlink(missing_ok=True)

    def idle_tick(self):
        with self.lock:
            if not self.active and time.monotonic()-self.last_use>self.idle_seconds:
                loaded=bool(self.backend or self.multilingual_backend);self.cancel_backend();return loaded
        return False

    def cancel_backend(self):
        with self.lock:
            for attr in ('backend','multilingual_backend'):
                process=getattr(self,attr)
                if process:process.terminate();setattr(self,attr,None)
