# Third-party software and models

The MIT license in this repository applies to Voxlet's original code and assets.
It does not relicense third-party software, downloaded checkpoints, or voice datasets.

The desktop ZIP contains PySide6/Qt, NumPy, sounddevice/PortAudio,
soundfile/libsndfile, pywin32, comtypes, psutil, langid and the PyInstaller runtime.
Their available distribution license files are copied into
`THIRD_PARTY_LICENSES/` by the release builder. Qt/PySide6 shared libraries remain
separate DLLs and can be replaced with compatible builds. See
[Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html) and
[Qt source](https://code.qt.io/cgit/) for LGPL/GPL terms and corresponding source.

Setup downloads additional software directly from its publishers:

| Project/model | Upstream / license |
| --- | --- |
| uv | [astral-sh/uv](https://github.com/astral-sh/uv), MIT/Apache-2.0 |
| Python | [python.org](https://www.python.org/), PSF and included notices |
| PyTorch | [pytorch/pytorch](https://github.com/pytorch/pytorch), BSD-style and included notices |
| faster-whisper | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper), MIT |
| CTranslate2 | [OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2), MIT |
| NB-Whisper | [NbAiLab/nb-whisper-large](https://huggingface.co/NbAiLab/nb-whisper-large), Apache-2.0; attribution to the National Library of Norway |
| Whisper Turbo | [checkpoint model card](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) and [OpenAI Whisper](https://github.com/openai/whisper) |
| Piper | [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl), GPL-3.0-or-later |
| Norwegian talesyntese voice | [model card](https://huggingface.co/rhasspy/piper-voices/blob/main/no/no_NO/talesyntese/medium/MODEL_CARD); the Norwegian dataset is CC0 |
| Alan voice | [model card](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_GB/alan/medium/MODEL_CARD); dataset terms linked there apply |
| Qwen3-TTS model | [Qwen model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base), Apache-2.0 |
| faster-qwen3-tts | [andimarafioti/faster-qwen3-tts](https://github.com/andimarafioti/faster-qwen3-tts), MIT |
| Chatterbox | [resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox), MIT; audio watermarking remains enabled |
| Perth | [resemble-ai/Perth](https://github.com/resemble-ai/Perth), upstream notices |
| FFmpeg | Downloaded through [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg); FFmpeg has LGPL/GPL terms depending on its build. See [FFmpeg source and licensing](https://ffmpeg.org/legal.html). It is not bundled in the Voxlet release ZIP. |

Installed dependencies retain their own license files inside each local Python
environment. Model downloads and their source are separate from the desktop
release. If you redistribute an installation with those runtimes or weights,
you must also satisfy the respective upstream distribution terms.
