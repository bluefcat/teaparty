"""Internal installer for teaparty.bat. Python standard library only.

Installs a single, compatible PyTorch/torchvision pair for CPU or NVIDIA CUDA,
then the complete, unified requirements.txt. No admin rights required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / 'requirements.txt'
STAMP = Path(sys.prefix) / '.teaparty-installed.json'
BOOTSTRAP_VERSION = 2
PYTORCH = ('torch==2.11.0', 'torchvision==0.26.0')
CUDA_INDEX = 'https://download.pytorch.org/whl/cu128'
CPU_INDEX = 'https://download.pytorch.org/whl/cpu'


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    print('  >', ' '.join(args), flush=True)
    return subprocess.run(args, cwd=ROOT, check=check)


def pip(*args: str) -> None:
    run(sys.executable, '-m', 'pip', *args)


def requirements_hash() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def has_nvidia() -> bool:
    binary = shutil.which('nvidia-smi')
    if not binary:
        return False
    try:
        result = subprocess.run([binary, '-L'], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=10, check=False)
        return result.returncode == 0 and b'GPU ' in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def installed_cuda_works() -> bool:
    code = 'import torch; assert torch.cuda.is_available(); print("CUDA:", torch.cuda.get_device_name(0))'
    return subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=False).returncode == 0


def check_imports() -> None:
    code = '''
import torch, torchvision, timm, einops, numpy, PIL, tqdm, send2trash
assert torch.__version__.split('+')[0] == '2.11.0', torch.__version__
assert torchvision.__version__.split('+')[0] == '0.26.0', torchvision.__version__
print('Core packages OK | PyTorch:', torch.__version__, '| CUDA:', torch.cuda.is_available())
'''
    run(sys.executable, '-c', code)


def read_stamp() -> dict:
    try:
        payload = json.loads(STAMP.read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def write_stamp(backend: str) -> None:
    payload = {'setup_version': BOOTSTRAP_VERSION,
               'requirements_hash': requirements_hash(),
               'python': list(sys.version_info[:2]),
               'backend': backend}
    temporary = STAMP.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    temporary.replace(STAMP)


def main() -> int:
    parser = argparse.ArgumentParser(description='TEA PARTY environment setup')
    parser.add_argument('--dry-run', action='store_true', help='show install plan without installing')
    parser.add_argument('--force', action='store_true', help='reinstall dependencies even if already installed')
    args = parser.parse_args()

    if sys.maxsize <= 2**32:
        raise RuntimeError('64-bit Python is required.')
    if not REQUIREMENTS.is_file():
        raise FileNotFoundError(f'Missing requirements.txt: {REQUIREMENTS}')
    if sys.prefix == sys.base_prefix and not args.dry_run:
        raise RuntimeError('Please run via teaparty.bat; virtual environment is not active.')

    gpu = has_nvidia()
    print('TEA PARTY setup | Python', sys.version.split()[0], '| NVIDIA GPU:', gpu, flush=True)
    if args.dry_run:
        print('Will install CUDA PyTorch first' if gpu else 'Will install CPU PyTorch', flush=True)
        print('Then will install all dependencies from requirements.txt', flush=True)
        return 0

    prev = read_stamp()
    if (not args.force and prev.get('setup_version') == BOOTSTRAP_VERSION
            and prev.get('requirements_hash') == requirements_hash()
            and prev.get('python') == list(sys.version_info[:2])):
        try:
            check_imports()
        except (subprocess.CalledProcessError, OSError):
            print('Environment needs repair. Reinstalling...', flush=True)
        else:
            print('Dependencies already installed. Launching TEA PARTY.', flush=True)
            return 0

    STAMP.unlink(missing_ok=True)
    pip('install', '--upgrade', 'pip')
    backend = 'cpu'
    if gpu:
        print('\nInstalling CUDA-enabled PyTorch...', flush=True)
        try:
            pip('install', *PYTORCH, '--index-url', CUDA_INDEX)
            if installed_cuda_works():
                backend = 'cuda'
            else:
                print('CUDA could not be activated; switching to CPU.', flush=True)
        except subprocess.CalledProcessError:
            print('CUDA installation failed; switching to CPU.', flush=True)

    if backend == 'cpu':
        print('\nInstalling CPU PyTorch...', flush=True)
        # Replaces a CUDA build if it was installed but cannot run on this PC.
        pip('install', '--force-reinstall', *PYTORCH, '--index-url', CPU_INDEX)

    print('\nInstalling TEA PARTY packages...', flush=True)
    pip('install', '-r', str(REQUIREMENTS))
    check_imports()
    write_stamp(backend)
    print('\nSetup complete | backend:', backend.upper(), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print('\nSETUP FAILED:', exc, file=sys.stderr, flush=True)
        print('Check network access, disk space, and Python version.', file=sys.stderr, flush=True)
        raise SystemExit(1)
