import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engines import Engines, detect_language, text_parts
from portable_config import load_paths
from samples import VOICE_PROMPTS
from scripts.check_public_files import check_file


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def engine(self):
        return Engines(self.root, None, load_paths(self.root))

    def test_portable_empty_library_even_with_custom_runtime(self):
        (self.root / 'runtime.json').write_text(json.dumps({'voices': [{'name': 'Do not import'}],
                                                        'root': 'elsewhere'}))
        with patch.dict(os.environ, {'HF_HOME': 'elsewhere'}):
            paths = load_paths(self.root)
            self.assertEqual(paths['voices'], [])
            self.assertEqual(paths['root'], str(self.root.resolve()))
            self.assertEqual(os.environ['HF_HOME'], str(self.root / '.cache' / 'huggingface'))
        engine = self.engine()
        self.assertEqual(engine.voices, [])
        self.assertEqual(json.loads(engine.voice_file.read_text()), [])
        self.assertEqual(paths['asr_device'], 'cpu')
        self.assertTrue(Path(paths['worker_python']).is_relative_to(self.root))

    def test_delete_owned_voice_keeps_original_upload(self):
        engine = self.engine()
        key = 'own-' + 'a' * 32
        original = self.root / 'upload.txt'
        original.write_text('original fixture')
        reference = engine.data / 'references' / (key + '.wav')
        reference.parent.mkdir()
        reference.write_bytes(b'synthetic test fixture')
        engine.voices = [{'key': key, 'name': 'Test voice', 'reference_path': str(reference)}]
        engine.save_voices()
        engine.delete_voice(key)
        self.assertFalse(reference.exists())
        self.assertTrue(original.exists())
        self.assertEqual(json.loads(engine.voice_file.read_text()), [])

    def test_delete_never_removes_external_reference(self):
        engine = self.engine()
        reference = self.root / 'external.txt'
        reference.write_text('original fixture')
        key = 'own-' + 'b' * 32
        engine.voices = [{'key': key, 'name': 'Test', 'reference_path': str(reference)}]
        engine.delete_voice(key)
        self.assertTrue(reference.exists())

    def test_delete_keeps_shared_reference(self):
        engine = self.engine()
        key = 'own-' + 'c' * 32
        reference = engine.data / 'references' / (key + '.wav')
        reference.parent.mkdir()
        reference.write_bytes(b'synthetic test fixture')
        engine.voices = [{'key': key, 'reference_path': str(reference)},
                         {'key': 'other', 'reference_path': str(reference)}]
        engine.delete_voice(key)
        self.assertTrue(reference.exists())

    def test_delete_refused_during_generation(self):
        engine = self.engine()
        engine.active = 1
        with self.assertRaises(RuntimeError):
            engine.delete_voice('anything')

    def test_cleanup_never_accepts_arbitrary_directory(self):
        engine = self.engine()
        outside = self.root / 'keep-me'
        outside.mkdir()
        with self.assertRaises(ValueError):
            engine._delete_folder(outside)
        self.assertTrue(outside.exists())
        session = Path(engine.new_session())
        (session / 'generated.txt').write_text('fixture')
        engine.cleanup_session(str(session))
        self.assertFalse(session.exists())

    def test_sentence_offsets_match_text(self):
        text = '  One sentence.\nHere is another sentence with several words! Last?'
        chunks = text_parts(text, limit=24)
        self.assertGreater(len(chunks), 3)
        for chunk in chunks:
            self.assertEqual(text[chunk['char_start']:chunk['char_end']], chunk['text'])

    def test_language_detection(self):
        self.assertEqual(detect_language('Jeg vil gjerne høre på denne teksten, og deretter gå en tur.'), 'no')
        self.assertEqual(detect_language('This is a long enough English sentence to read aloud.'), 'en')

    def test_norwegian_prompts_have_requested_words(self):
        for key in ('nb', 'nb_long'):
            text = VOICE_PROMPTS[key][2]
            for word in ('YouTube', 'screenshot', 'voiceover'):
                self.assertIn(word, text)
        self.assertNotIn('nn', VOICE_PROMPTS)

    def test_public_guard_rejects_recordings_and_private_paths(self):
        with self.assertRaises(ValueError):
            check_file(self.root, Path('private.wav'))
        source = self.root / 'example.py'
        source.write_text('C:' + '/Users/' + 'Example/private.txt')
        with self.assertRaises(ValueError):
            check_file(self.root, source.relative_to(self.root))
        source.write_text('No local configuration or recordings here.')
        check_file(self.root, source.relative_to(self.root))


if __name__ == '__main__':
    unittest.main()
