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
# torch 는 CPU 전용 휠로 설치하고, **requirements.txt 에서는 빼 둔다.**
#
# 2026-09-01 에 같은 사고가 났고(libcublasLt.so.12 추출 중 no space left on device),
# 그때 처방은 "양쪽 버전을 똑같이 고정한다" 였다. **그 처방은 듣지 않았다** —
# 2026-09-19 배포 이미지가 여전히 torch 2.5.0+cu124, nvidia/ 2.7 GB, 이미지 10.3 GB 였다.
#
# 진짜 원인은 `--prefix=/install` 이다. 첫 설치는 /install 로 들어가는데 그 경로는
# 두 번째 pip 의 sys.path 에 없다. 그래서 pip 는 torch 가 이미 있는 줄 모르고,
# 버전이 같아도 requirements.txt 의 torch==2.5.0 을 **PyPI 에서 다시 받는다** → CUDA 판.
# 따라서 해결은 버전 고정이 아니라 **requirements.txt 에서 torch 줄을 지우는 것**이다.
# (2026-09-19 배포 두 번 실패. 48 GB 서버에 14 GB 여유로도 빌드가 안 됐다.)
RUN python -m pip install --upgrade pip && \
    python -m pip install --prefix=/install \
        torch==2.5.0 --index-url https://download.pytorch.org/whl/cpu && \
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
# vpti_core는 app이 최상위로 import하는 별도 패키지 (app/services/orchestrator.py,
# app/api/routes.py에서 `from vpti_core import ...`). 빠뜨리면 기동 시
# ModuleNotFoundError: No module named 'vpti_core' 로 죽습니다.
COPY --chown=climax:climax app ./app
COPY --chown=climax:climax vpti_core ./vpti_core

# 모델 캐시 디렉토리 (non-root 사용자가 쓸 수 있게)
RUN mkdir -p /app/.cache/huggingface /app/.cache/concept && \
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
