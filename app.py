import base64
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from openai import OpenAI
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / os.getenv("DATABASE_PATH", "comic_app.db")
UPLOAD_FOLDER = BASE_DIR / os.getenv("UPLOAD_FOLDER", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


class User:
    def __init__(self, user_id, email):
        self.id = str(user_id)
        self.email = email

    @property
    def is_authenticated(self):
        return True


def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    UPLOAD_FOLDER.mkdir(exist_ok=True)
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS comic_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                issue TEXT,
                publisher TEXT,
                grade TEXT,
                estimated_value_low REAL NOT NULL,
                estimated_value_high REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                source_note TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                image_filename TEXT NOT NULL,
                title_guess TEXT,
                issue_guess TEXT,
                publisher_guess TEXT,
                description TEXT NOT NULL,
                search_terms TEXT,
                value_summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        ensure_scan_columns(conn)
        count = conn.execute("SELECT COUNT(*) FROM comic_values").fetchone()[0]
        if count == 0:
            seed_values(conn)
        repair_raw_json_scans(conn)


def ensure_scan_columns(conn):
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(scans)").fetchall()
    }
    if "search_terms" not in columns:
        conn.execute("ALTER TABLE scans ADD COLUMN search_terms TEXT")


def seed_values(conn):
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        ("Amazing Spider-Man", "300", "Marvel", "CGC 9.8", 5500, 7500, "USD", "Demo seed data. Replace with live market data.", now),
        ("Batman", "1", "DC", "Good", 50000, 90000, "USD", "Demo seed data. Replace with live market data.", now),
        ("X-Men", "1", "Marvel", "CGC 6.0", 25000, 40000, "USD", "Demo seed data. Replace with live market data.", now),
        ("The Walking Dead", "1", "Image", "Near Mint", 1200, 2200, "USD", "Demo seed data. Replace with live market data.", now),
        ("Spawn", "1", "Image", "Near Mint", 25, 60, "USD", "Demo seed data. Replace with live market data.", now),
    ]
    conn.executemany(
        """
        INSERT INTO comic_values
        (title, issue, publisher, grade, estimated_value_low, estimated_value_high, currency, source_note, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def load_user(user_id):
    with get_db() as conn:
        row = conn.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return User(row["id"], row["email"])


@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.current_user = load_user(user_id) if user_id else None


@app.context_processor
def inject_current_user():
    return {"current_user": g.get("current_user")}


@app.template_filter("from_json")
def from_json_filter(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.current_user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def strip_json_markdown(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def normalize_analysis(parsed):
    search_terms = parsed.get("search_terms", [])
    if isinstance(search_terms, str):
        search_terms = [term.strip() for term in search_terms.split(",") if term.strip()]
    elif not isinstance(search_terms, list):
        search_terms = []

    return {
        "title_guess": str(parsed.get("title_guess") or "Unknown").strip(),
        "issue_guess": str(parsed.get("issue_guess") or "").strip(),
        "publisher_guess": str(parsed.get("publisher_guess") or "").strip(),
        "description": str(parsed.get("description") or "").strip(),
        "search_terms": [str(term).strip() for term in search_terms if str(term).strip()],
    }


def parse_analysis_text(text):
    cleaned = strip_json_markdown(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {
            "title_guess": "Unknown",
            "issue_guess": "",
            "publisher_guess": "",
            "description": text.strip(),
            "search_terms": [],
        }
    return normalize_analysis(parsed)


def repair_raw_json_scans(conn):
    rows = conn.execute(
        """
        SELECT id, description
        FROM scans
        WHERE description LIKE '```json%'
           OR description LIKE '{%'
        """
    ).fetchall()
    for row in rows:
        analysis = parse_analysis_text(row["description"])
        if analysis["description"] == row["description"]:
            continue
        conn.execute(
            """
            UPDATE scans
            SET title_guess = ?,
                issue_guess = ?,
                publisher_guess = ?,
                description = ?,
                search_terms = ?
            WHERE id = ?
            """,
            (
                analysis["title_guess"],
                analysis["issue_guess"],
                analysis["publisher_guess"],
                analysis["description"],
                json.dumps(analysis["search_terms"]),
                row["id"],
            ),
        )


def analyze_comic_image(image_path):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured. Add it to your .env file.")

    client = OpenAI(api_key=api_key)
    image_bytes = image_path.read_bytes()
    mime_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(image_path.suffix.lower(), "image/jpeg")
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime_type};base64,{encoded}"

    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Inspect this comic book cover. Return strict JSON with keys: "
                            "title_guess, issue_guess, publisher_guess, description, search_terms. "
                            "If a detail is uncertain, say so clearly."
                        ),
                    },
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
    )

    return parse_analysis_text(response.output_text)


def find_value_match(analysis):
    title = (analysis.get("title_guess") or "").strip()
    issue = (analysis.get("issue_guess") or "").strip()

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM comic_values
            WHERE lower(title) LIKE lower(?)
              AND (? = '' OR issue = ?)
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (f"%{title}%", issue, issue),
        ).fetchone()

    if row is None:
        return {
            "summary": "No matching value record was found in the local database yet.",
            "match": None,
        }

    summary = (
        f"{row['title']} #{row['issue']} ({row['publisher']}), {row['grade']}: "
        f"{row['currency']} {row['estimated_value_low']:,.0f} - {row['estimated_value_high']:,.0f}. "
        f"{row['source_note']}"
    )
    return {"summary": summary, "match": dict(row)}


@app.route("/")
def index():
    if g.current_user is not None:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.", "error")
            return redirect(url_for("register"))

        try:
            with get_db() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO users (email, password_hash, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        email,
                        generate_password_hash(password),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                user_id = cursor.lastrowid
            session.clear()
            session["user_id"] = user_id
            return redirect(url_for("dashboard"))
        except sqlite3.IntegrityError:
            flash("An account with that email already exists.", "error")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with get_db() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["user_id"] = row["id"]
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    with get_db() as conn:
        scans = conn.execute(
            """
            SELECT * FROM scans
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 12
            """,
            (g.current_user.id,),
        ).fetchall()
    return render_template("dashboard.html", scans=scans)


@app.route("/scan", methods=["POST"])
@login_required
def scan():
    file = request.files.get("comic_image")
    if not file or file.filename == "":
        flash("Choose an image to upload.", "error")
        return redirect(url_for("dashboard"))

    if not allowed_file(file.filename):
        flash("Upload a PNG, JPG, JPEG, or WEBP image.", "error")
        return redirect(url_for("dashboard"))

    original_name = secure_filename(file.filename)
    extension = Path(original_name).suffix.lower()
    filename = f"{uuid.uuid4().hex}{extension}"
    image_path = UPLOAD_FOLDER / filename
    file.save(image_path)

    try:
        analysis = analyze_comic_image(image_path)
        value_result = find_value_match(analysis)
    except Exception as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO scans
            (user_id, image_filename, title_guess, issue_guess, publisher_guess, description, search_terms, value_summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                g.current_user.id,
                filename,
                analysis.get("title_guess", ""),
                analysis.get("issue_guess", ""),
                analysis.get("publisher_guess", ""),
                analysis.get("description", ""),
                json.dumps(analysis.get("search_terms", [])),
                value_result["summary"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
