ARG BASE_IMAGE=nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04
FROM ${BASE_IMAGE}
ARG APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/ubuntu
ARG MINICONDA_URL=https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-py311_24.11.1-0-Linux-x86_64.sh
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG DUPLEX_CONDA_CHANNEL=https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge
ARG DUPLEX_TORCH_INDEX_URL=https://mirror.sjtu.edu.cn/pytorch-wheels/cu128
USER root
WORKDIR /app
# Acquire CA roots before switching the Ubuntu repositories to HTTPS mirrors.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && sed -i -E "s|https?://(archive.ubuntu.com|security.ubuntu.com)/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends curl bzip2 libgomp1 libsndfile1 libnuma1 \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --retry 3 "$MINICONDA_URL" -o /tmp/miniconda.sh \
    && bash /tmp/miniconda.sh -b -p /opt/conda && rm /tmp/miniconda.sh
ENV PATH=/opt/duopra/env/bin:/opt/conda/bin:$PATH
ENV DUPLEX_CONDA_PREFIX=/opt/duopra/env DUPLEX_HARNESS_DEPS=/opt/duopra/env/harness-deps
ENV DUPLEX_HARNESS_PYTHON=/opt/duopra/env/bin/python
COPY requirements.txt pyproject.toml README.md /app/
COPY deploy/nvidia/constraints.txt /app/deploy/nvidia/constraints.txt
COPY backend/harness/requirements.txt /app/backend/harness/requirements.txt
RUN conda create -y -p "$DUPLEX_CONDA_PREFIX" --override-channels -c "$DUPLEX_CONDA_CHANNEL" \
        python=3.11 pip nodejs=22 decord=0.6.0 numpy=1.26.4 \
    && python -m pip install --no-cache-dir -c deploy/nvidia/constraints.txt --index-url "$DUPLEX_TORCH_INDEX_URL" \
        --extra-index-url "$PIP_INDEX_URL" torch==2.8.0+cu128 torchaudio==2.8.0+cu128 torchvision==0.23.0+cu128 \
    && python -m pip install --no-cache-dir -c deploy/nvidia/constraints.txt -r requirements.txt \
    && python -m pip install --no-cache-dir --target "$DUPLEX_HARNESS_DEPS" -r backend/harness/requirements.txt \
    && python -m pip check \
    && rm -rf /opt/conda/pkgs/*
COPY backend/ /app/backend/
COPY frontend/static/ /app/frontend/static/
COPY deploy/ /app/deploy/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
ENV PYTHONPATH=/app/backend:/app/backend/runtime:/app/backend/tools:/app/backend/web
ENV DUPLEX_RUN_DIR=/var/lib/duopra DUPLEX_PORT=8000
RUN DUPLEX_GPU_ID=0 bash -c 'source deploy/nvidia/environment.sh; python -c "from cuda_runtime import runtime_preflight; runtime_preflight(check_device=False)"'
EXPOSE 8000
ENTRYPOINT ["bash", "/app/deploy/nvidia/start.sh"]
