FROM runpod/base:1.0.2-cuda1281-ubuntu2204

SHELL ["/bin/bash", "-c"]
WORKDIR /

# 1.  system packages + build tools + fresh CA certs
RUN apt-get update && \
    apt-get install -y \
        build-essential \
        python3-dev \
        ffmpeg \
        wget \
        git \
        ca-certificates \
        libcudnn8 \
        libcudnn8-dev && \
        apt-get clean && rm -rf /var/lib/apt/lists/*

# 2.  cache directories
RUN mkdir -p /cache/models /root/.cache/torch

# 3.  clone whisperx *before* pip needs it
#RUN git clone --depth 1 https://github.com/m-bain/whisperx.git /tmp/whisperx && \
#    cd /tmp/whisperx && \
#    git reset --hard 58f00339af7dcc9705ef40d97a1f40764b7cf555

# 4.  requirements file (local copy that uses the clone)
COPY builder/requirements.txt /builder/requirements.txt

# 5.  python dependencies
RUN python3 -m pip install --upgrade pip \
 && python3 -m pip install hf_transfer \ 
 && python3 -m pip install --no-cache-dir -r /builder/requirements.txt

# 6.  local VAD model
COPY models/whisperx-vad-segmentation.bin /root/.cache/torch/whisperx-vad-segmentation.bin

# 7.  builder scripts + model downloader
COPY builder /builder
RUN chmod +x /builder/download_models.sh
RUN --mount=type=secret,id=hf_token /builder/download_models.sh
# 8.  application code
COPY src .

CMD ["python3", "-u", "/rp_handler.py"]
