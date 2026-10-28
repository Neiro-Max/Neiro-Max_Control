from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime
import os

app=FastAPI(title='NEIRO MAX CONTROL',version='0.8-live-preview')
projects=[]

class Cmd(BaseModel):
    command:str
    autonomy_level:int=2

def route(command:str):
    s=command.lower()
    if 'telegram' in s or 'бот' in s or 'vk' in s or 'вк' in s:
        return 'bot',['analyst','bot_developer','qa_tester','deploy_agent']
    if 'сайт' in s or 'web' in s or 'лендинг' in s:
        return 'website',['analyst','web_developer','qa_tester','deploy_agent']
    return 'general',['analyst','project_manager']

@app.get('/api/health')
def health(): return {'status':'ok','app':'NEIRO MAX CONTROL','version':'0.8-live-preview'}

@app.get('/api/projects')
def list_projects(): return projects

@app.post('/api/projects')
def create_project(cmd:Cmd):
    typ,agents=route(cmd.command)
    p={'id':len(projects)+1,'name':cmd.command[:70],'description':cmd.command,'project_type':typ,'status':'PLANNING','autonomy_level':cmd.autonomy_level,'assigned_agents':agents,'created_at':datetime.utcnow().isoformat()}
    projects.append(p); return p

@app.post('/api/pipeline/{project_id}/run')
def run_pipeline(project_id:int):
    for p in projects:
        if p['id']==project_id:
            p['status']='WAITING_APPROVAL'
            p['tasks']=[{'agent':a,'status':'COMPLETED' if a!='deploy_agent' else 'WAITING_APPROVAL'} for a in p['assigned_agents']]
            return {'project_id':project_id,'status':p['status'],'tasks':p['tasks']}
    return {'error':'not found'}

@app.get('/api/connections')
def connections():
    items=[]
    defs=[('OpenAI','OPENAI_API_KEY'),('GitHub','GITHUB_TOKEN'),('Railway','RAILWAY_TOKEN'),('Telegram','TELEGRAM_TOKEN'),('VK','VK_TOKEN'),('Database','DATABASE_URL')]
    for name,key in defs:
        items.append({'name':name,'status':'CONNECTED' if os.getenv(key) else 'NOT_CONFIGURED','missing_names':[] if os.getenv(key) else [key]})
    connected=sum(i['status']=='CONNECTED' for i in items)
    prod=all(os.getenv(k) for k in ['OPENAI_API_KEY','GITHUB_TOKEN','RAILWAY_TOKEN','DATABASE_URL'])
    return {'summary':{'connected':connected,'total':len(items),'production_ready':prod},'items':items}

HTML='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NEIRO MAX CONTROL</title><style>
*{box-sizing:border-box}body{margin:0;background:#0b0e14;color:#edf2f8;font:14px Arial,sans-serif}.app{display:grid;grid-template-columns:230px 1fr;min-height:100vh}.side{background:#090c12;border-right:1px solid #273142;padding:24px 16px}.brand{font-weight:800;font-size:20px;margin-bottom:34px}.brand small{display:block;color:#8996a8;font-size:10px;letter-spacing:2px}.nav{padding:11px 13px;margin:6px 0;border-radius:10px;color:#adb8c7}.nav.active{background:#171d29;color:white}.ok{position:absolute;bottom:25px;color:#4fd49a}.main{padding:28px 34px;max-width:1400px;width:100%}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}h1{margin:0;font-size:28px}.sub{color:#8e9bad}.cmd{background:linear-gradient(120deg,#171527,#111620);border:1px solid #5b478f;border-radius:18px;padding:24px;margin-bottom:18px}.cmd h2{margin:0 0 8px}.row{display:flex;gap:10px;margin-top:16px}.row input{flex:1;background:#0d1118;border:1px solid #273142;color:white;padding:15px;border-radius:11px}.btn{border:0;border-radius:10px;padding:12px 18px;font-weight:700;cursor:pointer}.primary{background:#7b61ff;color:white}.secondary{background:#151b25;color:#dce6f3;border:1px solid #2a3443}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}.stat,.card{background:#121722;border:1px solid #273142;border-radius:14px;padding:17px}.stat strong{font-size:24px;display:block;margin-top:6px}.grid{display:grid;grid-template-columns:1.4fr 1fr;gap:14px}.project{background:#0e131c;border:1px solid #202939;padding:13px;border-radius:11px;margin:9px 0}.tag{display:inline-block;padding:4px 8px;border-radius:8px;background:#1a2230;margin:5px 5px 0 0;font-size:11px}.warning{color:#ffc55b}.good{color:#4fd49a}@media(max-width:850px){.app{grid-template-columns:1fr}.side{display:none}.stats{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.main{padding:18px}}
</style></head><body><div class="app"><aside class="side"><div class="brand">NEIRO MAX<small>CONTROL CENTER</small></div><div class="nav active">◈ Центр</div><div class="nav">▦ Проекты</div><div class="nav">◉ Работоботы</div><div class="nav">⚙ Подключения</div><div class="ok">● Система онлайн</div></aside><main class="main"><div class="top"><div><h1>Центр управления</h1><div class="sub">NEIRO MAX CONTROL · live preview</div></div><b>Сергей</b></div><section class="cmd"><h2>Что нужно сделать?</h2><div class="sub">Дай задачу обычным языком. Orchestrator определит тип и исполнителей.</div><div class="row"><input id="cmd" placeholder="Например: нужен Telegram-бот для стоматологии"><button class="btn primary" onclick="go()">Запустить</button></div><div id="msg" class="sub" style="margin-top:10px"></div></section><section class="stats"><div class="stat"><span class="sub">Проекты</span><strong id="n">0</strong></div><div class="stat"><span class="sub">В работе</span><strong id="r">0</strong></div><div class="stat"><span class="sub">Ждут решения</span><strong id="w">0</strong></div><div class="stat"><span class="sub">Статус</span><strong class="good">ONLINE</strong></div></section><div class="grid"><section class="card"><h3>Проекты</h3><div id="projects"><div class="sub">Пока пусто. Дай первую команду.</div></div></section><section class="card"><h3>Как работает</h3><p>Ты → <b>Orchestrator</b> → Analyst → профильный Developer → QA → Deploy.</p><p class="sub">Внешние действия остаются на подтверждении владельца. Это live preview интерфейса; полный Agent Layer подключается следующим шагом.</p><button class="btn secondary" onclick="conn()">Проверить подключения</button><div id="connections" style="margin-top:12px"></div></section></div></main></div><script>
async function load(){let ps=await fetch('/api/projects').then(r=>r.json());document.getElementById('n').textContent=ps.length;document.getElementById('r').textContent=ps.filter(x=>x.status==='RUNNING').length;document.getElementById('w').textContent=ps.filter(x=>x.status==='WAITING_APPROVAL').length;let root=document.getElementById('projects');root.innerHTML=ps.length?'':'<div class="sub">Пока пусто.</div>';ps.slice().reverse().forEach(p=>{let d=document.createElement('div');d.className='project';d.innerHTML='<b>#'+p.id+' '+esc(p.name)+'</b><div class="sub">'+p.project_type+' · <span class="warning">'+p.status+'</span></div><div>'+p.assigned_agents.map(a=>'<span class="tag">'+a+'</span>').join('')+'</div>';root.appendChild(d)})}function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}async function go(){let command=document.getElementById('cmd').value.trim();if(!command)return;document.getElementById('msg').textContent='Orchestrator анализирует…';let p=await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command,autonomy_level:2})}).then(r=>r.json());await fetch('/api/pipeline/'+p.id+'/run',{method:'POST'});document.getElementById('msg').textContent='Проект создан и доведён до точки подтверждения.';document.getElementById('cmd').value='';load()}async function conn(){let d=await fetch('/api/connections').then(r=>r.json());document.getElementById('connections').innerHTML='<div class="'+(d.summary.production_ready?'good':'warning')+'">Production ready: '+(d.summary.production_ready?'ДА':'НЕТ')+'</div>'+d.items.map(x=>'<div class="sub">'+x.name+': '+x.status+'</div>').join('')}load();
</script></body></html>'''

@app.get('/',response_class=HTMLResponse)
def root(): return HTML
