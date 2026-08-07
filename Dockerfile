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

# 本地镜像标签：仅保留与 Git 无关的版本/时间信息。
ARG BUILD_VERSION=e4-local
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.version=${BUILD_VERSION}
LABEL org.opencontainers.image.created=${BUILD_DATE}

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

# 构建期下载中文 embedding 模型到 HF_HOME，运行时 HF_HUB_OFFLINE=1 离线加载。
RUN python - <<'PY'
from sentence_transformers import SentenceTransformer
_ = SentenceTransformer("BAAI/bge-small-zh-v1.5")
PY

# 只复制运行时需要的源码；正式数据目录（agent_data/vanna_data/data）与
# 运行期凭据不进镜像，由容器卷在首次启动时创建。
COPY backend/ /opt/water-agent/backend/
COPY config/ /opt/water-agent/config/
COPY deploy/ /opt/water-agent/deploy/
# E-3 推荐问题同步在运行期依赖生成器管线。
COPY tools/generate_question_suggestions.py /opt/water-agent/tools/generate_question_suggestions.py
COPY training/mysql_lzh_monitor_training.py /opt/water-agent/training/mysql_lzh_monitor_training.py
COPY training/sop/__init__.py training/sop/batch_schema.py training/sop/batch_validator.py training/sop/ddl_memory_identity.py training/sop/memory_write_plan.py /opt/water-agent/training/sop/
COPY training/mysql_lzh_monitor/sql_examples.json /opt/water-agent/training/mysql_lzh_monitor/sql_examples.json
COPY step4_server.py requirements.txt /opt/water-agent/
COPY --from=frontend-builder /build/frontend/dist /opt/water-agent/frontend/dist

RUN chmod +x /opt/water-agent/deploy/docker/entrypoint.sh \
    && mkdir -p /opt/water-agent/runtime/reports \
                 /opt/water-agent/runtime/traces

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)" || exit 1

# 单容器单进程：entrypoint 只启动一个 uvicorn 实例，无 --workers / gunicorn 池。
ENTRYPOINT ["/opt/water-agent/deploy/docker/entrypoint.sh"]
