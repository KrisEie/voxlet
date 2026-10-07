"""The native workstation interface and reference-voice capture dialog."""
import json
from pathlib import Path
import shutil
import threading
import uuid
import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPalette, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QComboBox,QPlainTextEdit,QTabWidget,QFrame,QSlider,QCheckBox,QListWidget,QListWidgetItem,
    QDialog,QLineEdit,QFileDialog,QDialogButtonBox,QFormLayout,QKeySequenceEdit,QDoubleSpinBox,QScrollArea)
from playback import Player,microphone_audio
from samples import VOICE_PROMPTS, READER_EXAMPLES
from media import MEDIA_FILTER, extract_reference
from engines import Cancelled
from lifecycle import startup_enabled

def label(text,name='muted'):
    x=QLabel(text);x.setObjectName(name);return x

def button(text,action,primary=False):
    x=QPushButton(text);x.clicked.connect(action)
    if primary:x.setObjectName('primary')
    return x

def build_ui(s):
    widget=QWidget();s.setCentralWidget(widget)
    layout=QVBoxLayout(widget);layout.setContentsMargins(26,20,26,16);layout.setSpacing(15)
    top=QHBoxLayout()
    branding=QVBoxLayout();branding.setSpacing(1)
    branding.addWidget(label('Voxlet','brand'));branding.addWidget(label('Speak, write, listen. On your PC.'))
    icon=QLabel();icon.setPixmap(QPixmap(str(s.asset_root/'voxlet.png')).scaled(48,48,Qt.KeepAspectRatio,Qt.SmoothTransformation));top.addWidget(icon);top.addLayout(branding);top.addStretch()
    s.resources=label('');top.addWidget(s.resources);layout.addLayout(top)
    bar=QFrame();bar.setObjectName('toolbar');row=QHBoxLayout(bar);row.setContentsMargins(15,10,15,10)
    for title,attr in [('Microphone','microphone'),('Output','output')]:
        column=QVBoxLayout();column.setSpacing(5);column.addWidget(label(title,'eyebrow'))
        combo=QComboBox();combo.setAccessibleName(title);combo.currentIndexChanged.connect(s.save_settings)
        setattr(s,attr,combo);column.addWidget(combo);row.addLayout(column,1)
    row.addWidget(button('Refresh',s.fill_devices));layout.addWidget(bar)
    s.tabs=QTabWidget();s.tabs.setDocumentMode(True);layout.addWidget(s.tabs,1)
    dictate=QWidget();dl=QVBoxLayout(dictate);dl.setContentsMargins(0,17,0,0);dl.setSpacing(12)
    heading=QHBoxLayout();heading.addWidget(label('Dictate','section'));heading.addStretch()
    s.dict_language=QComboBox()
    for text,value in [('Norwegian','no'),('English','en'),('Detect language',None)]:s.dict_language.addItem(text,value)
    s.dict_language.setCurrentIndex(s.config.get('dict_language',0));s.dict_language.currentIndexChanged.connect(s.save_settings)
    heading.addWidget(s.dict_language);dl.addLayout(heading)
    s.dictation_hint=label('');s.dictation_hint.setWordWrap(True);dl.addWidget(s.dictation_hint)
    meter=QFrame();meter.setObjectName('toolbar');ml=QVBoxLayout(meter);ml.setSpacing(5)
    s.level=s.level_class();ml.addWidget(s.level)
    gain=QHBoxLayout();s.gain_label=label('Gain  +12 dB')
    gain.addWidget(s.gain_label);s.mic_gain=QSlider(Qt.Horizontal);s.mic_gain.setRange(0,24)
    s.mic_gain.setValue(s.config.get('mic_gain',12));s.mic_gain.setAccessibleName('Microphone gain')
    s.mic_gain.valueChanged.connect(s.save_settings);gain.addWidget(s.mic_gain,1)
    s.auto_gain=QCheckBox('Auto level');s.auto_gain.setChecked(s.config.get('auto_gain',True))
    s.auto_gain.toggled.connect(s.save_settings);gain.addWidget(s.auto_gain)
    s.mic_level_label=label('Start a microphone test to check your level.');ml.addLayout(gain);ml.addWidget(s.mic_level_label);dl.addWidget(meter)
    actions=QHBoxLayout()
    s.dict_button=button('Start dictation',lambda:s.toggle_record('dictate',external=False),True)
    s.test_button=button('Test microphone',lambda:s.toggle_record('test',external=False))
    s.play_test=button('Play test',s.preview_recording);s.play_test.setEnabled(False)
    s.save_test=button('Save recording',s.export_test);s.save_test.setEnabled(False)
    for b in [s.dict_button,s.test_button,s.play_test,s.save_test]:actions.addWidget(b)
    dl.addLayout(actions)
    s.transcript=QPlainTextEdit();s.transcript.setAccessibleName('Last transcript')
    s.transcript.setPlaceholderText('Your transcript appears here. You can edit it before copying.');dl.addWidget(s.transcript,1)
    ending=QHBoxLayout();ending.addWidget(button('Copy text',lambda:QApplication.clipboard().setText(s.transcript.toPlainText())))
    ending.addStretch();ending.addWidget(label('Your audio is processed locally.'));dl.addLayout(ending)
    s.tabs.addTab(dictate,'Dictation')
    read=QWidget();rl=QVBoxLayout(read);rl.setContentsMargins(0,17,0,0);rl.setSpacing(11)
    heading=QHBoxLayout();heading.addWidget(label('Read aloud','section'));heading.addStretch()
    s.reading_hint=label('');heading.addWidget(s.reading_hint);rl.addLayout(heading)
    options=QHBoxLayout()
    for title,attr in [('English voice','voice'),('Norwegian voice','nor_voice')]:
        col=QVBoxLayout();col.setSpacing(5);col.addWidget(label(title,'eyebrow'))
        combo=QComboBox();combo.setAccessibleName(title);combo.currentIndexChanged.connect(s.save_settings)
        setattr(s,attr,combo);col.addWidget(combo);options.addLayout(col,1)
    rl.addLayout(options)
    language_row=QHBoxLayout();language_row.addWidget(label('Text language'))
    s.read_language=QComboBox()
    for text,value in [('Detect language','auto'),('Norwegian','no'),('English','en')]:s.read_language.addItem(text,value)
    s.read_language.setCurrentIndex(s.config.get('read_language',0));s.read_language.currentIndexChanged.connect(s.save_settings)
    language_row.addWidget(s.read_language);language_row.addStretch()
    language_row.addWidget(label('Norwegian delivery'))
    s.nor_delivery=QComboBox();s.nor_delivery.setAccessibleName('Norwegian delivery')
    s.nor_delivery.addItem('Standard','standard');s.nor_delivery.addItem('Calmer','calmer')
    s.nor_delivery.setCurrentIndex(max(0,s.nor_delivery.findData(s.config.get('norwegian_delivery','standard'))))
    s.nor_delivery.currentIndexChanged.connect(s.save_settings);language_row.addWidget(s.nor_delivery)
    language_row.addWidget(button('Try Norwegian',lambda:s.reader_example('no')))
    language_row.addWidget(button('Try English',lambda:s.reader_example('en')))
    language_row.addWidget(button('Try mixed text',lambda:s.reader_example('mixed')));rl.addLayout(language_row)
    s.language_note=label('Language is detected automatically. You can override it below.')
    s.language_note.setWordWrap(True);rl.addWidget(s.language_note)
    model_note=label('Cloned voices: English uses Qwen3-TTS. Norwegian uses Chatterbox V3. Calmer changes the Norwegian delivery.')
    model_note.setWordWrap(True);rl.addWidget(model_note)
    s.read_text=QPlainTextEdit();s.read_text.setAccessibleName('Text to read')
    s.read_text.setPlaceholderText('Paste text here. The current sentence is highlighted while it plays.');rl.addWidget(s.read_text,1)
    player=QFrame();player.setObjectName('player');pl=QVBoxLayout(player);pl.setSpacing(8)
    progress=QHBoxLayout();s.elapsed_label=label('0:00','time');s.duration_label=label('0:00','time')
    s.seek=QSlider(Qt.Horizontal);s.seek.setRange(0,0);s.seek.setAccessibleName('Playback position')
    s.seek.sliderReleased.connect(s.seek_player)
    progress.addWidget(s.elapsed_label);progress.addWidget(s.seek,1);progress.addWidget(s.duration_label);pl.addLayout(progress)
    controls=QHBoxLayout()
    s.back_button=button('↶  10 s',lambda:s.jump_player(-10));s.pause_button=button('▶  Play',s.pause_player)
    s.forward_button=button('10 s  ↷',lambda:s.jump_player(10))
    s.previous_sentence=button('‹ Sentence',lambda:s.jump_sentence(-1));s.next_sentence=button('Sentence ›',lambda:s.jump_sentence(1))
    for b in [s.previous_sentence,s.back_button,s.pause_button,s.forward_button,s.next_sentence]:controls.addWidget(b)
    controls.addStretch();controls.addWidget(label('Volume'))
    s.volume=QSlider(Qt.Horizontal);s.volume.setRange(0,100);s.volume.setValue(s.config.get('volume',75));s.volume.setFixedWidth(100)
    s.volume.valueChanged.connect(s.save_settings);controls.addWidget(s.volume);pl.addLayout(controls)
    s.playback_note=label('Seek through available audio, or click in the text and choose Read from here.')
    pl.addWidget(s.playback_note);rl.addWidget(player)
    buttons=QHBoxLayout();s.read_button=button('Read text',lambda:s.start_reading(s.read_text.toPlainText()),True)
    buttons.addWidget(s.read_button);buttons.addWidget(button('Read from here',s.read_from_cursor))
    s.save_audio_button=button('Save audio',s.export_audio);s.save_audio_button.setEnabled(False);buttons.addWidget(s.save_audio_button)
    buttons.addStretch();s.timing_label=label('');buttons.addWidget(s.timing_label);rl.addLayout(buttons)
    s.tabs.addTab(read,'Reader')
    voices=QWidget();vl=QVBoxLayout(voices);vl.setContentsMargins(0,17,0,0);vl.setSpacing(12)
    heading=QHBoxLayout();heading.addWidget(label('Voice library','section'));heading.addStretch()
    heading.addWidget(button('+  Create a voice',s.add_voice,True));vl.addLayout(heading)
    text=label('Upload audio or a video, or record using a reading prompt. Choose a clear part with one person speaking.\nLanguage and transcript are filled in automatically. Review the words, then save your voice.')
    text.setWordWrap(True);vl.addWidget(text)
    s.voice_list=QListWidget();s.voice_list.setObjectName('voices');s.voice_list.setSpacing(6);vl.addWidget(s.voice_list,1)
    s.voice_list.currentItemChanged.connect(s.select_voice)
    row=QHBoxLayout();row.addWidget(button('Use for English',lambda:s.use_selected_voice('en')));row.addWidget(button('Use for Norwegian',lambda:s.use_selected_voice('no')));row.addWidget(button('Rename',s.rename_voice))
    s.delete_voice_button=button('Delete voice',s.delete_voice);s.delete_voice_button.setObjectName('danger');s.delete_voice_button.setEnabled(False);row.addWidget(s.delete_voice_button);row.addStretch();vl.addLayout(row)
    s.tabs.addTab(voices,'Voices')
    settings_contents=QWidget();sl=QVBoxLayout(settings_contents);sl.setContentsMargins(0,17,14,10);sl.setSpacing(15)
    sl.addWidget(label('Make it yours','section'))
    shortcuts=QFormLayout();shortcuts.setVerticalSpacing(12)
    s.dictation_key=QKeySequenceEdit(QKeySequence(s.config.get('dictation_shortcut','Ctrl+Shift+Space')))
    s.reading_key=QKeySequenceEdit(QKeySequence(s.config.get('reading_shortcut','Alt+O')))
    for edit in (s.dictation_key,s.reading_key):edit.setMaximumSequenceLength(1);edit.setFixedHeight(42)
    shortcuts.addRow('Start / stop dictation',s.dictation_key);shortcuts.addRow('Read selection / pause',s.reading_key)
    sl.addLayout(shortcuts);row=QHBoxLayout();row.addWidget(button('Apply shortcuts',s.apply_shortcuts));row.addStretch();sl.addLayout(row)
    note=label('Alt+P is reserved for SteelSeries Moments. Esc cancels the current recording or reading.');note.setWordWrap(True);sl.addWidget(note)
    sl.addWidget(label('When you close the window','section'))
    s.keep_background=QCheckBox('Keep shortcuts available in the notification area')
    s.keep_background.setChecked(s.config.get('keep_background',True));s.keep_background.toggled.connect(s.save_settings);sl.addWidget(s.keep_background)
    s.start_windows=QCheckBox('Start Voxlet with Windows, with the window hidden')
    s.start_windows.setChecked(startup_enabled());s.start_windows.toggled.connect(s.change_startup);sl.addWidget(s.start_windows)
    note=label('Closing the window keeps a small shortcut listener running. Speech models unload when idle.\nChoose Quit Voxlet from the icon beside the clock to stop everything.');note.setWordWrap(True);sl.addWidget(note)
    sl.addWidget(label('Transcription','section'))
    s.accuracy=QCheckBox('Use NB-Whisper for accurate Norwegian transcription')
    s.accuracy.setChecked(s.config.get('accurate_norwegian',True));s.accuracy.toggled.connect(s.save_settings);sl.addWidget(s.accuracy)
    sl.addWidget(label('Turn this off to use the faster general model. Both work locally.'))
    s.vocabulary=QLineEdit(s.config.get('vocabulary',''));s.vocabulary.setPlaceholderText('Names and terms, e.g. Astra, SteelSeries, Voxlet')
    s.vocabulary.setMinimumHeight(38)
    s.vocabulary.editingFinished.connect(s.save_settings);sl.addWidget(s.vocabulary)
    sl.addWidget(label('Temporary audio','section'))
    s.delete_audio=QCheckBox('Delete temporary reading audio after playback')
    s.delete_audio.setChecked(s.config.get('delete_after_playback',True));s.delete_audio.toggled.connect(s.save_settings);sl.addWidget(s.delete_audio)
    note=label('Pause and seek while listening. Save audio before playback ends if you want a copy.\nEsc and Quit Voxlet clear temporary audio. Saved voices and exports are kept.');note.setWordWrap(True);sl.addWidget(note)
    sl.addStretch()
    settings=QScrollArea();settings.setWidgetResizable(True);settings.setFrameShape(QFrame.NoFrame)
    settings.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);settings.setWidget(settings_contents)
    s.tabs.addTab(settings,'Settings')
    bottom=QHBoxLayout();s.status=label('Ready','status');s.status.setWordWrap(True);bottom.addWidget(s.status,1)
    s.stop_button=button('Stop  ·  Esc',s.cancel);s.stop_button.setEnabled(False);bottom.addWidget(s.stop_button);layout.addLayout(bottom)
    layout.addWidget(label('Close the window to keep shortcuts ready. Quit Voxlet from the icon beside the clock to stop the app.','footer'))


class VoiceDialog(QDialog):
    result=Signal(str,object)
    def __init__(self,studio,recorder_factory):
        super().__init__(studio)
        self.s=studio;self.recorder=recorder_factory();self.player=Player()
        self.cancel_event=threading.Event();self.temp=None;self.recording=False;self.working=False
        self.source_path=None;self.expected_language=None
        self.setWindowTitle('Create a voice');self.resize(740,850)
        layout=QVBoxLayout(self);layout.setContentsMargins(24,20,24,20);layout.setSpacing(10)
        layout.addWidget(label('Name your voice','section'))
        hint=label('Use your normal voice in a quiet room, without music or other speakers.');hint.setWordWrap(True);layout.addWidget(hint)
        self.name=QLineEdit();self.name.setPlaceholderText('e.g. My narrator');layout.addWidget(self.name)
        prompt=QFrame();prompt.setObjectName('toolbar');pl=QVBoxLayout(prompt);pl.setSpacing(8)
        row=QHBoxLayout();row.addWidget(label('Something to read','eyebrow'));self.prompt_language=QComboBox()
        for key,(name,language,text) in VOICE_PROMPTS.items():self.prompt_language.addItem(name,key)
        default_prompt='en' if self.s.dict_language.currentData()=='en' else 'nb_long'
        self.prompt_language.setCurrentIndex(self.prompt_language.findData(default_prompt))
        self.prompt_language.currentIndexChanged.connect(self.update_prompt);row.addWidget(self.prompt_language);row.addStretch()
        row.addWidget(button('Copy prompt',lambda:QApplication.clipboard().setText(self.prompt_text.toPlainText())));pl.addLayout(row)
        self.prompt_text=QPlainTextEdit();self.prompt_text.setReadOnly(True);self.prompt_text.setAccessibleName('Suggested recording text')
        self.prompt_text.setFixedHeight(134);pl.addWidget(self.prompt_text)
        self.prompt_help=label('');self.prompt_help.setWordWrap(True);pl.addWidget(self.prompt_help)
        layout.addWidget(prompt);self.update_prompt()
        row=QHBoxLayout()
        self.upload=button('Choose audio or video',self.choose_file);row.addWidget(self.upload)
        self.record=button('Record sample',self.record_sample);row.addWidget(self.record)
        self.preview=button('Play sample',self.preview_sample);self.preview.setEnabled(False);row.addWidget(self.preview);layout.addLayout(row)
        self.file_note=label('No sample selected');self.file_note.setWordWrap(True);layout.addWidget(self.file_note)
        self.clip_box=QFrame();self.clip_box.setObjectName('toolbar');cl=QVBoxLayout(self.clip_box);cl.setSpacing(6)
        cl.addWidget(label('Choose the part with clear speech','eyebrow'));row=QHBoxLayout()
        row.addWidget(label('Start'));self.clip_start=QDoubleSpinBox();self.clip_start.setRange(0,86400);self.clip_start.setDecimals(1);self.clip_start.setSuffix(' s');self.clip_start.setAccessibleName('Clip start');row.addWidget(self.clip_start)
        row.addWidget(label('Length'));self.clip_length=QDoubleSpinBox();self.clip_length.setRange(3,90);self.clip_length.setValue(25);self.clip_length.setDecimals(1);self.clip_length.setSuffix(' s');self.clip_length.setAccessibleName('Clip length');row.addWidget(self.clip_length)
        self.use_clip=button('Use this clip',self.reimport_clip);row.addWidget(self.use_clip);cl.addLayout(row);layout.addWidget(self.clip_box);self.clip_box.hide()
        row=QHBoxLayout();row.addWidget(label('Sample language','eyebrow'));self.language=QComboBox()
        self.language.addItem('English','en');self.language.addItem('Norwegian','no')
        self.language.setCurrentIndex(1 if VOICE_PROMPTS[self.prompt_language.currentData()][1]=='no' else 0)
        row.addWidget(self.language);row.addStretch();layout.addLayout(row)
        self.text=QPlainTextEdit();self.text.setAccessibleName('Recording transcript');self.text.setMinimumHeight(105);self.text.setPlaceholderText('The exact words spoken in your sample…');layout.addWidget(self.text,1)
        self.note=label('Check that these words match the recording exactly before saving.')
        self.note.setWordWrap(True);layout.addWidget(self.note)
        row=QHBoxLayout();self.retry=button('Transcribe again',self.transcribe);self.retry.setEnabled(False);row.addWidget(self.retry);row.addStretch()
        self.save=button('Save voice',self.save_voice,True);self.save.setEnabled(False);row.addWidget(self.save)
        row.addWidget(button('Cancel',self.reject));layout.addLayout(row)
        self.result.connect(self.on_result)
        self.timer=QTimer(self);self.timer.timeout.connect(self.record_tick)
        self.restore_update_recording()

    def restore_update_recording(self):
        draft_path=self.s.engines.data/'update-voice-draft.json'
        if not draft_path.exists():return
        try:
            draft=json.loads(draft_path.read_text(encoding='utf-8'))
            source=Path(draft['path']).resolve()
            allowed=(self.s.engines.data/'recovered-samples'/'update-reference.wav').resolve()
            if source!=allowed or not source.is_file():return
            folder=self.s.engines.data/'references';folder.mkdir(exist_ok=True)
            restored=folder/('pending-'+uuid.uuid4().hex+'.wav')
            shutil.copyfile(source,restored);self.temp=str(restored)
            self.name.setText(draft.get('name',''));self.text.setPlainText(draft['text'])
            self.language.setCurrentIndex(1 if draft.get('language')=='no' else 0)
            self.file_note.setText(f'Previous recording restored  ·  {sf.info(restored).duration:.1f} seconds')
            self.set_working(False)
            self.note.setText('Your recording was kept during the update. Record a new sample to use the new text, or name and save this one.')
            draft_path.unlink()
        except (OSError,ValueError,KeyError):
            self.note.setText('Your previous recording is kept in recovered-samples. You can choose it as an audio file.')

    def choose_file(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose audio or a video','',''+MEDIA_FILTER)
        if path:self.load_sample(path,seconds=90 if self.prompt_language.currentData().endswith('_long') else 25)

    def update_prompt(self):
        self.prompt_text.setPlainText(VOICE_PROMPTS[self.prompt_language.currentData()][2])
        longer=self.prompt_language.currentData().endswith('_long')
        self.prompt_text.setFixedHeight(210 if longer else 134)
        self.prompt_help.setText('Read for about 45–90 seconds, using your own dialect and everyday wording. Scroll for the next paragraph.\nA longer sample can help voice colour; it does not train the model to pronounce every word.' if longer else
            'Read at a comfortable pace, in your own accent or dialect. Aim for 10–25 seconds.\nYou can also say something else; the transcript must match what you actually said.')
        if hasattr(self,'language') and not self.temp:
            self.language.setCurrentIndex(1 if VOICE_PROMPTS[self.prompt_language.currentData()][1]=='no' else 0)

    def set_working(self,enabled):
        self.working=enabled
        for widget in (self.upload,self.record,self.retry,self.use_clip,self.clip_start,self.clip_length):widget.setEnabled(not enabled and not self.recording)
        self.record.setEnabled(not enabled)
        self.retry.setEnabled(not enabled and bool(self.temp))
        self.save.setEnabled(not enabled and not self.recording and bool(self.temp))
        self.preview.setEnabled(not enabled and not self.recording and bool(self.temp))
        self.prompt_language.setEnabled(not enabled and not self.recording)

    def reimport_clip(self):
        if self.source_path:self.load_sample(self.source_path,self.clip_start.value(),self.clip_length.value(),reuse_range=True)

    def load_sample(self,path,start=0,seconds=25,reuse_range=False,expected_language=None,show_clip=True):
        if self.working or self.cancel_event.is_set():return
        self.player.close()
        self.source_path=str(path);self.expected_language=expected_language
        if not reuse_range:self.clip_start.setValue(start);self.clip_length.setValue(seconds)
        self.clip_box.setVisible(show_clip)
        if self.temp:Path(self.temp).unlink(missing_ok=True);self.temp=None
        self.text.clear();self.set_working(True);self.note.setText('Extracting a voice sample from your recording…')
        folder=self.s.engines.data/'references';folder.mkdir(exist_ok=True)
        destination=folder/('pending-'+uuid.uuid4().hex+'.wav')
        def work():
            try:
                value=extract_reference(path,destination,self.s.engines.root,self.s.owner,self.cancel_event,start,seconds)
                if not self.cancel_event.is_set():self.result.emit('sample',value)
                else:destination.unlink(missing_ok=True)
            except Cancelled:pass
            except Exception as error:
                if not self.cancel_event.is_set():self.result.emit('import-error',str(error))
        self.s.thread(work)

    def record_sample(self):
        if self.recording:
            self.timer.stop();self.recording=False
            path=Path(self.s.scratch.name)/('reference-'+uuid.uuid4().hex+'.wav')
            self.recorder.stop(path);self.record.setText('Record sample')
            self.load_sample(path,seconds=90,expected_language=VOICE_PROMPTS[self.prompt_language.currentData()][1],show_clip=False)
            return
        device=self.s.microphone.currentData()
        if not device:self.note.setText('Choose a microphone in the main window first.');return
        try:
            self.recorder.gain_db=self.s.mic_gain.value();self.recorder.normalise=self.s.auto_gain.isChecked()
            self.recorder.start(device['index']);self.recording=True;self.seconds=0
            self.record_limit=90 if self.prompt_language.currentData().endswith('_long') else 30
            self.record.setText('Stop recording');self.timer.start(1000);self.note.setText('Read the prompt above in your natural voice, then stop recording.')
            for widget in (self.upload,self.retry,self.preview,self.save,self.use_clip,self.clip_start,self.clip_length,self.prompt_language):widget.setEnabled(False)
        except Exception as error:self.note.setText(str(error))

    def record_tick(self):
        self.seconds+=1;self.file_note.setText(f'Recording  ·  {self.seconds} seconds')
        if self.seconds>=self.record_limit:self.record_sample()

    def transcribe(self):
        if not self.temp or self.working:return
        self.set_working(True);self.prompt_language.setEnabled(True)
        self.note.setText('Transcribing the sample and detecting its language…')
        def work():
            try:
                value=self.s.engines.transcribe(self.temp,self.expected_language,self.cancel_event,lambda msg:None)
                if not self.cancel_event.is_set():self.result.emit('transcript',value)
            except Exception as error:
                if not self.cancel_event.is_set():self.result.emit('error',str(error))
        self.s.thread(work)

    def on_result(self,kind,value):
        if self.cancel_event.is_set():
            if kind=='sample':Path(value['path']).unlink(missing_ok=True)
            return
        self.set_working(False)
        if kind=='sample':
            self.temp=value['path']
            self.file_note.setText(f"{value['name']}  ·  {value['duration']:.1f} seconds from {value['start']:.1f} s")
            self.transcribe();return
        if kind=='import-error':self.note.setText(str(value));return
        self.set_working(False)
        if kind=='transcript':
            self.text.setPlainText(value['text']);self.language.setCurrentIndex(1 if value['language']=='no' else 0)
            self.note.setText('Norwegian detected. Review the transcript, name your voice and save.' if value['language']=='no' else 'English detected. Review the transcript, name your voice and save.')
        else:self.note.setText('Automatic transcription failed: '+value+'. You can enter the transcript yourself.')

    def preview_sample(self):
        if not self.temp or not self.s.output.currentData():return
        self.player.reset(self.s.output.currentData()['index'],self.s.volume.value()/100)
        self.player.append(self.temp);self.player.complete=True;self.player.play()

    def save_voice(self):
        if self.working or self.recording:return
        if not self.name.text().strip() or not self.temp or not self.text.toPlainText().strip():
            self.note.setText('Name the voice and enter the exact words from the recording.');return
        key='own-'+uuid.uuid4().hex;target=Path(self.temp).parent/(key+'.wav');Path(self.temp).replace(target);self.temp=None
        self.s.engines.voices.append({'key':key,'name':self.name.text().strip(),'language':self.language.currentData(),
            'reference_path':str(target),'reference_text':self.text.toPlainText().strip(),'description':'Personal voice sample'})
        self.s.engines.save_voices();self.s.config['norwegian_voice' if self.language.currentData()=='no' else 'english_voice']=key;self.s.fill_voices();self.s.save_settings();self.accept()

    def done(self,value):
        self.cancel_event.set();self.timer.stop();self.recorder.cancel();self.player.close()
        if self.temp:
            try:Path(self.temp).unlink(missing_ok=True)
            except OSError:pass
        super().done(value)


STYLE='''
QWidget {font-family:"Segoe UI";font-size:13px;color:#e4e7ed;background:transparent;}
QMainWindow,QDialog {background:#17191e;}
QLabel#brand {font-size:27px;font-weight:700;color:#f5f7fa;}
QLabel#section {font-size:21px;font-weight:600;color:#f5f7fa;}
QLabel#muted {color:#949cae;font-size:12px;}
QLabel#eyebrow {color:#b0b7c6;font-size:11px;font-weight:600;}
QLabel#footer {color:#737c8e;font-size:11px;}
QLabel#status {color:#b6c5e0;font-size:12px;}
QLabel#time {color:#aeb9cd;font-family:"Consolas";font-size:12px;min-width:38px;}
QFrame#toolbar {background:#20232b;border:1px solid #303540;border-radius:10px;}
QFrame#player {background:#20232b;border:1px solid #333a48;border-radius:10px;}
QComboBox,QLineEdit,QDoubleSpinBox {background:#242831;border:1px solid #383f4d;border-radius:6px;padding:8px 10px;selection-background-color:#40608f;}
QComboBox::drop-down {width:22px;border:0;}
QComboBox QAbstractItemView {background:#252a33;selection-background-color:#3d547d;color:#e4e7ed;}
QPlainTextEdit {background:#1e2128;border:1px solid #353b47;border-radius:10px;padding:16px;font-size:16px;selection-background-color:#3b557f;}
QPlainTextEdit:focus,QLineEdit:focus {border-color:#779bd5;}
QPushButton {background:#282d37;border:1px solid #3b4351;border-radius:7px;padding:10px 13px;font-weight:600;}
QPushButton:hover {background:#343c4b;border-color:#71809a;}
QPushButton:pressed {background:#3c4759;}
QPushButton:disabled {color:#687385;background:#22262e;border-color:#303743;}
QPushButton#primary {background:#779ad3;color:#101a2a;border-color:#779ad3;}
QPushButton#primary:hover {background:#91b4ec;}
QPushButton#primary:disabled {background:#3d5070;color:#92a2bc;border-color:#3d5070;}
QPushButton#danger {color:#efaaa2;border-color:#6b4546;}
QPushButton#danger:hover {background:#493033;border-color:#b56c67;}
QTabWidget::pane {border:0;}
QTabBar::tab {background:transparent;color:#8994a9;padding:12px 23px;border-bottom:2px solid transparent;font-weight:600;}
QTabBar::tab:selected {color:#a9c8f7;border-bottom:2px solid #87ace4;}
QTabBar::tab:hover {color:#d2def0;}
QSlider::groove:horizontal {height:5px;background:#3a4250;border-radius:2px;}
QSlider::sub-page:horizontal {background:#87ace4;border-radius:2px;}
QSlider::handle:horizontal {width:14px;margin:-5px 0;background:#b5cff3;border-radius:7px;}
QCheckBox {color:#acb7ca;spacing:7px;}
QCheckBox::indicator {width:15px;height:15px;border:1px solid #59697f;border-radius:3px;background:#20252e;}
QCheckBox::indicator:checked {background:#87ace4;image:none;border-color:#87ace4;}
QListWidget#voices {background:#1e2128;border:1px solid #353b47;border-radius:10px;padding:8px;}
QListWidget::item {background:#242a34;border:1px solid #333d4a;border-radius:8px;padding:16px;}
QListWidget::item:selected {background:#30435f;border-color:#6e93cb;}
QScrollBar:vertical {width:9px;background:#20242c;margin:3px;}
QScrollBar::handle:vertical {background:#465369;border-radius:4px;min-height:30px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
'''

def dark_palette(app):
    p=app.palette()
    for role,color in [(QPalette.Window,'#17191e'),(QPalette.WindowText,'#e4e7ed'),(QPalette.Base,'#20232b'),
        (QPalette.AlternateBase,'#282d37'),(QPalette.Text,'#e4e7ed'),(QPalette.Button,'#282d37'),
        (QPalette.ButtonText,'#e4e7ed'),(QPalette.Highlight,'#3b557f'),(QPalette.HighlightedText,'#ffffff'),
        (QPalette.ToolTipBase,'#282d37'),(QPalette.ToolTipText,'#e4e7ed')]:p.setColor(role,QColor(color))
    app.setPalette(p)
