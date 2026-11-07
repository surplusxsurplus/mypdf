# PDF 분석기 (Streamlit)

PDF를 업로드하면 OpenAI 모델(`gpt-6.1-terra`)로 요약/핵심 정리/질의응답을 해주는 앱.

## 로컬 실행
```bash
pip install -r requirements.txt
cp .env.example .env   # OPENAI_API_KEY 입력
streamlit run app.py
```

## Streamlit Cloud 배포
1. 이 폴더를 GitHub 저장소에 push (`.env` 는 `.gitignore` 로 제외됨)
2. https://share.streamlit.io 에서 저장소 선택, Main file: `app.py`
3. App settings → Secrets 에 입력:
```toml
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL = "gpt-6.1-terra"
APP_PASSWORD = "원하는-비밀번호"      # 공개 배포 시 권장 (API 비용 보호)
MAX_CALLS_PER_SESSION = "20"          # 선택
```

> API 키는 `sk-` 로 시작하는 값만 따옴표 안에 넣으세요 (앞에 `$` 등 다른 문자 금지).
> 로컬에서 `.env` 를 고쳤다면 실행 중인 Streamlit 서버를 **재시작**해야 반영됩니다.
> OpenAI 대시보드에서 월 사용 한도도 함께 설정하세요.
