#!/bin/sh
set -eu

cd /opt/water-agent
python deploy/docker/prepare_runtime.py
exec python -m deploy.docker.server
