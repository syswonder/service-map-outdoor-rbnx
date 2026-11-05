#!/usr/bin/env bash
set -euo pipefail
PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG"
PY="${OUTDOOR_MAP_PYTHON:-$PKG/.venv/bin/python}"
set +u
source "${ROS_SETUP:-/opt/ros/${ROS_DISTRO:-humble}/setup.bash}"
if [[ -n "${GAIT_ROS_SETUP:-}" ]]; then source "$GAIT_ROS_SETUP"; fi
source "$PKG/rbnx-build/codegen/ros2_idl/install/setup.bash"
set -u
export PYTHONPATH="$PKG:$PKG/rbnx-build/codegen/proto_gen:$PKG/rbnx-build/codegen/robonix_mcp_types:$(rbnx path robonix-api):${PYTHONPATH:-}"
export ROS_LOG_DIR="$PKG/rbnx-build/ros_logs"
export ROBONIX_PROVIDER_BIND_HOST="${ROBONIX_PROVIDER_BIND_HOST:-127.0.0.1}"
mkdir -p "$ROS_LOG_DIR"
exec "$PY" -u -m outdoor_mapping.main
