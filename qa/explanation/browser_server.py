
from docling_desk import config as desk_config
"""Isolated synthetic QA server with deterministic model responses."""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import docling_desk.app as app
import docling_desk.explanation.service as service
from test_explanation import Fixed
from docling_desk.explanation.store import read_state
import time
import uvicorn
base=ROOT / '.cache/explanation-browser'
base.mkdir(parents=True,exist_ok=True)
provider=Fixed()
service.provider_for=lambda profile: provider
service.configured_profile=lambda: provider.profile
desk_config.DATA=base
for kind,jid in {'slide':'eb9e9df049b44b0193760203c3fb0f36','sheet':'536cfca7d87b49bfa9be5f4ddf218efb','page':'cdb93ad1297f406899ff983f8b97054b'}.items():
 folder=base/jid
 if not folder.exists():
  shutil.copytree(ROOT/'tests/fixtures/documents'/kind,folder,ignore=shutil.ignore_patterns('explanations','translation-source.json','translations','thumbnails'))
manager=service.ExplanationManager()
for folder in base.iterdir():
 if folder.name=='4'*32 or not (folder/'job.json').exists():continue
 source=service.snapshot(folder)
 for unit in source['units']:
  manager.submit(folder,unit['id'])
  while read_state(folder,unit['id'])['state'] in {'queued','running'}:time.sleep(.01)
manager.close()
manager.executor.shutdown(wait=True)
fresh=base/('4'*32)
if not fresh.exists():
 shutil.copytree(ROOT/'tests/fixtures/documents/slide',fresh,ignore=shutil.ignore_patterns('explanations','translation-source.json','translations','thumbnails'))
 job=json.loads((fresh/'job.json').read_text())
 job['id']=fresh.name
 (fresh/'job.json').write_text(json.dumps(job))
uvicorn.run(app.app,host='127.0.0.1',port=8871,access_log=False)
