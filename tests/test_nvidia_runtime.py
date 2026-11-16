import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend'), str(ROOT/'backend/runtime'), str(ROOT/'backend/web'), str(ROOT/'backend/tools')]
import cuda_runtime as nvidia
from deployment_model import ReleaseError, serving_config
from security import public_proof


class NvidiaConfigurationTests(unittest.TestCase):
    def config(self):
        return serving_config({'update': 420}, '/models/duopra', ROOT/'deploy/nvidia/profile.json')

    def test_cuda_config_preserves_model_precision(self):
        value = self.config()
        self.assertEqual(value['runtime_backend'], 'nvidia-cuda')
        self.assertEqual(value['nvidia_profile'], nvidia.PROFILE)
        self.assertEqual(value['thinker_dtype'], 'float32')
        self.assertEqual(value['speech_dtype'], 'bfloat16')
        self.assertEqual(value['system_prompt'], 'Streaming Omni Conversation.')

    def test_profile_requires_exact_precision_settings(self):
        for value in (dict(nvidia.PROFILE, allow_tf32=True),
                      dict(nvidia.PROFILE, unregistered_option=True),
                      dict(nvidia.PROFILE, allow_tf32=0), None):
            with self.assertRaises(ReleaseError):nvidia.validate_profile(value)

    def test_plain_import_never_imports_torch(self):
        result = subprocess.run([sys.executable, '-S', '-c',
            'import sys; sys.path.insert(0, sys.argv[1]); import cuda_runtime; '
            'assert "torch" not in sys.modules',
            str(ROOT/'backend/runtime')], check=True, capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_public_hardware_label_has_no_node_identity(self):
        value = public_proof({'backend': 'nvidia-cuda', 'node_id': 'not-public', 'device_path': 'not-public'})
        self.assertEqual(value['backend'], 'nvidia-cuda')
        self.assertNotIn('not-public', json.dumps(value))

    def test_compose_mounts_gpu_and_runtime_secrets(self):
        text = (ROOT/'compose.nvidia.yaml').read_text()
        self.assertIn('driver: nvidia', text)
        self.assertIn('device_ids:', text)
        self.assertIn('capabilities: [gpu]', text)
        self.assertNotIn('privileged: true', text)
        self.assertIn('target: inventory.json', text)
        self.assertIn('target: access.json', text)
        shared = (ROOT/'scripts/deploy.sh').read_text()
        self.assertIn('--env-file', shared)
        self.assertIn('--check', shared)

    def test_runtime_contract_matches_audio_dependencies(self):
        contract = json.loads(nvidia.CONTRACT.read_text())
        self.assertEqual(contract['python'], '3.11')
        self.assertEqual(contract['packages']['transformers'], '4.57.6')
        self.assertIn('stepaudio2', contract['required_imports'])


class NvidiaDeviceTests(unittest.TestCase):
    def device(self, *, count=1, capability=(8, 0), bf16=True, available=True, cuda='12.8', hip=None):
        torch = MagicMock()
        torch.version = SimpleNamespace(cuda=cuda, hip=hip)
        torch.cuda.is_available.return_value = available
        torch.cuda.device_count.return_value = count
        torch.cuda.get_device_capability.return_value = capability
        torch.cuda.is_bf16_supported.return_value = bf16
        torch.equal.return_value = True
        return torch

    def test_wrong_hardware_and_multiple_owners_rejected(self):
        for kwargs in ({'count': 0}, {'count': 2}, {'capability': (7, 5)}, {'bf16': False},
                       {'available': False}, {'cuda': None}, {'hip': '6.0'}):
            with self.subTest(options=kwargs), self.assertRaises(ReleaseError):
                nvidia.validate_device(self.device(**kwargs))

    def test_device_smoke_is_not_model_acceptance(self):
        torch = self.device()
        proof = nvidia.validate_device(torch)
        self.assertTrue(proof['device_smoke_passed'])
        self.assertFalse(proof['vram_capacity_qualified'])
        torch.cuda.synchronize.assert_called_once()
        torch.equal.return_value = False
        with self.assertRaises(ReleaseError):nvidia.validate_device(torch)

    def test_runtime_preflight_stays_on_main_thread(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaisesRegex(ReleaseError, 'main thread'):
                pool.submit(nvidia.runtime_preflight, False).result(timeout=5)


if __name__ == '__main__':
    unittest.main()
