from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime, timezone
import os, json, traceback, base64, secrets
from urllib.request import Request, urlopen
from artifacts import ProjectSpec, build_artifacts
from pipeline_state import prepare_publication
from publish_executor import publish_project

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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS control_approvals (
                    id BIGSERIAL PRIMARY KEY,
                    project_id BIGINT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    decided_at TIMESTAMPTZ,
                    execution_status TEXT,
                    execution_message TEXT
                )
            """)
            cur.execute("ALTER TABLE control_approvals ADD COLUMN IF NOT EXISTS execution_status TEXT")
            cur.execute("ALTER TABLE control_approvals ADD COLUMN IF NOT EXISTS execution_message TEXT")
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

def create_approval(project_id: int, action: str):
    if not db_ready():
        return None
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO control_approvals(project_id, action) VALUES (%s,%s) RETURNING id", (project_id, action))
            aid = cur.fetchone()[0]
        conn.commit()
    return aid

def list_approvals(status: str | None = None):
    if not db_ready():
        return []
    sql = "SELECT id, project_id, action, status, created_at, decided_at, execution_status, execution_message FROM control_approvals"
    params = ()
    if status:
        sql += " WHERE status=%s"
        params = (status,)
    sql += " ORDER BY id DESC"
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [{"id":r[0],"project_id":r[1],"action":r[2],"status":r[3],"created_at":r[4].isoformat(),"decided_at":r[5].isoformat() if r[5] else None,"execution_status":r[6],"execution_message":r[7]} for r in rows]

def decide_approval(approval_id: int, decision: str):
    decision = decision.upper()
    if decision not in ("APPROVED","REJECTED"):
        raise HTTPException(400, "Invalid decision")
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE control_approvals SET status=%s, decided_at=NOW() WHERE id=%s AND status='PENDING' RETURNING id", (decision, approval_id))
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "Approval not found")
    return {"id": approval_id, "status": decision}

def set_approval_execution(approval_id: int, status: str, message: str):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE control_approvals SET execution_status=%s, execution_message=%s WHERE id=%s", (status, message, approval_id))
        conn.commit()

def get_approval_record(approval_id: int):
    return next((a for a in list_approvals() if a["id"] == approval_id), None)

def execute_github_approval(approval_id: int):
    approval = get_approval_record(approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    if approval["status"] != "APPROVED":
        raise HTTPException(409, "Approval must be approved first")
    if approval["action"] != "PUBLISH_GITHUB":
        raise HTTPException(409, "This approval is not a GitHub publication")
    if approval.get("execution_status") == "SUCCESS":
        return {"status": "SUCCESS", "message": approval.get("execution_message") or "Already published"}
    project = db_get_project(approval["project_id"])
    if not project:
        raise HTTPException(404, "Project not found")
    artifacts = project.get("artifacts") or build_artifacts(ProjectSpec(
        project["id"], project["name"], project["description"], project["project_type"]
    ))
    set_approval_execution(approval_id, "RUNNING", "Publishing project files to GitHub")
    try:
        result = publish_project(project["id"], artifacts)
        message = f"{result['repository']}:{result['branch']}/{result['prefix']}"
        set_approval_execution(approval_id, "SUCCESS", message)
        project.setdefault("publication", {})
        project["publication"]["github_gate"] = "SUCCESS"
        project["publication"]["github_message"] = message
        project["publication"]["github_branch"] = result["branch"]
        project["publication"]["github_prefix"] = result["prefix"]
        project["publication"]["github_commit"] = result.get("commit_sha")
        project["updated_at"] = now()
        db_save_project(project)
        return {"status": "SUCCESS", "message": message, "result": result}
    except Exception as exc:
        set_approval_execution(approval_id, "FAILED", str(exc))
        return {"status": "FAILED", "message": str(exc)}

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
    enabled = os.getenv("AI_EXECUTION_ENABLED", "false").lower() in ("1", "true", "yes", "on")
    return enabled and bool(os.getenv("OPENAI_API_KEY")) and OpenAI is not None

def model_name():
    return os.getenv("OPENAI_MODEL", "gpt-6-luna")

def route(command: str):
    s = command.lower()
    if any(x in s for x in ["сайт", "web", "лендинг", "landing", "website"]):
        return "website", ["analyst", "web_developer", "qa_tester", "deploy_agent"]
    words = set(s.replace(",", " ").replace(".", " ").replace(":", " ").split())
    if any(x in s for x in ["telegram", "телеграм", "vk", "вк"]) or "бот" in words or "bot" in words:
        return "bot", ["analyst", "bot_developer", "qa_tester", "deploy_agent"]
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
    try:
        client = OpenAI()
        prompt = AGENT_INSTRUCTIONS[agent]
        response = client.responses.create(
            model=model_name(),
            instructions=prompt,
            input=f"ИСХОДНЫЙ ЗАПРОС:\n{user_request}\n\nКОНТЕКСТ ПРЕДЫДУЩИХ ЭТАПОВ:\n{context}",
        )
        return {"mode": "AI", "text": response.output_text, "model": model_name()}
    except Exception as exc:
        return {
            "mode": "DEMO",
            "text": f"{agent}: API недоступен, инфраструктурный режим продолжен без AI-вызова. {str(exc)[:160]}",
            "model": None
        }

def execute_railway_approval(approval_id: int):
    approval = get_approval_record(approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")
    if approval["status"] != "APPROVED":
        raise HTTPException(409, "Approval must be approved first")
    if approval["action"] != "DEPLOY_RAILWAY":
        raise HTTPException(409, "This approval is not a Railway deployment")
    if approval.get("execution_status") == "SUCCESS":
        return {"status": "SUCCESS", "message": approval.get("execution_message") or "Already deployed"}

    project = db_get_project(approval["project_id"])
    if not project:
        raise HTTPException(404, "Project not found")

    pub = project.get("publication") or {}
    if pub.get("github_gate") != "SUCCESS":
        msg = "GitHub publication must succeed first"
        set_approval_execution(approval_id, "BLOCKED", msg)
        return {"status": "BLOCKED", "message": msg}

    required = pub.get("required_variables") or []
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        msg = "Missing required variables: " + ", ".join(missing)
        set_approval_execution(approval_id, "BLOCKED", msg)
        return {"status": "BLOCKED", "message": msg}

    payload = {
        "projectId": os.getenv("RAILWAY_PROJECT_ID"),
        "environmentId": os.getenv("RAILWAY_ENVIRONMENT_ID"),
        "generatedProjectId": project["id"],
        "projectType": project["project_type"],
        "rootDirectory": pub.get("github_prefix") or f"generated/project-{project['id']}",
        "commitSha": pub.get("github_commit"),
    }
    set_approval_execution(approval_id, "RUNNING", "Sending approved deployment to Railway executor")
    try:
        req = Request(
            "http://railway-executor.railway.internal:3000/deploy",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        status = result.get("status", "FAILED")
        if status == "TRIGGERED":
            message = f"Service {result.get('serviceName')} · deployment {result.get('deploymentId')}"
            set_approval_execution(approval_id, "SUCCESS", message)
            project.setdefault("publication", {})
            project["publication"]["railway_gate"] = "SUCCESS"
            project["publication"]["railway_message"] = message
            project["publication"]["railway_service_id"] = result.get("serviceId")
            project["publication"]["railway_deployment_id"] = result.get("deploymentId")
            project["updated_at"] = now()
            db_save_project(project)
            return {"status": "SUCCESS", "message": message, "result": result}
        message = result.get("message") or "Railway executor failed"
        set_approval_execution(approval_id, status if status in ("BLOCKED","FAILED") else "FAILED", message)
        return {"status": status, "message": message}
    except Exception as exc:
        set_approval_execution(approval_id, "FAILED", str(exc))
        return {"status": "FAILED", "message": str(exc)}


def sync_project_deployment(project_id: int):
    project = db_get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    pub = project.get("publication") or {}
    if not pub.get("railway_service_id") and pub.get("railway_gate") != "SUCCESS":
        return {"status": "NOT_STARTED", "url": None}

    try:
        base = "http://railway-executor.railway.internal:3000/status"
        query = (
            f"?projectId={os.getenv('RAILWAY_PROJECT_ID','')}"
            f"&environmentId={os.getenv('RAILWAY_ENVIRONMENT_ID','')}"
            f"&generatedProjectId={project_id}"
        )
        with urlopen(base + query, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"status": "UNKNOWN", "url": pub.get("public_url"), "message": str(exc)}

    status = result.get("status") or "UNKNOWN"
    changed = False
    project.setdefault("publication", {})
    if project["publication"].get("railway_deployment_status") != status:
        project["publication"]["railway_deployment_status"] = status
        changed = True
    if result.get("serviceId") and project["publication"].get("railway_service_id") != result.get("serviceId"):
        project["publication"]["railway_service_id"] = result.get("serviceId")
        changed = True
    if result.get("deploymentId") and project["publication"].get("railway_deployment_id") != result.get("deploymentId"):
        project["publication"]["railway_deployment_id"] = result.get("deploymentId")
        changed = True
    if result.get("url") and project["publication"].get("public_url") != result.get("url"):
        project["publication"]["public_url"] = result.get("url")
        changed = True

    if status == "SUCCESS" and project.get("status") != "DEPLOYED":
        project["status"] = "DEPLOYED"
        project["publication"]["railway_gate"] = "SUCCESS"
        project["publication"]["railway_message"] = "Deployment SUCCESS"
        changed = True
    elif status in ("FAILED", "CRASHED") and project.get("status") != "DEPLOY_FAILED":
        project["status"] = "DEPLOY_FAILED"
        changed = True
    elif status in ("BUILDING", "DEPLOYING", "INITIALIZING", "QUEUED") and project.get("status") != "DEPLOYING":
        project["status"] = "DEPLOYING"
        changed = True

    if changed:
        project["updated_at"] = now()
        db_save_project(project)

    return {
        "status": status,
        "url": result.get("url"),
        "service_id": result.get("serviceId"),
        "deployment_id": result.get("deploymentId"),
    }

@app.post("/api/projects/{project_id}/deployment-sync")
def deployment_sync(project_id: int):
    return sync_project_deployment(project_id)

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "app": "NEIRO MAX CONTROL",
        "version": "2.1-live-sync",
        "agent_mode": "AI" if ai_ready() else "DEMO",
        "database": "CONNECTED" if db_ready() else "NOT_CONFIGURED",
        "auth": "ENABLED" if auth_enabled() else "NOT_CONFIGURED",
        "model": model_name() if ai_ready() else None
    }

@app.get("/api/system-state")
def system_state():
    return {
        "version": "1.9-live-deployment-status",
        "modules": [
            {"name":"PostgreSQL","status":"ACTIVE" if db_ready() else "OFF"},
            {"name":"Approval Center","status":"ACTIVE" if db_ready() else "OFF"},
            {"name":"Project Artifacts","status":"ACTIVE"},
            {"name":"Execution Gate","status":"ACTIVE"},
            {"name":"GitHub Adapter","status":"READY" if os.getenv("GITHUB_TOKEN") else "WAITING_CONNECTION"},
            {"name":"Railway Adapter","status":"READY" if os.getenv("RAILWAY_TOKEN") else "WAITING_CONNECTION"},
            {"name":"Session Auth","status":"PREPARED"},
        ],
        "pipeline": ["Analyst","Developer","QA","Approval","GitHub","Railway"]
    }

@app.get("/api/projects")
def list_projects():
    items = db_list_projects()
    refreshed = []
    for p in items:
        pub = p.get("publication") or {}
        should_sync = (
            pub.get("github_gate") == "SUCCESS"
            or pub.get("railway_service_id")
            or p.get("status") in ("WAITING_APPROVAL", "DEPLOYING", "DEPLOYED", "DEPLOY_FAILED")
        )
        if should_sync:
            try:
                sync_project_deployment(p["id"])
                latest = db_get_project(p["id"])
                refreshed.append(latest or p)
                continue
            except Exception:
                pass
        refreshed.append(p)
    return refreshed

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
    created = db_create_project(p)
    created["artifacts"] = build_artifacts(ProjectSpec(
        created["id"], created["name"], created["description"], created["project_type"]
    ))
    pub = prepare_publication(
        project_id=created["id"],
        name=created["name"],
        description=created["description"],
        project_type=created["project_type"],
        github_connected=bool(os.getenv("GITHUB_TOKEN")),
        railway_connected=bool(os.getenv("RAILWAY_TOKEN")),
    )
    created["publication"] = {
        "github_gate": pub.github_gate,
        "github_message": pub.github_message,
        "github_repository": pub.github_plan.repository_name if pub.github_plan else None,
        "railway_gate": pub.railway_gate,
        "railway_message": pub.railway_message,
        "railway_service": pub.railway_plan.service_name if pub.railway_plan else None,
        "required_variables": list(pub.railway_plan.required_variables) if pub.railway_plan else [],
    }
    db_save_project(created)
    return created

@app.get("/api/projects/{project_id}/publication")
def project_publication(project_id: int):
    p = db_get_project(project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    pub = prepare_publication(
        project_id=p["id"], name=p["name"], description=p["description"], project_type=p["project_type"],
        github_connected=bool(os.getenv("GITHUB_TOKEN")),
        railway_connected=bool(os.getenv("RAILWAY_TOKEN")),
    )
    return {
        "github_gate": pub.github_gate,
        "github_message": pub.github_message,
        "github_repository": pub.github_plan.repository_name if pub.github_plan else None,
        "railway_gate": pub.railway_gate,
        "railway_message": pub.railway_message,
        "railway_service": pub.railway_plan.service_name if pub.railway_plan else None,
        "required_variables": list(pub.railway_plan.required_variables) if pub.railway_plan else [],
    }

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
                if db_ready():
                    pending = list_approvals(status="PENDING")
                    project_pending = [a for a in pending if a["project_id"] == project_id]
                    if not any(a["action"] == "PUBLISH_GITHUB" for a in project_pending):
                        create_approval(project_id, "PUBLISH_GITHUB")
                    if not any(a["action"] == "DEPLOY_RAILWAY" for a in project_pending):
                        create_approval(project_id, "DEPLOY_RAILWAY")
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

@app.get("/api/approvals")
def approvals(status: str | None = None):
    return list_approvals(status=status)

@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: int):
    result = decide_approval(approval_id, "APPROVED")
    approval = get_approval_record(approval_id)
    if approval and approval["action"] == "PUBLISH_GITHUB":
        return execute_github_approval(approval_id)
    if approval and approval["action"] == "DEPLOY_RAILWAY":
        return execute_railway_approval(approval_id)
    return result

@app.post("/api/approvals/{approval_id}/reject")
def reject(approval_id: int):
    return decide_approval(approval_id, "REJECTED")

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
*{box-sizing:border-box}body{margin:0;background:#0b0e14;color:#edf2f8;font:14px Arial,sans-serif}.app{display:grid;grid-template-columns:230px 1fr;min-height:100vh}.side{background:#090c12;border-right:1px solid #273142;padding:24px 16px;position:relative}.brand{font-weight:800;font-size:20px;margin-bottom:34px}.brand small{display:block;color:#8996a8;font-size:10px;letter-spacing:2px}.nav{padding:11px 13px;margin:6px 0;border-radius:10px;color:#adb8c7;cursor:pointer;user-select:none}.nav:hover{background:#121824;color:#fff}.nav.active{background:#171d29;color:white}.view{display:none}.view.active{display:block}.section-title{margin:0 0 14px;font-size:22px}.workspace{display:grid;grid-template-columns:1.2fr .8fr;gap:14px}.file-list{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.kv{display:grid;grid-template-columns:140px 1fr;gap:8px;margin:6px 0}.timeline{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.timeline .step{font-size:12px}@media(max-width:900px){.workspace{grid-template-columns:1fr}}.ok{position:absolute;bottom:25px;color:#4fd49a}.main{padding:28px 34px;max-width:1500px;width:100%}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}h1{margin:0;font-size:28px}.sub{color:#8e9bad}.cmd{background:linear-gradient(120deg,#171527,#111620);border:1px solid #5b478f;border-radius:18px;padding:24px;margin-bottom:18px}.row{display:flex;gap:10px;margin-top:16px}.row input{flex:1;background:#0d1118;border:1px solid #273142;color:white;padding:15px;border-radius:11px}.btn{border:0;border-radius:10px;padding:12px 18px;font-weight:700;cursor:pointer}.primary{background:#7b61ff;color:white}.secondary{background:#151b25;color:#dce6f3;border:1px solid #2a3443}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}.stat,.card{background:#121722;border:1px solid #273142;border-radius:14px;padding:17px}.stat strong{font-size:24px;display:block;margin-top:6px}.grid{display:grid;grid-template-columns:1.45fr 1fr;gap:14px}.project{background:#0e131c;border:1px solid #202939;padding:14px;border-radius:11px;margin:9px 0;cursor:pointer}.task{background:#0b1018;border:1px solid #222c3c;border-radius:10px;padding:11px;margin:8px 0}.task pre{white-space:pre-wrap;color:#c9d4e2;font:12px/1.45 Consolas,monospace;max-height:220px;overflow:auto}.tag{display:inline-block;padding:4px 8px;border-radius:8px;background:#1a2230;margin:5px 5px 0 0;font-size:11px}.warning{color:#ffc55b}.good{color:#4fd49a}.bad{color:#ff6b7a}.mode{padding:5px 9px;border-radius:8px;background:#20283a;font-size:12px}.system{margin:18px 0;background:#10151f;border:1px solid #2c3750;border-radius:14px;padding:17px}.pipeline{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.step{padding:8px 10px;border-radius:9px;background:#171f2d;border:1px solid #2d3a50}.module-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-top:12px}.module{background:#0d121a;border:1px solid #232e3f;border-radius:10px;padding:10px}.module b{display:block;margin-bottom:4px}@media(max-width:900px){.module-grid{grid-template-columns:repeat(2,1fr)}}.approval{background:#0f151f;border:1px solid #364158;border-radius:10px;padding:12px;margin:9px 0}.approval-actions{display:flex;gap:8px;margin-top:9px}.approve{background:#1f8f5f;color:#fff}.reject{background:#7a2f3a;color:#fff}@media(max-width:850px){.app{grid-template-columns:1fr}.side{display:none}.stats{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.main{padding:18px}}
</style></head><body><div class="app"><aside class="side"><div class="brand">NEIRO MAX<small>CONTROL CENTER</small></div><div class="nav active" data-view="center" onclick="switchView('center',this)">◈ Центр</div><div class="nav" data-view="projectsView" onclick="switchView('projectsView',this)">▦ Проекты</div><div class="nav" data-view="agentsView" onclick="switchView('agentsView',this)">◉ Работоботы</div><div class="nav" data-view="connectionsView" onclick="switchView('connectionsView',this)">⚙ Подключения</div><div class="ok">● Система онлайн</div></aside><main class="main"><div class="top"><div><h1>Центр управления</h1><div class="sub">NEIRO MAX CONTROL · Live Sync v2.1</div></div><div><span id="mode" class="mode">...</span> &nbsp; <b>Сергей</b></div></div><div id="center" class="view active"><section class="cmd"><h2>Что нужно сделать?</h2><div class="sub">Дай задачу обычным языком. Orchestrator выберет исполнителей и запустит цепочку.</div><div class="row"><input id="cmd" placeholder="Например: создай Telegram-бота для стоматологии с записью клиентов"><button class="btn primary" id="go" onclick="go()">Запустить</button></div><div id="msg" class="sub" style="margin-top:10px"></div></section><section class="stats"><div class="stat"><span class="sub">Проекты</span><strong id="n">0</strong></div><div class="stat"><span class="sub">В работе</span><strong id="r">0</strong></div><div class="stat"><span class="sub">Ждут решения</span><strong id="w">0</strong></div><div class="stat"><span class="sub">Статус</span><strong class="good">ONLINE</strong></div></section><section class="system"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><div><h3 style="margin:0">Система NEIRO MAX</h3><div class="sub">Живая архитектура и готовность модулей</div></div><div class="mode good" id="sysver">v1.3</div></div><div class="pipeline" id="pipeline"></div><div class="module-grid" id="modules"></div></section><div class="grid"><section class="card"><h3>Проекты</h3><div id="projects"></div></section><section class="card"><h3>Выбранный проект</h3><div id="detail" class="sub">Выбери проект слева.</div><hr style="border-color:#273142;margin:20px 0"><h3>Нужно твоё решение</h3><div id="approvals"></div><hr style="border-color:#273142;margin:20px 0"><h3>Подключения</h3><button class="btn secondary" onclick="conn()">Проверить</button><div id="connections" style="margin-top:12px"></div></section></div>
<div id="projectsView" class="view"><section class="card"><h2 class="section-title">Проекты</h2><div class="sub">Все проекты, их статус, тип и назначенные агенты.</div><div id="projectsFull" style="margin-top:14px"></div></section></div>
<div id="agentsView" class="view"><section class="card"><h2 class="section-title">Работоботы</h2><div class="sub">Исполнители системы и их роль в цепочке.</div><div id="agentsFull" class="module-grid" style="margin-top:14px"></div></section></div>
<div id="connectionsView" class="view"><section class="card"><h2 class="section-title">Подключения</h2><div class="sub">Состояние внешних сервисов и инфраструктуры.</div><button class="btn secondary" style="margin-top:14px" onclick="connFull()">Обновить статус</button><div id="connectionsFull" style="margin-top:14px"></div></section></div>
<div id="projectWorkspace" class="view"><div class="workspace"><section class="card"><button class="btn secondary" onclick="switchView('projectsView',document.querySelector('[data-view=\'projectsView\']'))">← К проектам</button><h2 class="section-title" style="margin-top:16px" id="workspaceTitle">Проект</h2><div id="workspaceMain"></div></section><section class="card"><h3>Публикация и решения</h3><div id="workspacePublish"></div><hr style="border-color:#273142;margin:20px 0"><h3>Approval Center</h3><div id="workspaceApprovals"></div></section></div></div>
</main></div><script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let activeProjectId=null;
function switchView(id,el){
  if(id!=='projectWorkspace') activeProjectId=null;
  document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  document.querySelectorAll('.nav').forEach(x=>x.classList.remove('active'));
  el.classList.add('active');
  if(id==='projectsView') loadFullProjects();
  if(id==='agentsView') loadAgents();
  if(id==='connectionsView') connFull();
}

async function health(){let h=await fetch('/api/health').then(r=>r.json());let m=document.getElementById('mode');m.textContent=h.agent_mode+(h.model?' · '+h.model:'');m.className='mode '+(h.agent_mode==='AI'?'good':'warning')}
async function load(){let [ps,pa]=await Promise.all([fetch('/api/projects',{cache:'no-store'}).then(r=>r.json()),fetch('/api/approvals?status=PENDING',{cache:'no-store'}).then(r=>r.json())]);n.textContent=ps.length;r.textContent=ps.filter(x=>x.status==='RUNNING'||x.status==='DEPLOYING').length;w.textContent=pa.length;let root=projects;root.innerHTML=ps.length?'':'<div class="sub">Пока пусто. Дай первую команду.</div>';ps.slice().reverse().forEach(p=>{let d=document.createElement('div');d.className='project';d.onclick=()=>show(p.id);d.innerHTML='<b>#'+p.id+' '+esc(p.name)+'</b><div class="sub">'+p.project_type+' · <span class="'+(p.status==='FAILED'||p.status==='DEPLOY_FAILED'?'bad':p.status==='WAITING_APPROVAL'||p.status==='DEPLOYING'?'warning':'good')+'">'+p.status+'</span> · '+p.agent_mode+'</div>'+(p.publication&&p.publication.public_url?'<div style="margin-top:6px"><a class="good" href="'+esc(p.publication.public_url)+'" target="_blank" onclick="event.stopPropagation()">Открыть сайт ↗</a></div>':'')+'<div>'+p.assigned_agents.map(a=>'<span class="tag">'+a+'</span>').join('')+'</div>';root.appendChild(d)})}
async function loadFullProjects(){let ps=await fetch('/api/projects').then(r=>r.json());projectsFull.innerHTML=ps.length?'':'<div class="sub">Проектов пока нет.</div>';ps.slice().reverse().forEach(p=>{let d=document.createElement('div');d.className='project';d.onclick=()=>openWorkspace(p.id);d.innerHTML='<b>#'+p.id+' '+esc(p.name)+'</b><div class="sub">'+esc(p.project_type)+' · '+esc(p.status)+' · '+esc(p.agent_mode)+'</div>'+(p.publication&&p.publication.public_url?'<div style="margin-top:6px"><a class="good" href="'+esc(p.publication.public_url)+'" target="_blank" onclick="event.stopPropagation()">Открыть сайт ↗</a></div>':'')+'<div>'+p.assigned_agents.map(a=>'<span class="tag">'+esc(a)+'</span>').join('')+'</div>';projectsFull.appendChild(d)})}
async function loadAgents(){let d=await fetch('/api/system-state').then(r=>r.json());let roles=[['Analyst','Разбирает запрос и формирует ТЗ'],['Developer','Создаёт реализацию и файлы проекта'],['QA','Проверяет результат и останавливает ошибки'],['Approval','Ждёт твоего решения перед внешним действием'],['GitHub','Готовит публикацию проекта'],['Railway','Готовит развёртывание сервиса']];agentsFull.innerHTML=roles.map(x=>'<div class="module"><b>'+x[0]+'</b><span class="sub">'+x[1]+'</span></div>').join('')}
async function connFull(){let d=await fetch('/api/connections').then(r=>r.json());connectionsFull.innerHTML='<div class="'+(d.summary.production_ready?'good':'warning')+'">Production ready: '+(d.summary.production_ready?'ДА':'НЕТ')+'</div>'+d.items.map(x=>'<div class="module" style="margin-top:8px"><b>'+esc(x.name)+'</b><span class="'+(x.status==='CONNECTED'?'good':'warning')+'">'+esc(x.status)+'</span>'+(x.missing_names.length?'<div class="sub">Не настроено: '+x.missing_names.map(esc).join(', ')+'</div>':'')+'</div>').join('')}
async function openWorkspace(id){
  activeProjectId=id;
  document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));
  document.getElementById('projectWorkspace').classList.add('active');
  document.querySelectorAll('.nav').forEach(x=>x.classList.remove('active'));
  let p=await fetch('/api/projects/'+id).then(r=>r.json());
  workspaceTitle.textContent='#'+p.id+' '+p.name;
  let files=p.artifacts?Object.keys(p.artifacts):[];
  let tasks=(p.tasks||[]);
  workspaceMain.innerHTML='<div class="kv"><span class="sub">Статус</span><b>'+esc(p.status)+'</b></div><div class="kv"><span class="sub">Тип</span><span>'+esc(p.project_type)+'</span></div><div class="kv"><span class="sub">Режим</span><span>'+esc(p.agent_mode)+'</span></div><div class="kv"><span class="sub">Описание</span><span>'+esc(p.description)+'</span></div><h3 style="margin-top:18px">Файлы проекта</h3><div class="file-list">'+(files.length?files.map(x=>'<span class="tag">'+esc(x)+'</span>').join(''):'<span class="sub">Файлы ещё не созданы</span>')+'</div><h3 style="margin-top:18px">Этапы</h3><div class="timeline">'+(tasks.length?tasks.map(t=>'<span class="step">'+esc(t.agent)+' · '+esc(t.status)+'</span>').join(''):'<span class="sub">Пайплайн ещё не запускался</span>')+'</div>';
  let pub=p.publication||await fetch('/api/projects/'+id+'/publication').then(r=>r.json());
  let live=await fetch('/api/projects/'+id+'/deployment-sync',{method:'POST'}).then(r=>r.json()).catch(()=>({status:'UNKNOWN'}));workspacePublish.innerHTML='<div class="module"><b>GitHub</b><span class="'+(pub.github_gate==='SUCCESS'||pub.github_gate==='READY'?'good':'warning')+'">'+esc(pub.github_gate)+'</span><div class="sub">'+esc(pub.github_message||'')+'</div>'+(pub.github_repository?'<div class="sub">Репозиторий: '+esc(pub.github_repository)+'</div>':'')+'</div><div class="module" style="margin-top:8px"><b>Railway</b><span class="'+(live.status==='SUCCESS'?'good':live.status==='FAILED'||live.status==='CRASHED'?'bad':'warning')+'">'+esc(live.status||pub.railway_gate)+'</span><div class="sub">'+esc(pub.railway_message||'')+'</div>'+(pub.railway_service?'<div class="sub">Сервис: '+esc(pub.railway_service)+'</div>':'')+(live.url?'<div style="margin-top:10px"><a class="btn approve" style="display:inline-block;text-decoration:none" href="'+esc(live.url)+'" target="_blank">Открыть сайт ↗</a></div>':'')+'</div>';
  let a=await fetch('/api/approvals').then(r=>r.json());a=a.filter(x=>x.project_id===id);workspaceApprovals.innerHTML=a.length?a.map(x=>'<div class="approval"><b>'+esc(x.action)+'</b><div class="'+(x.status==='APPROVED'?'good':x.status==='REJECTED'?'bad':'warning')+'">'+esc(x.status)+'</div>'+(x.execution_status?'<div class="sub">Исполнение: '+esc(x.execution_status)+(x.execution_message?' · '+esc(x.execution_message):'')+'</div>':'')+'</div>').join(''):'<div class="sub">Решений пока нет.</div>';
}


async function show(id){let p=await fetch('/api/projects/'+id).then(r=>r.json());let html='<b>#'+p.id+' '+esc(p.name)+'</b><div class="sub">'+p.status+' · '+p.agent_mode+'</div>';if(p.artifacts){html+='<div class="task"><b>Файлы проекта</b><div>'+Object.keys(p.artifacts).map(x=>'<span class="tag">'+esc(x)+'</span>').join('')+'</div></div>'}if(p.publication){html+='<div class="task"><b>Публикация</b><div class="sub">GitHub: '+esc(p.publication.github_gate)+' · '+esc(p.publication.github_message)+'</div><div class="sub">Railway: '+esc(p.publication.railway_gate)+' · '+esc(p.publication.railway_message)+'</div></div>'};(p.tasks||[]).forEach(t=>{let tx=t.result?.text||t.error||'';html+='<div class="task"><b>'+esc(t.agent)+'</b> · <span class="'+(t.status==='FAILED'?'bad':t.status==='WAITING_APPROVAL'?'warning':'good')+'">'+t.status+'</span><pre>'+esc(tx)+'</pre></div>'});detail.innerHTML=html}
async function go(){let command=cmd.value.trim();if(!command)return;go.disabled=true;msg.textContent='Orchestrator запускает цепочку...';let p=await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command,autonomy_level:2})}).then(r=>r.json());let done=await fetch('/api/pipeline/'+p.id+'/run',{method:'POST'}).then(r=>r.json());msg.textContent=done.status==='FAILED'?'Цепочка остановлена: QA/агент сообщил об ошибке.':'Цепочка завершена до точки подтверждения.';cmd.value='';go.disabled=false;await load();show(p.id)}
async function approvalLoad(){let a=await fetch('/api/approvals?status=PENDING').then(r=>r.json());approvals.innerHTML=a.length?'':'<div class="sub">Нет ожидающих решений.</div>';a.forEach(x=>{let d=document.createElement('div');d.className='approval';d.innerHTML='<b>Проект #'+x.project_id+'</b><div class="warning">'+esc(x.action)+'</div><div class="sub">'+(x.execution_status?('Исполнение: '+esc(x.execution_status)+(x.execution_message?' · '+esc(x.execution_message):'')):'')+'</div><div class="approval-actions"><button class="btn approve" onclick="decide('+x.id+',\'approve\')">Одобрить</button><button class="btn reject" onclick="decide('+x.id+',\'reject\')">Отклонить</button></div>';approvals.appendChild(d)})}
async function decide(id,what){await fetch('/api/approvals/'+id+'/'+what,{method:'POST'});await approvalLoad();await load()}
async function conn(){let d=await fetch('/api/connections').then(r=>r.json());connections.innerHTML='<div class="'+(d.summary.production_ready?'good':'warning')+'">Production ready: '+(d.summary.production_ready?'ДА':'НЕТ')+'</div><div class="sub">Agent mode: '+d.summary.agent_mode+'</div>'+d.items.map(x=>'<div class="sub">'+x.name+': '+x.status+(x.missing_names.length?' ('+x.missing_names.join(', ')+')':'')+'</div>').join('')}
async function systemState(){let d=await fetch('/api/system-state').then(r=>r.json());sysver.textContent=d.version;pipeline.innerHTML=d.pipeline.map(x=>'<span class="step">'+esc(x)+'</span>').join('<span class="sub">→</span>');modules.innerHTML=d.modules.map(x=>'<div class="module"><b>'+esc(x.name)+'</b><span class="'+(x.status==='ACTIVE'||x.status==='READY'?'good':x.status==='PREPARED'?'warning':'sub')+'">'+esc(x.status)+'</span></div>').join('')}
health();load();conn();approvalLoad();systemState();
let refreshBusy=false;
async function refreshDashboard(){
 if(refreshBusy||document.hidden)return;
 refreshBusy=true;
 try{
   const ps=await fetch('/api/projects',{cache:'no-store'}).then(r=>r.json());
   const target=ps.find(p=>p.publication&&p.publication.railway_service_id&&p.status!=='DEPLOYED'&&p.status!=='DEPLOY_FAILED');
   if(target){await fetch('/api/projects/'+target.id+'/deployment-sync',{method:'POST'});}
   await load();
   await approvalLoad();
   if(activeProjectId&&document.getElementById('projectWorkspace').classList.contains('active')){
     await openWorkspace(activeProjectId);
   }
 }catch(e){console.error('Dashboard refresh failed',e)}
 finally{refreshBusy=false}
}
setInterval(refreshDashboard,10000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshDashboard()});
refreshDashboard();
</script></body></html>'''

@app.get("/", response_class=HTMLResponse)
def root():
    return HTML
