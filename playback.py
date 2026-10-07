"""One continuous WASAPI stream, with a growing, seekable audio timeline."""
import threading
import numpy as np
import sounddevice as sd
import soundfile as sf


class Player:
    def __init__(self):
        self.lock = threading.RLock()
        self.stream = None
        self.rate = 48000
        self.frames = 0
        self.position = 0
        self.buffer = np.empty(0, dtype=np.float32)
        self.paused = True
        self.complete = False
        self.started = False
        self.volume = .75
        self.underrun_frames = 0
        self.error = None
        self.cues = []

    def reset(self, device, volume=.75):
        self.close()
        self.rate = int(sd.query_devices(device)['default_samplerate'])
        self.channels = int(sd.query_devices(device)['max_output_channels'])
        self.volume = volume
        self.buffer = np.zeros(self.rate * 30, dtype=np.float32)
        self.position = self.frames = self.underrun_frames = 0
        self.paused = True
        self.complete = self.started = False
        self.cues = []
        self.error = None
        self.stream = sd.OutputStream(device=device, samplerate=self.rate,
            channels=self.channels, dtype='float32', blocksize=1024, callback=self.callback)
        self.stream.start()

    def append(self, path, cue=None):
        x, sr = sf.read(path, dtype='float32', always_2d=True)
        x = x.mean(axis=1)
        if sr != self.rate:
            x = np.interp(np.arange(round(len(x)*self.rate/sr))*sr/self.rate,
                          np.arange(len(x)), x).astype(np.float32)
        with self.lock:
            end = self.frames + len(x)
            if end > len(self.buffer):
                larger = np.zeros(max(end, max(len(self.buffer)*2, self.rate*30)), dtype=np.float32)
                larger[:self.frames] = self.buffer[:self.frames]
                self.buffer = larger
            if cue:
                previous = self.cues[-1] if self.cues else None
                if previous and previous['index'] == cue['index']:
                    previous['end'] = end / self.rate
                else:
                    self.cues.append(dict(cue, start=self.frames/self.rate, end=end/self.rate))
            self.buffer[self.frames:end] = x
            self.frames = end

    def callback(self, out, frames, timing, status):
        out.fill(0)
        with self.lock:
            if self.paused:
                return
            count = min(frames, self.frames-self.position)
            if count:
                audio = self.buffer[self.position:self.position+count] * self.volume
                out[:count, 0] = audio
                if self.channels > 1:
                    out[:count, 1] = audio
                self.position += count
                self.started = True
            if count < frames and self.started and not self.complete:
                self.underrun_frames += frames-count
            if self.complete and self.position >= self.frames:
                self.paused = True

    def play(self):
        with self.lock:
            if self.complete and self.position >= self.frames:
                self.position = 0
            self.paused = False

    def seek(self, seconds):
        with self.lock:
            self.position = min(self.frames, max(0, round(seconds*self.rate)))

    def jump(self, seconds):
        self.seek(self.position/self.rate + seconds)

    def snapshot(self):
        with self.lock:
            seconds = self.position/self.rate
            cue = next((c for c in self.cues if c['start'] <= seconds < c['end']), None)
            return {'position': seconds, 'duration': self.frames/self.rate,
                    'paused': self.paused, 'complete': self.complete,
                    'buffering': not self.complete and self.position >= self.frames,
                    'cue': cue, 'underrun_seconds': self.underrun_frames/self.rate}

    def export(self, path):
        with self.lock:
            sf.write(path, self.buffer[:self.frames], self.rate, subtype='PCM_16')

    def close(self):
        if self.stream:
            self.stream.abort()
            self.stream.close()
            self.stream = None
        self.paused = True

    def clear(self):
        self.close()
        with self.lock:
            self.buffer = np.empty(0, dtype=np.float32)
            self.frames = self.position = 0
            self.cues = []
            self.complete = True


def microphone_audio(data, gain_db=12, normalise=True):
    """Keep the stronger input channel; add gain without clipping or speed changes."""
    if not len(data):
        return np.empty(0, dtype=np.float32), 0
    if data.ndim > 1:
        energy = np.mean(np.square(data), axis=0)
        mono = data[:, int(np.argmax(energy))].copy()
    else:
        mono = data.copy()
    mono -= np.mean(mono)
    factor = 10**(gain_db/20)
    peak = float(np.max(np.abs(mono)))
    rms = float(np.sqrt(np.mean(mono**2)))
    if normalise and rms > .0001 and peak > .0003:
        # Bring speech toward -23 dBFS RMS, capped at +24 dB total boost.
        factor = min(10**(24/20), max(factor, .07/max(rms, .0001)))
    if peak:
        factor = min(factor, .94/peak)
    return (mono*factor).astype(np.float32), 20*np.log10(max(factor, .00001))
