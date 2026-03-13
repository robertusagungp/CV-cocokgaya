import os
import re
import json
import time
import hashlib
import uuid
from datetime import datetime
from contextlib import closing

import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor

try:
    import pdfplumber
except:
    pdfplumber = None

try:
    import docx
except:
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
# LANGUAGE
# =========================
if "lang" not in st.session_state:
    st.session_state.lang = "id"

def T(id_text, en_text):
    return id_text if st.session_state.lang == "id" else en_text


# =========================
# DATABASE
# =========================
def get_conn():
    return psycopg2.connect(st.secrets["DATABASE_URL"])


def init_db():
    with closing(get_conn()) as conn:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            subscription_status TEXT DEFAULT 'active',
            created_at TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS resumes (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            filename TEXT,
            resume_text TEXT,
            created_at TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            resume_id INTEGER,
            cv_score INTEGER,
            ats_score INTEGER,
            clarity_score INTEGER,
            impact_score INTEGER,
            strengths_json TEXT,
            weaknesses_json TEXT,
            recommendations_json TEXT,
            rewritten_summary TEXT,
            created_at TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS job_matches (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            resume_id INTEGER,
            target_job TEXT,
            location TEXT,
            level TEXT,
            match_score INTEGER,
            missing_skills_json TEXT,
            fit_reasons_json TEXT,
            action_plan_json TEXT,
            created_at TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS interview_sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            target_job TEXT,
            difficulty TEXT,
            questions_json TEXT,
            answers_json TEXT,
            feedback_json TEXT,
            created_at TIMESTAMP
        )
        """)

        # NEW TABLES FOR LOGGING
        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id UUID PRIMARY KEY,
            user_id INTEGER,
            session_start TIMESTAMP,
            last_activity TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_activity_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            session_id UUID,
            action_type TEXT,
            action_detail TEXT,
            created_at TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS login_logs (
            id SERIAL PRIMARY KEY,
            email TEXT,
            success BOOLEAN,
            created_at TIMESTAMP
        )
        """)

        conn.commit()

init_db()

# =========================
# SESSION STATE
# =========================
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "user_id" not in st.session_state:
    st.session_state.user_id = None

if "current_resume_text" not in st.session_state:
    st.session_state.current_resume_text = ""

if "current_resume_id" not in st.session_state:
    st.session_state.current_resume_id = None

if "interview_questions" not in st.session_state:
    st.session_state.interview_questions = []

if "interview_session_id" not in st.session_state:
    st.session_state.interview_session_id = None


# =========================
# LOGGING
# =========================
def log_activity(user_id, action, detail=""):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO user_activity_logs
        (user_id, session_id, action_type, action_detail, created_at)
        VALUES (%s,%s,%s,%s,%s)
        """, (
            user_id,
            st.session_state.session_id,
            action,
            detail,
            datetime.utcnow()
        ))
        conn.commit()


# =========================
# PASSWORD HASH
# =========================
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


# =========================
# AUTH
# =========================
def create_user(email, password, name):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO users (email,password_hash,full_name,created_at)
        VALUES (%s,%s,%s,%s)
        """, (
            email.lower(),
            hash_password(password),
            name,
            datetime.utcnow()
        ))
        conn.commit()


def login_user(email, password):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT id,email,full_name,subscription_status
        FROM users
        WHERE email=%s AND password_hash=%s
        """, (
            email.lower(),
            hash_password(password)
        ))
        row = cur.fetchone()

        cur.execute("""
        INSERT INTO login_logs (email,success,created_at)
        VALUES (%s,%s,%s)
        """, (email,bool(row),datetime.utcnow()))
        conn.commit()

        return row


def get_user_by_id(uid):
    with closing(get_conn()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id,email,full_name,subscription_status FROM users WHERE id=%s",(uid,))
        return cur.fetchone()


# =========================
# FILE TEXT EXTRACTION
# =========================
def extract_text_from_pdf(file):
    texts=[]
    with pdfplumber.open(file) as pdf:
        for p in pdf.pages:
            texts.append(p.extract_text() or "")
    return "\n".join(texts)

def extract_text_from_docx(file):
    document=docx.Document(file)
    return "\n".join([p.text for p in document.paragraphs])

def extract_resume_text(uploaded_file):
    name=uploaded_file.name.lower()
    if name.endswith(".pdf"):
        return extract_text_from_pdf(uploaded_file)
    if name.endswith(".docx"):
        return extract_text_from_docx(uploaded_file)
    if name.endswith(".txt"):
        return uploaded_file.read().decode()
    raise ValueError("Unsupported format")


# =========================
# GROQ
# =========================
def get_groq_api_key():
    if "GROQ_API_KEY" in st.secrets:
        return st.secrets["GROQ_API_KEY"]
    return os.getenv("GROQ_API_KEY")

client = Groq(api_key=get_groq_api_key())


def ask_groq_json(system_prompt,user_prompt):

    resp=client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        temperature=0.35,
        messages=[
            {"role":"system","content":system_prompt},
            {"role":"user","content":user_prompt}
        ],
        response_format={"type":"json_object"}
    )

    return json.loads(resp.choices[0].message.content)


# =========================
# SAVE RESUME
# =========================
def save_resume(user_id,filename,resume_text):
    with closing(get_conn()) as conn:
        cur=conn.cursor()
        cur.execute("""
        INSERT INTO resumes (user_id,filename,resume_text,created_at)
        VALUES (%s,%s,%s,%s)
        RETURNING id
        """,(
            user_id,
            filename,
            resume_text,
            datetime.utcnow()
        ))
        rid=cur.fetchone()[0]
        conn.commit()

    log_activity(user_id,"UPLOAD_RESUME",filename)

    return rid


# =========================
# UI
# =========================
st.title("🧠 AI Career Copilot")

# =========================
# LOGIN UI
# =========================
if st.session_state.user_id is None:

    tab1,tab2=st.tabs(["Login","Register"])

    with tab1:
        email=st.text_input("Email")
        pw=st.text_input("Password",type="password")

        if st.button("Login"):
            row=login_user(email,pw)

            if row:
                st.session_state.user_id=row[0]

                with closing(get_conn()) as conn:
                    cur=conn.cursor()
                    cur.execute("""
                    INSERT INTO user_sessions
                    (id,user_id,session_start,last_activity)
                    VALUES (%s,%s,%s,%s)
                    """,(
                        st.session_state.session_id,
                        row[0],
                        datetime.utcnow(),
                        datetime.utcnow()
                    ))
                    conn.commit()

                st.rerun()
            else:
                st.error("Invalid login")

    with tab2:
        name=st.text_input("Name")
        email=st.text_input("Email ")
        pw=st.text_input("Password ",type="password")

        if st.button("Register"):
            create_user(email,pw,name)
            st.success("Account created")

    st.stop()

# =========================
# USER INFO
# =========================
user=get_user_by_id(st.session_state.user_id)

with closing(get_conn()) as conn:
    cur=conn.cursor()
    cur.execute("""
    UPDATE user_sessions
    SET last_activity=%s
    WHERE id=%s
    """,(
        datetime.utcnow(),
        st.session_state.session_id
    ))
    conn.commit()

st.sidebar.write(user[2])
st.sidebar.write(user[1])

if st.sidebar.button("Logout"):
    st.session_state.user_id=None
    st.rerun()


# =========================
# MAIN APP
# =========================
tab1,tab2=st.tabs(["Resume Analyzer","History"])

with tab1:

    uploaded_file=st.file_uploader("Upload CV",type=["pdf","docx","txt"])

    if st.button("Analyze CV"):

        if uploaded_file is None:
            st.warning("Upload file first")
        else:

            text=extract_resume_text(uploaded_file)

            rid=save_resume(
                st.session_state.user_id,
                uploaded_file.name,
                text
            )

            log_activity(st.session_state.user_id,"AI_ANALYZE_RESUME")

            system_prompt="You are an expert resume reviewer. Return JSON."
            user_prompt=f"""
Return JSON with cv_score 0-100.

Resume:
{text[:12000]}
"""

            data=ask_groq_json(system_prompt,user_prompt)

            st.write(data)


with tab2:

    st.subheader("Activity Logs")

    with closing(get_conn()) as conn:
        cur=conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
        SELECT action_type,action_detail,created_at
        FROM user_activity_logs
        WHERE user_id=%s
        ORDER BY id DESC
        LIMIT 50
        """,(st.session_state.user_id,))

        rows=cur.fetchall()

    for r in rows:
        st.write(
            r["action_type"],
            r["action_detail"],
            r["created_at"]
        )


st.markdown("---")
st.caption("AI Career Copilot MVP")
