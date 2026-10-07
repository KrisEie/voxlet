"""Download public model checkpoints. No reference voices or recordings are shipped."""
import argparse
import os
from pathlib import Path
import shutil
from model_registry import QWEN_BASE, QWEN_TOKENIZER, CHATTERBOX, CHATTERBOX_FILES

ROOT = Path(__file__).resolve().parent
os.environ['HF_HOME'] = str(ROOT / '.cache' / 'huggingface')
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'


def download_standard():
    from huggingface_hub import hf_hub_download, snapshot_download
    turbo = ROOT / 'models' / 'whisper-turbo'
    print('Downloading Whisper Turbo for English and language detection...', flush=True)
    snapshot_download('dropbox-dash/faster-whisper-large-v3-turbo',
                      revision='0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf',
                      local_dir=turbo, allow_patterns=['*.json', 'model.bin', 'README.md'])
    nb = ROOT / 'models' / 'nb-whisper-large'; nb.mkdir(parents=True, exist_ok=True)
    print('Downloading NB-Whisper Large for Norwegian (several GB)...', flush=True)
    for name in ('ct2/config.json', 'ct2/model.bin', 'ct2/vocabulary.json', 'tokenizer.json', 'preprocessor_config.json'):
        downloaded = hf_hub_download('NbAiLab/nb-whisper-large', filename=name,
                                    revision='8c6249fdeeb4dcd05e5735a4c39640607eb6e4ac')
        target = nb / Path(name).name
        if not target.exists() or target.stat().st_size != Path(downloaded).stat().st_size:
            shutil.copyfile(downloaded, target)
    print('Downloading the two fast standard voices...', flush=True)
    for stem in ('no/no_NO/talesyntese/medium/no_NO-talesyntese-medium', 'en/en_GB/alan/medium/en_GB-alan-medium'):
        for suffix in ('.onnx', '.onnx.json'):
            hf_hub_download('rhasspy/piper-voices', filename=stem + suffix,
                            revision='c10ece1aade47bb51c153c893d14e5bf8e5b7117', local_dir=ROOT / 'models' / 'piper')


def download_cloning():
    from huggingface_hub import snapshot_download
    print('Downloading the English cloning model and tokenizer...', flush=True)
    for repo, revision in (QWEN_BASE, QWEN_TOKENIZER):
        snapshot_download(repo, revision=revision)
    print('Downloading the Norwegian cloning model...', flush=True)
    snapshot_download(CHATTERBOX[0], revision=CHATTERBOX[1], allow_patterns=CHATTERBOX_FILES)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['standard', 'full'], default='standard')
    parser.add_argument('--skip-models', action='store_true')
    args = parser.parse_args()
    import imageio_ffmpeg
    tools = ROOT / 'media-tools'; tools.mkdir(exist_ok=True)
    shutil.copyfile(imageio_ffmpeg.get_ffmpeg_exe(), tools / 'ffmpeg.exe')
    if not args.skip_models:
        download_standard()
        if args.mode == 'full': download_cloning()
    print('Public model/tool setup complete.', flush=True)


if __name__ == '__main__': main()
