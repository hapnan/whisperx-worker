FROM runpod/base:0.6.2-cuda12.4.1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
WORKDIR /

# 1.  system packages + build tools + fresh CA certs
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        ffmpeg \
        wget \
        git \
        ca-certificates \
        libcudnn8 \
        libcudnn8-dev && \
    update-ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 2.  cache directories
RUN mkdir -p /cache/models /root/.cache/torch

# 3.  clone whisperx *before* pip needs it
RUN git clone --depth 1 https://github.com/m-bain/whisperx.git /tmp/whisperx && \
    cd /tmp/whisperx && \
    git reset --hard 58f00339af7dcc9705ef40d97a1f40764b7cf555

# 4.  requirements file (local copy that uses the clone)
COPY builder/requirements.txt /builder/requirements.txt

# 5.  python dependencies
RUN python3 -m pip install --upgrade pip hf_transfer --no-cache-dir && \
    python3 -m pip install -r /builder/requirements.txt --no-cache-dir \
    --log /tmp/pip-reqs.log || (echo '----- pip-reqs.log -----'; sed -n '1,2000p' /tmp/pip-reqs.log; exit 1)

# 6.  local VAD model
COPY models/whisperx-vad-segmentation.bin /root/.cache/torch/whisperx-vad-segmentation.bin

# 7.  builder scripts + model downloader
COPY builder /builder
RUN chmod +x /builder/download_models.sh && /builder/download_models.sh

# 8.  application code
COPY src .

CMD ["python3", "-u", "/rp_handler.py"]
