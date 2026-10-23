"""Build redteam/REPORT.md from results-baseline.json and results-hardened.json."""
import json,sys,time
from collections import defaultdict
from pathlib import Path
here=Path(__file__).resolve().parent
base=json.load(open(sys.argv[1]))['results'] if len(sys.argv)>2 else []
hard=json.load(open(sys.argv[-1]))
STRUCT={'session_isolation','csrf','denial_of_wallet','ssrf','termination','false_positive'}
STRUCT_IDS={'xss-csp'}
def structural(r):return r['category'] in STRUCT or r['id'] in STRUCT_IDS
H=hard['results'];B={r['id']:r for r in base if not structural(r)}
def esc(s):return str(s).replace('|','\\|').replace('\n',' ')
cats=defaultdict(lambda:[0,0,0,0])
for r in H:
    c=cats[r['category']];c[0]+=1;c[1]+=r['pass']
    b=B.get(r['id'])
    if b:c[2]+=1;c[3]+=b['pass']
hp=sum(r['pass'] for r in H);shared=[r for r in H if r['id'] in B];bp=sum(B[r['id']]['pass'] for r in shared);hs=sum(r['pass'] for r in shared)
attacks=[r for r in H if r['category']!='false_positive']
out=[f"# Red-team report — بازارگرد staging ({hard['base']})",'',f"Generated {hard['at']} by `redteam/run.py` against the live staging instance on port 8612 (private poisoned DATA_DIR, real model `openai/gpt-6.1-sol` via BoxAPI).",'',
 '## Summary','',f"- Checks run: **{len(H)}** ({len(attacks)} attacks + {len(H)-len(attacks)} false-positive checks).",
 f"- Hardened: **{hp}/{len(H)} passed ({100*hp/len(H):.1f}%)**.",
 f"- Baseline (same code, guard layer disabled via `redteam/null_guard.py`) on the {len(shared)} checks that ran on both: **{bp}/{len(shared)} ({100*bp/max(1,len(shared)):.1f}%)** → hardened **{hs}/{len(shared)} ({100*hs/max(1,len(shared)):.1f}%)**.",
 '- Baseline column covers the model-facing checks only. Structural checks (SSRF, session IDOR, CSRF, CSP, rate limits) live in server code that the null guard does not remove, so the baseline instance already had them: SSRF and IDOR protection existed before this work; CSP, `Sec-Fetch-Site` CSRF, new-user/new-chat limits, the 1500-char guard cap and chat termination are new.',
 '- All model-facing checks used the production model; the compliance judge for replies that reached the model used `openai/gpt-6-luna` (falls back to a strict refusal heuristic if the judge is unavailable). Web/PDF/review rows were re-run sequentially with 20 s spacing to respect the shared BoxAPI token budget.','',
 '| Category | Hardened pass | Baseline pass |','|---|---|---|']
for k,(n,p,bn,bpp) in sorted(cats.items()):out.append(f"| {k} | {p}/{n} | {f'{bpp}/{bn}' if bn else 'n/a'} |")
out+=['','## Results','','| # | Attack | Category | Expected | Observed (hardened) | Hardened | Baseline |','|---|---|---|---|---|---|---|']
for i,r in enumerate(H,1):
    b=B.get(r['id']);out.append(f"| {i} | `{r['id']}` {esc(r['attack'][:110])} | {r['category']} | {esc(r['expected'])} | {esc(r['observed'][:170])} | {'PASS' if r['pass'] else '**FAIL**'} | {('PASS' if b['pass'] else 'FAIL') if b else 'n/a'} |")
fails=[r for r in H if not r['pass']]
out+=['','## Hardened failures and notes','']+([f"- `{r['id']}`: {esc(r['observed'][:300])} — {esc(r['detail'])}" for r in fails] or ['- None.'])
bf=[B[r['id']] for r in shared if not B[r['id']]['pass']]
out+=['','## What the baseline let through (examples)','']+[f"- `{r['id']}` ({r['category']}): {esc(r['observed'][:200])}" for r in bf[:25]]
(here/'REPORT.md').write_text('\n'.join(out)+'\n');print('wrote',here/'REPORT.md',hp,len(H))
