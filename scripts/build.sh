#!/usr/bin/env bash
set -euo pipefail
PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG"
SOURCE="$(rbnx path root)"
test -f "$SOURCE/capabilities/service/map_outdoor/get_map.v1.toml" || {
  echo 'Update the configured Robonix checkout with the map_outdoor contracts first.' >&2
  exit 1
}
PY="${OUTDOOR_MAP_PYTHON:-$PKG/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  uv venv --system-site-packages --python "${ROS_PYTHON:-/usr/bin/python3}" "$PKG/.venv"
fi
uv pip install --python "$PY" -e "$(rbnx path robonix-api)" \
  'grpcio-tools==1.76.0' 'numpy<2' 'setuptools<81' 'packaging>=24'
rbnx validate "$PKG"
rbnx codegen -p "$PKG" --mcp --ros2 --python "$PY"
set +u
source "${ROS_SETUP:-/opt/ros/${ROS_DISTRO:-humble}/setup.bash}"
set -u
cd "$PKG/rbnx-build/codegen/ros2_idl"
CMAKE_BUILD_PARALLEL_LEVEL=4 colcon build --packages-select map \
  --cmake-args "-DPython3_EXECUTABLE=$PY" "-DPYTHON_EXECUTABLE=$PY"
touch "$PKG/rbnx-build/.rbnx-built"
