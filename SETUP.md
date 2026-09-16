# 새 컴퓨터 세팅 가이드

다른 PC에서 이 프로젝트를 이어서 개발하기 위한 절차. (앱 *사용*만 할 거면 세팅 불필요 — Streamlit Cloud 주소로 접속하면 됨)

## 1. 필수 프로그램 설치

- **Python 3.12+** — https://www.python.org/downloads/ (설치 시 "Add to PATH" 체크)
- **Git** — https://git-scm.com/downloads
- (개발용) **Claude Code** — https://claude.com/claude-code

## 2. 코드 받기

```bash
git clone https://github.com/SONGSONGSONG1127/korea-trading-agents.git
cd korea-trading-agents
pip install -r requirements.txt
```

## 3. 비밀 파일 배치 (필수)

`.streamlit/secrets.toml` 은 보안상 git에 없다. **이사 키트(USB)** 에서 복사해
프로젝트 폴더 안 `.streamlit/secrets.toml` 위치에 그대로 넣는다.

```
korea-trading-agents/
└── .streamlit/
    └── secrets.toml   ← 여기
```

⚠️ 이 파일은 절대 git 커밋·메신저 공개방·공유폴더에 올리지 말 것.
(DART 키, 구글 서비스계정 개인키, 앱 접속코드 포함)

## 4. 푸시 권한 (개발 시)

이사 키트의 `git-token.txt` 에 있는 명령을 터미널에 붙여넣으면 push 권한 연결 완료.

## 5. 로컬 실행 (선택)

```bash
python -m streamlit run app.py
```

브라우저에서 http://localhost:8501 → 접속 코드 입력.

## 참고

- 코드 수정 후 `git push` 하면 Streamlit Cloud 가 1~2분 내 자동 재배포
- 매일 아침 7시(KST) 텔레그램 브리핑은 GitHub Actions 가 실행 — PC 세팅과 무관하게 항상 작동
- 간단한 수정은 PC 세팅 없이 github.com 에서 파일 직접 편집해도 됨
