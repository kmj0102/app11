"""둘의 하루 — Streamlit 커플 다이어리

실행: pip install -r requirements.txt && streamlit run app.py
배포: 두 파일을 GitHub에 올리고 Streamlit Community Cloud에서 app.py를 지정.
주의: GitHub Pages는 Python 서버를 실행하지 못합니다.

Community Cloud > App settings > Secrets에 다음을 설정하세요.
DATABASE_URL = "postgresql://USER:PASSWORD@HOST:PORT/DATABASE?sslmode=require"
APP_URL = "https://YOUR-APP.streamlit.app"

DATABASE_URL: Supabase/Neon 등의 PostgreSQL 연결 문자열. Supabase에서는
Connect 메뉴의 Session pooler 연결 문자열 사용을 권장합니다.
APP_URL: 배포 완료 후 실제 앱 주소. QR에 사용합니다.
비밀값을 GitHub에 커밋하지 마세요. 테이블은 첫 연결 때 자동 생성됩니다.
DATABASE_URL이 없으면 SQLite 로컬 체험 모드입니다. 클라우드의 SQLite 파일은
재시작/재배포 시 사라질 수 있으므로 실제 사용에는 외부 DB가 필요합니다.

처음 한 사람이 '처음 연결'에서 두 사람의 별명과 각자 비밀번호를 등록하고,
생성된 연결 코드를 상대에게 전달합니다. 상대는 자신의 비밀번호로 로그인합니다.
QR에는 연결 코드만 포함되며 비밀번호는 포함되지 않습니다.
비밀번호 재설정/백업 복구 기능은 없으므로 비밀번호를 안전하게 보관하세요.
"""
import os
import re
import hmac
import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
from io import BytesIO
from urllib.parse import urlencode

import streamlit as st
import qrcode

st.set_page_config(page_title="둘의 하루", page_icon="💌", layout="centered", initial_sidebar_state="collapsed")
KST = timezone(timedelta(hours=9))
MOODS = ["😊 행복", "🥰 설렘", "😌 평온", "😢 우울", "😤 속상", "😴 피곤"]


def setting(key, default=""):
    try:
        return str(st.secrets.get(key, os.environ.get(key, default)))
    except FileNotFoundError:
        return os.environ.get(key, default)


DB_URL = setting("DATABASE_URL")


def connection():
    if DB_URL:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(DB_URL, row_factory=dict_row, connect_timeout=10)
    conn = sqlite3.connect("couple_diary.sqlite3", timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def execute(conn, sql, args=()):
    return conn.execute(sql if DB_URL else sql.replace("%s", "?"), args)


@st.cache_resource
def initialize():
    with connection() as conn:
        execute(conn, """CREATE TABLE IF NOT EXISTS couple_rooms (
            room TEXT PRIMARY KEY, name_a TEXT NOT NULL, name_b TEXT NOT NULL,
            hash_a TEXT NOT NULL, hash_b TEXT NOT NULL)""")
        execute(conn, """CREATE TABLE IF NOT EXISTS couple_entries (
            id TEXT PRIMARY KEY, room TEXT NOT NULL, author TEXT NOT NULL,
            mood TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
            read_at TEXT)""")
        execute(conn, "CREATE INDEX IF NOT EXISTS couple_room_time ON couple_entries(room, created_at)")
        conn.commit()
    return True


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 260000).hex()
    return salt + ":" + value


def password_ok(password, saved):
    return hmac.compare_digest(password_hash(password, saved.split(":")[0]), saved)


def now():
    return datetime.now(timezone.utc).isoformat()


def display_time(value):
    return datetime.fromisoformat(value).astimezone(KST).strftime("%m/%d %H:%M")


def clean_room(value):
    return re.sub(r"[^A-Z0-9]", "", value.upper())[:16]


st.markdown("""<style>
.stApp {background: #fff9f7;}
.block-container {max-width: 460px; padding: .65rem .9rem 1rem;}
header[data-testid="stHeader"] {background: transparent; height: 0;}
[data-testid="stToolbar"] {display: none;}
[data-testid="stVerticalBlock"] {gap: .42rem;}
h1 {font-size: 1.5rem !important; padding: 0 !important;}
p {margin-bottom: .25rem;}
.stButton button, .stFormSubmitButton button {border-radius: 12px; min-height: 2.35rem;}
textarea {font-size: 16px !important;}
input {font-size: 16px !important;}
[data-testid="stCaptionContainer"] {font-size: .75rem;}
@media(max-width: 480px) {
 .block-container {padding-top: .45rem;}
 [data-testid="stHorizontalBlock"] {gap: .4rem; flex-wrap: nowrap;}
 [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {min-width: 0 !important; flex: 1 1 0 !important;}
}
</style>""", unsafe_allow_html=True)

try:
    initialize()
except Exception:
    st.error("저장소에 연결하지 못했어요. DATABASE_URL과 DB의 접속 허용 설정을 확인해 주세요.")
    st.stop()


@st.dialog("처음 연결하기")
def create_room():
    st.caption("한 사람이 두 계정을 만들어 주세요. 비밀번호는 서로 다르게 정해 주세요.")
    with st.form("new_room"):
        a = st.text_input("나의 별명", max_chars=12)
        pa = st.text_input("나의 비밀번호 (8자 이상)", type="password")
        b = st.text_input("상대 별명", max_chars=12)
        pb = st.text_input("상대 초기 비밀번호 (8자 이상)", type="password")
        submit = st.form_submit_button("우리 공간 만들기", use_container_width=True)
    if submit:
        if not a.strip() or not b.strip() or a.strip() == b.strip():
            st.error("서로 다른 별명 두 개를 입력해 주세요.")
            return
        if min(len(pa), len(pb)) < 8 or pa == pb:
            st.error("8자 이상의 서로 다른 비밀번호를 정해 주세요.")
            return
        room = secrets.token_hex(6).upper()
        try:
            with connection() as conn:
                execute(conn, "INSERT INTO couple_rooms VALUES (%s,%s,%s,%s,%s)",
                        (room, a.strip(), b.strip(), password_hash(pa), password_hash(pb)))
                conn.commit()
        except Exception:
            st.error("공간을 만들지 못했어요. 잠시 후 다시 시도해 주세요.")
            return
        st.session_state.auth = {"room": room, "slot": "a", "name": a.strip(), "partner": b.strip()}
        st.query_params["room"] = room
        st.rerun()


@st.dialog("우리 공간 · QR 공유")
def share(auth):
    st.write("연결 코드")
    st.code(auth["room"], language=None)
    st.caption("상대는 '상대(B)'를 선택하고 자신의 비밀번호로 들어오면 돼요.")
    base = st.text_input("앱 주소", value=setting("APP_URL"), placeholder="https://your-app.streamlit.app")
    if base:
        if not base.startswith("https://"):
            st.info("배포된 https:// 앱 주소를 입력해 주세요.")
        else:
            url = base.split("?")[0].split("#")[0].rstrip("/") + "/?" + urlencode({"room": auth["room"]})
            buf = BytesIO()
            qrcode.make(url).save(buf, format="PNG")
            st.image(buf.getvalue(), width=210)
            st.code(url, language=None)
            st.download_button("QR 이미지 다운로드", buf.getvalue(), "couple-qr.png", "image/png")
    st.caption("QR에 비밀번호는 들어가지 않아요. 코드와 비밀번호는 별도로 안전하게 전달하세요.")


st.title("💌 둘의 하루")
if "auth" not in st.session_state:
    st.caption("기분 하나, 짧은 일기 하나. 서로의 하루를 만나세요.")
    if not DB_URL:
        st.info("현재 로컬 체험 모드 · 실제 배포 시 DATABASE_URL을 설정하세요.")
    with st.form("login"):
        room = st.text_input("우리 연결 코드", value=clean_room(st.query_params.get("room", "")), max_chars=16)
        slot_label = st.selectbox("나는", ["나(A) · 공간을 만든 사람", "상대(B) · 초대받은 사람"])
        password = st.text_input("내 비밀번호", type="password")
        login = st.form_submit_button("우리 공간 들어가기", use_container_width=True, type="primary")
    if login:
        room = clean_room(room)
        slot = "a" if slot_label.startswith("나") else "b"
        try:
            with connection() as conn:
                row = execute(conn, "SELECT * FROM couple_rooms WHERE room=%s", (room,)).fetchone()
            if row and password_ok(password, row["hash_" + slot]):
                st.session_state.auth = {"room": room, "slot": slot, "name": row["name_" + slot],
                                         "partner": row["name_b" if slot == "a" else "name_a"]}
                st.query_params["room"] = room
                st.rerun()
            else:
                st.error("연결 코드 또는 비밀번호가 맞지 않아요.")
        except Exception:
            st.error("로그인에 실패했어요. DB 연결을 확인해 주세요.")
    if st.button("처음 연결하기", use_container_width=True):
        create_room()
    st.stop()

auth = st.session_state.auth
left, right = st.columns([3, 2])
left.caption(f"{auth['name']} ♡ {auth['partner']}")
with right:
    with st.popover("⚙️ 공간", use_container_width=True):
        if st.button("QR · 연결 코드", use_container_width=True):
            share(auth)
        if st.button("로그아웃", use_container_width=True):
            del st.session_state["auth"]
            st.rerun()
        if not DB_URL:
            st.caption("로컬 체험 모드: 클라우드에서는 기록이 유실될 수 있어요.")

if st.session_state.pop("saved_notice", False):
    st.toast("오늘의 마음을 전했어요 💌")

with st.form("diary", clear_on_submit=True):
    mood = st.selectbox("오늘의 기분", MOODS, label_visibility="collapsed")
    body = st.text_area("오늘의 일기", placeholder="오늘의 마음을 들려줘 (최대 500자)",
                        height=86, max_chars=500, label_visibility="collapsed")
    sent = st.form_submit_button("💌 마음 전하기", type="primary", use_container_width=True)
if sent:
    if not body.strip():
        st.toast("짧게라도 마음을 적어 주세요.")
    else:
        try:
            with connection() as conn:
                execute(conn, "INSERT INTO couple_entries (id,room,author,mood,body,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                        (secrets.token_hex(16), auth["room"], auth["slot"], mood, body.strip(), now()))
                conn.commit()
            st.session_state.saved_notice = True
            st.rerun()
        except Exception:
            st.error("저장하지 못했어요. 다시 작성하기 전에 DB 연결을 확인해 주세요.")

c1, c2 = st.columns([3, 1])
c1.markdown("**📬 우리의 기록**")
if c2.button("↻", help="상대의 새 일기와 읽음 상태 새로고침", use_container_width=True):
    st.rerun()
try:
    with connection() as conn:
        rows = [dict(r) for r in execute(conn,
                "SELECT * FROM couple_entries WHERE room=%s ORDER BY created_at DESC LIMIT 100",
                (auth["room"],)).fetchall()]
except Exception:
    st.error("일기를 불러오지 못했어요. 새로고침해 주세요.")
    st.stop()

if not rows:
    st.caption("아직 비어 있어요. 첫 번째 마음을 남겨 주세요 🌷")
else:
    def entry_label(i):
        e = rows[i]
        who = "나" if e["author"] == auth["slot"] else auth["partner"]
        state = " ✓" if e["read_at"] else ""
        return f"{display_time(e['created_at'])} · {who} · {e['mood']}{state}"

    index = st.selectbox("최근 일기 100개", range(len(rows)), format_func=entry_label,
                         label_visibility="collapsed")
    entry = rows[index]
    # Plain-text rendering prevents HTML or Markdown injection from diary content.
    with st.container(height=110, border=True):
        st.text(entry["body"])
    if entry["author"] != auth["slot"]:
        read = st.checkbox("💗 읽었어요", value=bool(entry["read_at"]), disabled=bool(entry["read_at"]),
                           key="read_" + entry["id"])
        if read and not entry["read_at"]:
            try:
                with connection() as conn:
                    execute(conn, "UPDATE couple_entries SET read_at=%s WHERE id=%s AND room=%s AND author<>%s AND read_at IS NULL",
                            (now(), entry["id"], auth["room"], auth["slot"]))
                    conn.commit()
                st.rerun()
            except Exception:
                st.error("읽음 표시를 저장하지 못했어요. 새로고침 후 다시 시도해 주세요.")
    elif entry["read_at"]:
        st.caption(f"💗 {auth['partner']}님이 확인했어요 · {display_time(entry['read_at'])}")
    else:
        st.caption(f"✉️ {auth['partner']}님이 아직 확인하지 않았어요")
