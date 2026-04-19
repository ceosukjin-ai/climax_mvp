# 개발 환경 구축 가이드

집·연구실 어디서든 동일한 환경에서 작업하기 위한 설정입니다.

## 1단계 — 오늘 (집 PC)

### GitHub 레포 생성

1. GitHub → New repository
2. Name: `climax-mvp`
3. Private 체크 (특허·비즈니스 코드)
4. `Create repository`

### SSH 키 생성 및 등록 (처음 한 번)

```bash
# SSH 키 생성 — Enter 3번 치면 기본 설정으로 완료
ssh-keygen -t ed25519 -C "your-email@example.com"

# 공개키 출력 (내용 전체 복사)
cat ~/.ssh/id_ed25519.pub
```

GitHub → Settings → SSH and GPG keys → New SSH key에 붙여넣기.

### 로컬 Git 초기 설정 (처음 한 번)

```bash
git config --global user.name "본인 이름"
git config --global user.email "github에 등록한 이메일"
```

### 이 프로젝트 레포에 푸시

```bash
# 현재 climax-mvp 디렉토리에서
cd climax-mvp
git init
git add .
git commit -m "Initial commit: VSI+SMTI+PWI+VPTI core engine with tests"
git branch -M main
git remote add origin git@github.com:본인계정/climax-mvp.git
git push -u origin main
```

### VS Code 필수 확장

VS Code 좌측 Extensions 탭에서 설치:

- `Python` (Microsoft)
- `Pylance`
- `Ruff`
- `Remote - SSH` (NCP 서버 접속용)
- `GitLens` (Git 히스토리 시각화)
- `Thunder Client` (API 테스트, Postman 대체)
- `Docker`
- `DotENV`

### 로컬 Python 환경

```bash
cd backend
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# .env 만들기
cp .env.example .env
# 에디터로 .env 열어서 API 키 채우기 (Google Street View, 기상청 등)

# 테스트 실행
pytest

# 서버 시작
uvicorn app.main:app --reload
# 브라우저에서 http://localhost:8000/docs 열기
```

### VS Code Settings Sync 설정

좌측 하단 계정 아이콘 → `Turn on Settings Sync` → GitHub 로그인.
이후 연구실 PC에서도 VS Code가 자동으로 설정 동기화합니다.

## 2단계 — 내일 (연구실 PC)

### SSH 키 생성·등록 (연구실 PC에서도)

위의 SSH 키 생성 과정 동일하게 반복. 한 GitHub 계정에 여러 SSH 키 등록 가능.

### 레포 클론

```bash
cd ~/Projects    # 원하는 경로
git clone git@github.com:본인계정/climax-mvp.git
cd climax-mvp/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # 같은 API 키 입력
```

끝. 이제 집과 동일한 환경입니다.

## 3단계 — 매일의 워크플로우

### 작업 시작할 때 (집이든 연구실이든)

```bash
cd climax-mvp
git pull                    # 반대편에서 올린 변경사항 받기
source backend/.venv/bin/activate
```

### 작업 중

```bash
# 테스트 돌리며 개발
cd backend
pytest                      # 빠르게 (약 5초)
pytest tests/test_vsi.py    # 특정 모듈만

# 서버 띄우고 API 테스트
uvicorn app.main:app --reload
# 다른 터미널에서 Thunder Client 또는 curl로 요청
```

### 작업 끝날 때

```bash
git status                              # 변경 내역 확인
git add .
git commit -m "VPTI 음영 계수 정밀화"   # 의미 있는 메시지
git push                                # GitHub 업로드
```

이제 반대편 위치에서 `git pull`로 받을 수 있습니다.

## 4단계 — NCP 개발 서버 (GPU 추론 시)

Step 2부터 SegFormer 추론이 시작되면 로컬 CPU로는 느려서 NCP 서버가 필요해집니다.

### NCP Server 생성 (간략)

1. NCP 콘솔 → Server → Create Server
2. 이미지: Ubuntu 22.04
3. 사양: 개발용 `Standard-g3` (4vCPU, 16GB) 또는 GPU `P40`
4. Public IP 할당
5. ACG에서 22 (SSH), 8000 (API) 포트 허용

### VS Code Remote-SSH 연결

`.ssh/config`에 추가:

```
Host climax-dev
    HostName YOUR_NCP_PUBLIC_IP
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519
```

VS Code → Command Palette (Ctrl+Shift+P) → `Remote-SSH: Connect to Host` → `climax-dev`.

이제 **VS Code가 NCP 서버의 파일을 직접 편집**하게 됩니다. 집이든 연구실이든 동일한 원격 환경.

### 서버에서 초기 세팅

```bash
ssh climax-dev
sudo apt update && sudo apt install -y python3-venv python3-pip git
git clone git@github.com:본인계정/climax-mvp.git
cd climax-mvp/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

GPU 서버인 경우 `torch`를 CUDA 버전으로 별도 설치:

```bash
pip uninstall torch -y
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

## 주의사항

- **`.env`는 절대 git commit 금지** (`.gitignore`에 이미 포함됨). API 키가 GitHub에 올라가면 사고.
- **NCP GPU 서버는 안 쓸 땐 반드시 끄기**. 시간당 과금, 켜놓고 잊으면 월 100만원 나갈 수 있음.
- **커밋 메시지는 의미 있게**. `"fix"`, `"update"` 금지. `"VSI 가중치 파라미터화"`, `"SMTI 열용량 정규화 방향 수정"`처럼.
- **큰 파일은 git에 넣지 말기**. 모델 가중치(.pt, .pth)는 NCP Object Storage에 올리고 코드에서 다운로드하는 방식.
