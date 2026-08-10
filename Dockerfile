# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/water-agent/.cache/huggingface

RUN sed -i \
      -e 's|http://deb.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g' \
      -e 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
       fonts-wqy-zenhei libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/water-agent
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install \
       --index-url https://download.pytorch.org/whl/cpu \
       torch==2.12.1+cpu \
    && python -m pip install -r requirements.txt

# 运行时只复制需要的目录/文件；前端源码、工具、文档与训练过程资料不进入镜像
COPY backend/ /opt/water-agent/backend/
COPY config/ /opt/water-agent/config/
COPY deploy/ /opt/water-agent/deploy/
COPY agent_data/ /opt/water-agent/agent_data/
COPY vanna_data/ /opt/water-agent/vanna_data/
COPY data/ /opt/water-agent/data/
COPY training/mysql_lzh_monitor_training.py /opt/water-agent/training/mysql_lzh_monitor_training.py
COPY training/sop/__init__.py training/sop/batch_schema.py training/sop/batch_validator.py training/sop/ddl_memory_identity.py training/sop/memory_write_plan.py /opt/water-agent/training/sop/
COPY training/mysql_lzh_monitor/sql_examples.json /opt/water-agent/training/mysql_lzh_monitor/sql_examples.json
COPY .cache/ /opt/water-agent/.cache/
COPY step4_server.py requirements.txt /opt/water-agent/
COPY --from=frontend-builder /build/frontend/dist /opt/water-agent/frontend/dist

RUN chmod +x /opt/water-agent/deploy/docker/entrypoint.sh \
    && python /opt/water-agent/deploy/docker/prepare_runtime.py \
    && mkdir -p /opt/water-agent/runtime/reports \
                 /opt/water-agent/runtime/traces

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)" || exit 1

ENTRYPOINT ["/opt/water-agent/deploy/docker/entrypoint.sh"]
