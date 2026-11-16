import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/runtime'))
from deployment_model import ReleaseError
from standalone_model import load_inventory


class StandaloneInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'model'
        self.root.mkdir()
        provenance = dict(standalone=True, runtime_overlay_required=False,
                          grpo_update=420, actor_sha256='a' * 64)
        (self.root / 'PROVENANCE.json').write_text(json.dumps(provenance))
        (self.root / 'weights').write_bytes(b'original bytes')
        self.inventory = dict(schema='duopra.standalone.v1', update=420, actor_sha256='a' * 64,
                              files={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in self.root.iterdir()})
        self.manifest = Path(self.temp.name) / 'inventory.json'

    def load(self):
        self.manifest.write_text(json.dumps(self.inventory))
        return load_inventory(self.manifest, self.root)

    def test_retains_actual_update(self):
        self.assertEqual(self.load()['update'], 420)

    def test_rejects_changed_bytes(self):
        (self.root / 'weights').write_bytes(b'modified bytes')
        with self.assertRaises(ReleaseError):
            self.load()

    def test_rejects_missing_or_extra_files(self):
        (self.root / 'extra').touch()
        with self.assertRaises(ReleaseError):
            self.load()
        (self.root / 'extra').unlink()
        (self.root / 'weights').unlink()
        with self.assertRaises(ReleaseError):
            self.load()

    def test_rejects_relabelled_update(self):
        self.inventory['update'] = 421
        with self.assertRaises(ReleaseError):
            self.load()

    def test_rejects_symlink(self):
        p = self.root / 'weights'
        p.unlink()
        p.symlink_to(self.root / 'PROVENANCE.json')
        with self.assertRaises(ReleaseError):
            self.load()


class StandalonePrecisionTests(unittest.TestCase):
    def test_restore_retains_fp32_training_bytes_and_rejects_bf16_export(self):
        try:
            import torch
            from safetensors.torch import save_file
        except ImportError:
            self.skipTest('Precision checks run with the model dependencies installed')
        from standalone_model import restore_fp32
        from types import SimpleNamespace
        # Match Duopra tensor counts while keeping the backbone fixture tiny.
        names = [f'llm.model.layers.{i}.weight' for i in range(396)] + ['llm.model.norm.weight']
        values = {name: torch.tensor([0.1234567], dtype=torch.float32) for name in names}
        values.update({f'tts.projector_semantic.{i}.weight': torch.full((934272,), 0.1234567)
                       for i in range(4)})
        targets = {name: value.bfloat16().float() for name, value in values.items()}
        model = SimpleNamespace(named_parameters=lambda: targets.items())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shard = root / 'model-00001.safetensors'
            save_file(values, shard)
            (root / 'model.safetensors.index.json').write_text(json.dumps({
                'weight_map': {name: shard.name for name in values}}))
            settings = {'model': str(root), 'release': {'update': 420, 'actor_sha256': 'a' * 64}}
            backbone, projector = restore_fp32(model, settings)
            self.assertEqual(backbone['tensor_count'], 397)
            self.assertEqual(projector['parameter_count'], 3737088)
            for name in values:
                self.assertTrue(torch.equal(targets[name], values[name]), name)
            values[names[0]] = values[names[0]].bfloat16()
            save_file(values, shard)
            with self.assertRaisesRegex(ReleaseError, 'Invalid standalone FP32 tensor'):
                restore_fp32(model, settings)
