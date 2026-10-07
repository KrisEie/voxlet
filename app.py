"""Voxlet: a native, manually started Windows speech workstation."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid

import numpy as np
import psutil
import sounddevice as sd
import soundfile as sf
import win32api
import win32event
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QMimeData, QUrl
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QIcon, QDesktopServices, QTextCursor, QTextCharFormat
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QTextEdit, QPlainTextEdit, QTabWidget, QFrame, QSlider,
    QFileDialog, QDialog, QLineEdit, QFormLayout, QDialogButtonBox, QMessageBox,QSystemTrayIcon,QMenu)

from native import Hotkeys, ProcessOwner, target_snapshot, same_target, shortcut, user32, modifiers_down,restore_own_window
from engines import Engines, Cancelled, detect_language, language_details
from playback import Player, microphone_audio
from ui import build_ui as make_ui, VoiceDialog, STYLE as DARK_STYLE, dark_palette
from samples import READER_EXAMPLES
from lifecycle import InstanceServer,notify_running,set_startup
from portable_config import application_root,load_paths,instance_suffix

BASE = Path(__file__).resolve().parent
ROOT = application_root()
PATHS = load_paths(ROOT)


class Events(QObject):
    update = Signal(int, str, object)


class LevelStrip(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(54)
        self.level = 0
        self.active = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = self.width()
        count = max(18, int(width / 11))
        db = 20 * math.log10(max(self.level, .00001))
        lit = round(max(0, min(1, (db + 60) / 60)) * count)
        for index in range(count):
            height = 23
            colour = '#e6ba72' if index / count > .85 else '#83bca6'
            painter.setPen(QPen(QColor(colour if self.active and index < lit else '#343d4d'), 4, Qt.SolidLine, Qt.RoundCap))
            x = (index + .5) * width / count
            painter.drawLine(int(x), int(27 - height / 2), int(x), int(27 + height / 2))


class Overlay(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        layout = QVBoxLayout(self)
        box = QFrame()
        box.setStyleSheet('QFrame {background:#18273d; border-radius:14px;} QLabel {color:white; background:transparent;}')
        inner = QVBoxLayout(box)
        inner.setContentsMargins(20, 12, 20, 12)
        self.title = QLabel()
        self.title.setFont(QFont('Segoe UI', 12, QFont.DemiBold))
        self.hint = QLabel()
        self.hint.setStyleSheet('color:#b9cce4; font-size:11px;')
        inner.addWidget(self.title)
        inner.addWidget(self.hint)
        layout.addWidget(box)
        self.resize(370, 88)

    def display(self, title, hint):
        self.title.setText(title)
        self.hint.setText(hint)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.x() + (screen.width() - self.width()) // 2, screen.bottom() - self.height() - 28)
        self.show()


class Recorder:
    def __init__(self):
        self.stream = None
        self.frames = []
        self.level = 0
        self.sample_rate = 48000
        self.errors = []
        self.gain_db = 12
        self.normalise = True
        self.applied_gain = 0
        self.peak = 0

    def start(self, device):
        self.frames = []
        self.errors = []
        self.level = 0
        self.sample_rate = int(sd.query_devices(device)['default_samplerate'])
        def callback(data, frames, timing, status):
            if status:
                self.errors.append(str(status))
            self.frames.append(data.copy())
            energies = np.mean(data ** 2, axis=0)
            mono = data[:, int(np.argmax(energies))]
            self.level = min(1, float(np.sqrt(np.max(energies))) * 10 ** (self.gain_db / 20))
            self.peak = min(1, float(np.max(np.abs(mono))) * 10 ** (self.gain_db / 20))
        channels = min(2, int(sd.query_devices(device)['max_input_channels']))
        self.stream = sd.InputStream(device=device, samplerate=self.sample_rate, channels=channels,
                                     dtype='float32', callback=callback, blocksize=0)
        self.stream.start()

    def stop(self, destination):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        audio = np.concatenate(self.frames, axis=0) if self.frames else np.empty((0, 1), dtype='float32')
        self.frames = []
        self.level = 0
        audio, self.applied_gain = microphone_audio(audio, self.gain_db, self.normalise)
        if len(audio):
            sf.write(str(destination), audio, self.sample_rate, subtype='PCM_16')
        rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0
        return len(audio) / self.sample_rate, rms

    def cancel(self):
        if self.stream:
            self.stream.abort()
            self.stream.close()
            self.stream = None
        self.frames = []
        self.level = 0


def caption(text, name=''):
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    return label


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Voxlet')
        self.setWindowIcon(QIcon(str(BASE / 'voxlet.ico')))
        self.resize(1080, 830)
        self.setMinimumSize(960, 760)
        self.owner = ProcessOwner()
        self.engines = Engines(ROOT, self.owner, PATHS)
        self.recorder = Recorder()
        self.player = Player()
        self.level_class = LevelStrip
        self.asset_root = BASE
        self.reading_session = None
        self.overlay = Overlay()
        self.events = Events()
        self.events.update.connect(self.on_update)
        self.token = 0
        self.phase = 'idle'
        self.cancel_event = threading.Event()
        self.closing = False
        self.exit_requested = False
        self.voice_dialog = None
        self.tray = None
        self.instance_server = None
        self.record_mode = 'dictate'
        self.target = None
        self.audio_queue = []
        self.generating = False
        self.playing = False
        self.latest_audio = None
        self.test_audio = None
        self.last_transcript = ''
        self.pending_clipboard = None
        self.capture_clipboard = None
        self.threads = []
        self.scratch = tempfile.TemporaryDirectory(prefix='voxlet-ui-')
        self.config_file = self.engines.data / 'settings.json'
        self.config = json.loads(self.config_file.read_text(encoding='utf-8')) if self.config_file.exists() else {}
        self.config.setdefault('dictation_shortcut','Ctrl+Shift+Space')
        self.config.setdefault('reading_shortcut','Alt+O')
        self.config.setdefault('keep_background',True)
        self.config.setdefault('accurate_norwegian',(ROOT/'models/nb-whisper-large/model.bin').exists())
        self.build_ui()
        self.fill_devices()
        self.fill_voices()
        self.save_settings()
        self.hotkeys = Hotkeys(self.on_hotkey)
        QApplication.instance().installNativeEventFilter(self.hotkeys)
        conflicts = self.hotkeys.register(self.config['dictation_shortcut'], self.config['reading_shortcut'])
        self.refresh_shortcut_labels()
        if conflicts:
            self.set_status('Shortcut unavailable: ' + ', '.join(conflicts) + '. Close the app using it, then restart Voxlet.', error=True)
        self.tick = QTimer(self)
        self.tick.timeout.connect(self.resource_tick)
        self.tick.start(5000)
        self.meter_tick = QTimer(self)
        self.meter_tick.timeout.connect(self.record_tick)
        self.player_timer = QTimer(self)
        self.player_timer.timeout.connect(self.player_tick)
        self.player_timer.start(100)
        self.reading_text = ''
        self.finished_notice = False
        self.active_cue = None
        self.resource_tick()
        self.setup_tray()
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    def build_ui(self):
        make_ui(self)

    def friendly_device(self, name):
        name = name.replace('(SteelSeries Sonar Virtual Audio Device)', '').strip()
        name = re.sub(r'\(\d+- ', '(', name)
        return name

    def fill_devices(self):
        if self.phase != 'idle':
            self.set_status('Stop recording or reading before changing audio devices.')
            return
        self.player.close()
        self.player.buffer = np.empty(0, dtype=np.float32)
        self.player.frames = self.player.position = 0
        sd._terminate()
        sd._initialize()
        devices = sd.query_devices()
        apis = sd.query_hostapis()
        for combo, direction, setting, default_device in [
            (self.microphone, 'max_input_channels', 'microphone', sd.default.device[0]),
            (self.output, 'max_output_channels', 'output', sd.default.device[1])]:
            combo.blockSignals(True)
            combo.clear()
            available = [(i, d) for i, d in enumerate(devices) if d[direction] > 0 and apis[d['hostapi']]['name'] == 'Windows WASAPI']
            for index, device in available:
                combo.addItem(self.friendly_device(device['name']), {'index': index, 'name': device['name']})
                combo.setItemData(combo.count() - 1, device['name'], Qt.ToolTipRole)
            saved = self.config.get(setting)
            chosen = next((i for i, (_, d) in enumerate(available) if self.friendly_device(d['name']) == self.friendly_device(saved or '')), None)
            if chosen is None and not saved:
                default_name = devices[default_device]['name'] if default_device >= 0 else ''
                chosen = next((i for i, (_, d) in enumerate(available) if d['name'] == default_name), 0 if available else -1)
            combo.setCurrentIndex(chosen if chosen is not None else -1)
            combo.blockSignals(False)
            if saved and chosen is None:
                self.set_status('The saved device is disconnected. Choose a microphone and output again.', True)
        self.save_settings()

    def fill_voices(self):
        for combo,language,setting,default in [(self.voice,'en','english_voice','piper:en'),(self.nor_voice,'no','norwegian_voice','piper:no')]:
            combo.blockSignals(True);combo.clear()
            for voice in self.engines.voices:combo.addItem(voice['name'],voice['key'])
            combo.addItem('Alan (fast)' if language=='en' else 'Norwegian (fast)', 'piper:'+language)
            for i in range(combo.count()):
                combo.setItemData(i, 'Fast local standard voice.' if str(combo.itemData(i)).startswith('piper:') else
                    'Norwegian voice cloning. Best pronunciation with a Norwegian sample; first use takes longer.' if language=='no' else
                    'English voice cloning. First use loads the voice engine.', Qt.ToolTipRole)
            selected=self.config.get(setting,self.config.get('voice',default) if language=='en' else default)
            index=combo.findData(selected)
            if index<0:index=combo.findData(default)
            if index<0:index=combo.findData('piper:'+language)
            combo.setCurrentIndex(index);combo.blockSignals(False)
        self.voice_list.clear()
        from PySide6.QtWidgets import QListWidgetItem
        for voice in self.engines.voices:
            language='Norwegian' if voice.get('language')=='no' else 'English'
            item=QListWidgetItem(voice['name']+'\n'+language+' sample • Reads in English and Norwegian')
            item.setData(Qt.UserRole,voice['key']);self.voice_list.addItem(item)
            if voice['key']==self.config.get('english_voice','jamie'):self.voice_list.setCurrentItem(item)
        self.select_voice(self.voice_list.currentItem())

    def refresh_shortcut_labels(self):
        self.dictation_hint.setText(self.config['dictation_shortcut']+' starts and stops dictation in your current text field. Esc cancels.')
        self.reading_hint.setText('Select text • '+self.config['reading_shortcut'])
        self.dict_button.setText(('Stop recording' if self.phase=='recording' and self.record_mode=='dictate' else 'Start dictation')+'  ·  '+self.config['dictation_shortcut'])

    def apply_shortcuts(self):
        if self.phase!='idle':self.set_status('Stop recording or reading before changing shortcuts.');return
        from PySide6.QtGui import QKeySequence
        values=[edit.keySequence().toString(QKeySequence.PortableText) for edit in (self.dictation_key,self.reading_key)]
        try:
            for value in values:Hotkeys.parse(value)
            if Hotkeys.parse(values[0])==Hotkeys.parse(values[1]):raise ValueError('Use a different shortcut for each action.')
        except ValueError as error:self.set_status(str(error),True);return
        old=[self.config['dictation_shortcut'],self.config['reading_shortcut']]
        self.hotkeys.close();conflicts=self.hotkeys.register(*values)
        if conflicts:
            self.hotkeys.close();restored=self.hotkeys.register(*old)
            self.set_status('Shortcut unavailable: '+', '.join(conflicts)+'. Choose another combination.'+(' Previous shortcuts are also unavailable.' if restored else ''),True);return
        self.config.update(dictation_shortcut=values[0],reading_shortcut=values[1]);self.save_settings();self.refresh_shortcut_labels();self.set_status('Shortcuts updated.')

    def reader_example(self,language):
        if self.phase!='idle':
            self.set_status('Stop the current reading before trying an example.');return
        self.read_language.setCurrentIndex(self.read_language.findData('no' if language=='mixed' else language))
        self.read_text.setPlainText(READER_EXAMPLES[language])
        self.read_text.setFocus()
        self.set_status('Example ready. Choose your voice, then press Read text.')

    def change_startup(self,enabled):
        try:
            set_startup(enabled)
            self.set_status('Voxlet will start with Windows, with its window hidden.' if enabled else 'Start Voxlet from its shortcut when you need it.')
        except OSError as error:
            self.start_windows.blockSignals(True);self.start_windows.setChecked(not enabled);self.start_windows.blockSignals(False)
            self.set_status('Could not update the Windows startup preference: '+str(error),True)

    def save_settings(self, *args):
        if not hasattr(self, 'volume'):
            return
        for combo, setting in [(self.microphone, 'microphone'), (self.output, 'output')]:
            if combo.currentData():
                self.config[setting] = combo.currentData()['name']
        self.recorder.gain_db = self.mic_gain.value()
        self.recorder.normalise = self.auto_gain.isChecked()
        self.gain_label.setText(f'Gain  +{self.mic_gain.value()} dB')
        self.player.volume = self.volume.value()/100
        self.config.update(mic_gain=self.mic_gain.value(), auto_gain=self.auto_gain.isChecked(), dict_language=self.dict_language.currentIndex(),
                           read_language=self.read_language.currentIndex(),
                           volume=self.volume.value())
        if self.voice.currentData():
            self.config['english_voice'] = self.voice.currentData()
        if self.nor_voice.currentData():self.config['norwegian_voice'] = self.nor_voice.currentData()
        self.config.update(delete_after_playback=self.delete_audio.isChecked(), accurate_norwegian=self.accuracy.isChecked(),vocabulary=self.vocabulary.text().strip())
        self.config['keep_background']=self.keep_background.isChecked()
        self.config['norwegian_delivery']=self.nor_delivery.currentData()
        self.engines.norwegian_delivery=self.nor_delivery.currentData()
        self.engines.accurate=self.accuracy.isChecked();self.engines.vocabulary=self.vocabulary.text().strip()
        self.config_file.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding='utf-8')

    def set_status(self, message, error=False):
        self.status.setText(message)
        self.status.setStyleSheet('color:#ed9b92;' if error else 'color:#b6c5e0;')

    def set_busy(self, phase):
        self.phase = phase
        busy = phase != 'idle'
        self.stop_button.setEnabled(busy or bool(self.reading_session))
        self.hotkeys.escape(busy or bool(self.reading_session))
        self.microphone.setEnabled(not busy)
        self.output.setEnabled(not busy)
        self.dict_language.setEnabled(not busy)
        self.nor_delivery.setEnabled(not busy)
        self.delete_voice_button.setEnabled(not busy and self.voice_list.currentItem() is not None)
        self.read_text.setReadOnly(phase in ['generating','speaking'])
        self.read_button.setEnabled(not busy)
        self.play_test.setEnabled(not busy and bool(self.test_audio))
        self.save_test.setEnabled(not busy and bool(self.test_audio))
        self.test_button.setEnabled(not busy or phase == 'recording')
        self.dict_button.setEnabled(not busy or phase == 'recording')
        self.test_button.setText('Stop microphone test' if phase == 'recording' and self.record_mode == 'test' else 'Test microphone')
        self.refresh_shortcut_labels()
        if not busy:
            self.overlay.hide()
            self.level.active = False
            self.level.level = 0
            self.level.update()
            self.resource_tick()

    def begin(self, phase):
        self.token += 1
        self.cancel_event = threading.Event()
        self.set_busy(phase)
        return self.token, self.cancel_event

    def thread(self, function):
        thread = threading.Thread(target=function, daemon=True)
        self.threads = [t for t in self.threads if t.is_alive()]
        self.threads.append(thread)
        thread.start()

    def on_hotkey(self, identity):
        if self.voice_dialog is not None and self.voice_dialog.isVisible():
            if identity==0x703:self.voice_dialog.reject()
            return
        if identity == 0x701:
            self.toggle_record('dictate', external=True)
        elif identity == 0x702:
            if self.phase in ['speaking','generating']:
                self.pause_player()
            elif self.phase == 'copying':
                self.cancel()
            elif self.phase == 'idle':
                self.copy_selection()
        elif identity == 0x703:
            self.cancel()

    def toggle_record(self, mode, external=False):
        if self.phase == 'recording':
            self.stop_recording()
            return
        if self.phase != 'idle':
            return
        device = self.microphone.currentData()
        if not device:
            self.set_status('Choose a connected microphone first.', True)
            return
        self.target = target_snapshot() if external else None
        if self.target and self.target.get('password'):
            self.set_status('Dictation is disabled in password fields.', True)
            return
        try:
            self.recorder.gain_db = self.mic_gain.value()
            self.recorder.normalise = self.auto_gain.isChecked()
            self.recorder.start(device['index'])
        except Exception as error:
            self.recorder.cancel()
            self.set_status('Could not open the microphone: ' + str(error), True)
            return
        self.record_mode = mode
        self.begin('recording')
        self.record_started = time.monotonic()
        self.meter_tick.start(80)
        self.level.active = True
        self.overlay.display('●  Recording your voice', self.config['dictation_shortcut']+': finish   ·   Esc: cancel')
        self.set_status('Recording · ' + self.friendly_device(device['name']))

    def record_tick(self):
        self.level.level = self.recorder.level
        db = 20 * math.log10(max(self.recorder.level, .00001))
        self.mic_level_label.setText(f'Level {db:.0f} dBFS · ' + ('Too loud: lower the gain' if self.recorder.peak > .95 else 'Move closer to the microphone or increase gain' if db < -42 else 'Good recording level'))
        self.level.update()
        duration = int(time.monotonic() - self.record_started)
        self.overlay.title.setText(f'●  Recording your voice   {duration // 60}:{duration % 60:02}')
        if duration >= 180:
            self.stop_recording()

    def stop_recording(self):
        self.meter_tick.stop()
        audio = Path(self.scratch.name) / (uuid.uuid4().hex + '.wav')
        duration, rms = self.recorder.stop(audio)
        self.level.active = False
        self.level.update()
        if duration < .35 or rms < .0003:
            self.set_busy('idle')
            self.set_status('No clear speech detected. Try Test microphone.', True)
            return
        if self.record_mode == 'test':
            self.test_audio = str(audio)
            self.set_busy('idle')
            self.set_status(f'Microphone test ready · {duration:.1f} s · boosted {self.recorder.applied_gain:.0f} dB. Choose Play test.')
            return
        token, cancel = self.token, self.cancel_event
        language = self.dict_language.currentData()
        self.set_busy('transcribing')
        self.overlay.display('Transcribing your speech…', 'Esc: cancel')
        self.set_status('Transcribing your speech…')
        def transcribe():
            try:
                result = self.engines.transcribe(audio, language, cancel,
                    lambda message: self.events.update.emit(token, 'status', message))
                if not cancel.is_set():
                    self.events.update.emit(token, 'transcript', result)
            except Cancelled:
                pass
            except Exception as error:
                self.events.update.emit(token, 'error', str(error))
            finally:
                audio.unlink(missing_ok=True)
        self.thread(transcribe)

    def clipboard_snapshot(self):
        source = QApplication.clipboard().mimeData()
        return {fmt: bytes(source.data(fmt)) for fmt in source.formats()} if source else {}

    def restore_clipboard(self, saved, sequence):
        if user32.GetClipboardSequenceNumber() == sequence:
            mime = QMimeData()
            for fmt, data in saved.items():
                mime.setData(fmt, data)
            QApplication.clipboard().setMimeData(mime)
        self.pending_clipboard = None

    def paste_transcript(self, text):
        if not same_target(self.target, target_snapshot()):
            self.set_busy('idle')
            self.set_status('Transcript ready here. The input field changed; copy the text to use it.')
            return
        attempts = [0]
        def paste():
            if self.closing or self.phase != 'transcribing':
                return
            if not same_target(self.target, target_snapshot()):
                self.set_busy('idle')
                self.set_status('Transcript ready here. The input field changed; copy the text to use it.')
                return
            if modifiers_down():
                attempts[0] += 1
                if attempts[0] < 20:
                    QTimer.singleShot(50, paste)
                    return
                self.set_busy('idle')
                self.set_status('Transcript ready here. Release the shortcut keys and copy the text.')
                return
            saved = self.clipboard_snapshot()
            QApplication.clipboard().setText(text + (' ' if not text[-1:].isspace() else ''))
            sequence = user32.GetClipboardSequenceNumber()
            self.pending_clipboard = saved, sequence
            if not shortcut('v'):
                self.restore_clipboard(saved, sequence)
                self.set_busy('idle')
                self.set_status('Transcript ready here. Copy the text to use it.')
                return
            QTimer.singleShot(900, lambda: self.restore_clipboard(saved, sequence))
            self.set_busy('idle')
            self.set_status('Done. Your transcript has been pasted.')
        QTimer.singleShot(0, paste)

    def copy_selection(self):
        selected = target_snapshot()
        if selected.get('password'):
            self.set_status('Reading is disabled in password fields.', True)
            return
        token, cancel = self.begin('copying')
        attempts = [0]
        def copy():
            if token != self.token or cancel.is_set():
                return
            if modifiers_down():
                attempts[0] += 1
                if attempts[0] < 30:
                    QTimer.singleShot(50, copy)
                    return
                self.set_busy('idle')
                self.set_status('Release the shortcut keys, select text and try again.', True)
                return
            if selected['window'] != target_snapshot()['window']:
                self.set_busy('idle')
                self.set_status('The window changed. Select the text again.', True)
                return
            saved = self.clipboard_snapshot()
            before = user32.GetClipboardSequenceNumber()
            self.capture_clipboard = saved, before
            shortcut('c')
            started = time.monotonic()
            def check():
                if token != self.token or cancel.is_set():
                    return
                now = user32.GetClipboardSequenceNumber()
                if now != before:
                    text = QApplication.clipboard().text().strip()
                    self.restore_clipboard(saved, now)
                    self.capture_clipboard = None
                    self.set_busy('idle')
                    if text:
                        self.read_text.setPlainText(text)
                        self.start_reading(text)
                    else:
                        self.set_status('No selected text. Select text and use your reading shortcut.', True)
                elif time.monotonic() - started < 1.5:
                    QTimer.singleShot(60, check)
                else:
                    self.capture_clipboard = None
                    self.set_busy('idle')
                    self.set_status('No text was copied. Select text and use your reading shortcut.', True)
            QTimer.singleShot(80, check)
        QTimer.singleShot(0, copy)

    def start_reading(self, text):
        if self.phase != 'idle':
            return
        text = text.strip()
        if not text:
            self.set_status('Enter or select some text first.')
            return
        if len(text) > 12000:
            self.set_status('Choose a shorter passage, up to 12,000 characters.', True)
            return
        if not self.output.currentData():
            self.set_status('Choose an output device first.', True)
            return
        self.tabs.setCurrentIndex(1)
        language = self.read_language.currentData()
        if language == 'auto':
            language, confidence = language_details(text)
        mode = 'studio'
        chosen = self.nor_voice if language=='no' else self.voice
        voice = chosen.currentData()
        self.clear_reading_audio()
        self.reading_session=self.engines.new_session()
        session=self.reading_session
        self.reading_text = text
        self.read_text.setPlainText(text)
        self.active_cue = None
        self.finished_notice = False
        self.timing_label.setText('')
        self.language_note.setText(('Norwegian' if language=='no' else 'English')+' • '+chosen.currentText())
        try:
            self.player.reset(self.output.currentData()['index'], self.volume.value()/100)
        except Exception as error:
            self.clear_reading_audio()
            self.set_status('Could not open the output: '+str(error), True)
            return
        token, cancel = self.begin('generating')
        self.generating = True
        self.playing = False
        self.generated_audio = []
        self.latest_audio = None
        self.save_audio_button.setEnabled(False)
        self.reading_started = time.monotonic()
        self.first_audio_delay = None
        self.user_paused = False
        self.prebuffer_seconds = 4 if language=='no' and not voice.startswith('piper:') else .6
        self.overlay.display('Preparing your reading…', self.config['reading_shortcut']+': pause   ·   Esc: clear')
        def generate():
            try:
                timing = self.engines.speak(text, language, voice, cancel,
                    lambda message: self.events.update.emit(token, 'status', message),
                    lambda audio: self.events.update.emit(token, 'audio', audio), mode, session=session)
                if not cancel.is_set():
                    self.events.update.emit(token, 'generated', timing)
            except Cancelled:
                pass
            except Exception as error:
                self.events.update.emit(token, 'error', str(error))
        self.thread(generate)

    def preview_recording(self):
        if self.phase != 'idle' or not self.test_audio or not self.output.currentData():
            return
        try:
            self.clear_reading_audio()
            self.player.reset(self.output.currentData()['index'], self.volume.value()/100)
            self.player.append(self.test_audio)
            self.player.complete = True
            self.player.play()
            self.begin('speaking')
            self.generating = False
            self.playing = True
            self.finished_notice = False
            self.reading_text = ''
            self.set_status('Playing the microphone test. Adjust playback volume in Reader.')
        except Exception as error:
            self.set_status('Could not play the test: '+str(error), True)

    def player_tick(self):
        state = self.player.snapshot()
        duration, position = state['duration'], state['position']
        self.pause_button.setEnabled(duration > 0)
        for button in [self.back_button,self.forward_button,self.previous_sentence,self.next_sentence]:
            button.setEnabled(duration > 0)
        if not self.seek.isSliderDown():
            self.seek.setMaximum(round(duration*100))
            self.seek.setValue(round(position*100))
        self.elapsed_label.setText(f'{int(position)//60}:{int(position)%60:02}')
        self.duration_label.setText(f'{int(duration)//60}:{int(duration)%60:02}' + (' +' if self.generating else ''))
        self.pause_button.setText('▶  Play' if state['paused'] else 'Ⅱ  Pause')
        cue = state['cue']
        if cue and cue['index'] != self.active_cue and self.reading_text:
            self.active_cue = cue['index']
            cursor = QTextCursor(self.read_text.document())
            # QTextCursor uses UTF-16 offsets, not Python's Unicode code-point offsets.
            prefix = self.reading_text[:cue['char_start']]
            end = self.reading_text[:cue['char_end']]
            cursor.setPosition(len(prefix.encode('utf-16-le'))//2)
            cursor.setPosition(len(end.encode('utf-16-le'))//2,QTextCursor.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(QColor('#334f73'))
            selection.format.setForeground(QColor('#f0f6ff'))
            self.read_text.setExtraSelections([selection])
            self.read_text.setTextCursor(cursor)
            self.read_text.ensureCursorVisible()
        if self.phase in ['generating','speaking']:
            self.playback_note.setText('Preparing more audio…' if state['buffering'] and not state['paused'] else
                'Paused. Seek or press Play to continue.' if state['paused'] and position < duration else
                'The current sentence is highlighted. Seek freely through available audio.')
            if state['complete'] and position >= duration and duration > 0 and not self.finished_notice:
                self.finished_notice = True
                self.playing = False
                self.set_busy('idle')
                self.read_text.setExtraSelections([])
                if self.reading_session and self.delete_audio.isChecked():
                    self.clear_reading_audio();self.set_busy('idle');self.set_status('Finished. Temporary audio deleted; your text is kept.')
                else:self.set_status('Finished. You can replay until you press Esc.')

    def seek_player(self):
        self.player.seek(self.seek.value()/100)
        self.active_cue = None

    def jump_player(self, amount):
        self.player.jump(amount)
        self.active_cue = None

    def pause_player(self):
        if self.generating and not self.playing:
            self.user_paused = not self.user_paused
            self.set_status('Paused while preparing audio.' if self.user_paused else 'Audio will play when ready.')
            return
        if not self.player.frames:
            return
        if self.player.paused:
            self.player.play()
            self.user_paused = False
            self.finished_notice = False
            self.playing = True
            if self.phase == 'idle':
                self.begin('speaking')
        else:
            self.player.paused = True
            self.user_paused = True

    def jump_sentence(self, direction):
        now = self.player.position/self.player.rate
        cues = self.player.cues
        if not cues:
            return self.jump_player(direction*10)
        if direction > 0:
            target = next((cue['start'] for cue in cues if cue['start'] > now+.2), self.player.frames/self.player.rate)
        else:
            target = next((cue['start'] for cue in reversed(cues) if cue['start'] < now-.8), 0)
        self.player.seek(target)
        self.active_cue = None

    def read_from_cursor(self):
        cursor = self.read_text.textCursor()
        selected = cursor.selectedText().replace(chr(8233), '\n')
        text = selected or self.read_text.toPlainText().encode('utf-16-le')[cursor.position()*2:].decode('utf-16-le')
        if self.phase != 'idle':
            self.cancel()
        self.start_reading(text)

    def on_update(self, token, kind, value):
        if token != self.token or self.closing or self.cancel_event.is_set():
            return
        if kind == 'status':
            self.set_status(value)
            if not self.playing:
                self.overlay.display(value, 'Esc: cancel')
        elif kind == 'error':
            self.cancel()
            self.set_status(value, True)
        elif kind == 'transcript':
            text = value['text']
            self.transcript.setPlainText(text)
            self.last_transcript = text
            if not text:
                self.set_busy('idle')
                self.set_status('No clear speech found. Try dictating again.', True)
            elif self.target:
                self.paste_transcript(text)
            else:
                self.set_busy('idle')
                self.set_status('Transcript ready. Use your dictation shortcut in another text field to paste automatically.')
        elif kind == 'audio':
            if self.first_audio_delay is None:
                self.first_audio_delay = time.monotonic() - self.reading_started
            self.generated_audio.append(value['audio'])
            self.latest_audio = value['audio']
            cue = {key:value[key] for key in ['index','text','char_start','char_end']}
            self.player.append(value['audio'], cue)
            self.save_audio_button.setEnabled(True)
            if not self.playing and self.player.frames/self.player.rate >= self.prebuffer_seconds:
                self.playing = True
                self.set_busy('speaking')
                if not self.user_paused:
                    self.player.play()
                self.overlay.display('Reading aloud', self.config['reading_shortcut']+': pause   ·   Esc: clear')
        elif kind == 'generated':
            self.generating = False
            self.player.complete = True
            self.timing_label.setText('Audio ready' if value.get('cached') else
                f'First audio {(self.first_audio_delay or 0):.1f} s · generated in {time.monotonic()-self.reading_started:.1f} s')
            if not self.playing:
                self.playing = True
                self.set_busy('speaking')
                if not self.user_paused:
                    self.player.play()


    def cancel(self):
        self.cancel_event.set()
        self.player.paused = True
        self.clear_reading_audio()
        self.player.complete = True
        self.finished_notice = True
        self.read_text.setExtraSelections([])
        self.token += 1
        self.meter_tick.stop()
        self.recorder.cancel()
        self.engines.cancel_backend()
        self.audio_queue = []
        self.generating = self.playing = False
        self.set_busy('idle')
        self.set_status('Stopped. Temporary audio cleared.')

    def resource_tick(self):
        if self.closing:
            return
        self.engines.idle_tick()
        own = psutil.Process()
        processes = [own] + own.children(recursive=True)
        memory = 0
        for process in processes:
            try:
                memory += process.memory_info().rss
            except psutil.Error:
                pass
        loaded = any(p and p.poll() is None for p in (self.engines.backend,self.engines.multilingual_backend))
        self.resources.setText(f'{memory / 1024 ** 2:.0f} MB RAM  ·  ' + ('Voice engine loaded' if loaded else 'Models unloaded' if self.phase == 'idle' else 'Working'))

    def export_audio(self):
        if not self.player.frames:
            return
        destination, _ = QFileDialog.getSaveFileName(self, 'Save reading', 'reading.wav', 'Audio file (*.wav)')
        if destination:
            self.player.export(destination)
            self.set_status('Audio file saved.' + (' This contains audio generated so far.' if self.generating else ''))

    def export_test(self):
        if not self.test_audio:
            return
        destination, _ = QFileDialog.getSaveFileName(self, 'Save voice recording', 'voice-sample.wav', 'Audio file (*.wav)')
        if destination:
            shutil.copyfile(self.test_audio, destination)
            self.set_status('Recording saved. You can use it to create a voice.')

    def clear_reading_audio(self):
        self.player.clear()
        self.engines.cleanup_session(self.reading_session)
        self.reading_session=None;self.latest_audio=None;self.generated_audio=[]
        self.save_audio_button.setEnabled(False)
        self.read_text.setExtraSelections([])

    def select_voice(self,item,*args):
        self.delete_voice_button.setEnabled(item is not None and self.phase=='idle')

    def delete_voice(self):
        if self.phase!='idle':
            self.set_status('Stop reading or recording before deleting a voice.');return
        item=self.voice_list.currentItem()
        if item is None:return
        key=item.data(Qt.UserRole)
        voice=next((v for v in self.engines.voices if v['key']==key),None)
        if voice is None:return
        confirm=QMessageBox(self);confirm.setWindowTitle('Delete voice');confirm.setIcon(QMessageBox.Question)
        confirm.setTextFormat(Qt.PlainText);confirm.setText(f'Delete “{voice["name"]}”?')
        confirm.setInformativeText('This removes the saved voice from your library and deletes its personal reference sample. Original uploads and exported audio are kept.' if key.startswith('own-') else 'This removes the voice from your library. Its source audio is kept.')
        delete_button=confirm.addButton('Delete voice',QMessageBox.DestructiveRole)
        confirm.addButton(QMessageBox.Cancel);confirm.setDefaultButton(QMessageBox.Cancel)
        confirm.exec()
        if confirm.clickedButton()!=delete_button:return
        if self.phase!='idle':
            self.set_status('Stop reading or recording before deleting a voice.');return
        row=self.voice_list.currentRow()
        try:result=self.engines.delete_voice(key)
        except Exception as error:
            self.set_status('Could not delete the voice: '+str(error),True);return
        keys={v['key'] for v in self.engines.voices}
        if self.config.get('english_voice')==key:
            self.config['english_voice']='jamie' if 'jamie' in keys else 'piper:en'
        if self.config.get('norwegian_voice')==key:
            self.config['norwegian_voice']=next((v['key'] for v in self.engines.voices if v.get('language')=='no'),'piper:no')
        if self.config.get('voice')==key:self.config['voice']=self.config.get('english_voice','piper:en')
        self.fill_voices();self.save_settings()
        if self.voice_list.count():self.voice_list.setCurrentRow(min(row,self.voice_list.count()-1))
        if result['cleanup_error']:
            self.set_status('Voice removed, but its reference audio could not be deleted: '+result['cleanup_error'],True)
        else:self.set_status(f'“{voice["name"]}” deleted.')

    def use_selected_voice(self,language='en'):
        if self.phase!='idle':return
        item=self.voice_list.currentItem()
        if item:
            combo=self.nor_voice if language=='no' else self.voice
            index=combo.findData(item.data(Qt.UserRole))
            if index>=0:combo.setCurrentIndex(index)
        self.tabs.setCurrentIndex(1);self.read_text.setFocus()

    def rename_voice(self):
        if self.phase!='idle':return
        from PySide6.QtWidgets import QInputDialog
        item=self.voice_list.currentItem()
        if not item:return
        voice=next(v for v in self.engines.voices if v['key']==item.data(Qt.UserRole))
        name,ok=QInputDialog.getText(self,'Rename voice','Voice name',text=voice['name'])
        if ok and name.strip():
            voice['name']=name.strip();self.engines.save_voices();self.fill_voices();self.set_status('Voice renamed.')

    def add_voice(self):
        if self.phase != 'idle':
            self.set_status('Stop reading or recording before creating a voice.')
            return
        self.voice_dialog = VoiceDialog(self, Recorder)
        try:
            if self.voice_dialog.exec() == QDialog.Accepted:
                self.set_status('Voice saved and selected for its language. Try an example in Reader.')
        finally:
            self.voice_dialog = None

    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():return
        self.tray=QSystemTrayIcon(self.windowIcon(),self)
        self.tray.setToolTip('Voxlet • shortcuts ready')
        menu=QMenu(self)
        menu.addAction('Open Voxlet',self.show_window)
        menu.addAction('Stop recording / reading',self.cancel)
        menu.addSeparator()
        menu.addAction('Quit Voxlet',self.request_exit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()

    def tray_activated(self,reason):
        if reason in (QSystemTrayIcon.Trigger,QSystemTrayIcon.DoubleClick):self.show_window()

    def show_window(self):
        if self.closing:return
        self.showNormal();restore_own_window(int(self.winId()));self.raise_();self.activateWindow()

    def request_exit(self):
        self.exit_requested=True
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if not self.exit_requested and self.config.get('keep_background',True) and self.tray is not None:
            self.hide();event.ignore()
            self.set_status('Shortcuts are ready while the window is hidden. Open Voxlet from the icon beside the clock.')
            return
        self.shutdown()
        event.accept()
        QApplication.instance().quit()

    def shutdown(self):
        if self.closing:return
        self.closing = True
        if self.instance_server:self.instance_server.close()
        if self.tray:self.tray.hide()
        if self.voice_dialog is not None:self.voice_dialog.reject()
        self.player_timer.stop()
        self.player.close()
        self.cancel_event.set()
        self.meter_tick.stop()
        self.tick.stop()
        self.recorder.cancel()
        self.overlay.close()
        self.hotkeys.close()
        QApplication.instance().removeNativeEventFilter(self.hotkeys)
        if self.pending_clipboard:
            self.restore_clipboard(*self.pending_clipboard)
        self.owner.close()
        self.engines.cleanup_all()
        for thread in self.threads:
            thread.join(timeout=.2)
        try:
            self.scratch.cleanup()
        except OSError:
            pass


STYLE = DARK_STYLE.replace('image:none', 'image:url("'+(BASE/'check.svg').as_posix()+'")') + '\nQComboBox::down-arrow {image:url("'+(BASE/'chevron.svg').as_posix()+'");width:14px;height:14px;}\n'


def main():
    mutex = win32event.CreateMutex(None, False, 'Local\\VoxletDesktop-'+instance_suffix())
    already_open = win32api.GetLastError() == 183
    app = QApplication(sys.argv)
    app.setApplicationName('Voxlet')
    app.setQuitOnLastWindowClosed(False)
    if already_open:
        command='quit' if '--quit' in sys.argv else 'show'
        if not notify_running(command):
            QMessageBox.information(None, 'Voxlet is running', 'Voxlet is already running. Open it from the icon beside the clock.')
        win32api.CloseHandle(mutex)
        return 0
    if '--quit' in sys.argv:
        win32api.CloseHandle(mutex);return 0
    app.setStyle('Fusion')
    from PySide6.QtGui import QFontDatabase
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/seguisym.ttf')
    dark_palette(app)
    app.setStyleSheet(STYLE)
    window = Studio()
    window.instance_server=InstanceServer(window,window.show_window,window.request_exit)
    # Match the native Windows title bar to the workstation's dark surfaces.
    import ctypes
    try:
        dark_titlebar = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(window.winId()), 20,
            ctypes.byref(dark_titlebar), ctypes.sizeof(dark_titlebar))
    except Exception:
        pass
    if '--background' not in sys.argv or window.tray is None:window.show()
    if '--background' not in sys.argv and (window.engines.data/'update-voice-draft.json').exists():
        QTimer.singleShot(200,window.add_voice)
    code = app.exec()
    win32api.CloseHandle(mutex)
    return code


if __name__ == '__main__':
    sys.exit(main())
