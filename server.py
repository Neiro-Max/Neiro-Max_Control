from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime, timezone
import os, json, traceback, base64, secrets

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    import psycopg
    from psycopg.types.json import Jsonb
except Exception:
    psycopg = None
    Jsonb = None

app = FastAPI(title="NEIRO MAX CONTROL", version="1.0-infrastructure")
projects = []
next_id = 1

def db_ready():
    return bool(os.getenv("DATABASE_URL")) and psycopg is not None

def db_conn():
    return psycopg.connect(os.environ["DATABASE_URL"])

def init_db():
    if not db_ready():
        return
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS control_projects (
                    id BIGSERIAL PRIMARY KEY,
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        conn.commit()

def db_list_projects():
    if not db_ready():
        return projects
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM control_projects ORDER BY id ASC")
            return [row[0] for row in cur.fetchall()]

def db_get_project(project_id: int):
    if not db_ready():
        return next((x for x in projects if x.get("id") == project_id), None)
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM control_projects WHERE id=%s", (project_id,))
            row = cur.fetchone()
            return row[0] if row else None

def db_create_project(data: dict):
    global next_id
    if not db_ready():
        data["id"] = next_id
        next_id += 1
        projects.append(data)
        return data
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO control_projects(data) VALUES (%s) RETURNING id", (Jsonb(data),))
            pid = cur.fetchone()[0]
            data["id"] = pid
            cur.execute("UPDATE control_projects SET data=%s, updated_at=NOW() WHERE id=%s", (Jsonb(data), pid))
        conn.commit()
    return data

def db_save_project(data: dict):
    if not db_ready():
        return data
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE control_projects SET data=%s, updated_at=NOW() WHERE id=%s", (Jsonb(data), data["id"]))
        conn.commit()
    return data

def auth_enabled():
    return bool(os.getenv("CONTROL_PASSWORD"))

@app.middleware("http")
async def owner_auth(request, call_next):
    if not auth_enabled() or request.url.path == "/api/health":
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    ok = False
    if auth.startswith("Basic "):
        try:
            raw = base64.b64decode(auth[6:]).decode("utf-8")
            username, password = raw.split(":", 1)
            ok = secrets.compare_digest(username, os.getenv("CONTROL_USER", "sergey")) and secrets.compare_digest(password, os.getenv("CONTROL_PASSWORD", ""))
        except Exception:
            ok = False
    if not ok:
        from fastapi.responses import Response
        return Response("Authentication required", status_code=401, headers={"WWW-Authenticate": 'Basic realm="NEIRO MAX CONTROL"'})
    return await call_next(request)

@app.on_event("startup")
def startup():
    init_db()

class Cmd(BaseModel):
    command: str
    autonomy_level: int = 2

def now():
    return datetime.now(timezone.utc).isoformat()

def ai_ready():
    return bool(os.getenv("OPENAI_API_KEY")) and OpenAI is not None

def model_name():
    return os.getenv("OPENAI_MODEL", "gpt-6-luna")

def route(command: str):
    s = command.lower()
    if any(x in s for x in ["telegram", "телеграм", "бот", "vk", "вк"]):
        return "bot", ["analyst", "bot_developer", "qa_tester", "deploy_agent"]
    if any(x in s for x in ["сайт", "web", "лендинг", "landing"]):
        return "website", ["analyst", "web_developer", "qa_tester", "deploy_agent"]
    return "general", ["analyst", "project_manager", "qa_tester"]

AGENT_INSTRUCTIONS = {
    "analyst": """Ты Analyst в NEIRO MAX CONTROL. Преобразуй запрос владельца в чёткое техническое задание.
Верни кратко: цель, пользовательский сценарий, функции, ограничения, критерии готовности. Не выдумывай внешние доступы.""",
    "bot_developer": """Ты Bot Developer. На основе ТЗ предложи готовую техническую реализацию Telegram/VK-бота.
Дай структуру файлов, зависимости и полный минимальный код ключевых файлов. Не вставляй секреты; используй переменные окружения.""",
    "web_developer": """Ты Web Developer. На основе ТЗ предложи готовую техническую реализацию сайта.
Дай структуру файлов и полный минимальный код ключевых файлов. Не вставляй секреты.""",
    "project_manager": """Ты Project Manager. Сформируй практический план выполнения задачи: этапы, результат каждого этапа, риски и критерии завершения.""",
    "qa_tester": """Ты QA Tester. Проверь результат предыдущего исполнителя против исходного запроса и ТЗ.
Выдай PASS или FAIL первой строкой, затем найденные проблемы и конкретные исправления. Будь строгим.""",
    "deploy_agent": """Ты Deploy Agent. Подготовь безопасный план публикации в GitHub/Railway.
Ничего не считай уже опубликованным. Перечисли файлы, необходимые переменные окружения, healthcheck и действия после одобрения владельца."""
}

def call_agent(agent: str, user_request: str, context: str):
    if not ai_ready():
        return {
            "mode": "DEMO",
            "text": f"{agent}: реальный запуск ожидает OPENAI_API_KEY. Запрос принят, контекст подготовлен.",
            "model": None
        }
    client = OpenAI()
    prompt = AGENT_INSTRUCTIONS[agent]
    response = client.responses.create(
        model=model_name(),
        instructions=prompt,
        input=f"ИСХОДНЫЙ ЗАПРОС:\n{user_request}\n\nКОНТЕКСТ ПРЕДЫДУЩИХ ЭТАПОВ:\n{context}",
    )
    return {"mode": "AI", "text": response.output_text, "model": model_name()}

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "app": "NEIRO MAX CONTROL",
        "version": "1.0-infrastructure",
        "agent_mode": "AI" if ai_ready() else "DEMO",
        "database": "CONNECTED" if db_ready() else "NOT_CONFIGURED",
        "auth": "ENABLED" if auth_enabled() else "NOT_CONFIGURED",
        "model": model_name() if ai_ready() else None
    }

@app.get("/api/projects")
def list_projects():
    return db_list_projects()

@app.get("/api/projects/{project_id}")
def get_project(project_id: int):
    p = db_get_project(project_id)
    if p:
        return p
    raise HTTPException(404, "Project not found")

@app.post("/api/projects")
def create_project(cmd: Cmd):
    typ, agents = route(cmd.command)
    p = {
        "name": cmd.command[:80],
        "description": cmd.command,
        "project_type": typ,
        "status": "PLANNING",
        "autonomy_level": cmd.autonomy_level,
        "assigned_agents": agents,
        "tasks": [],
        "created_at": now(),
        "updated_at": now(),
        "agent_mode": "AI" if ai_ready() else "DEMO"
    }
    return db_create_project(p)

@app.post("/api/pipeline/{project_id}/run")
def run_pipeline(project_id: int):
    p = db_get_project(project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    p["status"] = "RUNNING"
    p["tasks"] = []
    context_parts = []
    try:
        for agent in p["assigned_agents"]:
            task = {"agent": agent, "status": "RUNNING", "started_at": now(), "result": None}
            p["tasks"].append(task)
            result = call_agent(agent, p["description"], "\n\n".join(context_parts[-3:]))
            task["result"] = result
            task["completed_at"] = now()
            if agent == "qa_tester" and result["mode"] == "AI" and result["text"].lstrip().upper().startswith("FAIL"):
                task["status"] = "FAILED"
                p["status"] = "FAILED"
                p["updated_at"] = now()
                db_save_project(p)
                return p
            if agent == "deploy_agent":
                task["status"] = "WAITING_APPROVAL"
                p["status"] = "WAITING_APPROVAL"
            else:
                task["status"] = "COMPLETED"
            context_parts.append(f"[{agent}]\n{result['text']}")
        p["updated_at"] = now()
        db_save_project(p)
        return p
    except Exception as exc:
        p["status"] = "FAILED"
        p["error"] = str(exc)
        p["updated_at"] = now()
        if p["tasks"]:
            p["tasks"][-1]["status"] = "FAILED"
            p["tasks"][-1]["error"] = str(exc)
        db_save_project(p)
        return p

@app.get("/api/connections")
def connections():
    defs = [
        ("OpenAI", ["OPENAI_API_KEY"]),
        ("GitHub", ["GITHUB_TOKEN"]),
        ("Railway", ["RAILWAY_TOKEN"]),
        ("Telegram", ["TELEGRAM_TOKEN"]),
        ("VK", ["VK_TOKEN"]),
        ("Database", ["DATABASE_URL"]),
    ]
    items = []
    for name, keys in defs:
        missing = [k for k in keys if not os.getenv(k)]
        items.append({"name": name, "status": "CONNECTED" if not missing else "NOT_CONFIGURED", "missing_names": missing})
    connected = sum(i["status"] == "CONNECTED" for i in items)
    return {
        "summary": {
            "connected": connected,
            "total": len(items),
            "production_ready": all(os.getenv(k) for k in ["OPENAI_API_KEY", "GITHUB_TOKEN", "RAILWAY_TOKEN", "DATABASE_URL"]),
            "agent_mode": "AI" if ai_ready() else "DEMO",
            "model": model_name() if ai_ready() else None
        },
        "items": items
    }

HTML = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEIRO MAX CONTROL</title><style>
*{box-sizing:border-box}body{margin:0;background:#0b0e14;color:#edf2f8;font:14px Arial,sans-serif}.app{display:grid;grid-template-columns:230px 1fr;min-height:100vh}.side{background:#090c12;border-right:1px solid #273142;padding:24px 16px;position:relative}.brand{font-weight:800;font-size:20px;margin-bottom:34px}.brand small{display:block;color:#8996a8;font-size:10px;letter-spacing:2px}.nav{padding:11px 13px;margin:6px 0;border-radius:10px;color:#adb8c7}.nav.active{background:#171d29;color:white}.ok{position:absolute;bottom:25px;color:#4fd49a}.main{padding:28px 34px;max-width:1500px;width:100%}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}h1{margin:0;font-size:28px}.sub{color:#8e9bad}.cmd{background:linear-gradient(120deg,#171527,#111620);border:1px solid #5b478f;border-radius:18px;padding:24px;margin-bottom:18px}.row{display:flex;gap:10px;margin-top:16px}.row input{flex:1;background:#0d1118;border:1px solid #273142;color:white;padding:15px;border-radius:11px}.btn{border:0;border-radius:10px;padding:12px 18px;font-weight:700;cursor:pointer}.primary{background:#7b61ff;color:white}.secondary{background:#151b25;color:#dce6f3;border:1px solid #2a3443}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}.stat,.card{background:#121722;border:1px solid #273142;border-radius:14px;padding:17px}.stat strong{font-size:24px;display:block;margin-top:6px}.grid{display:grid;grid-template-columns:1.45fr 1fr;gap:14px}.project{background:#0e131c;border:1px solid #202939;padding:14px;border-radius:11px;margin:9px 0;cursor:pointer}.task{background:#0b1018;border:1px solid #222c3c;border-radius:10px;padding:11px;margin:8px 0}.task pre{white-space:pre-wrap;color:#c9d4e2;font:12px/1.45 Consolas,monospace;max-height:220px;overflow:auto}.tag{display:inline-block;padding:4px 8px;border-radius:8px;background:#1a2230;margin:5px 5px 0 0;font-size:11px}.warning{color:#ffc55b}.good{color:#4fd49a}.bad{color:#ff6b7a}.mode{padding:5px 9px;border-radius:8px;background:#20283a;font-size:12px}@media(max-width:850px){.app{grid-template-columns:1fr}.side{display:none}.stats{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.main{padding:18px}}
</style></head><body><div class="app"><aside class="side"><div class="brand">NEIRO MAX<small>CONTROL CENTER</small></div><div class="nav active">◈ Центр</div><div class="nav">▦ Проекты</div><div class="nav">◉ Работоботы</div><div class="nav">⚙ Подключения</div><div class="ok">● Система онлайн</div></aside><main class="main"><div class="top"><div><h1>Центр управления</h1><div class="sub">NEIRO MAX CONTROL · Infrastructure v1.0</div></div><div><span id="mode" class="mode">...</span> &nbsp; <b>Сергей</b></div></div><section class="cmd"><h2>Что нужно сделать?</h2><div class="sub">Дай задачу обычным языком. Orchestrator выберет исполнителей и запустит цепочку.</div><div class="row"><input id="cmd" placeholder="Например: создай Telegram-бота для стоматологии с записью клиентов"><button class="btn primary" id="go" onclick="go()">Запустить</button></div><div id="msg" class="sub" style="margin-top:10px"></div></section><section class="stats"><div class="stat"><span class="sub">Проекты</span><strong id="n">0</strong></div><div class="stat"><span class="sub">В работе</span><strong id="r">0</strong></div><div class="stat"><span class="sub">Ждут решения</span><strong id="w">0</strong></div><div class="stat"><span class="sub">Статус</span><strong class="good">ONLINE</strong></div></section><div class="grid"><section class="card"><h3>Проекты</h3><div id="projects"></div></section><section class="card"><h3>Выбранный проект</h3><div id="detail" class="sub">Выбери проект слева.</div><hr style="border-color:#273142;margin:20px 0"><h3>Подключения</h3><button class="btn secondary" onclick="conn()">Проверить</button><div id="connections" style="margin-top:12px"></div></section></div></main></div><script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function health(){let h=await fetch('/api/health').then(r=>r.json());let m=document.getElementById('mode');m.textContent=h.agent_mode+(h.model?' · '+h.model:'');m.className='mode '+(h.agent_mode==='AI'?'good':'warning')}
async function load(){let ps=await fetch('/api/projects').then(r=>r.json());n.textContent=ps.length;r.textContent=ps.filter(x=>x.status==='RUNNING').length;w.textContent=ps.filter(x=>x.status==='WAITING_APPROVAL').length;let root=projects;root.innerHTML=ps.length?'':'<div class="sub">Пока пусто. Дай первую команду.</div>';ps.slice().reverse().forEach(p=>{let d=document.createElement('div');d.className='project';d.onclick=()=>show(p.id);d.innerHTML='<b>#'+p.id+' '+esc(p.name)+'</b><div class="sub">'+p.project_type+' · <span class="'+(p.status==='FAILED'?'bad':p.status==='WAITING_APPROVAL'?'warning':'good')+'">'+p.status+'</span> · '+p.agent_mode+'</div><div>'+p.assigned_agents.map(a=>'<span class="tag">'+a+'</span>').join('')+'</div>';root.appendChild(d)})}
async function show(id){let p=await fetch('/api/projects/'+id).then(r=>r.json());let html='<b>#'+p.id+' '+esc(p.name)+'</b><div class="sub">'+p.status+' · '+p.agent_mode+'</div>';(p.tasks||[]).forEach(t=>{let tx=t.result?.text||t.error||'';html+='<div class="task"><b>'+esc(t.agent)+'</b> · <span class="'+(t.status==='FAILED'?'bad':t.status==='WAITING_APPROVAL'?'warning':'good')+'">'+t.status+'</span><pre>'+esc(tx)+'</pre></div>'});detail.innerHTML=html}
async function go(){let command=cmd.value.trim();if(!command)return;go.disabled=true;msg.textContent='Orchestrator запускает цепочку...';let p=await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command,autonomy_level:2})}).then(r=>r.json());let done=await fetch('/api/pipeline/'+p.id+'/run',{method:'POST'}).then(r=>r.json());msg.textContent=done.status==='FAILED'?'Цепочка остановлена: QA/агент сообщил об ошибке.':'Цепочка завершена до точки подтверждения.';cmd.value='';go.disabled=false;await load();show(p.id)}
async function conn(){let d=await fetch('/api/connections').then(r=>r.json());connections.innerHTML='<div class="'+(d.summary.production_ready?'good':'warning')+'">Production ready: '+(d.summary.production_ready?'ДА':'НЕТ')+'</div><div class="sub">Agent mode: '+d.summary.agent_mode+'</div>'+d.items.map(x=>'<div class="sub">'+x.name+': '+x.status+(x.missing_names.length?' ('+x.missing_names.join(', ')+')':'')+'</div>').join('')}
health();load();conn();
</script></body></html>'''

@app.get("/", response_class=HTMLResponse)
def root():
    return HTML
