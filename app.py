import os
import re
import json
import time
import sqlite3
import hashlib
from datetime import datetime
from contextlib import closing

import streamlit as st

# Optional parsers
try:
    import pdfplumber
except Exception:
    pdfplumber = None

try:
    import docx
except Exception:
    docx = None

from groq import Groq


# =========================
# PAGE CONFIG
# =========================
st.set_page_config(
    page_title="AI Career Copilot - Groq",
    page_icon="🧠",
    layout="wide",
)

# =========================
# SIMPLE I18N
# =========================
if "lang" not in st.session_state:
    st.session_state.lang = "id"


def T(id_text: str, en_text: str) -> str:
    return id_text if st.session_state.lang == "id" else en_text


# =========================
# DB
# =========================
DB_PATH = "career_copilot.db"


def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    with closing(get_conn()) as conn:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            subscription_status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS resumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT,
            resume_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            resume_id INTEGER NOT NULL,
            cv_score INTEGER,
            ats_score INTEGER,
            clarity_score INTEGER,
            impact_score INTEGER,
            strengths_json TEXT,
            weaknesses_json TEXT,
            recommendations_json TEXT,
            rewritten_summary TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(resume_id) REFERENCES resumes(id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS job_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            resume_id INTEGER NOT NULL,
            target_job TEXT NOT NULL,
            location TEXT,
            level TEXT,
            match_score INTEGER,
            missing_skills_json TEXT,
            fit_reasons_json TEXT,
            action_plan_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(resume_id) REFERENCES resumes(id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS interview_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            target_job TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            questions_json TEXT NOT NULL,
            answers_json TEXT,
            feedback_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """)

        conn.commit()


init_db()

# =========================
# AUTH HELPERS
# =========================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def create_user(email: str, password: str, full_name: str):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (email, password_hash, full_name, subscription_status, created_at)
            VALUES (?, ?, ?, 'active', ?)
        """, (email.strip().lower(), hash_password(password), full_name.strip(), datetime.utcnow().isoformat()))
        conn.commit()


def login_user(email: str, password: str):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, email, full_name, subscription_status
            FROM users
            WHERE email = ? AND password_hash = ?
        """, (email.strip().lower(), hash_password(password)))
        row = cur.fetchone()
        return row


def get_user_by_id(user_id: int):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, email, full_name, subscription_status
            FROM users
            WHERE id = ?
        """, (user_id,))
        return cur.fetchone()


# =========================
# FILE TEXT EXTRACTION
# =========================
def extract_text_from_pdf(file) -> str:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber belum terinstall.")
    texts = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            texts.append(txt)
    return "\n".join(texts).strip()


def extract_text_from_docx(file) -> str:
    if docx is None:
        raise RuntimeError("python-docx belum terinstall.")
    document = docx.Document(file)
    return "\n".join([p.text for p in document.paragraphs]).strip()


def extract_resume_text(uploaded_file) -> str:
    name = uploaded_file.name.lower()
    if name.endswith(".pdf"):
        return extract_text_from_pdf(uploaded_file)
    elif name.endswith(".docx"):
        return extract_text_from_docx(uploaded_file)
    elif name.endswith(".txt"):
        return uploaded_file.read().decode("utf-8", errors="ignore")
    else:
        raise ValueError("Format file tidak didukung. Gunakan PDF, DOCX, atau TXT.")


# =========================
# GROQ SETTINGS
# =========================
st.sidebar.header(T("Pengaturan AI", "AI Settings"))

DEFAULT_MODEL_OLLAMA = "llama3"  # dipertahankan agar sesuai snippet user
DEFAULT_MODEL_GROQ = "llama-3.3-70b-versatile"

temperature = st.sidebar.slider(
    T("Kreativitas (temperature)", "Creativity (temperature)"),
    0.0, 1.0, 0.35, 0.05
)

# disimpan agar sesuai snippet user, tapi app ini Groq-only
use_ollama_first = st.sidebar.toggle(
    T("Coba Ollama dulu (lokal)", "Try Ollama first (local)"),
    value=False
)
ollama_model = st.sidebar.text_input(
    T("Ollama model", "Ollama model"),
    value=DEFAULT_MODEL_OLLAMA
)

groq_model = st.sidebar.text_input(
    T("Groq model", "Groq model"),
    value=DEFAULT_MODEL_GROQ
)
st.sidebar.caption(T(
    "App ini dibuat Groq-only. Toggle Ollama dipertahankan hanya agar struktur setting tetap mirip snippet kamu.",
    "This app is built Groq-only. The Ollama toggle is kept only so the settings layout stays close to your snippet."
))
st.sidebar.caption(T(
    "Cloud: butuh GROQ_API_KEY di environment variable atau Streamlit secrets.",
    "Cloud: requires GROQ_API_KEY in environment variables or Streamlit secrets."
))

lang_choice = st.sidebar.radio(
    "Language / Bahasa",
    ["id", "en"],
    index=0 if st.session_state.lang == "id" else 1,
    horizontal=True
)
st.session_state.lang = lang_choice

# =========================
# GROQ CLIENT
# =========================
def get_groq_api_key():
    if "GROQ_API_KEY" in st.secrets:
        return st.secrets["GROQ_API_KEY"]
    return os.getenv("GROQ_API_KEY")


GROQ_API_KEY = get_groq_api_key()
client = None
if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)


def parse_json_safely(text: str):
    """
    Tries hard to parse JSON even if model wraps it in ```json ... ```
    """
    if not text:
        raise ValueError("Empty model response.")

    text = text.strip()

    # remove markdown fences
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # try extracting first JSON object / array
    match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
    if match:
        return json.loads(match.group(1))

    raise ValueError("Failed to parse JSON from model output.")


def ask_groq_json(system_prompt: str, user_prompt: str):
    if client is None:
        raise RuntimeError("GROQ_API_KEY belum ada.")
    resp = client.chat.completions.create(
        model=groq_model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content
    return parse_json_safely(content)


def ask_groq_text(system_prompt: str, user_prompt: str):
    if client is None:
        raise RuntimeError("GROQ_API_KEY belum ada.")
    resp = client.chat.completions.create(
        model=groq_model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.choices[0].message.content.strip()


# =========================
# APP STATE
# =========================
if "user_id" not in st.session_state:
    st.session_state.user_id = None

if "current_resume_id" not in st.session_state:
    st.session_state.current_resume_id = None

if "current_resume_text" not in st.session_state:
    st.session_state.current_resume_text = ""

if "interview_questions" not in st.session_state:
    st.session_state.interview_questions = []

if "interview_session_id" not in st.session_state:
    st.session_state.interview_session_id = None


# =========================
# DB HELPERS FOR APP
# =========================
def save_resume(user_id: int, filename: str, resume_text: str) -> int:
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO resumes (user_id, filename, resume_text, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, filename, resume_text, datetime.utcnow().isoformat()))
        conn.commit()
        return cur.lastrowid


def save_analysis(user_id: int, resume_id: int, data: dict):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO analyses (
                user_id, resume_id,
                cv_score, ats_score, clarity_score, impact_score,
                strengths_json, weaknesses_json, recommendations_json,
                rewritten_summary, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            resume_id,
            int(data.get("cv_score", 0)),
            int(data.get("ats_score", 0)),
            int(data.get("clarity_score", 0)),
            int(data.get("impact_score", 0)),
            json.dumps(data.get("strengths", []), ensure_ascii=False),
            json.dumps(data.get("weaknesses", []), ensure_ascii=False),
            json.dumps(data.get("recommendations", []), ensure_ascii=False),
            data.get("rewritten_summary", ""),
            datetime.utcnow().isoformat()
        ))
        conn.commit()


def save_job_match(user_id: int, resume_id: int, target_job: str, location: str, level: str, data: dict):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO job_matches (
                user_id, resume_id, target_job, location, level,
                match_score, missing_skills_json, fit_reasons_json, action_plan_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            resume_id,
            target_job,
            location,
            level,
            int(data.get("match_score", 0)),
            json.dumps(data.get("missing_skills", []), ensure_ascii=False),
            json.dumps(data.get("fit_reasons", []), ensure_ascii=False),
            json.dumps(data.get("action_plan", []), ensure_ascii=False),
            datetime.utcnow().isoformat()
        ))
        conn.commit()


def save_interview_session(user_id: int, target_job: str, difficulty: str, questions: list) -> int:
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO interview_sessions (
                user_id, target_job, difficulty, questions_json, answers_json, feedback_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            target_job,
            difficulty,
            json.dumps(questions, ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            datetime.utcnow().isoformat()
        ))
        conn.commit()
        return cur.lastrowid


def update_interview_feedback(session_id: int, answers: list, feedback: list):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE interview_sessions
            SET answers_json = ?, feedback_json = ?
            WHERE id = ?
        """, (
            json.dumps(answers, ensure_ascii=False),
            json.dumps(feedback, ensure_ascii=False),
            session_id
        ))
        conn.commit()


def get_latest_analyses(user_id: int, limit: int = 10):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT a.id, r.filename, a.cv_score, a.ats_score, a.clarity_score, a.impact_score, a.created_at
            FROM analyses a
            JOIN resumes r ON a.resume_id = r.id
            WHERE a.user_id = ?
            ORDER BY a.id DESC
            LIMIT ?
        """, (user_id, limit))
        return cur.fetchall()


def get_latest_job_matches(user_id: int, limit: int = 10):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT target_job, match_score, created_at
            FROM job_matches
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (user_id, limit))
        return cur.fetchall()


def get_latest_interviews(user_id: int, limit: int = 10):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT target_job, difficulty, created_at
            FROM interview_sessions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (user_id, limit))
        return cur.fetchall()


# =========================
# AI PROMPTS
# =========================
def analyze_resume_ai(resume_text: str):
    system_prompt = """
You are an expert ATS resume reviewer and career coach.
Return STRICT JSON only.
Be concise, practical, and honest.
"""

    user_prompt = f"""
Analyze the following resume text.

Return JSON with this exact schema:
{{
  "cv_score": 0-100 integer,
  "ats_score": 0-100 integer,
  "clarity_score": 0-100 integer,
  "impact_score": 0-100 integer,
  "strengths": ["...", "..."],
  "weaknesses": ["...", "..."],
  "recommendations": ["...", "..."],
  "rewritten_summary": "2-4 sentence professional summary rewritten to be stronger"
}}

Resume:
\"\"\"
{resume_text[:15000]}
\"\"\"
"""
    return ask_groq_json(system_prompt, user_prompt)


def job_match_ai(resume_text: str, target_job: str, location: str, level: str):
    system_prompt = """
You are a senior recruiter and career strategist.
Return STRICT JSON only.
"""

    user_prompt = f"""
Based on the resume below, evaluate the candidate for this target role.

Target job: {target_job}
Preferred location: {location}
Experience level target: {level}

Return JSON with schema:
{{
  "match_score": 0-100 integer,
  "missing_skills": ["...", "..."],
  "fit_reasons": ["...", "..."],
  "action_plan": ["...", "..."]
}}

Resume:
\"\"\"
{resume_text[:15000]}
\"\"\"
"""
    return ask_groq_json(system_prompt, user_prompt)


def interview_questions_ai(target_job: str, difficulty: str):
    system_prompt = """
You are an experienced technical and behavioral interviewer.
Return STRICT JSON only.
"""

    user_prompt = f"""
Create 5 interview questions for this role.

Role: {target_job}
Difficulty: {difficulty}

Return JSON with schema:
{{
  "questions": [
    {{
      "question": "...",
      "type": "technical or behavioral",
      "what_good_answers_should_cover": ["...", "..."]
    }}
  ]
}}
"""
    result = ask_groq_json(system_prompt, user_prompt)
    return result.get("questions", [])


def interview_feedback_ai(target_job: str, questions: list, answers: list):
    system_prompt = """
You are an interview coach.
Return STRICT JSON only.
"""

    payload = []
    for i, q in enumerate(questions):
        payload.append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "ideal_points": q.get("what_good_answers_should_cover", []),
            "user_answer": answers[i] if i < len(answers) else ""
        })

    user_prompt = f"""
Evaluate the user's interview answers for role: {target_job}

Return JSON with schema:
{{
  "overall_score": 0-100 integer,
  "feedback": [
    {{
      "question": "...",
      "score": 0-100 integer,
      "strengths": ["...", "..."],
      "improvements": ["...", "..."],
      "sample_better_answer": "..."
    }}
  ],
  "overall_summary": "..."
}}

Data:
{json.dumps(payload, ensure_ascii=False)}
"""
    return ask_groq_json(system_prompt, user_prompt)


# =========================
# UI - HEADER
# =========================
st.title(T("🧠 AI Career Copilot", "🧠 AI Career Copilot"))
st.caption(T(
    "Analisis CV, job matching, dan simulasi interview — semua lewat Groq.",
    "Resume analysis, job matching, and interview simulation — all powered by Groq."
))

if client is None:
    st.error(T(
        "GROQ_API_KEY belum ditemukan. Tambahkan di environment variable atau Streamlit secrets dulu.",
        "GROQ_API_KEY was not found. Add it to environment variables or Streamlit secrets first."
    ))
    st.stop()

# =========================
# AUTH UI
# =========================
if st.session_state.user_id is None:
    tab_login, tab_register = st.tabs([
        T("Login", "Login"),
        T("Daftar", "Register")
    ])

    with tab_login:
        st.subheader(T("Masuk", "Sign In"))
        login_email = st.text_input(T("Email", "Email"), key="login_email")
        login_password = st.text_input(T("Password", "Password"), type="password", key="login_password")
        if st.button(T("Login", "Login"), use_container_width=True):
            try:
                row = login_user(login_email, login_password)
                if row:
                    st.session_state.user_id = row[0]
                    st.success(T("Login berhasil.", "Login successful."))
                    st.rerun()
                else:
                    st.error(T("Email atau password salah.", "Invalid email or password."))
            except Exception as e:
                st.error(f"Error: {e}")

    with tab_register:
        st.subheader(T("Buat Akun", "Create Account"))
        reg_name = st.text_input(T("Nama lengkap", "Full name"), key="reg_name")
        reg_email = st.text_input(T("Email", "Email"), key="reg_email")
        reg_password = st.text_input(T("Password", "Password"), type="password", key="reg_password")
        reg_password2 = st.text_input(T("Ulangi password", "Repeat password"), type="password", key="reg_password2")
        if st.button(T("Daftar", "Register"), use_container_width=True):
            try:
                if not reg_name.strip():
                    st.error(T("Nama wajib diisi.", "Name is required."))
                elif reg_password != reg_password2:
                    st.error(T("Password tidak sama.", "Passwords do not match."))
                elif len(reg_password) < 6:
                    st.error(T("Password minimal 6 karakter.", "Password must be at least 6 characters."))
                else:
                    create_user(reg_email, reg_password, reg_name)
                    st.success(T("Akun berhasil dibuat. Silakan login.", "Account created successfully. Please sign in."))
            except sqlite3.IntegrityError:
                st.error(T("Email sudah terdaftar.", "Email is already registered."))
            except Exception as e:
                st.error(f"Error: {e}")

    st.stop()

# =========================
# LOGGED IN
# =========================
user = get_user_by_id(st.session_state.user_id)
user_id, user_email, user_name, user_sub = user

with st.sidebar:
    st.markdown("---")
    st.write(f"**{user_name}**")
    st.caption(f"{user_email} | {user_sub}")
    if st.button(T("Logout", "Logout"), use_container_width=True):
        st.session_state.user_id = None
        st.session_state.current_resume_id = None
        st.session_state.current_resume_text = ""
        st.session_state.interview_questions = []
        st.session_state.interview_session_id = None
        st.rerun()

# =========================
# MAIN TABS
# =========================
tab1, tab2, tab3, tab4 = st.tabs([
    T("Resume Analyzer", "Resume Analyzer"),
    T("Job Match", "Job Match"),
    T("Interview Simulator", "Interview Simulator"),
    T("History", "History")
])

# -------------------------
# TAB 1 - RESUME ANALYZER
# -------------------------
with tab1:
    st.subheader(T("Upload CV", "Upload Resume"))
    uploaded_file = st.file_uploader(
        T("Pilih file CV (PDF/DOCX/TXT)", "Choose resume file (PDF/DOCX/TXT)"),
        type=["pdf", "docx", "txt"]
    )

    col_a, col_b = st.columns([1, 1])

    with col_a:
        if st.button(T("Simpan & Analisis CV", "Save & Analyze Resume"), use_container_width=True):
            try:
                if uploaded_file is None:
                    st.warning(T("Upload file dulu.", "Please upload a file first."))
                else:
                    with st.spinner(T("Mengekstrak teks CV...", "Extracting resume text...")):
                        resume_text = extract_resume_text(uploaded_file)

                    if not resume_text.strip():
                        st.error(T("Teks CV kosong atau gagal diekstrak.", "Resume text is empty or extraction failed."))
                    else:
                        with st.spinner(T("Menyimpan CV...", "Saving resume...")):
                            resume_id = save_resume(user_id, uploaded_file.name, resume_text)
                            st.session_state.current_resume_id = resume_id
                            st.session_state.current_resume_text = resume_text

                        with st.spinner(T("AI sedang menganalisis CV...", "AI is analyzing your resume...")):
                            data = analyze_resume_ai(resume_text)
                            save_analysis(user_id, resume_id, data)

                        st.success(T("CV berhasil dianalisis.", "Resume analyzed successfully."))
                        st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

    with col_b:
        if st.button(T("Gunakan resume terakhir", "Use latest resume"), use_container_width=True):
            with closing(get_conn()) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, resume_text
                    FROM resumes
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                """, (user_id,))
                row = cur.fetchone()
                if row:
                    st.session_state.current_resume_id = row[0]
                    st.session_state.current_resume_text = row[1]
                    st.success(T("Resume terakhir dipakai.", "Latest resume loaded."))
                else:
                    st.warning(T("Belum ada resume tersimpan.", "No saved resume found."))

    st.markdown("---")
    st.subheader(T("Hasil Analisis Terakhir", "Latest Analysis"))

    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT cv_score, ats_score, clarity_score, impact_score,
                   strengths_json, weaknesses_json, recommendations_json,
                   rewritten_summary, created_at
            FROM analyses
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,))
        row = cur.fetchone()

    if row:
        cv_score, ats_score, clarity_score, impact_score, strengths_json, weaknesses_json, recommendations_json, rewritten_summary, created_at = row

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("CV Score", cv_score)
        c2.metric("ATS", ats_score)
        c3.metric(T("Kejelasan", "Clarity"), clarity_score)
        c4.metric(T("Impact", "Impact"), impact_score)

        st.write(f"**{T('Waktu analisis', 'Analyzed at')}:** {created_at}")

        left, right = st.columns(2)
        with left:
            st.markdown(f"### {T('Kelebihan', 'Strengths')}")
            for item in json.loads(strengths_json or "[]"):
                st.write(f"- {item}")

            st.markdown(f"### {T('Kelemahan', 'Weaknesses')}")
            for item in json.loads(weaknesses_json or "[]"):
                st.write(f"- {item}")

        with right:
            st.markdown(f"### {T('Rekomendasi', 'Recommendations')}")
            for item in json.loads(recommendations_json or "[]"):
                st.write(f"- {item}")

            st.markdown(f"### {T('Ringkasan CV yang diperbaiki', 'Improved Resume Summary')}")
            st.info(rewritten_summary)
    else:
        st.info(T("Belum ada analisis CV.", "No resume analysis yet."))

# -------------------------
# TAB 2 - JOB MATCH
# -------------------------
with tab2:
    st.subheader(T("Job Matching", "Job Matching"))
    target_job = st.text_input(T("Posisi target", "Target role"), value="Data Analyst")
    location = st.text_input(T("Lokasi target", "Preferred location"), value="Jakarta")
    level = st.selectbox(T("Level", "Level"), ["Intern", "Entry Level", "Mid Level", "Senior"])

    if st.button(T("Analisis Kecocokan", "Analyze Match"), use_container_width=True):
        try:
            resume_text = st.session_state.current_resume_text
            resume_id = st.session_state.current_resume_id

            if not resume_text or not resume_id:
                with closing(get_conn()) as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT id, resume_text
                        FROM resumes
                        WHERE user_id = ?
                        ORDER BY id DESC
                        LIMIT 1
                    """, (user_id,))
                    row = cur.fetchone()
                    if row:
                        resume_id, resume_text = row
                        st.session_state.current_resume_id = resume_id
                        st.session_state.current_resume_text = resume_text

            if not resume_text or not resume_id:
                st.warning(T("Upload atau pilih resume dulu.", "Please upload or select a resume first."))
            else:
                with st.spinner(T("AI sedang menilai kecocokan job...", "AI is evaluating job fit...")):
                    data = job_match_ai(resume_text, target_job, location, level)
                    save_job_match(user_id, resume_id, target_job, location, level, data)

                st.success(T("Job match selesai.", "Job match completed."))
                st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")

    st.markdown("---")
    st.subheader(T("Hasil Job Match Terakhir", "Latest Job Match"))
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT target_job, location, level, match_score, missing_skills_json, fit_reasons_json, action_plan_json, created_at
            FROM job_matches
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,))
        row = cur.fetchone()

    if row:
        target_job_db, location_db, level_db, match_score, missing_skills_json, fit_reasons_json, action_plan_json, created_at = row
        st.metric(T("Match Score", "Match Score"), match_score)
        st.write(f"**{T('Posisi', 'Role')}:** {target_job_db}")
        st.write(f"**{T('Lokasi', 'Location')}:** {location_db}")
        st.write(f"**{T('Level', 'Level')}:** {level_db}")
        st.write(f"**{T('Waktu', 'Time')}:** {created_at}")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f"### {T('Alasan cocok', 'Fit Reasons')}")
            for x in json.loads(fit_reasons_json or "[]"):
                st.write(f"- {x}")

        with col2:
            st.markdown(f"### {T('Skill yang kurang', 'Missing Skills')}")
            for x in json.loads(missing_skills_json or "[]"):
                st.write(f"- {x}")

        with col3:
            st.markdown(f"### {T('Action Plan', 'Action Plan')}")
            for x in json.loads(action_plan_json or "[]"):
                st.write(f"- {x}")
    else:
        st.info(T("Belum ada hasil job match.", "No job match results yet."))

# -------------------------
# TAB 3 - INTERVIEW
# -------------------------
with tab3:
    st.subheader(T("Interview Simulator", "Interview Simulator"))

    interview_role = st.text_input(T("Posisi interview", "Interview role"), value="Data Analyst", key="interview_role")
    interview_difficulty = st.selectbox(
        T("Tingkat kesulitan", "Difficulty"),
        ["easy", "medium", "hard"],
        index=1
    )

    if st.button(T("Generate Pertanyaan Interview", "Generate Interview Questions"), use_container_width=True):
        try:
            with st.spinner(T("AI sedang membuat pertanyaan interview...", "AI is generating interview questions...")):
                questions = interview_questions_ai(interview_role, interview_difficulty)
                if not questions:
                    st.error(T("AI tidak mengembalikan pertanyaan.", "AI did not return questions."))
                else:
                    st.session_state.interview_questions = questions
                    session_id = save_interview_session(user_id, interview_role, interview_difficulty, questions)
                    st.session_state.interview_session_id = session_id
                    st.success(T("Pertanyaan interview berhasil dibuat.", "Interview questions generated successfully."))
        except Exception as e:
            st.error(f"Error: {e}")

    if st.session_state.interview_questions:
        st.markdown("---")
        st.subheader(T("Jawab Pertanyaan", "Answer the Questions"))
        answer_list = []

        for i, q in enumerate(st.session_state.interview_questions):
            st.markdown(f"### {T('Pertanyaan', 'Question')} {i+1}")
            st.write(q.get("question", ""))

            q_type = q.get("type", "")
            if q_type:
                st.caption(f"{T('Tipe', 'Type')}: {q_type}")

            ans = st.text_area(
                T("Jawaban kamu", "Your answer"),
                key=f"ans_{i}",
                height=140
            )
            answer_list.append(ans)

        if st.button(T("Nilai Jawaban Interview", "Evaluate Interview Answers"), use_container_width=True):
            try:
                with st.spinner(T("AI sedang menilai jawaban...", "AI is evaluating your answers...")):
                    result = interview_feedback_ai(interview_role, st.session_state.interview_questions, answer_list)

                overall_score = int(result.get("overall_score", 0))
                overall_summary = result.get("overall_summary", "")
                feedback = result.get("feedback", [])

                if st.session_state.interview_session_id:
                    update_interview_feedback(st.session_state.interview_session_id, answer_list, feedback)

                st.success(T("Penilaian interview selesai.", "Interview evaluation completed."))
                st.metric(T("Skor Interview", "Interview Score"), overall_score)
                st.info(overall_summary)

                for i, fb in enumerate(feedback):
                    with st.expander(f"{T('Feedback Soal', 'Question Feedback')} {i+1}", expanded=(i == 0)):
                        st.write(f"**{T('Pertanyaan', 'Question')}:** {fb.get('question', '')}")
                        st.write(f"**{T('Skor', 'Score')}:** {fb.get('score', 0)}")

                        st.markdown(f"**{T('Kekuatan', 'Strengths')}**")
                        for s in fb.get("strengths", []):
                            st.write(f"- {s}")

                        st.markdown(f"**{T('Perbaikan', 'Improvements')}**")
                        for s in fb.get("improvements", []):
                            st.write(f"- {s}")

                        st.markdown(f"**{T('Contoh jawaban yang lebih baik', 'Sample Better Answer')}**")
                        st.write(fb.get("sample_better_answer", ""))
            except Exception as e:
                st.error(f"Error: {e}")

# -------------------------
# TAB 4 - HISTORY
# -------------------------
with tab4:
    st.subheader(T("Riwayat", "History"))

    st.markdown(f"### {T('Riwayat Analisis CV', 'Resume Analysis History')}")
    analyses = get_latest_analyses(user_id, limit=10)
    if analyses:
        for row in analyses:
            _, filename, cv_score, ats_score, clarity_score, impact_score, created_at = row
            st.write(
                f"- **{filename}** | CV {cv_score} | ATS {ats_score} | "
                f"{T('Kejelasan', 'Clarity')} {clarity_score} | Impact {impact_score} | {created_at}"
            )
    else:
        st.write(T("Belum ada.", "None yet."))

    st.markdown(f"### {T('Riwayat Job Match', 'Job Match History')}")
    matches = get_latest_job_matches(user_id, limit=10)
    if matches:
        for target_job_db, match_score, created_at in matches:
            st.write(f"- **{target_job_db}** | Match {match_score} | {created_at}")
    else:
        st.write(T("Belum ada.", "None yet."))

    st.markdown(f"### {T('Riwayat Interview', 'Interview History')}")
    interviews = get_latest_interviews(user_id, limit=10)
    if interviews:
        for target_job_db, difficulty_db, created_at in interviews:
            st.write(f"- **{target_job_db}** | {difficulty_db} | {created_at}")
    else:
        st.write(T("Belum ada.", "None yet."))

# =========================
# FOOTER
# =========================
st.markdown("---")
st.caption(T(
    "Catatan: versi ini adalah MVP. Belum ada payment gateway, parsing DOC lama, atau crawling lowongan otomatis.",
    "Note: this is an MVP. No payment gateway, legacy DOC parsing, or automatic job crawling yet."
))
