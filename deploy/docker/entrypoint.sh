#!/bin/sh
set -eu

cd /opt/water-agent
# 默认零修改：不执行 legacy 路径迁移、不触碰 Catalog。
# 仅当显式设置 WATER_AGENT_ENABLE_LEGACY_PATH_MIGRATION=1 时才执行迁移工具。
if [ "${WATER_AGENT_ENABLE_LEGACY_PATH_MIGRATION:-}" = "1" ]; then
    python deploy/docker/prepare_runtime.py
fi
exec python -m deploy.docker.server
