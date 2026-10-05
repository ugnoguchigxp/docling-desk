"""Opt-in live QA on copies of synthetic fixtures; never uses private documents."""
import json
import shutil
import time
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from docling_desk.explanation.service import ExplanationManager, result
from docling_desk.explanation.store import read_state

samples = {'slide':'eb9e9df049b44b0193760203c3fb0f36','sheet':'536cfca7d87b49bfa9be5f4ddf218efb','page':'cdb93ad1297f406899ff983f8b97054b'}
base = ROOT / '.cache/explanation-live'
base.mkdir(parents=True,exist_ok=True)
manager = ExplanationManager()
checks=[]
try:
 for kind, jid in samples.items():
  if '--all' not in sys.argv and kind != 'slide': continue
  folder = base / jid
  if not folder.exists():
   shutil.copytree(ROOT / 'tests/fixtures/documents' / kind, folder,ignore=shutil.ignore_patterns('translations','translation-source.json','thumbnails','explanations'))
  manager.submit(folder,kind+'-1',True)
  while read_state(folder,kind+'-1')['state'] in {'queued','running'}:
   state=read_state(folder,kind+'-1')
   print(kind,state['stage'],flush=True)
   time.sleep(8)
  record=result(folder,kind+'-1')
  value=record['result']
  check={'kind':kind,'state':record['state']['state'],'error':record['state'].get('error'),'web_status':value['web_search']['status'] if value else None,'calls':value['verification']['calls'] if value else None}
  checks.append(check)
  print(json.dumps(check,ensure_ascii=False),flush=True)
  if value:
   (ROOT / 'qa/explanation' / f'live-{kind}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
finally:
 manager.close()
 manager.executor.shutdown(wait=True)
 (ROOT / 'qa/explanation/live-checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
