# Local speech recognition service: AI4Bharat IndicConformer behind a small HTTP API.
#
# Build from the PROJECT ROOT (it needs backend/ and gpu/ too):
#     docker build -f asr/Dockerfile -t lecture-asr:v1 .
#
# This deliberately mirrors gpu/Dockerfile's NeMo installation. The two are kept
# separate because they do different jobs - this one serves the website, that one
# trains on a rented GPU - and a service used by the public site should not change
# whenever the training image is edited.
ARG BASE_IMAGE=nvcr.io/nvidia/pytorch:24.01-py3
FROM ${BASE_IMAGE}

RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 sox ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# AI4Bharat's fork. The official nemo_toolkit cannot load the IndicConformer
# checkpoint, and the fork is also what adds the language_id argument the model
# needs (see common.transcribe).
ARG NEMO_REF=nemo-v2
RUN git clone --depth 1 --branch ${NEMO_REF} https://github.com/AI4Bharat/NeMo.git /opt/NeMo

WORKDIR /opt/NeMo
RUN pip install --no-cache-dir -r requirements/requirements.txt \
    && pip install --no-cache-dir -e ".[asr]"

# The fork asks for "pytorch-lightning>=2.2.1" with no upper bound, so a fresh
# install picks up 2.6.x, which dropped NeptuneLogger - a name NeMo 1.23 imports
# directly, making every "import nemo.collections.asr" fail. Pin it.
RUN pip install --no-cache-dir "pytorch-lightning==2.2.1"

RUN pip install --no-cache-dir fastapi "uvicorn[standard]" huggingface_hub

WORKDIR /app
# normalise.py is shared with the website so text is cleaned up identically;
# common.py holds the model loading and the language_id handling.
COPY backend/normalise.py /app/
COPY gpu/common.py /app/
COPY asr/server.py /app/

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/data/hf_cache \
    DATA_DIR=/data \
    # This container runs as an ordinary user, so these libraries cannot write
    # their caches next to their own (root-owned) source files. Numba in
    # particular fails outright with "cannot cache function ... no locator
    # available" unless it is given somewhere writable.
    NUMBA_CACHE_DIR=/tmp/numba \
    XDG_CACHE_HOME=/tmp/cache \
    MPLCONFIGDIR=/tmp/mpl

EXPOSE 9000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000"]
