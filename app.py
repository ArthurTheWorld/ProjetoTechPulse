"""
app.py — TechPulse Jobs (tudo em um arquivo)
Rodar: python app.py
Abrir: http://localhost:5000
"""

from flask import Flask, request, jsonify, session, render_template_string
from functools import wraps
import time, requests, feedparser, sqlite3, hashlib, os
from datetime import datetime

app = Flask(__name__)
app.secret_key = "techpulse-2024-secret"

# ─── Banco de dados SQLite ───────────────────────────────────────────────────
DB_PATH   = "techpulse.db"
CACHE_TTL = 300  # segundos entre cada novo scraping

def get_db():
    """Retorna conexão com row_factory para dicts."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()

def init_db():
    """Cria tabelas e usuários padrão na primeira execução."""
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            email      TEXT    UNIQUE NOT NULL,
            senha_hash TEXT    NOT NULL,
            criado_em  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo      TEXT NOT NULL,
            empresa     TEXT,
            url         TEXT UNIQUE NOT NULL,
            fonte       TEXT,
            data        TEXT,
            salario     TEXT,
            senioridade TEXT,
            coletado_em TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS jobs_tags (
            job_id  INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
            tag     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scrape_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            fonte      TEXT,
            total      INTEGER,
            executado  TEXT DEFAULT (datetime('now'))
        );
    """)

    # Usuários padrão (só cria se não existirem)
    usuarios = [
        ("admin@tech.com",  "123456"),
        ("aluno@facul.com", "senha123"),
    ]
    for email, senha in usuarios:
        c.execute(
            "INSERT OR IGNORE INTO users (email, senha_hash) VALUES (?, ?)",
            (email, hash_senha(senha))
        )

    conn.commit()
    conn.close()
    print("Banco inicializado em", DB_PATH)

def salvar_jobs(jobs: list):
    """Insere vagas novas no banco (ignora duplicatas pela URL)."""
    conn = get_db()
    c = conn.cursor()
    inseridos = 0
    for j in jobs:
        try:
            c.execute("""
                INSERT OR IGNORE INTO jobs (titulo, empresa, url, fonte, data, salario, senioridade)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (j["titulo"], j["empresa"], j["url"], j["fonte"],
                    j["data"], j["salario"], j["senioridade"]))
            if c.rowcount:
                job_id = c.lastrowid
                for tag in j.get("tags", []):
                    c.execute("INSERT INTO jobs_tags (job_id, tag) VALUES (?, ?)", (job_id, tag))
                inseridos += 1
        except Exception as e:
            print(f"[DB] erro ao salvar vaga: {e}")

    # Registra no log
    fontes = {}
    for j in jobs:
        fontes[j["fonte"]] = fontes.get(j["fonte"], 0) + 1
    for fonte, total in fontes.items():
        c.execute("INSERT INTO scrape_log (fonte, total) VALUES (?, ?)", (fonte, total))

    conn.commit()
    conn.close()
    print(f"💾 {inseridos} novas vagas salvas no banco")
    return inseridos

def carregar_jobs_db(fonte="", q="", senioridade="") -> list:
    """Carrega vagas do banco com filtros opcionais."""
    conn = get_db()
    c = conn.cursor()

    sql = """
        SELECT j.id, j.titulo, j.empresa, j.url, j.fonte,
               j.data, j.salario, j.senioridade,
               GROUP_CONCAT(t.tag, '|||') as tags_raw
        FROM jobs j
        LEFT JOIN jobs_tags t ON t.job_id = j.id
        WHERE 1=1
    """
    params = []
    if fonte:
        sql += " AND j.fonte = ?"
        params.append(fonte)
    if senioridade:
        sql += " AND j.senioridade = ?"
        params.append(senioridade)
    if q:
        sql += " AND (j.titulo LIKE ? OR j.empresa LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]

    sql += " GROUP BY j.id ORDER BY j.coletado_em DESC LIMIT 200"

    rows = c.execute(sql, params).fetchall()
    conn.close()

    result = []
    for row in rows:
        tags = [t for t in (row["tags_raw"] or "").split("|||") if t]
        result.append({
            "titulo":      row["titulo"],
            "empresa":     row["empresa"] or "—",
            "url":         row["url"],
            "fonte":       row["fonte"],
            "data":        row["data"] or "",
            "salario":     row["salario"] or "—",
            "senioridade": row["senioridade"] or "Pleno",
            "tags":        tags,
        })
    return result

def stats_db() -> dict:
    """Calcula métricas direto do banco."""
    conn = get_db()
    c = conn.cursor()

    total = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    fontes = {}
    for row in c.execute("SELECT fonte, COUNT(*) as n FROM jobs GROUP BY fonte"):
        fontes[row["fonte"]] = row["n"]

    sen_count = {}
    for row in c.execute("SELECT senioridade, COUNT(*) as n FROM jobs GROUP BY senioridade"):
        sen_count[row["senioridade"]] = row["n"]

    top_tags = []
    for row in c.execute("""
        SELECT tag, COUNT(*) as n FROM jobs_tags
        GROUP BY tag ORDER BY n DESC LIMIT 8
    """):
        top_tags.append({"tag": row["tag"], "count": row["n"]})

    conn.close()
    return {"total": total, "fontes": fontes, "senioridades": sen_count, "top_tags": top_tags}

# ─── Cache leve (evita bater o DB a cada request) ────────────────────────────
_last_scrape = 0

# ─── Scraping ────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, */*",
}
TODAY = datetime.today().strftime("%Y-%m-%d")

# ─── Whitelist de tecnologias ─────────────────────────────────────────────────
TECH_TAGS = {
    # Linguagens
    "python","javascript","typescript","java","kotlin","swift","go","golang",
    "rust","c","c++","c#","ruby","php","scala","r","dart","elixir","haskell",
    "lua","perl","shell","bash","powershell","groovy","clojure",
    # Frontend
    "react","vue","angular","svelte","nextjs","nuxt","html","css","sass",
    "tailwind","webpack","vite","jquery","redux","remix","htmx",
    # Backend / frameworks
    "node","nodejs","express","fastapi","flask","django","spring","rails",
    "laravel","nestjs","gin","fiber","phoenix","asp.net","dotnet",
    # Mobile
    "react native","flutter","android","ios","swiftui",
    # Banco de dados
    "postgresql","postgres","mysql","mongodb","redis","sqlite","oracle",
    "cassandra","dynamodb","elasticsearch","neo4j","supabase","firebase",
    "mariadb","sql server","cockroachdb",
    # Cloud & devops
    "aws","gcp","azure","docker","kubernetes","k8s","terraform","ansible",
    "jenkins","github actions","gitlab ci","linux","nginx","kafka",
    "rabbitmq","airflow","spark","hadoop","flink",
    # Data & ML
    "machine learning","deep learning","tensorflow","pytorch","keras",
    "scikit-learn","pandas","numpy","data science","mlops","llm","nlp",
    "hugging face","langchain","openai","sql","dbt","tableau","powerbi",
    # Outros
    "graphql","rest","microservices","grpc","blockchain","web3",
    "solidity","unity","git","github","gitlab","agile","scrum",
}

def filter_tech_tags(tags: list) -> list:
    """Mantém só tags de linguagens/tecnologias, descarta 'remote', cidades, etc."""
    result = []
    for tag in tags:
        clean = tag.strip().lower()
        if clean in TECH_TAGS:
            result.append(tag.strip())
            continue
        # variações: node.js→node, react.js→react, vue.js→vue
        normalized = clean.replace(".js","").replace(".","").replace("-","").replace(" ","")
        for tech in TECH_TAGS:
            if normalized == tech.replace(".","").replace("-","").replace(" ",""):
                result.append(tag.strip())
                break
    seen, unique = set(), []
    for t in result:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique.append(t)
    return unique[:5]



def detect_seniority(titulo: str) -> str:
    """Detecta o nível de senioridade pelo título da vaga."""
    t = titulo.lower()

    intern_kw  = ["intern","internship","estági","estagio","trainee","apprentice","working student"]
    junior_kw  = ["junior","júnior","jr","jr.","entry level","entry-level","associate","graduate","jr "]
    senior_kw  = ["senior","sênior","sr","sr.","sr ","staff","principal","lead","tech lead","squad lead"]
    manager_kw = ["manager","director","head of","vp ","vice president","cto","cpo","ceo","engineering manager","em "]

    if any(k in t for k in manager_kw): return "Liderança"
    if any(k in t for k in senior_kw):  return "Sênior"
    if any(k in t for k in junior_kw):  return "Júnior"
    if any(k in t for k in intern_kw):  return "Estágio"
    return "Pleno"


def scrape_remoteok():
    jobs = []
    try:
        r = requests.get("https://remoteok.com/api", headers=HEADERS, timeout=10)
        r.raise_for_status()
        for item in r.json()[1:]:
            if not isinstance(item, dict) or not item.get("position"):
                continue
            url = item.get("url", "")
            if not url:
                slug   = item.get("slug", "")
                job_id = str(item.get("id", ""))
                url = (f"https://remoteok.com/remote-jobs/{job_id}-{slug}" if slug and job_id
                       else f"https://remoteok.com/remote-jobs/{job_id}" if job_id else "")
            if not url:
                continue
            jobs.append({
                "titulo":  item.get("position", ""),
                "empresa": item.get("company", "—"),
                "tags":    [t for t in item.get("tags", []) if t][:4],
                "url":     url,
                "fonte":   "RemoteOK",
                "data":    (item.get("date") or TODAY)[:10],
                "salario": item.get("salary") or "—",
            })
            if len(jobs) >= 25:
                break
    except Exception as e:
        print(f"[RemoteOK] {e}")
    return jobs


def scrape_remotive():
    jobs = []
    for cat in ["software-dev", "devops-sysadmin", "data"]:
        try:
            r = requests.get(
                f"https://remotive.com/api/remote-jobs?category={cat}&limit=15",
                headers=HEADERS, timeout=10)
            r.raise_for_status()
            for item in r.json().get("jobs", []):
                url = item.get("url") or ""
                if not url:
                    continue
                jobs.append({
                    "titulo":  item.get("title", ""),
                    "empresa": item.get("company_name", "—"),
                    "tags":    [t.strip() for t in item.get("tags", []) if t][:4],
                    "url":     url,
                    "fonte":   "Remotive",
                    "data":    (item.get("publication_date") or TODAY)[:10],
                    "salario": item.get("salary") or "—",
                })
        except Exception as e:
            print(f"[Remotive/{cat}] {e}")
    return jobs


def scrape_arbeitnow():
    jobs = []
    try:
        r = requests.get("https://www.arbeitnow.com/api/job-board-api",
                         headers=HEADERS, timeout=10)
        r.raise_for_status()
        for item in r.json().get("data", [])[:25]:
            url = item.get("url") or (
                f"https://www.arbeitnow.com/jobs/{item.get('slug','')}"
                if item.get("slug") else "")
            if not url:
                continue
            jobs.append({
                "titulo":  item.get("title", ""),
                "empresa": item.get("company_name", "—"),
                "tags":    [t for t in item.get("tags", []) if t][:4],
                "url":     url,
                "fonte":   "Arbeitnow",
                "data":    (item.get("created_at") or TODAY)[:10],
                "salario": "—",
            })
    except Exception as e:
        print(f"[Arbeitnow] {e}")
    return jobs


def scrape_jobicy():
    jobs = []
    for feed_url in [
        "https://jobicy.com/?feed=job_feed&job_categories=dev&job_types=full-time",
        "https://jobicy.com/?feed=job_feed&job_categories=data-science&job_types=full-time",
    ]:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:15]:
                url = entry.get("link", "").strip()
                if not url or not url.startswith("http"):
                    continue
                tags = list({tag.get("term", "").strip()
                             for tag in entry.get("tags", [])
                             if tag.get("term", "").strip()})
                jobs.append({
                    "titulo":  entry.get("title", "").strip(),
                    "empresa": (entry.get("author") or "—").strip(),
                    "tags":    tags[:4],
                    "url":     url,
                    "fonte":   "Jobicy",
                    "data":    (entry.get("published") or TODAY)[:10],
                    "salario": "—",
                })
        except Exception as e:
            print(f"[Jobicy] {e}")
    return jobs


def scrape_themuse():
    """
    The Muse — API pública gratuita, sem chave necessária.
    Foco em entry-level / junior. Inclui vagas BR e internacionais.
    Docs: https://www.themuse.com/developers/api/v2
    """
    jobs = []
    try:
        # level=entry+level é o filtro nativo de júnior/entry da API
        r = requests.get(
            "https://www.themuse.com/api/public/jobs"
            "?category=Engineering&level=Entry+Level&page=1&descended=true",
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        for item in r.json().get("results", [])[:30]:
            # URL direta: https://www.themuse.com/jobs/{company}/{slug}
            url = item.get("refs", {}).get("landing_page", "")
            if not url:
                continue
            # tags: extraídas do campo "categories" e "levels"
            tags = [c.get("name","") for c in item.get("categories",[]) if c.get("name")]
            # empresa
            company = item.get("company", {}).get("name", "—")
            # data
            pub = (item.get("publication_date") or TODAY)[:10]
            jobs.append({
                "titulo":  item.get("name", ""),
                "empresa": company,
                "tags":    tags[:4],
                "url":     url,
                "fonte":   "TheMuse",
                "data":    pub,
                "salario": "—",
            })
        # segunda página com mais categorias para aumentar volume
        r2 = requests.get(
            "https://www.themuse.com/api/public/jobs"
            "?category=Data+Science&level=Entry+Level&page=1",
            headers=HEADERS, timeout=10
        )
        r2.raise_for_status()
        for item in r2.json().get("results", [])[:20]:
            url = item.get("refs", {}).get("landing_page", "")
            if not url:
                continue
            tags = [c.get("name","") for c in item.get("categories",[]) if c.get("name")]
            jobs.append({
                "titulo":  item.get("name", ""),
                "empresa": item.get("company", {}).get("name", "—"),
                "tags":    tags[:4],
                "url":     url,
                "fonte":   "TheMuse",
                "data":    (item.get("publication_date") or TODAY)[:10],
                "salario": "—",
            })
    except Exception as e:
        print(f"[TheMuse] {e}")
    return jobs


def get_all_jobs():
    all_jobs = scrape_remoteok() + scrape_remotive() + scrape_arbeitnow() + scrape_jobicy() + scrape_themuse()
    cleaned = []
    for j in all_jobs:
        titulo = (j.get("titulo") or "").strip()
        url    = (j.get("url") or "").strip()
        if not titulo or not url or url == "#":
            continue
        cleaned.append({
            "titulo":  titulo,
            "empresa": (j.get("empresa") or "—").strip(),
            "tags":    filter_tech_tags(j.get("tags") or []),
            "url":     url,
            "fonte":   j.get("fonte", "?"),
            "data":    (j.get("data") or TODAY)[:10],
            "salario": (j.get("salario") or "—").strip(),
            "senioridade": detect_seniority(titulo),
        })
    print(f"{len(cleaned)} vagas coletadas")
    return cleaned


def refresh_cache():
    """Roda scraping se passou o TTL; salva novas vagas no banco."""
    global _last_scrape
    if time.time() - _last_scrape > CACHE_TTL:
        print("🔄 Executando scraping...")
        jobs = get_all_jobs()
        salvar_jobs(jobs)
        _last_scrape = time.time()


# ─── Auth decorator ──────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return jsonify({"error": "Não autenticado"}), 401
        return f(*args, **kwargs)
    return decorated


# ─── Rotas da API ────────────────────────────────────────────────────────────
@app.route("/login", methods=["POST"])
def login():
    d = request.get_json()
    email = (d.get("email") or "").strip().lower()
    senha = d.get("senha") or ""
    conn  = get_db()
    user  = conn.execute(
        "SELECT * FROM users WHERE email=? AND senha_hash=?",
        (email, hash_senha(senha))
    ).fetchone()
    conn.close()
    if user:
        session["user"] = email
        return jsonify({"ok": True, "user": email})
    return jsonify({"ok": False, "error": "Credenciais inválidas"}), 401


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    if "user" in session:
        return jsonify({"logged": True, "user": session["user"]})
    return jsonify({"logged": False})


@app.route("/api/jobs")
@login_required
def jobs():
    refresh_cache()
    fonte      = request.args.get("fonte", "").strip()
    q          = request.args.get("q", "").strip().lower()
    senioridade= request.args.get("senioridade", "").strip()
    return jsonify(carregar_jobs_db(fonte=fonte, q=q, senioridade=senioridade))


@app.route("/api/stats")
@login_required
def stats():
    refresh_cache()
    return jsonify(stats_db())


# ─── Rota principal: serve o frontend ────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML)


# ─── HTML embutido ───────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TechPulse Jobs</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:#09090b; --surface:#111113; --surface2:#18181b;
  --border:#27272a; --border2:#3f3f46;
  --text:#fafafa; --muted:#71717a; --muted2:#52525b;
  --lime:#a855f7; --lime-dim:rgba(168,85,247,.12); --lime-glow:rgba(168,85,247,.3);
  --red:#f87171; --font-head:'Syne',sans-serif; --font-mono:'DM Mono',monospace; --r:8px;
}
body { font-family:var(--font-mono); background:var(--bg); color:var(--text); min-height:100vh; overflow-x:hidden; }
body::before { content:''; position:fixed; inset:0; z-index:0; pointer-events:none;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E"); opacity:.4; }

/* LOGIN */
#login-screen { position:relative; z-index:1; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:1.5rem; }
#login-screen::after { content:''; position:fixed; inset:0; z-index:0; pointer-events:none;
  background-image:linear-gradient(rgba(168,85,247,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(168,85,247,.04) 1px,transparent 1px);
  background-size:40px 40px; }
.login-wrap { position:relative; z-index:1; width:100%; max-width:400px; animation:fadeUp .5s ease both; }
@keyframes fadeUp { from{opacity:0;transform:translateY(16px)} to{opacity:1;transform:translateY(0)} }
.login-eyebrow { font-family:var(--font-mono); font-size:.68rem; letter-spacing:.18em; color:var(--lime); text-transform:uppercase; margin-bottom:.75rem; }
.login-heading { font-family:var(--font-head); font-size:2.4rem; font-weight:800; line-height:1; margin-bottom:.4rem; }
.login-sub { font-size:.78rem; color:var(--muted); margin-bottom:2rem; line-height:1.6; }
.login-box { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:2rem; }
.field { margin-bottom:1.1rem; }
.field label { display:block; font-size:.7rem; letter-spacing:.1em; text-transform:uppercase; color:var(--muted); margin-bottom:.4rem; }
.field input { width:100%; padding:10px 12px; background:var(--bg); border:1px solid var(--border); border-radius:var(--r); color:var(--text); font-family:var(--font-mono); font-size:.85rem; transition:border-color .2s; }
.field input:focus { outline:none; border-color:var(--lime); }
.btn-login { width:100%; padding:11px; background:var(--lime); border:none; border-radius:var(--r); color:#fff; font-family:var(--font-head); font-size:.95rem; font-weight:700; cursor:pointer; transition:opacity .15s,transform .1s; margin-top:.25rem; }
.btn-login:hover { opacity:.9; transform:translateY(-1px); }
.login-error { font-size:.75rem; color:var(--red); margin-top:.6rem; display:none; }
.login-hint { font-size:.7rem; color:var(--muted2); margin-top:1.25rem; text-align:center; line-height:1.7; }
.login-hint code { background:var(--surface2); border:1px solid var(--border); border-radius:4px; padding:1px 5px; color:var(--lime); }

/* APP */
#app { display:none; position:relative; z-index:1; }
.layout { display:flex; min-height:100vh; }
.sidebar { width:230px; flex-shrink:0; background:var(--surface); border-right:1px solid var(--border); display:flex; flex-direction:column; padding:1.25rem .875rem; position:fixed; top:0; bottom:0; left:0; overflow-y:auto; z-index:10; }
.sb-logo { font-family:var(--font-head); font-weight:800; font-size:1.05rem; color:var(--text); display:flex; align-items:center; gap:8px; padding:0 .5rem; margin-bottom:1.75rem; }
.sb-logo-dot { width:8px; height:8px; border-radius:50%; background:var(--lime); box-shadow:0 0 8px var(--lime-glow); animation:glow 2s ease-in-out infinite; }
@keyframes glow { 0%,100%{box-shadow:0 0 6px var(--lime-glow)} 50%{box-shadow:0 0 14px var(--lime)} }
.sb-section { font-size:.62rem; letter-spacing:.12em; text-transform:uppercase; color:var(--muted2); padding:.9rem .5rem .3rem; }
.sb-item { display:flex; align-items:center; gap:9px; padding:7px 10px; border-radius:var(--r); font-size:.8rem; color:var(--muted); cursor:pointer; transition:all .15s; text-decoration:none; margin-bottom:1px; border:1px solid transparent; }
.sb-item:hover { background:var(--surface2); color:var(--text); }
.sb-item.active { background:var(--lime-dim); color:var(--lime); border-color:rgba(168,85,247,.2); }
.sb-item svg { flex-shrink:0; }
.fonte-count { margin-left:auto; font-size:.65rem; padding:1px 6px; border-radius:20px; background:var(--surface2); color:var(--muted); }
.sb-bottom { margin-top:auto; border-top:1px solid var(--border); padding-top:.875rem; }
.sb-user { display:flex; align-items:center; gap:9px; padding:6px 10px; margin-bottom:6px; }
.sb-avatar { width:28px; height:28px; border-radius:50%; background:var(--lime-dim); border:1px solid rgba(168,85,247,.3); color:var(--lime); font-family:var(--font-head); font-size:.7rem; font-weight:700; display:flex; align-items:center; justify-content:center; flex-shrink:0; }
.sb-email { font-size:.72rem; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.btn-logout { width:100%; padding:6px; background:transparent; border:1px solid var(--border); border-radius:var(--r); color:var(--muted); font-family:var(--font-mono); font-size:.75rem; cursor:pointer; transition:all .15s; }
.btn-logout:hover { border-color:var(--red); color:var(--red); }
.main { margin-left:230px; flex:1; min-width:0; }
.topbar { position:sticky; top:0; z-index:5; background:rgba(9,9,11,.85); backdrop-filter:blur(12px); border-bottom:1px solid var(--border); padding:.875rem 1.5rem; display:flex; align-items:center; justify-content:space-between; }
.topbar-left { display:flex; align-items:center; gap:10px; }
.topbar-title { font-family:var(--font-head); font-weight:700; font-size:.9rem; }
.live-pill { display:flex; align-items:center; gap:5px; background:var(--lime-dim); border:1px solid rgba(168,85,247,.2); border-radius:20px; padding:2px 9px; font-size:.65rem; letter-spacing:.08em; text-transform:uppercase; color:var(--lime); }
.live-dot { width:5px; height:5px; border-radius:50%; background:var(--lime); animation:glow 1.8s infinite; }
.search-input { background:var(--surface); border:1px solid var(--border); border-radius:var(--r); padding:7px 12px; color:var(--text); font-family:var(--font-mono); font-size:.78rem; width:220px; transition:border-color .2s; }
.search-input:focus { outline:none; border-color:var(--lime); }
.search-input::placeholder { color:var(--muted2); }
.content { padding:1.5rem; }
.metrics { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:1.5rem; }
.metric { background:var(--surface); border:1px solid var(--border); border-radius:var(--r); padding:.9rem 1rem; transition:border-color .2s; }
.metric:hover { border-color:var(--border2); }
.metric-label { font-size:.65rem; letter-spacing:.1em; text-transform:uppercase; color:var(--muted); margin-bottom:.35rem; }
.metric-value { font-family:var(--font-head); font-size:1.7rem; font-weight:800; line-height:1; }
.metric-value.lime { color:var(--lime); }
.metric-sub { font-size:.65rem; color:var(--muted2); margin-top:.25rem; }
.grid2 { display:grid; grid-template-columns:2fr 1fr; gap:1.25rem; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:var(--r); padding:1.1rem 1.25rem; }
.card-title { font-size:.65rem; letter-spacing:.1em; text-transform:uppercase; color:var(--muted); margin-bottom:1rem; }
#job-list { display:flex; flex-direction:column; }
.job-item { display:grid; grid-template-columns:1fr auto; align-items:start; gap:10px; padding:12px 0; border-bottom:1px solid var(--border); text-decoration:none; animation:fadeUp .3s ease both; }
.job-item:last-child { border-bottom:none; }
.job-item:hover .job-title { color:var(--lime); }
.job-title { font-family:var(--font-head); font-weight:600; font-size:.875rem; color:var(--text); margin-bottom:.25rem; transition:color .15s; line-height:1.3; }
.job-meta { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.job-empresa { font-size:.73rem; color:var(--muted); }
.job-source { font-size:.65rem; padding:1px 7px; border-radius:20px; font-weight:500; }
.src-remoteok  { background:rgba(100,200,100,.1); color:#6dc87a; }
.src-remotive  { background:rgba(248,180,113,.1); color:#f8b471; }
.src-arbeitnow { background:rgba(96,165,250,.1);  color:#60a5fa; }
.src-jobicy    { background:rgba(192,132,252,.1); color:#c084fc; }
.sen-estagio  { background:rgba(251,191,36,.1);  color:#fbbf24; }
.sen-junior   { background:rgba(52,211,153,.1);  color:#34d399; }
.sen-pleno    { background:rgba(96,165,250,.1);  color:#60a5fa; }
.sen-senior   { background:rgba(248,113,113,.1); color:#f87171; }
.sen-lideranca{ background:rgba(192,132,252,.1); color:#c084fc; }
.src-themuse   { background:rgba(244,114,182,.1); color:#f472b6; }
.job-tags { display:flex; gap:4px; flex-wrap:wrap; margin-top:4px; }
.job-tag { font-size:.62rem; padding:1px 6px; border-radius:4px; background:var(--surface2); border:1px solid var(--border); color:var(--muted); }
.job-right { text-align:right; flex-shrink:0; }
.job-data { font-size:.65rem; color:var(--muted2); }
.job-salary { font-size:.7rem; color:var(--lime); font-weight:500; margin-top:2px; }
.job-link { font-size:.68rem; color:var(--muted); margin-top:4px; display:inline-block; }
.bar-row { display:flex; align-items:center; gap:8px; margin-bottom:9px; }
.bar-label { font-size:.72rem; color:var(--muted); width:80px; flex-shrink:0; }
.bar-track { flex:1; height:4px; background:var(--surface2); border-radius:2px; overflow:hidden; }
.bar-fill { height:100%; border-radius:2px; background:var(--lime); transition:width .7s cubic-bezier(.4,0,.2,1); }
.bar-val { font-size:.65rem; color:var(--muted2); width:24px; text-align:right; }
.skel { background:linear-gradient(90deg,var(--surface2) 25%,var(--border) 50%,var(--surface2) 75%); background-size:200% 100%; animation:shimmer 1.3s infinite; border-radius:4px; height:13px; }
@keyframes shimmer { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
.filter-row { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:1rem; }
.filter-btn { font-family:var(--font-mono); font-size:.7rem; padding:4px 10px; border-radius:20px; border:1px solid var(--border); background:var(--surface2); color:var(--muted); cursor:pointer; transition:all .15s; }
.filter-btn:hover { border-color:var(--border2); color:var(--text); }
.filter-btn.active { background:var(--lime-dim); border-color:rgba(168,85,247,.3); color:var(--lime); }
.empty { text-align:center; padding:2rem; color:var(--muted); font-size:.8rem; }
.toast { position:fixed; bottom:1.5rem; right:1.5rem; z-index:999; background:var(--surface2); border:1px solid var(--border); border-radius:var(--r); padding:9px 14px; font-size:.75rem; color:var(--muted); opacity:0; transform:translateY(8px); transition:all .25s; pointer-events:none; }
.toast.show { opacity:1; transform:translateY(0); }
@media(max-width:900px) { .sidebar{display:none} .main{margin-left:0} .metrics{grid-template-columns:1fr 1fr} .grid2{grid-template-columns:1fr} .search-input{width:150px} }
</style>
</head>
<body>

<div id="login-screen">
  <div class="login-wrap">
    <p class="login-eyebrow">// monitoramento em tempo real</p>
    <h1 class="login-heading">TechPulse<br>Jobs</h1>
    <p class="login-sub">Vagas de emprego tech coletadas ao vivo de RemoteOK, Remotive, Arbeitnow e Jobicy.</p>
    <div class="login-box">
      <div class="field"><label>E-mail</label><input type="email" id="email" placeholder="seu@email.com" onkeydown="if(event.key==='Enter')doLogin()"></div>
      <div class="field"><label>Senha</label><input type="password" id="senha" placeholder="••••••••" onkeydown="if(event.key==='Enter')doLogin()"></div>
      <button class="btn-login" onclick="doLogin()">Entrar →</button>
      <p class="login-error" id="login-error">E-mail ou senha incorretos.</p>
    </div>
  </div>
</div>

<div id="app">
  <div class="layout">
    <nav class="sidebar">
      <div class="sb-logo"><div class="sb-logo-dot"></div>TechPulse Jobs</div>
      <a class="sb-item active" href="#" onclick="filterFonte('');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
        Todas as vagas <span class="fonte-count" id="cnt-all">—</span>
      </a>
      <div class="sb-section">Fontes</div>
      <a class="sb-item" href="#" onclick="filterFonte('RemoteOK');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="8 12 12 16 16 12"/><line x1="12" y1="8" x2="12" y2="16"/></svg>
        RemoteOK <span class="fonte-count" id="cnt-remoteok">—</span>
      </a>
      <a class="sb-item" href="#" onclick="filterFonte('Remotive');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
        Remotive <span class="fonte-count" id="cnt-remotive">—</span>
      </a>
      <a class="sb-item" href="#" onclick="filterFonte('Arbeitnow');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>
        Arbeitnow <span class="fonte-count" id="cnt-arbeitnow">—</span>
      </a>
      <a class="sb-item" href="#" onclick="filterFonte('Jobicy');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>
        Jobicy <span class="fonte-count" id="cnt-jobicy">—</span>
      </a>
      <a class="sb-item" href="#" onclick="filterFonte('TheMuse');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        TheMuse <span class="fonte-count" id="cnt-themuse">—</span>
      </a>
      <div class="sb-section">Senioridade</div>
      <a class="sb-item" href="#" onclick="filterSenioridade('');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>
        Todos os níveis
      </a>
      <a class="sb-item" href="#" onclick="filterSenioridade('Estágio');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>
        Estágio <span class="fonte-count" id="cnt-estagio">—</span>
      </a>
      <a class="sb-item" href="#" onclick="filterSenioridade('Júnior');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z"/><path d="M12 8v4l3 3"/></svg>
        Júnior <span class="fonte-count" id="cnt-junior">—</span>
      </a>
      <a class="sb-item" href="#" onclick="filterSenioridade('Pleno');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 3H8L6 7h12z"/></svg>
        Pleno <span class="fonte-count" id="cnt-pleno">—</span>
      </a>
      <a class="sb-item" href="#" onclick="filterSenioridade('Sênior');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
        Sênior <span class="fonte-count" id="cnt-senior">—</span>
      </a>
      <a class="sb-item" href="#" onclick="filterSenioridade('Liderança');return false">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
        Liderança <span class="fonte-count" id="cnt-lideranca">—</span>
      </a>
      <div class="sb-section">Tecnologia</div>
      <div id="sb-tags" style="display:flex;flex-direction:column;gap:1px">
        <span class="sb-item" style="color:var(--muted2);font-size:.72rem;padding:4px 10px">Carregando...</span>
      </div>
      <div class="sb-bottom">
        <div class="sb-user">
          <div class="sb-avatar" id="sb-avatar">?</div>
          <div class="sb-email" id="sb-email">—</div>
        </div>
        <button class="btn-logout" onclick="doLogout()">Sair</button>
      </div>
    </nav>

    <div class="main">
      <div class="topbar">
        <div class="topbar-left">
          <span class="topbar-title">Vagas Tech</span>
          <div class="live-pill"><div class="live-dot"></div>ao vivo</div>
        </div>
        <input class="search-input" id="search-input" type="text" placeholder="Buscar cargo ou empresa..." oninput="onSearch(this.value)">
      </div>
      <div class="content">
        <div class="metrics">
          <div class="metric"><div class="metric-label">Total de vagas</div><div class="metric-value lime" id="m-total">—</div><div class="metric-sub">coletadas agora</div></div>
          <div class="metric"><div class="metric-label">Fontes ativas</div><div class="metric-value">5</div><div class="metric-sub">RemoteOK · Remotive · Arbeitnow · Jobicy · TheMuse</div></div>
          <div class="metric"><div class="metric-label">Tag #1</div><div class="metric-value" id="m-tag1" style="font-size:1rem;padding-top:6px">—</div><div class="metric-sub">mais frequente</div></div>
          <div class="metric"><div class="metric-label">Atualizado</div><div class="metric-value" id="m-hora" style="font-size:1rem;padding-top:6px">—</div><div class="metric-sub">cache 5 min</div></div>
        </div>
        <div class="grid2">
          <div class="card">
            <div class="card-title">Lista de vagas</div>
            <div class="filter-row">
              <button class="filter-btn active" onclick="filterFonte('')">Todas fontes</button>
              <button class="filter-btn" onclick="filterFonte('RemoteOK')">RemoteOK</button>
              <button class="filter-btn" onclick="filterFonte('Remotive')">Remotive</button>
              <button class="filter-btn" onclick="filterFonte('Arbeitnow')">Arbeitnow</button>
              <button class="filter-btn" onclick="filterFonte('Jobicy')">Jobicy</button>
              <button class="filter-btn" onclick="filterFonte('TheMuse')">TheMuse ⭐</button>
            </div>
            <div id="job-list">
              <div class="skel" style="width:90%;margin-bottom:12px"></div>
              <div class="skel" style="width:75%;margin-bottom:12px"></div>
              <div class="skel" style="width:85%"></div>
            </div>
          </div>
          <div class="card">
            <div class="card-title">Top tecnologias</div>
            <div id="bar-chart">
              <div class="skel" style="margin-bottom:10px"></div>
              <div class="skel" style="width:80%;margin-bottom:10px"></div>
              <div class="skel" style="width:65%"></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const API = "";  // vazio = mesmo servidor, sem CORS
let allJobs = [], activeFilter = "", activeSeniority = "", activeTag = "", searchQ = "";

async function doLogin() {
  const email = document.getElementById("email").value.trim().toLowerCase();
  const senha = document.getElementById("senha").value;
  const errEl = document.getElementById("login-error");
  errEl.style.display = "none";
  const r = await fetch("/login", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({email, senha})
  });
  const d = await r.json();
  if (d.ok) { showApp(email); loadData(); }
  else errEl.style.display = "block";
}

async function doLogout() {
  await fetch("/logout", {method:"POST"});
  document.getElementById("app").style.display = "none";
  document.getElementById("login-screen").style.display = "flex";
  document.getElementById("senha").value = "";
  allJobs = [];
}

function showApp(email) {
  document.getElementById("login-screen").style.display = "none";
  document.getElementById("app").style.display = "block";
  document.getElementById("sb-email").textContent = email;
  document.getElementById("sb-avatar").textContent = email.slice(0,2).toUpperCase();
  document.getElementById("m-hora").textContent =
    new Date().toLocaleTimeString("pt-BR",{hour:"2-digit",minute:"2-digit"});
}

async function loadData() {
  const [jobsRes, statsRes] = await Promise.all([
    fetch("/api/jobs"), fetch("/api/stats")
  ]);
  if (jobsRes.ok)  allJobs = await jobsRes.json();
  if (statsRes.ok) renderStats(await statsRes.json());
  renderJobs();
}

function filterFonte(fonte) {
  activeFilter = fonte;
  document.querySelectorAll(".sb-item").forEach(el => {
    const txt = el.textContent.trim().toLowerCase();
    el.classList.toggle("active", fonte==="" ? txt.startsWith("todas") : txt.startsWith(fonte.toLowerCase()));
  });
  document.querySelectorAll(".filter-btn").forEach(el => {
    el.classList.toggle("active", fonte==="" ? el.textContent==="Todas" : el.textContent===fonte);
  });
  renderJobs();
}

let searchTimer;
function onSearch(q) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(()=>{ searchQ=q; renderJobs(); }, 250);
}

function renderJobs() {
  let jobs = allJobs;
  if (activeFilter)    jobs = jobs.filter(j=>j.fonte===activeFilter);
  if (activeSeniority) jobs = jobs.filter(j=>j.senioridade===activeSeniority);
  if (activeTag)       jobs = jobs.filter(j=>j.tags.map(t=>t.toLowerCase()).includes(activeTag));
  if (searchQ) {
    const q = searchQ.toLowerCase();
    jobs = jobs.filter(j=>j.titulo.toLowerCase().includes(q)||j.empresa.toLowerCase().includes(q));
  }
  const el = document.getElementById("job-list");
  if (!jobs.length) { el.innerHTML='<div class="empty">Nenhuma vaga encontrada.</div>'; return; }
  const srcClass = {RemoteOK:"src-remoteok",Remotive:"src-remotive",Arbeitnow:"src-arbeitnow",Jobicy:"src-jobicy",TheMuse:"src-themuse"};
  el.innerHTML = jobs.slice(0,40).map((j,i)=>`
    <a class="job-item" style="animation-delay:${i*25}ms"
       href="${j.url}" target="_blank" rel="noopener noreferrer">
      <div>
        <div class="job-title">${j.titulo}</div>
        <div class="job-meta">
          <span class="job-empresa">${j.empresa}</span>
          <span class="job-source ${srcClass[j.fonte]||''}">${j.fonte}</span>
          <span class="job-source ${senClass(j.senioridade)}">${j.senioridade||'Pleno'}</span>
        </div>
        <div class="job-tags">${(j.tags||[]).map(t=>`<span class="job-tag">${t}</span>`).join("")}</div>
      </div>
      <div class="job-right">
        <div class="job-data">${fmtDate(j.data)}</div>
        ${j.salario!=="—"?`<div class="job-salary">${j.salario}</div>`:""}
        <span class="job-link">ver vaga →</span>
      </div>
    </a>`).join("");
}

function renderStats(stats) {
  document.getElementById("m-total").textContent     = stats.total||"—";
  document.getElementById("cnt-all").textContent     = stats.total||"—";
  document.getElementById("cnt-remoteok").textContent  = stats.fontes?.RemoteOK||"0";
  document.getElementById("cnt-remotive").textContent  = stats.fontes?.Remotive||"0";
  document.getElementById("cnt-arbeitnow").textContent = stats.fontes?.Arbeitnow||"0";
  document.getElementById("cnt-jobicy").textContent    = stats.fontes?.Jobicy||"0";
  document.getElementById("cnt-themuse").textContent   = stats.fontes?.TheMuse||"0";
  // contadores de senioridade
  const sen = stats.senioridades||{};
  document.getElementById("cnt-estagio").textContent  = sen["Estágio"]  ||"0";
  document.getElementById("cnt-junior").textContent   = sen["Júnior"]   ||"0";
  document.getElementById("cnt-pleno").textContent    = sen["Pleno"]    ||"0";
  document.getElementById("cnt-senior").textContent   = sen["Sênior"]   ||"0";
  document.getElementById("cnt-lideranca").textContent= sen["Liderança"]||"0";
  const tags = stats.top_tags||[];
  if (tags.length) document.getElementById("m-tag1").textContent = "#"+tags[0].tag;
  const max = tags[0]?.count||1;
  // sidebar de tecnologias clicável
  const sbTags = document.getElementById("sb-tags");
  if (sbTags) sbTags.innerHTML = tags.map(({tag})=>`
    <a class="sb-item" href="#" onclick="filterTag('${tag}');return false"
       style="font-size:.75rem">
      <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
      ${tag}
    </a>`).join("");

  document.getElementById("bar-chart").innerHTML = tags.map(({tag,count})=>`
    <div class="bar-row" onclick="filterTag('${tag}')" style="cursor:pointer" title="Filtrar por ${tag}">
      <div class="bar-label" style="color:var(--text)">${tag}</div>
      <div class="bar-track"><div class="bar-fill" id="bar-${tag.replace(/[^a-z0-9]/gi,'_')}" style="width:${Math.round(count/max*100)}%"></div></div>
      <div class="bar-val">${count}</div>
    </div>`).join("");
}

function filterTag(tag) {
  activeTag = activeTag === tag.toLowerCase() ? "" : tag.toLowerCase(); // toggle
  // highlight visual na barra ativa
  document.querySelectorAll(".bar-row").forEach(el => {
    const lbl = el.querySelector(".bar-label");
    if (lbl) lbl.style.color = (activeTag && lbl.textContent.toLowerCase()===activeTag) ? "var(--lime)" : "var(--text)";
  });
  // atualiza card-title com filtro ativo
  const title = document.querySelector(".card-title");
  if (title) title.textContent = activeTag ? `vagas com ${activeTag}` : "lista de vagas";
  renderJobs();
}

function filterSenioridade(nivel) {
  activeSeniority = nivel;
  document.querySelectorAll(".sb-item").forEach(el => {
    const txt = el.textContent.trim();
    if (nivel === "") el.classList.toggle("active", txt.startsWith("Todos os níveis"));
    else el.classList.toggle("active", txt.startsWith(nivel));
  });
  renderJobs();
}

function senClass(s) {
  const m = {"Estágio":"sen-estagio","Júnior":"sen-junior","Pleno":"sen-pleno","Sênior":"sen-senior","Liderança":"sen-lideranca"};
  return m[s] || "sen-pleno";
}

function fmtDate(d) {
  try { return new Date(d).toLocaleDateString("pt-BR",{day:"2-digit",month:"short"}); } catch { return d||""; }
}

function showToast(msg) {
  const t=document.getElementById("toast");
  t.textContent=msg; t.classList.add("show");
  setTimeout(()=>t.classList.remove("show"),3000);
}

window.addEventListener("load", async ()=>{
  const r = await fetch("/api/me");
  const d = await r.json();
  if (d.logged) { showApp(d.user); loadData(); }
});
</script>
</body>
</html>"""

if __name__ == "__main__":
    init_db()
    print("🚀 TechPulse Jobs rodando em http://localhost:5000")
    app.run(debug=True, port=5000)
