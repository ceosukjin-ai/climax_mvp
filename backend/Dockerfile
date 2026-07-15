# ===========================================================================
# Stage 1: Build dependencies
# ===========================================================================
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 빌드 도구 (psycopg, numpy 등 네이티브 컴파일용)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# CPU 버전 PyTorch를 별도 인덱스에서 설치 (이미지 크기 절반 줄임)
#
# 주의: pip 업그레이드에 --prefix를 쓰면 안 됩니다.
# 기존 pip를 지우고 새 pip를 /install에 설치해버려서 PATH에서 사라지고,
# 다음 pip 호출이 "pip: not found" (exit 127)로 죽습니다.
# 업그레이드는 prefix 없이, 패키지 설치만 --prefix로. 호출은 python -m pip 사용.
RUN python -m pip install --upgrade pip && \
    python -m pip install --prefix=/install \
        torch --index-url https://download.pytorch.org/whl/cpu && \
    python -m pip install --prefix=/install -r requirements.txt


# ===========================================================================
# Stage 2: Runtime image
# ===========================================================================
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface

# 런타임 전용 최소 라이브러리
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# non-root 사용자 (보안 권장)
RUN useradd --create-home --shell /bin/bash climax

WORKDIR /app

# builder에서 Python 패키지 복사
COPY --from=builder /install /usr/local

# 애플리케이션 코드
COPY --chown=climax:climax app ./app

# 모델 캐시 디렉토리 (non-root 사용자가 쓸 수 있게)
RUN mkdir -p /app/.cache/huggingface && \
    chown -R climax:climax /app/.cache

USER climax

# 헬스체크 — NCP Load Balancer가 이 경로를 폴링
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

EXPOSE 8000

# uvicorn 직접 실행 — gunicorn 없이 단순하게
# 워커 수는 NCP 서버 사양에 맞춰 --workers 로 조정
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2", \
     "--log-level", "info", \
     "--no-access-log"]
