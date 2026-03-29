#!/usr/bin/env bash
# macOS / Linux：在项目根目录执行 ./start_desktop.sh（若无执行权限：chmod +x start_desktop.sh）
set -euo pipefail
cd "$(dirname "$0")"

PORT="${FLASK_PORT:-5000}"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate

pip install -r requirements.txt

export FLASK_PORT="$PORT"
exec python desktop_pet.py
