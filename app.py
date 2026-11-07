import hmac
import io
import logging
import os

import streamlit as st
from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from pypdf import PdfReader

load_dotenv()
logger = logging.getLogger("pdf_analyzer")

DEFAULT_MODEL = "gpt-6.1-terra"
MAX_CHARS = 200_000  # 너무 긴 PDF는 잘라서 전송 (PRD F7)
DEFAULT_MAX_CALLS = 20  # 세션당 AI 호출 횟수 상한 (비용 방어)

SYSTEM_PROMPT = (
    "당신은 PDF 문서를 정확하게 분석하는 어시스턴트입니다. "
    "문서 내용에 근거해서만 한국어로 답하세요. 문서에 없는 내용은 없다고 말하세요."
)
MODE_PROMPTS = {
    "요약": "다음 문서를 한국어로 간결하게 요약해 주세요.",
    "핵심 포인트": "다음 문서의 핵심 포인트를 한국어 글머리표로 정리해 주세요.",
}
MODES = ["요약", "핵심 포인트", "자유 질문"]


class PdfProblem(Exception):
    """사용자에게 그대로 보여줘도 되는 PDF 관련 문제."""


def get_secret(key: str, default: str | None = None) -> str | None:
    """로컬은 .env(환경변수) 우선, 없으면 Streamlit Cloud의 st.secrets 에서 읽는다."""
    value = os.getenv(key)
    if value:
        return value
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


@st.cache_data(show_spinner=False, ttl=600, max_entries=5)
def extract_text(data: bytes) -> tuple[str, int, int]:
    """(전체 텍스트, 페이지 수, 본문 글자 수). 문서 보관을 줄이려고 10분/5개까지만 캐시."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfProblem("암호로 보호된 PDF입니다. 암호를 해제한 파일을 올려 주세요.")
        n_pages = len(reader.pages)
        if n_pages == 0:
            raise PdfProblem("페이지가 없는 빈 PDF입니다.")
        pages, body_len = [], 0
        for i, page in enumerate(reader.pages, start=1):
            t = page.extract_text() or ""
            body_len += len(t.strip())
            pages.append(f"[페이지 {i}]\n{t}")
    except PdfProblem:
        raise
    except Exception:
        logger.exception("PDF 읽기 실패")
        raise PdfProblem("PDF를 읽을 수 없습니다. 손상되었거나 PDF 형식이 아닌 파일일 수 있습니다.")
    return "\n\n".join(pages), n_pages, body_len


def friendly_error(e: Exception) -> str:
    """OpenAI 오류를 사용자용 문구로 변환한다. 상세 원인은 서버 로그에만 남긴다."""
    logger.exception("OpenAI 호출 실패")
    if isinstance(e, AuthenticationError):
        return "API 키 인증에 실패했습니다. 관리자에게 문의해 주세요."
    if isinstance(e, PermissionDeniedError):
        return "이 API 키에는 해당 모델을 사용할 권한이 없습니다."
    if isinstance(e, NotFoundError):
        return "설정된 모델을 찾을 수 없습니다. 모델 이름 설정을 확인해 주세요."
    if isinstance(e, RateLimitError):
        return "요청이 너무 많거나 API 사용 한도를 초과했습니다. 잠시 후 다시 시도해 주세요."
    if isinstance(e, BadRequestError) and "context" in str(e).lower():
        return "문서가 너무 길어 모델이 처리할 수 없습니다. 더 짧은 문서로 시도해 주세요."
    if isinstance(e, (APIConnectionError, APITimeoutError)):
        return "OpenAI 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요."
    return "분석 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


def analyze(client: OpenAI, model: str, instruction: str, text: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{instruction}\n\n--- 문서 ---\n{text}"},
        ],
    )
    return resp.choices[0].message.content or "(빈 응답)"


def require_password(password: str) -> None:
    """APP_PASSWORD 가 설정된 경우 비밀번호를 맞춰야 앱을 사용할 수 있다."""
    if st.session_state.get("authed"):
        return
    with st.form("login"):
        entered = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("입장")
    if submitted:
        if hmac.compare_digest(entered.encode(), password.encode()):
            st.session_state["authed"] = True
            st.rerun()
        st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


st.set_page_config(page_title="PDF 분석기", page_icon="📄", layout="wide")

st.html(
    """
    <style>
    .block-container {padding-top: 2rem; max-width: 1200px;}
    div[data-testid="stMetric"] {
        background: #F1F5F9; border-radius: 12px; padding: 12px 16px;
    }
    .step-card {
        background: #F1F5F9; border-radius: 12px; padding: 16px 20px; margin-bottom: 12px;
    }
    .step-card b {color: #2563EB;}
    </style>
    """
)

api_key = get_secret("OPENAI_API_KEY")
model = get_secret("OPENAI_MODEL", DEFAULT_MODEL)
app_password = get_secret("APP_PASSWORD")
try:
    max_calls = int(get_secret("MAX_CALLS_PER_SESSION", str(DEFAULT_MAX_CALLS)))
except ValueError:
    max_calls = DEFAULT_MAX_CALLS

# ── 헤더 ──────────────────────────────────────────────
st.title("📄 PDF 분석기")
st.markdown("PDF 문서를 올리면 AI가 **요약**하고, **핵심을 정리**하고, **질문에 답해** 드립니다.")
st.caption("🔒 업로드한 문서의 내용은 분석을 위해 OpenAI로 전송됩니다. 기밀 문서는 올리지 마세요.")
st.divider()

if not api_key:
    st.error(
        "OPENAI_API_KEY 가 설정되지 않았습니다. 로컬에서는 .env 파일에, "
        "Streamlit Cloud 에서는 Settings → Secrets 에 추가하세요."
    )
    st.stop()

if app_password:
    require_password(app_password)

client = OpenAI(api_key=api_key)
calls_used = st.session_state.get("calls", 0)
limit_reached = calls_used >= max_calls

left, right = st.columns([1, 1.4], gap="large")

# ── 왼쪽: 업로드 / 설정 ───────────────────────────────
with left:
    st.subheader("1. 문서 업로드")
    uploaded = st.file_uploader("PDF 파일", type=["pdf"], label_visibility="collapsed")

    # 파일이 바뀌면 이전 결과/오류/질문을 비운다 (위젯은 항상 렌더링해서 상태 꼬임 방지)
    file_id = uploaded.file_id if uploaded else None
    if st.session_state.get("file_id") != file_id:
        st.session_state["file_id"] = file_id
        st.session_state.pop("result", None)
        st.session_state.pop("error", None)
        st.session_state["question"] = ""

    text = send_text = None
    file_problem = None
    if uploaded:
        try:
            text, n_pages, body_len = extract_text(uploaded.getvalue())
        except PdfProblem as e:
            file_problem = ("error", str(e))
        else:
            if body_len == 0:
                file_problem = (
                    "warning",
                    "텍스트를 추출할 수 없습니다. 스캔본(이미지) PDF일 수 있습니다.",
                )
                text = None

    if file_problem:
        getattr(st, file_problem[0])(file_problem[1])

    if text:
        c1, c2 = st.columns(2)
        c1.metric("페이지", f"{n_pages:,}")
        c2.metric("글자 수", f"{body_len:,}")
        st.caption(f"사용 모델: `{model}`")

        with st.expander("추출된 텍스트 미리보기"):
            st.text(text[:5000] + ("..." if len(text) > 5000 else ""))

        send_text = text
        if len(text) > MAX_CHARS:
            st.info(f"문서가 길어 앞쪽 {MAX_CHARS:,}자만 분석합니다.")
            send_text = text[:MAX_CHARS]

    st.subheader("2. 분석 방식")
    ready = bool(text)
    mode = st.radio(
        "분석 방식", MODES, horizontal=True, label_visibility="collapsed",
        key="mode", disabled=not ready,
    )
    question = ""
    if mode == "자유 질문":
        question = st.text_input(
            "문서에 대해 질문하세요", placeholder="예: 이 문서의 결론은 무엇인가요?",
            key="question", disabled=not ready,
        )
        instruction = f"다음 문서를 읽고 질문에 답해 주세요.\n질문: {question}"
        button_label = "질문하기"
        can_run = ready and bool(question.strip())
    else:
        instruction = MODE_PROMPTS[mode]
        button_label = "분석 시작"
        can_run = ready

    run = st.button(
        button_label, type="primary", use_container_width=True,
        disabled=not can_run or limit_reached,
    )
    if limit_reached:
        st.warning(f"이 세션의 분석 횟수({max_calls}회)를 모두 사용했습니다. 새로고침 후 다시 시도해 주세요.")

    if run and send_text:
        st.session_state.pop("result", None)
        st.session_state.pop("error", None)
        st.session_state["calls"] = calls_used + 1
        try:
            with st.spinner("분석 중..."):
                st.session_state["result"] = (
                    mode, analyze(client, model, instruction, send_text)
                )
        except Exception as e:
            st.session_state["error"] = friendly_error(e)
        st.rerun()  # 호출 횟수·제한 표시를 즉시 갱신

# ── 오른쪽: 결과 / 오류 / 빈 상태 안내 ─────────────────
with right:
    if "result" in st.session_state:
        res_mode, content = st.session_state["result"]
        head_col, dl_col = st.columns([3, 1], vertical_alignment="center")
        head_col.subheader(f"분석 결과 · {res_mode}")
        dl_col.download_button(
            "⬇ 다운로드", data=content, file_name="analysis.md",
            mime="text/markdown", use_container_width=True,
        )
        st.divider()
        st.markdown(content)
    elif "error" in st.session_state:
        st.subheader("분석에 실패했습니다")
        st.error(st.session_state["error"])
    elif not uploaded:
        st.subheader("사용 방법")
        for n, title, desc in [
            (1, "PDF 업로드", "왼쪽에서 분석할 PDF 파일을 선택하세요."),
            (2, "분석 방식 선택", "요약 · 핵심 포인트 · 자유 질문 중 고르세요."),
            (3, "결과 확인", "이 영역에 결과가 표시되며, 파일로 저장할 수 있습니다."),
        ]:
            st.markdown(
                f'<div class="step-card"><b>STEP {n}</b> &nbsp; <strong>{title}</strong>'
                f'<br>{desc}</div>',
                unsafe_allow_html=True,
            )
    elif file_problem:
        st.info("읽을 수 있는 PDF 파일을 올리면 이곳에 결과가 표시됩니다.")
    else:
        st.info("분석 방식을 선택하고 버튼을 누르면 결과가 여기에 표시됩니다.")
