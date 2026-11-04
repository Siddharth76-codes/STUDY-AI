import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
default_database_path = Path("/tmp/studyai.db") if os.environ.get("VERCEL") else BASE_DIR / "studyai.db"
DATABASE_PATH = Path(os.environ.get("STUDYAI_DATABASE", default_database_path))
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET") or ""
if not app.secret_key:
    secret_path = BASE_DIR / ".session_secret"
    try:
        secret_fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        app.secret_key = secret_path.read_text(encoding="utf-8").strip()
    else:
        app.secret_key = secrets.token_hex(32)
        with os.fdopen(secret_fd, "w", encoding="utf-8") as secret_file:
            secret_file.write(app.secret_key)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    SESSION_COOKIE_NAME="studyai_session",
    SESSION_REFRESH_EACH_REQUEST=False,
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024 + 64 * 1024,
    MAX_FORM_MEMORY_SIZE=64 * 1024,
    MAX_FORM_PARTS=10,
    PREFERRED_URL_SCHEME="https" if os.environ.get("COOKIE_SECURE") == "1" else "http",
)
DUMMY_PASSWORD_HASH = generate_password_hash(
    secrets.token_urlsafe(32),
    method="pbkdf2:sha256:600000",
)

CLASS_OPTIONS = [f"Class {grade}" for grade in range(1, 13)] + ["Other"]
BOARD_OPTIONS = ["CBSE", "CISCE (ICSE/ISC)", "State Board", "IB", "Cambridge", "Other"]
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_PAIR_ATTEMPT_LIMIT = 5
LOGIN_IP_ATTEMPT_LIMIT = 30
LOGIN_BLOCK_SECONDS = 15 * 60
PLAN_LIMITS = {
    "FREE": {"tutor": 15, "practice": 5},
    "GO": {"tutor": 50, "practice": 25},
    "PRO": {"tutor": None, "practice": None},
}
INDIA_TIMEZONE = ZoneInfo("Asia/Kolkata")


def get_db():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_progress (
                topic TEXT PRIMARY KEY COLLATE NOCASE,
                questions_asked INTEGER NOT NULL DEFAULT 0,
                practice_total INTEGER NOT NULL DEFAULT 0,
                correct_total INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS practice_sets (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                questions_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                school_class TEXT NOT NULL,
                board TEXT NOT NULL,
                plan TEXT NOT NULL DEFAULT 'FREE',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        user_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)")
        }
        if "plan" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'FREE'"
            )
        connection.execute("DROP TABLE IF EXISTS pending_email_verifications")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_topic_progress (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                topic TEXT NOT NULL COLLATE NOCASE,
                questions_asked INTEGER NOT NULL DEFAULT 0,
                practice_total INTEGER NOT NULL DEFAULT 0,
                correct_total INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, topic)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_practice_sets (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                topic TEXT NOT NULL,
                questions_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_notes (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                topic TEXT NOT NULL,
                summary TEXT NOT NULL,
                key_points_json TEXT NOT NULL,
                key_terms_json TEXT NOT NULL,
                remember TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                usage_date TEXT NOT NULL,
                category TEXT NOT NULL CHECK (category IN ('tutor', 'practice')),
                used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                PRIMARY KEY (user_id, usage_date, category)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS login_attempts (
                key_hash TEXT PRIMARY KEY,
                window_started INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                blocked_until INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def india_today():
    return datetime.now(INDIA_TIMEZONE).date().isoformat()


def usage_limit(plan, category):
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["FREE"])[category]


def usage_count(connection, user_id, category, usage_date=None):
    row = connection.execute(
        """
        SELECT used FROM daily_usage
        WHERE user_id = ? AND usage_date = ? AND category = ?
        """,
        (user_id, usage_date or india_today(), category),
    ).fetchone()
    return row["used"] if row else 0


def subscription_status(user_id, plan):
    plan = plan if plan in PLAN_LIMITS else "FREE"
    with get_db() as connection:
        status = {}
        for category in ("tutor", "practice"):
            limit = usage_limit(plan, category)
            used = usage_count(connection, user_id, category)
            status[category] = {
                "used": used,
                "limit": limit,
                "remaining": None if limit is None else max(0, limit - used),
            }
    return {"plan": plan, "date": india_today(), "usage": status}


def consume_daily_usage(connection, user_id, plan, category, amount):
    limit = usage_limit(plan, category)
    today = india_today()
    if limit is not None:
        cursor = connection.execute(
            """
            INSERT INTO daily_usage (user_id, usage_date, category, used)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, usage_date, category) DO UPDATE SET
                used = daily_usage.used + excluded.used
            WHERE daily_usage.used + excluded.used <= ?
            """,
            (user_id, today, category, amount, limit),
        )
        return cursor.rowcount > 0
    connection.execute(
        """
        INSERT INTO daily_usage (user_id, usage_date, category, used)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, usage_date, category) DO UPDATE SET
            used = daily_usage.used + excluded.used
        """,
        (user_id, today, category, amount),
    )
    return True


def record_topic(connection, user_id, topic, *, asked=0, practiced=0, correct=0):
    connection.execute(
        """
        INSERT INTO user_topic_progress
            (user_id, topic, questions_asked, practice_total, correct_total)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, topic) DO UPDATE SET
            questions_asked = questions_asked + excluded.questions_asked,
            practice_total = practice_total + excluded.practice_total,
            correct_total = correct_total + excluded.correct_total,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, topic, asked, practiced, correct),
    )


def student_context():
    user = g.user
    return (
        f"The student is in {user['school_class']} and follows the {user['board']} board. "
        "Adapt explanations, vocabulary, examples, and practice difficulty to this "
        "school level and curriculum context. Keep concepts accurate; do not claim "
        "that a topic is on their exact syllabus unless that is known. This is "
        "prompt-based personalization, not model fine-tuning."
    )


def login_attempt_keys(email, client_ip):
    secret = app.secret_key.encode("utf-8")
    values = (f"ip:{client_ip}", f"pair:{client_ip}\0{email}")
    return tuple(
        hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
        for value in values
    )


def login_is_limited(connection, keys, now):
    placeholders = ",".join("?" for _ in keys)
    rows = connection.execute(
        f"SELECT blocked_until FROM login_attempts WHERE key_hash IN ({placeholders})",
        keys,
    ).fetchall()
    return any(row["blocked_until"] > now for row in rows)


def record_login_failure(connection, keys, now):
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "DELETE FROM login_attempts WHERE window_started < ?",
        (now - 24 * 60 * 60,),
    )
    for index, key in enumerate(keys):
        row = connection.execute(
            "SELECT window_started, attempts FROM login_attempts WHERE key_hash = ?",
            (key,),
        ).fetchone()
        limit = LOGIN_IP_ATTEMPT_LIMIT if index == 0 else LOGIN_PAIR_ATTEMPT_LIMIT
        if row is None or row["window_started"] <= now - LOGIN_WINDOW_SECONDS:
            window_started = now
            attempts = 1
        else:
            window_started = row["window_started"]
            attempts = row["attempts"] + 1
        blocked_until = now + LOGIN_BLOCK_SECONDS if attempts >= limit else 0
        connection.execute(
            """
            INSERT INTO login_attempts (key_hash, window_started, attempts, blocked_until)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key_hash) DO UPDATE SET
                window_started = excluded.window_started,
                attempts = excluded.attempts,
                blocked_until = excluded.blocked_until
            """,
            (key, window_started, attempts, blocked_until),
        )


def clear_login_failures(connection, keys):
    connection.execute(
        "DELETE FROM login_attempts WHERE key_hash = ?",
        (keys[1],),
    )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify(error="Please sign in to continue."), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def load_user_and_check_csrf():
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        with get_db() as connection:
            g.user = connection.execute(
                "SELECT id, email, school_class, board, plan FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if g.user is None:
            session.clear()

    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not (
        request.path.startswith("/api/") and g.user is None
    ):
        submitted = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("_csrf_token", "")
        if not expected or not secrets.compare_digest(submitted, expected):
            if request.is_json:
                return jsonify(error="Your session expired. Refresh the page and try again."), 400
            abort(400)


@app.after_request
def add_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-XSS-Protection"] = "0"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "font-src 'self' https://fonts.gstatic.com https://fonts.googleapis.com; "
        "script-src 'self'; "
        "connect-src 'self' http://127.0.0.1:11434 https://api.groq.com https://api.openai.com; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    if request.is_secure or os.environ.get("COOKIE_SECURE") == "1":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.errorhandler(413)
def request_too_large(error):
    if request.path.startswith("/api/"):
        return jsonify(error="That upload is too large. Choose an image under 5 MB."), 413
    return error


@app.context_processor
def inject_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return {"csrf_token": session["_csrf_token"]}


def get_ai_provider():
    # Keep every AI feature on the local model so pages behave consistently.
    return "ollama"


def is_ai_configured():
    provider = get_ai_provider()
    if provider == "ollama":
        return True
    if provider == "aimlapi":
        return bool(os.environ.get("AIMLAPI_API_KEY"))
    if provider == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY"))
    if provider == "groq":
        return bool(os.environ.get("GROQ_API_KEY"))
    if provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    return False


def call_ai(system_prompt, user_prompt, *, image=None):
    provider = get_ai_provider()
    if provider not in ("ollama", "aimlapi", "gemini", "groq", "openai"):
        raise RuntimeError(
            "Unknown AI provider. Set STUDYAI_PROVIDER to ollama, aimlapi, gemini, groq, or openai."
        )
    if provider == "aimlapi":
        api_key = os.environ.get("AIMLAPI_API_KEY")
    elif provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
    elif provider == "groq":
        api_key = os.environ.get("GROQ_API_KEY")
    elif provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
    else:
        api_key = None
    if provider != "ollama" and not api_key:
        raise RuntimeError(
            f"Add the API key for {provider.title()} to your .env file, "
            "or set STUDYAI_PROVIDER=ollama to use a local model."
        )

    model_variable = {
        "ollama": "OLLAMA_MODEL",
        "aimlapi": "AIMLAPI_MODEL",
        "gemini": "GEMINI_MODEL",
        "groq": "GROQ_MODEL",
        "openai": "OPENAI_MODEL",
    }[provider]
    default_model = {
        "ollama": "qwen2.5:3b",
        "aimlapi": "z-ai/glm-5-turbo",
        "gemini": "gemini-3.8-flash",
        "groq": "llama-3.3-70b-versatile",
        "openai": "gpt-4o-mini",
    }[provider]
    if image:
        model_variable = {
            "ollama": "OLLAMA_VISION_MODEL",
            "aimlapi": "AIMLAPI_VISION_MODEL",
            "gemini": "GEMINI_VISION_MODEL",
            "groq": "GROQ_VISION_MODEL",
            "openai": "OPENAI_VISION_MODEL",
        }[provider]
        default_model = {
            "ollama": "llava:7b",
            "aimlapi": "z-ai/glm-5-turbo",
            "gemini": "gemini-3.8-flash",
            "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
            "openai": "gpt-4o-mini",
        }[provider]
    user_content = user_prompt
    if image:
        mime_type, image_bytes = image
        image_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        user_content = [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
    payload = {
        "model": os.environ.get(model_variable, default_model),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.6,
    }
    if provider == "ollama" and "valid json" in system_prompt.lower():
        payload["response_format"] = {"type": "json_object"}
        payload["temperature"] = 0.2
    base_url = os.environ.get(
        {
            "ollama": "OLLAMA_BASE_URL",
            "aimlapi": "AIMLAPI_BASE_URL",
            "gemini": "GEMINI_BASE_URL",
            "groq": "GROQ_BASE_URL",
            "openai": "OPENAI_BASE_URL",
        }[provider]
    )
    if not base_url:
        base_url = {
            "ollama": "http://127.0.0.1:11434/v1",
            "aimlapi": "https://api.aimlapi.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
            "groq": "https://api.groq.com/openai/v1",
            "openai": "https://api.openai.com/v1",
        }[provider]
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120 if provider == "ollama" else 90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if provider == "ollama" and error.code == 404:
            raise RuntimeError(
                f"Ollama can't find model {payload['model']}. Run "
                f"`ollama pull {payload['model']}` in Terminal, then try again."
            ) from error
        if error.code in (401, 403):
            raise RuntimeError(
                f"The {provider.title()} API key was not accepted. Check it in your .env file."
            ) from error
        raise RuntimeError(f"The AI service returned an error ({error.code}). Try again.") from error
    except socket.timeout as error:
        raise RuntimeError(
            "The AI service took too long to answer. Try a shorter question, or try again in a moment."
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        if provider == "ollama":
            raise RuntimeError(
                "StudyAI can't connect to Ollama. Install and start the Ollama app, "
                f"then download the model with `ollama pull {payload['model']}`."
            ) from error
        raise RuntimeError("Could not reach the AI service. Check your connection and try again.") from error

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("The AI service returned an unexpected response. Try again.") from error


def decode_ai_json(content):
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as error:
        decoder = json.JSONDecoder()
        for index, character in enumerate(cleaned):
            if character == "{":
                try:
                    result, _ = decoder.raw_decode(cleaned[index:])
                    return result
                except json.JSONDecodeError:
                    continue
        raise RuntimeError("The AI response could not be read. Please try again.") from error


@app.get("/")
@login_required
def home():
    return redirect(url_for("study_room"))


@app.get("/study-room")
@login_required
def study_room():
    return render_template(
        "index.html",
        ai_configured=is_ai_configured(),
        ai_provider=get_ai_provider(),
        user=g.user,
        subscription=subscription_status(g.user["id"], g.user["plan"]),
    )


@app.get("/notes")
@login_required
def notes_page():
    return render_template(
        "notes.html",
        ai_configured=is_ai_configured(),
        ai_provider=get_ai_provider(),
        user=g.user,
        subscription=subscription_status(g.user["id"], g.user["plan"]),
    )


@app.get("/progress")
@login_required
def progress_page():
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT topic, questions_asked, practice_total, correct_total, updated_at
            FROM user_topic_progress
            WHERE user_id = ?
            ORDER BY updated_at DESC, topic COLLATE NOCASE
            """,
            (g.user["id"],),
        ).fetchall()
    topics = []
    for row in rows:
        accuracy = round(row["correct_total"] / row["practice_total"] * 100) if row["practice_total"] else None
        topics.append(
            {
                "topic": row["topic"],
                "questions_asked": row["questions_asked"],
                "practice_total": row["practice_total"],
                "correct_total": row["correct_total"],
                "accuracy": accuracy,
                "weak": row["practice_total"] >= 3 and accuracy is not None and accuracy < 60,
                "updated_at": row["updated_at"],
            }
        )
    practice_total = sum(topic["practice_total"] for topic in topics)
    correct_total = sum(topic["correct_total"] for topic in topics)
    return render_template(
        "progress.html",
        ai_configured=is_ai_configured(),
        ai_provider=get_ai_provider(),
        user=g.user,
        subscription=subscription_status(g.user["id"], g.user["plan"]),
        topics=topics,
        topics_studied=len(topics),
        questions_asked=sum(topic["questions_asked"] for topic in topics),
        practice_total=practice_total,
        accuracy=round(correct_total / practice_total * 100) if practice_total else 0,
    )


@app.get("/plans")
@login_required
def plans_page():
    return render_template(
        "plans.html",
        ai_configured=is_ai_configured(),
        ai_provider=get_ai_provider(),
        user=g.user,
        subscription=subscription_status(g.user["id"], g.user["plan"]),
    )


@app.post("/api/plan/activate")
@login_required
def activate_plan():
    data = request.get_json(silent=True) or {}
    plan = str(data.get("plan", "")).strip().upper()
    if plan not in PLAN_LIMITS:
        return jsonify(error="Choose a valid StudyAI plan."), 400
    if plan != "FREE":
        return jsonify(
            error="Paid plan activation is unavailable until a payment provider can verify the transaction."
        ), 503

    with get_db() as connection:
        connection.execute(
            "UPDATE users SET plan = ? WHERE id = ?",
            (plan, g.user["id"]),
        )
    g.user = dict(g.user)
    g.user["plan"] = plan
    subscription = subscription_status(g.user["id"], g.user["plan"])
    return jsonify(plan=plan, subscription=subscription)

@app.post("/api/coupon/redeem")
@login_required
def redeem_coupon():
    data = request.get_json(silent=True) or {}
    code = str(data.get("code", "")).strip()
    if code.upper() != "DEV-PREMIUM":
        return jsonify(error="Invalid coupon code."), 400
    with get_db() as connection:
        already = connection.execute(
            "SELECT 1 FROM redeemed_coupons WHERE code = ?", (code,)
        ).fetchone()
        if already:
            return jsonify(error="This coupon has already been used."), 400
        connection.execute(
            "INSERT INTO redeemed_coupons (user_id, code) VALUES (?, ?)",
            (g.user["id"], code),
        )
        connection.execute(
            "UPDATE users SET plan = 'PRO' WHERE id = ?", (g.user["id"],)
        )
    g.user = dict(g.user)
    g.user["plan"] = "PRO"
    subscription = subscription_status(g.user["id"], g.user["plan"])
    return jsonify(plan="PRO", subscription=subscription)


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user is not None:
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        school_class = request.form.get("school_class", "").strip()
        board = request.form.get("board", "").strip()

        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
            error = "Enter a valid email address."
        elif len(password) < 12 or len(password) > 256:
            error = "Choose a password between 12 and 256 characters."
        elif password != password_confirm:
            error = "Those passwords don't match. Try again."
        elif school_class not in CLASS_OPTIONS:
            error = "Choose your class to personalize your study space."
        elif board not in BOARD_OPTIONS:
            error = "Choose your school board to personalize your study space."
        else:
            try:
                with get_db() as connection:
                    cursor = connection.execute(
                        """
                        INSERT INTO users (email, password_hash, school_class, board)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            email,
                            generate_password_hash(password, method="pbkdf2:sha256:600000"),
                            school_class,
                            board,
                        ),
                    )
                    user_id = cursor.lastrowid
                    if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1:
                        connection.execute(
                            """
                            INSERT INTO user_topic_progress
                                (user_id, topic, questions_asked, practice_total, correct_total, updated_at)
                            SELECT ?, topic, questions_asked, practice_total, correct_total, updated_at
                            FROM topic_progress
                            """,
                            (user_id,),
                        )
            except sqlite3.IntegrityError:
                error = "An account with that email already exists. Sign in instead."
            else:
                session.clear()
                session["user_id"] = user_id
                session.permanent = True
                return redirect(url_for("home"))
    return render_template(
        "auth.html",
        register=True,
        error=error,
        class_options=CLASS_OPTIONS,
        board_options=BOARD_OPTIONS,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("home"))
    error = None
    email = request.form.get("email", "").strip().lower()
    if request.method == "POST":
        password = request.form.get("password", "")
        now = int(time.time())
        attempt_keys = login_attempt_keys(email[:254], request.remote_addr or "unknown")
        with get_db() as connection:
            connection.execute(
                "DELETE FROM login_attempts WHERE window_started < ?",
                (now - 24 * 60 * 60,),
            )
            if login_is_limited(connection, attempt_keys, now):
                return render_template(
                    "auth.html",
                    register=False,
                    error="Too many sign-in attempts. Wait 15 minutes, then try again.",
                ), 429
            user = None
            if len(email) <= 254:
                user = connection.execute(
                    "SELECT id, email, password_hash FROM users WHERE email = ?",
                    (email,),
                ).fetchone()
        password_hash = user["password_hash"] if user else DUMMY_PASSWORD_HASH
        password_matches = (
            len(password) <= 256 and check_password_hash(password_hash, password)
        )
        if user is None or not password_matches:
            with get_db() as connection:
                record_login_failure(connection, attempt_keys, now)
            error = "Email or password didn't match. Please try again."
        else:
            with get_db() as connection:
                clear_login_failures(connection, attempt_keys)
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            return redirect(url_for("home"))
    return render_template("auth.html", register=False, error=error)


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/dashboard")
@login_required
def dashboard():
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT topic, questions_asked, practice_total, correct_total, updated_at
            FROM user_topic_progress
            WHERE user_id = ?
            ORDER BY updated_at DESC, topic COLLATE NOCASE
            """,
            (g.user["id"],),
        ).fetchall()
    topics = []
    for row in rows:
        accuracy = round(row["correct_total"] / row["practice_total"] * 100) if row["practice_total"] else None
        topics.append(
            {
                "topic": row["topic"],
                "questions_asked": row["questions_asked"],
                "practice_total": row["practice_total"],
                "correct_total": row["correct_total"],
                "accuracy": accuracy,
                "weak": row["practice_total"] >= 3 and accuracy < 60,
                "updated_at": row["updated_at"],
            }
        )
    practice_total = sum(topic["practice_total"] for topic in topics)
    correct_total = sum(topic["correct_total"] for topic in topics)
    return jsonify(
        {
            "topics": topics,
            "topics_studied": len(topics),
            "questions_asked": sum(topic["questions_asked"] for topic in topics),
            "practice_total": practice_total,
            "accuracy": round(correct_total / practice_total * 100) if practice_total else 0,
            "ai_configured": is_ai_configured(),
            "subscription": subscription_status(g.user["id"], g.user["plan"]),
        }
    )


@app.post("/api/ask")
@login_required
def ask():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    topic = str(data.get("topic", "")).strip() or "General"
    if not question:
        return jsonify(error="Write a question first."), 400
    current_subscription = subscription_status(g.user["id"], g.user["plan"])
    if current_subscription["usage"]["tutor"]["remaining"] == 0:
        return jsonify(
            error="You've reached today's tutor question limit. Your allowance resets at midnight India time.",
            subscription=current_subscription,
        ), 429
    try:
        explanation = call_ai(
            "You are a warm, patient study tutor. Explain ideas to beginners in clear "
            "steps, define unfamiliar terms, and use a small example where useful. "
            "Keep the answer focused and encouraging. " + student_context(),
            f"Study topic: {topic}\nStudent question: {question}",
        )
        with get_db() as connection:
            if not consume_daily_usage(connection, g.user["id"], g.user["plan"], "tutor", 1):
                current_subscription = subscription_status(g.user["id"], g.user["plan"])
                return jsonify(
                    error="You've reached today's tutor question limit. Your allowance resets at midnight India time.",
                    subscription=current_subscription,
                ), 429
            record_topic(connection, g.user["id"], topic, asked=1)
        return jsonify(
            explanation=explanation,
            topic=topic,
            subscription=subscription_status(g.user["id"], g.user["plan"]),
        )
    except RuntimeError as error:
        return jsonify(error=str(error)), 503


@app.post("/api/solve-image")
@login_required
def solve_image():
    image_file = request.files.get("image")
    if image_file is None or not image_file.filename:
        return jsonify(error="Choose a picture of the question first."), 400
    image_bytes = image_file.stream.read(5 * 1024 * 1024 + 1)
    if not image_bytes:
        return jsonify(error="The selected image is empty. Choose another image."), 400
    if len(image_bytes) > 5 * 1024 * 1024:
        return jsonify(error="That image is too large. Choose an image under 5 MB."), 413

    image_signatures = (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
    )
    mime_type = next(
        (mime for signature, mime in image_signatures if image_bytes.startswith(signature)),
        None,
    )
    if mime_type is None and (
        len(image_bytes) >= 12
        and image_bytes.startswith(b"RIFF")
        and image_bytes[8:12] == b"WEBP"
    ):
        mime_type = "image/webp"
    if mime_type is None:
        return jsonify(error="Use a PNG, JPG, or WebP image of the question."), 400

    topic = request.form.get("topic", "").strip() or "Image question"
    student_question = request.form.get("question", "").strip()
    current_subscription = subscription_status(g.user["id"], g.user["plan"])
    if current_subscription["usage"]["tutor"]["remaining"] == 0:
        return jsonify(
            error="You've reached today's tutor question limit. Your allowance resets at midnight India time.",
            subscription=current_subscription,
        ), 429
    user_prompt = (
        f"Study topic: {topic}\n"
        "Read the question or relevant text visible in this image and solve it. "
        "Show the key steps in beginner-friendly language, then clearly state the final answer. "
        "If the image is blurry, incomplete, or has multiple possible questions, say what you can "
        "read and ask the student to upload a clearer or more closely cropped image."
    )
    if student_question:
        user_prompt += f"\nThe student also asks: {student_question}"
    try:
        explanation = call_ai(
            "You are a careful, patient study tutor who can read educational images. "
            "Do not guess at unreadable text, and explain calculations step by step. "
            + student_context(),
            user_prompt,
            image=(mime_type, image_bytes),
        )
        with get_db() as connection:
            if not consume_daily_usage(connection, g.user["id"], g.user["plan"], "tutor", 1):
                current_subscription = subscription_status(g.user["id"], g.user["plan"])
                return jsonify(
                    error="You've reached today's tutor question limit. Your allowance resets at midnight India time.",
                    subscription=current_subscription,
                ), 429
            record_topic(connection, g.user["id"], topic, asked=1)
        return jsonify(
            explanation=explanation,
            topic=topic,
            subscription=subscription_status(g.user["id"], g.user["plan"]),
        )
    except RuntimeError as error:
        return jsonify(error=str(error)), 503


@app.post("/api/notes")
@login_required
def create_notes():
    data = request.get_json(silent=True) or {}
    topic = str(data.get("topic", "")).strip()
    source = str(data.get("source", "")).strip()
    if not source:
        return jsonify(error="Paste your lesson text or add a topic to make study notes."), 400
    if len(source) > 12000:
        return jsonify(error="Please keep the lesson text under 12,000 characters."), 400
    topic = topic or "Study notes"
    try:
        content = call_ai(
            "Turn the student's lesson into clear beginner-friendly study notes. "
            "Return only valid JSON with this exact shape: "
            '{"title":"short topic title","summary":"2-4 sentence summary",'
            '"key_points":["point one","point two","point three"],'
            '"key_terms":["important term one","important term two"],'
            '"remember":"one memorable takeaway"}. Keep the notes accurate to the '
            "provided lesson. Use 3 to 6 concise key points and 0 to 5 useful terms. "
            "If the input is only a topic rather than lesson text, make a short, "
            "accurate beginner overview. " + student_context(),
            f"Topic: {topic}\n\nLesson text or topic:\n{source}",
        )
        result = decode_ai_json(content)
        title = result.get("title") if isinstance(result, dict) else None
        summary = result.get("summary") if isinstance(result, dict) else None
        key_points = result.get("key_points") if isinstance(result, dict) else None
        raw_terms = result.get("key_terms") if isinstance(result, dict) else None
        remember = result.get("remember") if isinstance(result, dict) else None
        key_terms = []
        if isinstance(raw_terms, list):
            for item in raw_terms[:5]:
                if isinstance(item, str):
                    key_terms.append({"term": item.strip(), "definition": ""})
                elif isinstance(item, dict):
                    key_terms.append(
                        {
                            "term": item.get("term"),
                            "definition": item.get("definition", ""),
                        }
                    )
        valid = (
            isinstance(title, str) and bool(title.strip())
            and isinstance(summary, str) and bool(summary.strip())
            and isinstance(key_points, list) and 3 <= len(key_points) <= 6
            and all(isinstance(point, str) and point.strip() for point in key_points)
            and all(
                isinstance(item, dict)
                and isinstance(item.get("term"), str) and item["term"].strip()
                and isinstance(item.get("definition"), str)
                for item in key_terms
            )
            and isinstance(remember, str) and bool(remember.strip())
        )
        if not valid:
            raise RuntimeError("The AI couldn't format those notes. Please try again.")
        return jsonify(
            note={
                "title": title.strip(),
                "topic": topic,
                "summary": summary.strip(),
                "key_points": [point.strip() for point in key_points],
                "key_terms": [
                    {"term": item["term"].strip(), "definition": item["definition"].strip()}
                    for item in key_terms
                ],
                "remember": remember.strip(),
            }
        )
    except RuntimeError as error:
        return jsonify(error=str(error)), 503


@app.get("/api/notes")
@login_required
def get_notes():
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT id, title, topic, summary, key_points_json, key_terms_json, remember, created_at
            FROM user_notes WHERE user_id = ? ORDER BY created_at DESC
            """,
            (g.user["id"],),
        ).fetchall()
    return jsonify(notes=[
        {
            "id": row["id"],
            "title": row["title"],
            "topic": row["topic"],
            "summary": row["summary"],
            "key_points": json.loads(row["key_points_json"]),
            "key_terms": json.loads(row["key_terms_json"]),
            "remember": row["remember"],
            "created_at": row["created_at"],
        }
        for row in rows
    ])


@app.post("/api/notes/save")
@login_required
def save_note():
    data = request.get_json(silent=True) or {}
    note = data.get("note")
    if not isinstance(note, dict):
        return jsonify(error="The note could not be saved. Please generate it again."), 400
    title = note.get("title")
    topic = note.get("topic")
    summary = note.get("summary")
    key_points = note.get("key_points")
    key_terms = note.get("key_terms")
    remember = note.get("remember")
    valid = (
        isinstance(title, str) and bool(title.strip())
        and isinstance(topic, str) and bool(topic.strip())
        and isinstance(summary, str) and bool(summary.strip())
        and isinstance(key_points, list) and all(isinstance(item, str) for item in key_points)
        and isinstance(key_terms, list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("term"), str)
            and isinstance(item.get("definition"), str)
            for item in key_terms
        )
        and isinstance(remember, str) and bool(remember.strip())
    )
    if not valid:
        return jsonify(error="The note is incomplete. Please generate it again."), 400
    note_id = uuid.uuid4().hex
    with get_db() as connection:
        connection.execute(
            """
            INSERT INTO user_notes
                (id, user_id, title, topic, summary, key_points_json, key_terms_json, remember)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id, g.user["id"], title.strip(), topic.strip(), summary.strip(),
                json.dumps(key_points), json.dumps(key_terms), remember.strip(),
            ),
        )
    return jsonify(id=note_id), 201


@app.delete("/api/notes/<note_id>")
@login_required
def delete_note(note_id):
    with get_db() as connection:
        cursor = connection.execute(
            "DELETE FROM user_notes WHERE id = ? AND user_id = ?",
            (note_id, g.user["id"]),
        )
    if cursor.rowcount == 0:
        return jsonify(error="That saved note was not found."), 404
    return jsonify(deleted=True)


@app.post("/api/practice")
@login_required
def practice():
    data = request.get_json(silent=True) or {}
    topic = str(data.get("topic", "")).strip()
    if not topic:
        return jsonify(error="Add a topic before generating practice questions."), 400
    current_subscription = subscription_status(g.user["id"], g.user["plan"])
    remaining = current_subscription["usage"]["practice"]["remaining"]
    if remaining == 0:
        return jsonify(
            error="You've used today's practice question allowance. It resets at midnight India time.",
            subscription=current_subscription,
        ), 429
    question_count = min(3, remaining) if remaining is not None else 3
    try:
        system_prompt = (
            f"Create exactly {question_count} beginner-friendly multiple-choice practice questions. "
            "Return only valid JSON in this exact shape: "
            '{"questions":[{"question":"...","options":["...","...","...","..."],'
            '"answer":"exact correct option text","explanation":"..."}]}. '
            "The answer field MUST copy the correct option exactly as text, not an index. "
            f"There must be exactly {question_count} questions "
            "and exactly 4 distinct options per question. Every option MUST be a JSON "
            "string, even when the option is a number. The answer field must exactly "
            "match one option. The explanation's final answer must match the answer "
            "field. Example format only: "
            '{"questions":[{"question":"How many sides does a triangle have?",'
            '"options":["2","3","4","5"],"answer":"3",'
            '"explanation":"A triangle has three sides."}]}. Do not reuse the example. '
            "Solve each question, then check both the answer index and explanation "
            "before returning. Prefer simple, unambiguous questions at the student's "
            "school level. " + student_context()
        )
        questions = None
        for attempt in range(2):
            user_prompt = f"Create practice questions about: {topic}"
            if attempt:
                user_prompt += (
                    " Follow the required JSON types exactly: questions is an array "
                    f"of {question_count} objects and every option is a string."
                )
            content = call_ai(system_prompt, user_prompt)
            try:
                result = decode_ai_json(content)
            except RuntimeError:
                if attempt:
                    raise
                continue
            candidate = result.get("questions") if isinstance(result, dict) else None
            valid = (
                isinstance(candidate, list)
                and len(candidate) == question_count
                and all(
                    isinstance(item, dict)
                    and isinstance(item.get("question"), str)
                    and item["question"].strip()
                    and isinstance(item.get("options"), list)
                    and len(item["options"]) == 4
                    and all(isinstance(option, str) and option.strip() for option in item["options"])
                    and len(set(item["options"])) == 4
                    and isinstance(item.get("answer"), str)
                    and item["answer"] in item["options"]
                    and isinstance(item.get("explanation"), str)
                    and item["explanation"].strip()
                    for item in candidate
                )
            )
            if valid:
                questions = candidate
                break
        if questions is None:
            raise RuntimeError(
                f"The AI response did not contain {question_count} valid questions. Please try again."
            )
        for item in questions:
            item["answer"] = item["options"].index(item["answer"])
        practice_id = uuid.uuid4().hex
        with get_db() as connection:
            connection.execute(
                "INSERT INTO user_practice_sets (id, user_id, topic, questions_json) VALUES (?, ?, ?, ?)",
                (practice_id, g.user["id"], topic, json.dumps(questions)),
            )
            record_topic(connection, g.user["id"], topic)
        public_questions = [
            {"question": item["question"], "options": item["options"]}
            for item in questions
        ]
        return jsonify(
            id=practice_id,
            topic=topic,
            questions=public_questions,
            subscription=subscription_status(g.user["id"], g.user["plan"]),
        )
    except RuntimeError as error:
        return jsonify(error=str(error)), 503


@app.post("/api/grade")
@login_required
def grade():
    data = request.get_json(silent=True) or {}
    practice_id = str(data.get("id", "")).strip()
    answers = data.get("answers")
    if not practice_id:
        return jsonify(error="Generate a practice set before checking answers."), 400
    if not isinstance(answers, list) or not 1 <= len(answers) <= 3 or any(
        type(answer) is not int or answer < 0 or answer > 3 for answer in answers
    ):
        return jsonify(error="Choose an answer for every question in this practice set."), 400
    quota_exceeded = False
    with get_db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        practice_set = connection.execute(
            "SELECT topic, questions_json FROM user_practice_sets WHERE id = ? AND user_id = ?",
            (practice_id, g.user["id"]),
        ).fetchone()
        if practice_set is None:
            return jsonify(error="That practice set has expired. Generate a new set and try again."), 400
        topic = practice_set["topic"]
        questions = json.loads(practice_set["questions_json"])
        if len(answers) != len(questions):
            return jsonify(error="Choose an answer for every question in this practice set."), 400

        results = []
        for question, selected in zip(questions, answers):
            correct = selected == question["answer"]
            results.append(
                {
                    "correct": correct,
                    "selected": selected,
                    "answer": question["answer"],
                    "explanation": question["explanation"],
                }
            )
        correct_count = sum(result["correct"] for result in results)
        quota_exceeded = not consume_daily_usage(
            connection, g.user["id"], g.user["plan"], "practice", len(results)
        )
        if not quota_exceeded:
            record_topic(
                connection,
                g.user["id"],
                topic,
                practiced=len(results),
                correct=correct_count,
            )
            connection.execute(
                "DELETE FROM user_practice_sets WHERE id = ? AND user_id = ?",
                (practice_id, g.user["id"]),
            )
    if quota_exceeded:
        current_subscription = subscription_status(g.user["id"], g.user["plan"])
        return jsonify(
            error="You've used today's practice question allowance. It resets at midnight India time.",
            subscription=current_subscription,
        ), 429
    return jsonify(
        results=results,
        correct=correct_count,
        total=len(results),
        subscription=subscription_status(g.user["id"], g.user["plan"]),
    )



init_db()


# Seed confidential developer account (override via env).
dev_email = os.environ.get("DEV_EMAIL", "developer@studyai.local")
dev_password = os.environ.get("DEV_PASSWORD", "DevAccess#2026!")
with get_db() as connection:
    existing = connection.execute("SELECT id FROM users WHERE email = ?", (dev_email,)).fetchone()
    if not existing:
        connection.execute(
            "INSERT INTO users (email, password_hash, school_class, board, plan) VALUES (?, ?, ?, ?, ?)",
            (dev_email, generate_password_hash(dev_password, method="pbkdf2:sha256:600000"), "Other", "Other", "PRO"),
        )
if __name__ == "__main__":
    debug_enabled = (
        os.environ.get("STUDYAI_ENV", "development").lower() == "development"
        and os.environ.get("FLASK_DEBUG") == "1"
    )
    app.run(debug=debug_enabled)
