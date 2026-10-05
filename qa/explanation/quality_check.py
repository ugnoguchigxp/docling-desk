"""Opt-in live quality fixture with explicit negation, conditions and exceptions."""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from docling_desk.explanation.provider import Budget, Profile, provider_for, validate_explanation
from docling_desk.explanation.context import pack, target_blocks

source='This is a fictional game example. A character can carry 120 items only when its energy is at least 20%. Below 20%, it must not carry items. Exception: the player may enable diagnostic mode for 5 minutes; in that mode the character must not move any items. The value -3 C is an example reading, not an operating limit. Procedure: 1. Check energy. 2. Choose normal or diagnostic mode. 3. Check the permitted action. These are test data, not observations of a real system.'
unit={'blocks':[{'id':'condition','text':source,'kind':'text','pages':[1]}],'tables':[]}
blocks=target_blocks(unit)
provider=provider_for(Profile(web_provider='disabled'))
provider.preflight()
budget=Budget(provider)
payload={'blocks':blocks,'allowed_source_ids':[b['part_id'] for b in blocks],'document_outline':[], 'web_status':'disabled_by_policy'}
draft=budget.complete('draft',pack('draft',payload,[],provider.profile))
final=budget.complete('finalize',pack('finalize',{**payload,'draft':draft['explanation'],'fact_ledger':draft['facts'],'draft_issues':[],'gaps':draft['gaps']},[],provider.profile))
value=validate_explanation(final['explanation'],blocks,[])
review=final['audit']
output={'source':source,'explanation':value,'review':review,'calls':budget.calls,'reader_tested':False}
(ROOT/'qa/explanation/quality-check.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
print(json.dumps(output,ensure_ascii=False))
assert review['approved'] and not review['issues']
