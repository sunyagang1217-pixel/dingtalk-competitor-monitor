#!/bin/zsh
set -euo pipefail

readonly script_dir="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$script_dir/configure_dingtalk.py"
