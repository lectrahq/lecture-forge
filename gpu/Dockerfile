# Image for the rented GPU machine: AI4Bharat's NeMo fork + our training scripts.
#
# Build from the PROJECT ROOT (it needs backend/normalise.py too):
#     docker build -f gpu/Dockerfile -t lecture-indicconformer:v1 .
#
# The base image is the one AI4Bharat's own fork is built against, so the CUDA,
# PyTorch and Python versions are known to work with the checkpoint.
ARG BASE_IMAGE=nvcr.io/nvidia/pytorch:24.01-py3
FROM ${BASE_IMAGE}

# libsndfile/sox/ffmpeg: reading and resampling audio.
# rsync/ssh: moving the dataset in and the results back out.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 sox ffmpeg rsync openssh-client \
    && rm -rf /var/lib/apt/lists/*

# AI4Bharat's fork of NeMo. The official nemo_toolkit cannot load the
# IndicConformer checkpoint, so the fork is required, not optional.
ARG NEMO_REF=nemo-v2
RUN git clone --depth 1 --branch ${NEMO_REF} https://github.com/AI4Bharat/NeMo.git /opt/NeMo

WORKDIR /opt/NeMo
# Only the speech-recognition parts are installed. The fork's own Dockerfile also
# compiles Megatron-LM, Apex and Transformer Engine, but those serve large
# language models, take hours to compile, and IndicConformer does not use them.
RUN pip install --no-cache-dir -r requirements/requirements.txt \
    && pip install --no-cache-dir -e ".[asr]"

# The fork asks for "pytorch-lightning>=2.2.1" with no upper bound, so a fresh
# install picks up 2.6.x, which no longer provides NeptuneLogger - and NeMo 1.23
# imports that by name, so every "import nemo.collections.asr" fails. Pin to the
# version the fork was written against.
RUN pip install --no-cache-dir "pytorch-lightning==2.2.1"

# jiwer: word and character error rates. huggingface_hub: fetching the checkpoint.
RUN pip install --no-cache-dir jiwer huggingface_hub

WORKDIR /app
# normalise.py is shared with the website so that the reference text and the
# model's output are cleaned up in exactly the same way.
COPY backend/normalise.py /app/
COPY gpu/*.py gpu/*.sh /app/
RUN chmod +x /app/*.sh

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/workspace/hf \
    DATA_ROOT=/workspace/data

CMD ["bash"]
