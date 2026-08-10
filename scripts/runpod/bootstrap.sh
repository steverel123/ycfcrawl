#!/usr/bin/env bash
# Run ON the RunPod GPU machine (via SSH). Installs jferments pipeline + weights.
set -euo pipefail

ROOT="${YCFCrawl_ROOT:-/workspace/ycfcrawl}"
mkdir -p "$ROOT/public/yachts"

cd "$ROOT"

if [[ ! -d jferments-watermark-remover ]]; then
  git clone https://github.com/jferments/watermark_remover.git jferments-watermark-remover
fi

cd jferments-watermark-remover

if [[ ! -d venv ]]; then
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

pip install -q --upgrade pip
pip install -q rich ultralytics simple-lama-inpainting opencv-python-headless torch torchvision

if [[ ! -f yolo11x-train28-best.pt ]]; then
  wget -q -O yolo11x-train28-best.pt \
    "https://huggingface.co/spaces/fancyfeast/joycaption-watermark-detection/resolve/main/yolo11x-train28-best.pt"
fi

python3 -c "import torch; print('CUDA:', torch.cuda.is_available(), 'GPUs:', torch.cuda.device_count())"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true

echo "Bootstrap done: $ROOT/jferments-watermark-remover"
