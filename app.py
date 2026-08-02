from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import Flask, request, redirect, render_template, render_template_string, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
import os
import re
import secrets
import shutil
import sqlite3
import string

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]

BASE_DIR = Path("/opt/proposal-manager")
DATABASE_PATH = BASE_DIR / "proposal_manager.db"
PROPOSAL_ROOT = Path("/var/www/proposal")
ALLOWED_EXTENSIONS = {"html", "htm"}

ADMIN_USERNAME = os.environ["ADMIN_USERNAME"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect("/admin/login")
        return view(*args, **kwargs)
    return wrapped_view






def client_login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("client_id"):
            return redirect("/client/login")
        return view(*args, **kwargs)
    return wrapped_view




def admin_redirect(path=""):
    path = path.lstrip("/")
    if path:
        return redirect(f"/admin/{path}")
    return redirect("/admin/")


def get_db():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                contact_name TEXT,
                email TEXT,
                login_id TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                html_path TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(id)
            )
            """
        )

        db.commit()


def valid_slug(slug: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", slug))


def allowed_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def generate_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_login_id(slug):
    with get_db() as db:
        number = 1

        while True:
            login_id = f"{slug}{number:03d}"

            exists = db.execute(
                "SELECT id FROM clients WHERE login_id = ?",
                (login_id,)
            ).fetchone()

            if not exists:
                return login_id

            number += 1



def get_dashboard_data():
    today = datetime.now().date().isoformat()

    with get_db() as db:
        total_clients = db.execute(
            "SELECT COUNT(*) FROM clients"
        ).fetchone()[0]

        total_proposals = db.execute(
            "SELECT COUNT(*) FROM proposals"
        ).fetchone()[0]

        today_views = db.execute(
            """
            SELECT COUNT(*)
            FROM proposal_views
            WHERE substr(viewed_at, 1, 10) = ?
            """,
            (today,)
        ).fetchone()[0]

        unread_clients = db.execute(
            """
            SELECT COUNT(*)
            FROM clients
            LEFT JOIN proposal_views
                ON proposal_views.client_id = clients.id
            WHERE proposal_views.id IS NULL
            """
        ).fetchone()[0]

        recent_views = db.execute(
            """
            SELECT
                clients.company_name,
                proposal_views.viewed_at,
                proposal_views.ip_address
            FROM proposal_views
            JOIN clients
                ON clients.id = proposal_views.client_id
            ORDER BY proposal_views.id DESC
            LIMIT 8
            """
        ).fetchall()

    return {
        "total_clients": total_clients,
        "total_proposals": total_proposals,
        "today_views": today_views,
        "unread_clients": unread_clients,
        "recent_views": recent_views,
    }


def get_clients():
    with get_db() as db:
        return db.execute(
            """
            SELECT
                clients.*,
                proposals.updated_at AS proposal_updated_at
            FROM clients
            LEFT JOIN proposals
                ON proposals.client_id = clients.id
            ORDER BY clients.id DESC
            """
        ).fetchall()






@app.route("/client/login", methods=["GET", "POST"])
def client_login():
    if request.method == "POST":
        login_id = request.form.get("login_id", "").strip()
        password = request.form.get("password", "")

        with get_db() as db:
            client = db.execute(
                "SELECT * FROM clients WHERE login_id = ?",
                (login_id,)
            ).fetchone()

        if client and check_password_hash(client["password_hash"], password):
            session.clear()
            session["client_id"] = client["id"]
            session["client_company_name"] = client["company_name"]
            return redirect("/client/proposal")

        flash("ログインIDまたはパスワードが違います。")

    return render_template("client_login.html")


@app.get("/client/proposal")
@client_login_required
def client_proposal():
    client_id = session.get("client_id")

    with get_db() as db:
        client = db.execute(
            "SELECT * FROM clients WHERE id = ?",
            (client_id,)
        ).fetchone()

        proposal = db.execute(
            "SELECT * FROM proposals WHERE client_id = ?",
            (client_id,)
        ).fetchone()

    if not client:
        session.clear()
        return redirect("/client/login")

    if not proposal:
        return (
            "<h1>提案書は現在準備中です。</h1>"
            "<p>担当者からの連絡をお待ちください。</p>",
            404
        )

    forwarded_for = request.headers.get("X-Forwarded-For", "")
    ip_address = (
        forwarded_for.split(",")[0].strip()
        if forwarded_for
        else request.remote_addr
    )
    user_agent = request.headers.get("User-Agent", "")[:500]

    with get_db() as db:
        db.execute(
            """
            INSERT INTO proposal_views (
                client_id,
                proposal_id,
                viewed_at,
                ip_address,
                user_agent
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                client_id,
                proposal["id"],
                datetime.now().isoformat(timespec="seconds"),
                ip_address,
                user_agent
            )
        )
        db.commit()

    html_path = Path(proposal["html_path"])

    if not html_path.exists():
        return (
            "<h1>提案書ファイルが見つかりません。</h1>"
            "<p>管理者へお問い合わせください。</p>",
            404
        )

    return html_path.read_text(encoding="utf-8")


@app.get("/client/logout")
def client_logout():
    session.clear()
    return redirect("/client/login")


@app.route("/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect("/admin/")

        flash("ユーザー名またはパスワードが違います。")

    return render_template("admin_login.html")


@app.get("/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")


@app.get("/")
@login_required
def index():
    dashboard = get_dashboard_data()

    return render_template(
        "dashboard.html",
        clients=get_clients(),
        dashboard=dashboard
    )


@app.post("/client/add")
@login_required
def add_client():
    company_name = request.form.get("company_name", "").strip()
    contact_name = request.form.get("contact_name", "").strip()
    email = request.form.get("email", "").strip()
    slug = request.form.get("slug", "").strip().lower()

    if not company_name:
        flash("会社名を入力してください。")
        return admin_redirect()

    if not valid_slug(slug):
        flash(
            "URL用英数字は、小文字英数字とハイフンのみで入力してください。"
        )
        return admin_redirect()

    login_id = generate_login_id(slug)
    temporary_password = generate_password()

    try:
        with get_db() as db:
            db.execute(
                """
                INSERT INTO clients (
                    company_name,
                    contact_name,
                    email,
                    login_id,
                    password_hash,
                    slug,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_name,
                    contact_name,
                    email,
                    login_id,
                    generate_password_hash(temporary_password),
                    slug,
                    datetime.now().isoformat(timespec="seconds")
                )
            )

            db.commit()

    except sqlite3.IntegrityError:
        flash(
            "同じURL用英数字が既に登録されています。"
            "別の文字を指定してください。"
        )
        return admin_redirect()

    flash(
        f"{company_name}様を登録しました。\n\n"
        f"ログインID：{login_id}\n"
        f"仮パスワード：{temporary_password}\n\n"
        "パスワードはこの画面を閉じる前に控えてください。"
    )

    return admin_redirect()


@app.post("/upload")
@login_required
def upload():
    client_id = request.form.get("client_id", "").strip()
    uploaded = request.files.get("file")

    if not client_id.isdigit():
        flash("顧客を選択してください。")
        return admin_redirect()

    with get_db() as db:
        client = db.execute(
            "SELECT * FROM clients WHERE id = ?",
            (int(client_id),)
        ).fetchone()

    if not client:
        flash("顧客が見つかりません。")
        return admin_redirect()

    if not uploaded or not uploaded.filename:
        flash("HTMLファイルを選択してください。")
        return admin_redirect()

    if not allowed_file(uploaded.filename):
        flash("HTMLファイルだけアップロードできます。")
        return admin_redirect()

    target_dir = PROPOSAL_ROOT / client["slug"]
    target_dir.mkdir(parents=True, exist_ok=True)

    target_file = target_dir / "index.html"
    uploaded.save(target_file)

    os.chown(target_dir, 33, 33)
    os.chown(target_file, 33, 33)
    os.chmod(target_dir, 0o755)
    os.chmod(target_file, 0o644)

    now = datetime.now().isoformat(timespec="seconds")

    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM proposals WHERE client_id = ?",
            (client["id"],)
        ).fetchone()

        if existing:
            db.execute(
                """
                UPDATE proposals
                SET html_path = ?, updated_at = ?
                WHERE client_id = ?
                """,
                (
                    str(target_file),
                    now,
                    client["id"]
                )
            )
        else:
            db.execute(
                """
                INSERT INTO proposals (
                    client_id,
                    html_path,
                    updated_at
                )
                VALUES (?, ?, ?)
                """,
                (
                    client["id"],
                    str(target_file),
                    now
                )
            )

        db.commit()

    flash(f"{client['company_name']}様の提案書を公開しました。")
    return admin_redirect()


@app.post("/client/<int:client_id>/reset-password")
@login_required
def reset_password(client_id):
    temporary_password = generate_password()

    with get_db() as db:
        client = db.execute(
            "SELECT * FROM clients WHERE id = ?",
            (client_id,)
        ).fetchone()

        if not client:
            flash("顧客が見つかりません。")
            return admin_redirect()

        db.execute(
            """
            UPDATE clients
            SET password_hash = ?
            WHERE id = ?
            """,
            (
                generate_password_hash(temporary_password),
                client_id
            )
        )

        db.commit()

    flash(
        f"{client['company_name']}様のパスワードを再発行しました。\n\n"
        f"ログインID：{client['login_id']}\n"
        f"新しいパスワード：{temporary_password}"
    )

    return admin_redirect()


@app.post("/client/<int:client_id>/delete")
@login_required
def delete_client(client_id):
    with get_db() as db:
        client = db.execute(
            "SELECT * FROM clients WHERE id = ?",
            (client_id,)
        ).fetchone()

        if not client:
            flash("顧客が見つかりません。")
            return admin_redirect()

        target_dir = PROPOSAL_ROOT / client["slug"]

        if target_dir.exists() and target_dir.is_dir():
            shutil.rmtree(target_dir)

        db.execute(
            "DELETE FROM proposals WHERE client_id = ?",
            (client_id,)
        )

        db.execute(
            "DELETE FROM clients WHERE id = ?",
            (client_id,)
        )

        db.commit()

    flash(f"{client['company_name']}様を削除しました。")
    return admin_redirect()


init_db()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000
    )
