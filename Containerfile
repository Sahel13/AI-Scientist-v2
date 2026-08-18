FROM docker.io/python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CUDA_VISIBLE_DEVICES= \
    HOME=/cache/home \
    HF_HOME=/cache/huggingface \
    TRANSFORMERS_CACHE=/cache/huggingface \
    MPLCONFIGDIR=/cache/matplotlib

# This is the orchestration image. Generated experiments are expected to be
# submitted to an external HPC/Slurm cluster rather than run in this VM.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        chktex \
        git \
        openssh-client \
        poppler-utils \
        rsync \
        texlive-fonts-recommended \
        texlive-latex-base \
        texlive-latex-extra \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

# Use the official CPU wheel index. No CUDA or ROCm runtime is installed.
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch torchvision \
    && python -m pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /workspace/AI-Scientist-v2

CMD ["bash"]
