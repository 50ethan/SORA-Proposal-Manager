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


CLIENT_LOGIN_HTML = """
<!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>提案書ログイン | SORA Proposal Manager</title>
    <style>
        * { box-sizing: border-box; }

        body {
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            padding: 20px;
            background: linear-gradient(135deg, #111827, #1d4ed8);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }

        .card {
            width: min(430px, 100%);
            padding: 32px;
            background: #fff;
            border-radius: 18px;
            box-shadow: 0 24px 60px rgba(0, 0, 0, .3);
        }

        .brand {
            margin-bottom: 6px;
            font-size: 13px;
            font-weight: 800;
            letter-spacing: .12em;
            color: #2563eb;
        }

        h1 {
            margin: 0 0 8px;
            font-size: 26px;
        }

        .description {
            margin: 0 0 24px;
            color: #6b7280;
        }

        label {
            display: block;
            margin: 16px 0 6px;
            font-weight: 700;
        }

        input {
            width: 100%;
            padding: 12px;
            border: 1px solid #cbd5e1;
            border-radius: 9px;
            font-size: 16px;
        }

        button {
            width: 100%;
            margin-top: 22px;
            padding: 13px;
            border: 0;
            border-radius: 9px;
            background: #111827;
            color: #fff;
            font-size: 16px;
            font-weight: 700;
            cursor: pointer;
        }

        .error {
            margin-bottom: 16px;
            padding: 12px;
            border-radius: 8px;
            background: #fee2e2;
            color: #991b1b;
        }
    </style>
</head>
<body>
    <main class="card">
        <div class="brand">SORA-NEXTAI</div>
        <h1>提案書ログイン</h1>
        <p class="description">
            発行されたログインIDとパスワードを入力してください。
        </p>

        {% with messages = get_flashed_messages() %}
            {% if messages %}
                {% for message in messages %}
                    <div class="error">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <form method="post">
            <label>ログインID</label>
            <input
                type="text"
                name="login_id"
                autocomplete="username"
                required
            >

            <label>パスワード</label>
            <input
                type="password"
                name="password"
                autocomplete="current-password"
                required
            >

            <button type="submit">提案書を開く</button>
        </form>
    </main>
</body>
</html>
"""


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


HTML = """
<!doctype html>
<html lang="ja">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>SORA Proposal Manager</title>

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            font-family:
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                sans-serif;
            margin: 0;
            background: #f4f6f8;
            color: #1f2937;
        }

        .header {
            background: #111827;
            color: white;
            padding: 22px 0;
        }

        .header-inner,
        .wrap {
            width: min(1100px, 92%);
            margin: auto;
        }

        .header h1 {
            margin: 0;
            font-size: 24px;
        }

        .header p {
            margin: 6px 0 0;
            color: #d1d5db;
        }

        .header-layout {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 24px;
        }

        .product-subtitle {
            font-size: 15px;
            font-weight: 700;
            color: #bfdbfe !important;
        }

        .product-catch {
            font-size: 13px;
            color: #94a3b8 !important;
        }

        .logout-button {
            flex-shrink: 0;
            padding: 10px 16px;
            border: 1px solid rgba(255, 255, 255, .25);
            border-radius: 9px;
            color: white;
            text-decoration: none;
            font-weight: 700;
        }

        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .metric-card {
            display: flex;
            align-items: center;
            gap: 16px;
            min-height: 128px;
            padding: 22px;
            background: white;
            border: 1px solid #e5e7eb;
            border-radius: 14px;
            box-shadow: 0 4px 18px rgba(0, 0, 0, .06);
        }

        .warning-card {
            border-color: #fed7aa;
            background: #fffaf5;
        }

        .metric-icon {
            display: grid;
            place-items: center;
            width: 48px;
            height: 48px;
            flex-shrink: 0;
            border-radius: 12px;
            background: #eff6ff;
            font-size: 24px;
        }

        .metric-label {
            margin-bottom: 4px;
            color: #64748b;
            font-size: 13px;
            font-weight: 700;
        }

        .metric-number {
            color: #0f172a;
            font-size: 34px;
            font-weight: 800;
            line-height: 1;
        }

        .metric-number span {
            margin-left: 3px;
            color: #64748b;
            font-size: 14px;
            font-weight: 700;
        }

        .recent-card {
            border-top: 4px solid #2563eb;
        }

        .section-heading {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 10px;
        }

        .section-kicker {
            margin: 0 0 4px;
            color: #2563eb;
            font-size: 11px;
            font-weight: 800;
            letter-spacing: .14em;
        }

        .activity-list {
            border-top: 1px solid #e5e7eb;
        }

        .activity-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            padding: 15px 0;
            border-bottom: 1px solid #e5e7eb;
        }

        .activity-company {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .activity-dot {
            width: 10px;
            height: 10px;
            flex-shrink: 0;
            border-radius: 50%;
            background: #22c55e;
            box-shadow: 0 0 0 5px #dcfce7;
        }

        .activity-ip {
            margin-top: 4px;
            color: #94a3b8;
            font-size: 12px;
        }

        .activity-item time {
            color: #64748b;
            font-size: 13px;
            white-space: nowrap;
        }

        .empty-state {
            padding: 28px 0;
            color: #64748b;
            text-align: center;
        }

        .wrap {
            padding: 30px 0 60px;
        }

        .card {
            background: white;
            border-radius: 14px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 4px 18px rgba(0, 0, 0, .07);
        }

        h2 {
            margin-top: 0;
        }

        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
        }

        label {
            display: block;
            font-weight: 700;
            margin-bottom: 6px;
        }

        input,
        select {
            width: 100%;
            padding: 11px;
            border: 1px solid #c7cdd4;
            border-radius: 8px;
            font-size: 16px;
        }

        button,
        .button {
            display: inline-block;
            margin-top: 18px;
            background: #111827;
            color: white;
            border: 0;
            border-radius: 8px;
            padding: 11px 18px;
            cursor: pointer;
            text-decoration: none;
            font-size: 14px;
        }

        .danger {
            background: #b91c1c;
            margin: 0;
            padding: 8px 12px;
        }

        .upload-button {
            background: #0369a1;
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        th,
        td {
            text-align: left;
            border-bottom: 1px solid #e5e7eb;
            padding: 13px 8px;
            vertical-align: middle;
        }

        th {
            background: #f9fafb;
        }

        .flash {
            padding: 16px;
            border-radius: 9px;
            background: #dcfce7;
            border: 1px solid #86efac;
            margin-bottom: 18px;
            white-space: pre-line;
            font-weight: 600;
        }

        .note {
            color: #6b7280;
            font-size: 14px;
        }

        .status-ok {
            color: #15803d;
            font-weight: 700;
        }

        .status-none {
            color: #9a3412;
            font-weight: 700;
        }

        .actions {
            display: flex;
            gap: 8px;
            align-items: center;
        }

        .actions form {
            margin: 0;
        }

        @media (max-width: 900px) {
            .dashboard-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }

        @media (max-width: 760px) {
            .header-layout {
                align-items: flex-start;
                flex-direction: column;
            }

            .dashboard-grid {
                grid-template-columns: 1fr;
            }

            .grid {
                grid-template-columns: 1fr;
            }

            table {
                display: block;
                overflow-x: auto;
                white-space: nowrap;
            }
        }
    </style>
</head>

<body>
    <header class="header">
        <div class="header-inner header-layout">
            <div>
                <h1>SORA Proposal Manager</h1>
                <p class="product-subtitle">
                    AI営業支援・提案書DXプラットフォーム
                </p>
                <p class="product-catch">
                    提案から契約まで、AIが営業をサポート。
                </p>
            </div>

            <a class="logout-button" href="/admin/logout">
                ログアウト
            </a>
        </div>
    </header>

    <main class="wrap">
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                {% for message in messages %}
                    <div class="flash">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}


        <section class="dashboard-grid">
            <article class="metric-card">
                <div class="metric-icon">👥</div>
                <div>
                    <div class="metric-label">登録顧客数</div>
                    <div class="metric-number">
                        {{ dashboard.total_clients }}
                        <span>社</span>
                    </div>
                </div>
            </article>

            <article class="metric-card">
                <div class="metric-icon">📄</div>
                <div>
                    <div class="metric-label">公開提案書数</div>
                    <div class="metric-number">
                        {{ dashboard.total_proposals }}
                        <span>件</span>
                    </div>
                </div>
            </article>

            <article class="metric-card">
                <div class="metric-icon">👀</div>
                <div>
                    <div class="metric-label">今日の閲覧数</div>
                    <div class="metric-number">
                        {{ dashboard.today_views }}
                        <span>回</span>
                    </div>
                </div>
            </article>

            <article class="metric-card warning-card">
                <div class="metric-icon">📬</div>
                <div>
                    <div class="metric-label">未閲覧顧客</div>
                    <div class="metric-number">
                        {{ dashboard.unread_clients }}
                        <span>社</span>
                    </div>
                </div>
            </article>
        </section>

        <section class="card recent-card">
            <div class="section-heading">
                <div>
                    <p class="section-kicker">RECENT ACTIVITY</p>
                    <h2>最近の閲覧履歴</h2>
                </div>
            </div>

            {% if dashboard.recent_views %}
                <div class="activity-list">
                    {% for view in dashboard.recent_views %}
                        <div class="activity-item">
                            <div class="activity-company">
                                <div class="activity-dot"></div>
                                <div>
                                    <strong>{{ view.company_name }}</strong>
                                    <div class="activity-ip">
                                        IP：{{ view.ip_address or "取得なし" }}
                                    </div>
                                </div>
                            </div>

                            <time>{{ view.viewed_at }}</time>
                        </div>
                    {% endfor %}
                </div>
            {% else %}
                <div class="empty-state">
                    閲覧履歴はまだありません。
                </div>
            {% endif %}
        </section>

        <section class="card">
            <h2>新規顧客登録</h2>

            <form method="post" action="/admin/client/add">
                <div class="grid">
                    <div>
                        <label>会社名</label>
                        <input
                            type="text"
                            name="company_name"
                            placeholder="株式会社タスク"
                            required
                        >
                    </div>

                    <div>
                        <label>担当者名</label>
                        <input
                            type="text"
                            name="contact_name"
                            placeholder="大関 智春"
                        >
                    </div>

                    <div>
                        <label>メールアドレス</label>
                        <input
                            type="email"
                            name="email"
                            placeholder="example@example.com"
                        >
                    </div>

                    <div>
                        <label>URL用英数字</label>
                        <input
                            type="text"
                            name="slug"
                            placeholder="task"
                            required
                        >
                    </div>
                </div>

                <p class="note">
                    登録すると、顧客用ログインIDとパスワードを自動発行します。
                </p>

                <button type="submit">顧客を登録する</button>
            </form>
        </section>

        <section class="card">
            <h2>提案書アップロード</h2>

            {% if clients %}
                <form
                    method="post"
                    action="/admin/upload"
                    enctype="multipart/form-data"
                >
                    <label>顧客</label>
                    <select name="client_id" required>
                        <option value="">選択してください</option>

                        {% for client in clients %}
                            <option value="{{ client.id }}">
                                {{ client.company_name }}
                            </option>
                        {% endfor %}
                    </select>

                    <label style="margin-top:16px;">HTMLファイル</label>
                    <input
                        type="file"
                        name="file"
                        accept=".html,.htm"
                        required
                    >

                    <button class="upload-button" type="submit">
                        提案書をアップロード
                    </button>
                </form>
            {% else %}
                <p class="note">
                    先に顧客を登録してください。
                </p>
            {% endif %}
        </section>

        <section class="card">
            <h2>顧客一覧</h2>

            <table>
                <thead>
                    <tr>
                        <th>会社名</th>
                        <th>担当者</th>
                        <th>ログインID</th>
                        <th>提案書</th>
                        <th>操作</th>
                    </tr>
                </thead>

                <tbody>
                    {% for client in clients %}
                        <tr>
                            <td>
                                <strong>{{ client.company_name }}</strong><br>
                                <span class="note">{{ client.email or "" }}</span>
                            </td>

                            <td>{{ client.contact_name or "未登録" }}</td>

                            <td>
                                <code>{{ client.login_id }}</code>
                            </td>

                            <td>
                                {% if client.proposal_updated_at %}
                                    <span class="status-ok">公開中</span><br>

                                    <a
                                        href="https://proposal.sora-nextai.com/{{ client.slug }}/"
                                        target="_blank"
                                    >
                                        提案書を見る
                                    </a>
                                {% else %}
                                    <span class="status-none">未登録</span>
                                {% endif %}
                            </td>

                            <td>
                                <div class="actions">
                                    <form
                                        method="post"
                                        action="/admin/client/{{ client.id }}/reset-password"
                                    >
                                        <button type="submit">
                                            PW再発行
                                        </button>
                                    </form>

                                    <form
                                        method="post"
                                        action="/admin/client/{{ client.id }}/delete"
                                        onsubmit="return confirm('顧客と提案書を削除しますか？');"
                                    >
                                        <button class="danger" type="submit">
                                            削除
                                        </button>
                                    </form>
                                </div>
                            </td>
                        </tr>
                    {% else %}
                        <tr>
                            <td colspan="5">顧客はまだ登録されていません。</td>
                        </tr>
                    {% endfor %}
                </tbody>
            </table>
        </section>
    </main>
</body>
</html>
"""




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

    return render_template_string(CLIENT_LOGIN_HTML)


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

    return render_template_string(
        HTML,
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
