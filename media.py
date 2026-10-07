"""Import bounded voice references from audio and video without changing originals."""
from pathlib import Path
import time
import numpy as np
import soundfile as sf
from engines import Cancelled
from playback import microphone_audio
from reference_audio import trim_quiet_edges

MEDIA_FILTER = ('Audio and video (*.wav *.flac *.ogg *.mp3 *.m4a *.aac *.opus *.wma '
                '*.mp4 *.mov *.m4v *.mkv *.webm *.avi *.mpeg *.mpg *.3gp);;All files (*)')


def extract_reference(source, destination, asset_root, owner, cancel, start=0, seconds=25):
    source, destination = Path(source), Path(destination)
    start, seconds = float(start), float(seconds)
    if not source.is_file():
        raise ValueError('The selected file is missing. Choose it again.')
    if start < 0 or not 3 <= seconds <= 90:
        raise ValueError('Choose a starting point and a 3–90 second clip.')
    ffmpeg = Path(asset_root) / 'media-tools' / 'ffmpeg.exe'
    if not ffmpeg.is_file():
        raise RuntimeError('The media importer is missing. Close Voxlet and run Setup.cmd.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if cancel.is_set():
        raise Cancelled()
    command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
               '-ss', str(start), '-i', source, '-t', str(seconds), '-map', '0:a:0',
               '-vn', '-sn', '-dn', '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', destination]
    child = owner.spawn(command, cwd=destination.parent)
    success = False
    try:
        deadline = time.monotonic() + 120
        while child.poll() is None:
            if cancel.wait(.05):
                raise Cancelled()
            if time.monotonic() > deadline:
                raise RuntimeError('Import took too long. Try a shorter local clip.')
        if cancel.is_set():
            raise Cancelled()
        if child.poll() != 0 or not destination.is_file():
            raise ValueError('No readable audio was found. Choose a video with sound or another audio file.')
        data, rate = sf.read(destination, dtype='float32', always_2d=True)
        duration = len(data) / rate
        if duration < 3:
            raise ValueError('There are less than 3 seconds left. Move the starting point earlier or choose a longer clip.')
        if not np.all(np.isfinite(data)) or float(np.max(np.abs(data))) < .0001:
            raise ValueError('This part of the recording is silent. Choose a part with clear speech.')
        mono, _ = microphone_audio(data, 0, True)
        mono,leading,trailing=trim_quiet_edges(mono,rate)
        sf.write(destination, mono, rate, subtype='PCM_16')
        if cancel.is_set():
            raise Cancelled()
        success = True
        return {'path': str(destination), 'name': source.name, 'start': start+leading,
                'duration':len(mono)/rate,'leading_trim_seconds':leading,'trailing_trim_seconds':trailing}
    finally:
        child.terminate()
        # Windows termination is asynchronous; release the file handle before cleanup.
        deadline = time.monotonic() + 2
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        if not success:
            destination.unlink(missing_ok=True)
