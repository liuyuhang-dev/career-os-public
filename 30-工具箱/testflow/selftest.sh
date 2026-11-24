#!/usr/bin/env bash
# 跨平台自测在 selftest.py(Windows 上不一定有 bash,PowerShell 的 > 重定向
# 还会把二进制写坏)。这个包装只是为了在 POSIX 上习惯性 `bash selftest.sh` 也能跑。
set -eu
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 || command -v python)"
exec "$PY" "$DIR/selftest.py" "$@"
