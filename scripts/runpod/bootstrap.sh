#!/usr/bin/env bash
# Run ON the RunPod GPU machine (via SSH). Installs the local_watermark pipeline + weights.
set -euo pipefail

ROOT="${YCFCrawl_ROOT:-/workspace/ycfcrawl}"
# Network volumes can't host a venv (ensurepip fails), so it lives on container disk.
VENV="${WM_VENV:-/root/wm-venv}"
mkdir -p "$ROOT/public/yachts" "$ROOT/jferments-watermark-remover"

cd "$ROOT"

if [[ ! -x "$VENV/bin/python" ]]; then
  # Reuse the CUDA build of torch that ships with the RunPod PyTorch image.
  python3 -m venv --system-site-packages "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

pip install -q --upgrade pip
pip install -q -r requirements-local-watermark.txt
pip uninstall -q -y opencv-python opencv-python-headless 2>/dev/null || true
pip install -q --force-reinstall --no-cache-dir "opencv-python-headless==4.11.0.86" "numpy>=1.24.3,<2"
"$VENV/bin/python" -c "import cv2"  # fail fast if wheel did not unpack

WEIGHTS="$ROOT/jferments-watermark-remover/yolo11x-train28-best.pt"
if [[ ! -f "$WEIGHTS" ]]; then
  wget -q -O "$WEIGHTS" \
    "https://huggingface.co/spaces/fancyfeast/joycaption-watermark-detection/resolve/main/yolo11x-train28-best.pt"
fi

python3 -c "import torch; print('CUDA:', torch.cuda.is_available(), 'GPUs:', torch.cuda.device_count())"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true

echo "==> Preloading LaMa weights (one-time download)"
python3 -c "import torch; from simple_lama_inpainting import SimpleLama; SimpleLama(device='cuda' if torch.cuda.is_available() else 'cpu'); print('LaMa ready')"

echo "Bootstrap done: $VENV"
