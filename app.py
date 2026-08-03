from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, redirect, render_template, render_template_string, flash, session, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import os
import re
import secrets
import shutil
import sqlite3
import string

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

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
                project_id INTEGER,
                html_path TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(id),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                project_name TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT '未対応',
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(id)
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                document_type TEXT NOT NULL,
                title TEXT NOT NULL,
                file_path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (project_id) REFERENCES projects(id)
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



def save_project_document(
    project_id,
    uploaded,
    document_type,
    title,
    target_filename,
    allowed_extensions,
    mime_type,
    sync_legacy_proposal=False,
):
    with get_db() as db:
        project = db.execute(
            """
            SELECT
                projects.*,
                clients.slug,
                clients.company_name
            FROM projects
            JOIN clients
                ON clients.id = projects.client_id
            WHERE projects.id = ?
            """,
            (project_id,)
        ).fetchone()

    if not project:
        return None, "対象案件が見つかりません。"

    if not uploaded or not uploaded.filename:
        return project, "ファイルを選択してください。"

    if "." not in uploaded.filename:
        return project, "ファイル形式を確認してください。"

    extension = uploaded.filename.rsplit(".", 1)[1].lower()

    if extension not in allowed_extensions:
        return project, "対応していないファイル形式です。"

    target_dir = (
        PROPOSAL_ROOT
        / project["slug"]
        / f"project-{project_id}"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    target_file = target_dir / target_filename
    uploaded.save(target_file)

    os.chown(target_dir, 33, 33)
    os.chown(target_file, 33, 33)
    os.chmod(target_dir, 0o755)
    os.chmod(target_file, 0o644)

    now = datetime.now().isoformat(timespec="seconds")

    with get_db() as db:
        existing_document = db.execute(
            """
            SELECT id
            FROM documents
            WHERE project_id = ?
              AND document_type = ?
            """,
            (project_id, document_type)
        ).fetchone()

        if existing_document:
            db.execute(
                """
                UPDATE documents
                SET
                    title = ?,
                    file_path = ?,
                    mime_type = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    str(target_file),
                    mime_type,
                    now,
                    existing_document["id"],
                )
            )
        else:
            db.execute(
                """
                INSERT INTO documents (
                    project_id,
                    document_type,
                    title,
                    file_path,
                    mime_type,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    document_type,
                    title,
                    str(target_file),
                    mime_type,
                    now,
                    now,
                )
            )

        if sync_legacy_proposal:
            existing_proposal = db.execute(
                """
                SELECT id
                FROM proposals
                WHERE project_id = ?
                """,
                (project_id,)
            ).fetchone()

            if existing_proposal:
                db.execute(
                    """
                    UPDATE proposals
                    SET
                        html_path = ?,
                        updated_at = ?
                    WHERE project_id = ?
                    """,
                    (
                        str(target_file),
                        now,
                        project_id,
                    )
                )
            else:
                db.execute(
                    """
                    INSERT INTO proposals (
                        client_id,
                        project_id,
                        html_path,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        project["client_id"],
                        project_id,
                        str(target_file),
                        now,
                    )
                )

        db.commit()

    return project, None


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

        sales_rows = db.execute(
            """
            SELECT
                clients.id,
                clients.company_name,
                proposals.id AS proposal_id,
                COUNT(proposal_views.id) AS view_count,
                MAX(proposal_views.viewed_at) AS last_viewed_at,
                SUM(
                    CASE
                        WHEN substr(proposal_views.viewed_at, 1, 10) = ?
                        THEN 1
                        ELSE 0
                    END
                ) AS today_view_count
            FROM clients
            LEFT JOIN proposals
                ON proposals.client_id = clients.id
            LEFT JOIN proposal_views
                ON proposal_views.client_id = clients.id
            GROUP BY
                clients.id,
                clients.company_name,
                proposals.id
            """,
            (today,)
        ).fetchall()

    now = datetime.now()
    sales_actions = []

    for row in sales_rows:
        score = 0
        reasons = []

        if row["proposal_id"]:
            score += 10
            reasons.append("提案書を公開済み")

        view_count = row["view_count"] or 0
        today_view_count = row["today_view_count"] or 0
        last_viewed_at = row["last_viewed_at"]

        if today_view_count > 0:
            score += 30
            reasons.append(f"本日{today_view_count}回閲覧")

        if view_count >= 5:
            score += 30
            reasons.append(f"累計{view_count}回閲覧")
        elif view_count >= 3:
            score += 20
            reasons.append(f"累計{view_count}回閲覧")
        elif view_count >= 1:
            score += 10
            reasons.append(f"累計{view_count}回閲覧")

        if last_viewed_at:
            try:
                last_viewed = datetime.fromisoformat(last_viewed_at)
                elapsed = now - last_viewed

                if elapsed <= timedelta(hours=24):
                    score += 25
                    reasons.append("24時間以内に閲覧")
                elif elapsed <= timedelta(days=3):
                    score += 15
                    reasons.append("3日以内に閲覧")
                elif elapsed <= timedelta(days=7):
                    score += 5
                    reasons.append("7日以内に閲覧")
            except ValueError:
                pass

        score = min(score, 100)

        if score >= 75:
            action = "今日、電話でフォロー"
            level = "high"
        elif score >= 50:
            action = "フォローメールを送信"
            level = "medium"
        elif score >= 25:
            action = "数日以内に状況確認"
            level = "low"
        else:
            action = "提案書の閲覧を案内"
            level = "none"

        sales_actions.append({
            "client_id": row["id"],
            "company_name": row["company_name"],
            "score": score,
            "stars": min(5, max(1, (score + 19) // 20)),
            "view_count": view_count,
            "last_viewed_at": last_viewed_at,
            "action": action,
            "level": level,
            "reasons": reasons,
        })

    sales_actions.sort(
        key=lambda item: (
            item["score"],
            item["last_viewed_at"] or ""
        ),
        reverse=True
    )

    return {
        "total_clients": total_clients,
        "total_proposals": total_proposals,
        "today_views": today_views,
        "unread_clients": unread_clients,
        "recent_views": recent_views,
        "sales_actions": sales_actions[:5],
    }



def get_projects():
    with get_db() as db:
        return db.execute(
            """
            SELECT
                projects.*,
                clients.company_name
            FROM projects
            JOIN clients
                ON clients.id = projects.client_id
            ORDER BY projects.id DESC
            """
        ).fetchall()


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








@app.get("/client/<int:client_id>/timeline")
@login_required
def client_timeline(client_id):
    with get_db() as db:
        client = db.execute(
            "SELECT * FROM clients WHERE id = ?",
            (client_id,)
        ).fetchone()

        if not client:
            flash("対象顧客が見つかりません。")
            return admin_redirect()

        view_rows = db.execute(
            """
            SELECT
                viewed_at AS created_at,
                ip_address,
                user_agent
            FROM proposal_views
            WHERE client_id = ?
            """,
            (client_id,)
        ).fetchall()

        action_rows = db.execute(
            """
            SELECT
                created_at,
                action_type,
                note
            FROM sales_action_logs
            WHERE client_id = ?
            """,
            (client_id,)
        ).fetchall()

    timeline = []

    for row in view_rows:
        detail_parts = []

        if row["ip_address"]:
            detail_parts.append(f'IP: {row["ip_address"]}')

        if row["user_agent"]:
            detail_parts.append(row["user_agent"][:80])

        timeline.append({
            "type": "view",
            "title": "提案書を閲覧",
            "detail": " / ".join(detail_parts),
            "created_at": row["created_at"],
        })

    action_labels = {
        "phone": "電話した",
        "email": "メールした",
        "complete": "フォロー完了",
    }

    for row in action_rows:
        timeline.append({
            "type": row["action_type"],
            "title": action_labels.get(
                row["action_type"],
                row["note"] or "営業対応"
            ),
            "detail": row["note"] or "",
            "created_at": row["created_at"],
        })

    timeline.sort(
        key=lambda item: item["created_at"],
        reverse=True
    )

    return render_template(
        "client_timeline.html",
        client=client,
        timeline=timeline
    )


@app.post("/sales-action/<int:client_id>")
@login_required
def save_sales_action(client_id):
    action_type = request.form.get("action_type", "").strip()

    allowed_actions = {
        "phone": "電話した",
        "email": "メールした",
        "complete": "フォロー完了",
    }

    if action_type not in allowed_actions:
        flash("不正な営業アクションです。")
        return admin_redirect()

    with get_db() as db:
        client = db.execute(
            "SELECT id, company_name FROM clients WHERE id = ?",
            (client_id,)
        ).fetchone()

        if not client:
            flash("対象顧客が見つかりません。")
            return admin_redirect()

        db.execute(
            """
            INSERT INTO sales_action_logs (
                client_id,
                action_type,
                note,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                client_id,
                action_type,
                allowed_actions[action_type],
                datetime.now().isoformat(timespec="seconds")
            )
        )
        db.commit()

    flash(
        f'{client["company_name"]}：'
        f'{allowed_actions[action_type]}を記録しました。'
    )
    return admin_redirect()


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
        projects=get_projects(),
        dashboard=dashboard
    )




@app.get("/project/<int:project_id>")
@login_required
def project_detail(project_id):
    with get_db() as db:
        project = db.execute(
            """
            SELECT
                projects.*,
                clients.company_name,
                clients.slug
            FROM projects
            JOIN clients
                ON clients.id = projects.client_id
            WHERE projects.id = ?
            """,
            (project_id,)
        ).fetchone()

        proposal = db.execute(
            """
            SELECT *
            FROM proposals
            WHERE project_id = ?
            """,
            (project_id,)
        ).fetchone()

        documents = db.execute(
            """
            SELECT *
            FROM documents
            WHERE project_id = ?
            ORDER BY id DESC
            """,
            (project_id,)
        ).fetchall()

    documents_by_type = {
        document["document_type"]: document
        for document in documents
    }

    if not project:
        flash("対象案件が見つかりません。")
        return admin_redirect()

    return render_template(
        "project_detail.html",
        project=project,
        proposal=proposal,
        documents=documents,
        documents_by_type=documents_by_type
    )




@app.get("/project/<int:project_id>/document/<int:document_id>/open")
@login_required
def open_project_document(project_id, document_id):
    with get_db() as db:
        document = db.execute(
            """
            SELECT *
            FROM documents
            WHERE id = ?
              AND project_id = ?
            """,
            (
                document_id,
                project_id
            )
        ).fetchone()

    if not document:
        flash("対象資料が見つかりません。")
        return redirect(f"/admin/project/{project_id}")

    file_path = Path(document["file_path"])

    if not file_path.is_file():
        flash("資料ファイルが見つかりません。")
        return redirect(f"/admin/project/{project_id}")

    response = send_file(
        file_path,
        mimetype=document["mime_type"],
        as_attachment=False
    )

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "sandbox allow-forms allow-scripts"
    )

    return response


@app.post("/project/<int:project_id>/proposal/upload")
@login_required
def upload_project_proposal(project_id):
    uploaded = request.files.get("file")

    project, error = save_project_document(
        project_id=project_id,
        uploaded=uploaded,
        document_type="proposal",
        title="HTML提案書",
        target_filename="index.html",
        allowed_extensions={"html", "htm"},
        mime_type="text/html",
        sync_legacy_proposal=True,
    )

    if error:
        flash(error)

        if project is None:
            return admin_redirect()

        return redirect(f"/admin/project/{project_id}")

    flash(
        f'{project["company_name"]}：'
        f'案件「{project["project_name"]}」の提案書を公開しました。'
    )
    return redirect(f"/admin/project/{project_id}")


@app.post("/project/<int:project_id>/company/upload")
@login_required
def upload_project_company(project_id):
    uploaded = request.files.get("file")

    project, error = save_project_document(
        project_id=project_id,
        uploaded=uploaded,
        document_type="company",
        title="HTML会社案内",
        target_filename="company.html",
        allowed_extensions={"html", "htm"},
        mime_type="text/html",
    )

    if error:
        flash(error)

        if project is None:
            return admin_redirect()

        return redirect(f"/admin/project/{project_id}")

    flash(
        f'{project["company_name"]}：'
        f'案件「{project["project_name"]}」の会社案内を公開しました。'
    )
    return redirect(f"/admin/project/{project_id}")



@app.post("/project/<int:project_id>/quotation/upload")
@login_required
def upload_project_quotation(project_id):
    uploaded = request.files.get("file")

    project, error = save_project_document(
        project_id=project_id,
        uploaded=uploaded,
        document_type="quotation",
        title="PDF見積書",
        target_filename="quotation.pdf",
        allowed_extensions={"pdf"},
        mime_type="application/pdf",
    )

    if error:
        flash(error)

        if project is None:
            return admin_redirect()

        return redirect(f"/admin/project/{project_id}")

    flash(
        f'{project["company_name"]}：'
        f'案件「{project["project_name"]}」の見積書を登録しました。'
    )
    return redirect(f"/admin/project/{project_id}")


@app.post("/project/add")
@login_required
def add_project():
    client_id = request.form.get("client_id", "").strip()
    project_name = request.form.get("project_name", "").strip()
    amount_raw = request.form.get("amount", "").strip()
    status = request.form.get("status", "未対応").strip()
    description = request.form.get("description", "").strip()

    allowed_statuses = {
        "未対応",
        "提案中",
        "商談中",
        "見積提出",
        "受注",
        "失注",
        "保留",
    }

    if not client_id.isdigit():
        flash("顧客を選択してください。")
        return admin_redirect()

    if not project_name:
        flash("案件名を入力してください。")
        return admin_redirect()

    if status not in allowed_statuses:
        flash("営業ステータスが不正です。")
        return admin_redirect()

    try:
        amount = int(amount_raw.replace(",", "")) if amount_raw else 0
    except ValueError:
        flash("予定金額は数字で入力してください。")
        return admin_redirect()

    if amount < 0:
        flash("予定金額は0円以上で入力してください。")
        return admin_redirect()

    now = datetime.now().isoformat(timespec="seconds")

    with get_db() as db:
        client = db.execute(
            "SELECT id, company_name FROM clients WHERE id = ?",
            (int(client_id),)
        ).fetchone()

        if not client:
            flash("対象顧客が見つかりません。")
            return admin_redirect()

        db.execute(
            """
            INSERT INTO projects (
                client_id,
                project_name,
                amount,
                status,
                description,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(client_id),
                project_name,
                amount,
                status,
                description,
                now,
                now,
            )
        )
        db.commit()

    flash(
        f'{client["company_name"]}：'
        f'案件「{project_name}」を登録しました。'
    )
    return admin_redirect()


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
