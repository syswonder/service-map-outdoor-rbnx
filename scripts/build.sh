#!/usr/bin/env bash
set -euo pipefail
PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG"
SOURCE="$(rbnx path root)"
export PYTHONPATH="$(rbnx path robonix-api):${PYTHONPATH:-}"
if command -v uv >/dev/null; then
  PY="${OUTDOOR_MAP_PYTHON:-$PKG/.venv/bin/python}"
  if [[ ! -x "$PY" ]]; then uv venv --system-site-packages --python "${ROS_PYTHON:-/usr/bin/python3}" "$PKG/.venv"; fi
  uv pip install --python "$PY" -e "$(rbnx path robonix-api)" 'grpcio-tools==1.76.0' 'numpy<2' 'setuptools<81' 'packaging>=24'
else
  PY="${OUTDOOR_MAP_PYTHON:-${ROS_PYTHON:-/usr/bin/python3}}"
  "$PY" -c 'import grpc_tools, numpy, setuptools, packaging, robonix_api'
fi
rbnx validate "$PKG"
rbnx codegen -p "$PKG" --mcp --ros2 --python "$PY"
set +u
source "${ROS_SETUP:-/opt/ros/${ROS_DISTRO:-humble}/setup.bash}"
set -u
cd "$PKG/rbnx-build/codegen/ros2_idl"
CMAKE_BUILD_PARALLEL_LEVEL=4 colcon build --packages-select map \
  --cmake-args "-DPython3_EXECUTABLE=$PY" "-DPYTHON_EXECUTABLE=$PY"
touch "$PKG/rbnx-build/.rbnx-built"
