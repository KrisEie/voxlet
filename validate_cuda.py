"""Check the optional GPU environment without quoting inline Python in PowerShell."""
import torch

if not torch.cuda.is_available():
    raise SystemExit('Cloning needs a compatible NVIDIA GPU and driver. Standard mode still works.')
print('CUDA is available for cloning.')
