"""Pinned public model versions, shared by setup and offline cloning workers."""
QWEN_BASE = ('Qwen/Qwen3-TTS-12Hz-1.7B-Base', 'fd4b254389122332181a7c3db7f27e918eec64e3')
QWEN_TOKENIZER = ('Qwen/Qwen3-TTS-Tokenizer-12Hz', '7dd38ad4e9bad454aae9cd937d0cd577604fe229')
CHATTERBOX = ('ResembleAI/chatterbox', '5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18')
CHATTERBOX_FILES = ['ve.pt', 's3gen.pt', 't3_mtl23ls_v3.safetensors',
                    'grapheme_mtl_merged_expanded_v1.json', 'conds.pt', 'Cangjie5_TC.json']


def cached_snapshot(model):
    from huggingface_hub import snapshot_download
    repo, revision = model
    return snapshot_download(repo, revision=revision, local_files_only=True)
