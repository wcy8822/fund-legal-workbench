import sys,tempfile,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from app.main import create_app
with tempfile.TemporaryDirectory(prefix='fwb-ui-api-') as data:
 client=TestClient(create_app(data))
 out={}
 def call(method,path,**kw):
  r=getattr(client,method)(path,**kw)
  if r.status_code>=400: raise RuntimeError(f'{method} {path}: {r.status_code} {r.text}')
  return r.json()
 demo=call('post','/api/demo'); pid=demo['project_id']; cid=demo['contract_id']; p=f'/api/projects/{pid}'; c=p+'/contracts/'+cid
 out['initial']=call('get',p)
 second=call('post',c+'/demo-version',json={'stage':2})
 out['diff']=call('get',p+f'/diff?left={demo["version_id"]}&right={second["id"]}')
 call('post',c+'/analyze',json={'version_id':second['id'],'provider':'demo'})
 third=call('post',c+'/demo-version',json={'stage':3})
 call('post',c+'/analyze',json={'version_id':third['id'],'provider':'demo'})
 out['reviewed']=call('get',p)
 internal=call('post',p+'/deliverables',json={'version_id':third['id'],'kind':'internal','format':'md'})
 external=call('post',p+'/deliverables',json={'version_id':third['id'],'kind':'external','format':'docx'})
 out['pending']=call('get',p)
 out['preview']=call('get',p+'/deliverables/'+external['id']+'/preview')
 assert client.get(p+'/deliverables/'+external['id']+'/download').status_code==403
 assert client.post(p+'/deliverables/'+external['id']+'/approve',json={'human_confirmed':False,'review_note':'未人工确认'}).status_code==422
 call('post',p+'/deliverables/'+external['id']+'/approve',json={'human_confirmed':True,'accept_open_issues':True,'review_note':'仅虚构测试，核对通知及附件遗留，以谈判阅读稿演示批准'})
 out['approved']=call('get',p)
 assert client.get(p+'/deliverables/'+external['id']+'/download').status_code==200
 assert client.get('/api/integration/projects/'+pid+'/deliverables/'+external['id']+'/download').status_code==503
 call('post',c+'/current',json={'version_id':second['id']})
 out['stale']=call('get',p)
 assert client.get(p+'/deliverables/'+external['id']+'/download').status_code==409
 out['projects']=call('get','/api/projects')
 out['official']=call('get','/api/sources/official')
 out['pid']=pid; out['cid']=cid
 Path(sys.argv[1]).write_text(json.dumps(out,ensure_ascii=False),encoding='utf8')
 print('API fixtures ready: initial, reviewed, pending, approved, stale; local gate 403/200/409; integration disabled 503')
