from pathlib import Path
from datetime import datetime
from flask import Flask, request, redirect, render_template_string, flash
from werkzeug.security import generate_password_hash
import os
import re
import secrets
import shutil
import sqlite3
import string

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "sora-proposal-manager-change-this-secret-key"
)

BASE_DIR = Path("/opt/proposal-manager")
DATABASE_PATH = BASE_DIR / "proposal_manager.db"
PROPOSAL_ROOT = Path("/var/www/proposal")
ALLOWED_EXTENSIONS = {"html", "htm"}


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

        @media (max-width: 760px) {
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
        <div class="header-inner">
            <h1>SORA Proposal Manager</h1>
            <p>顧客・提案書管理</p>
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


@app.get("/")
def index():
    return render_template_string(
        HTML,
        clients=get_clients()
    )


@app.post("/client/add")
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
