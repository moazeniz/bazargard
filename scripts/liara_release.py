#!/usr/bin/env python3
"""Deploy and operate the Liara app through the Liara REST API (stdlib only).

Credentials come from the environment only and are never printed:
  LIARA_API_TOKEN   API token (Liara console -> API)
  LIARA_TEAM_ID     team id (appended as ?teamID=)
  LIARA_API_BASE    default https://api.iran.liara.ir
  LIARA_APP         default bazargard-ai

  scripts/liara_release.py deploy [--message TEXT] [--no-wait]   package (.liaraignore), upload, release, wait
  scripts/liara_release.py wait                                  poll the latest release until it settles
  scripts/liara_release.py releases [--count N]                  list releases (current one marked *)
  scripts/liara_release.py rollback [RELEASE_ID]                 roll back (default: previous READY release)
  scripts/liara_release.py status                                project state + CPU/memory/disk snapshot
  scripts/liara_release.py logs [--minutes N] [--search TEXT]    recent runtime logs
  scripts/liara_release.py restart | start | stop

Release bodies never include `envs` (that would replace every variable); manage
variables in the console, with `liara env:set`, or POST /v1/projects/update-envs.
"""
import argparse,json,os,subprocess,sys,time,urllib.error,urllib.request,uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=os.getenv('LIARA_API_BASE','https://api.iran.liara.ir').rstrip('/')
APP=os.getenv('LIARA_APP','bazargard-ai')
SETTLED={'READY','FAILED','CANCELED','TIMEDOUT','UNHEALTHY'}

def call(method,path,body=None,raw=None,ctype=None,timeout=60):
    token,team=os.getenv('LIARA_API_TOKEN'),os.getenv('LIARA_TEAM_ID')
    if not token or not team:sys.exit('set LIARA_API_TOKEN and LIARA_TEAM_ID in the environment')
    data=raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers={'Authorization':'Bearer '+token}
    if data is not None:headers['Content-Type']=ctype or 'application/json'
    req=urllib.request.Request(BASE+path+('&' if '?' in path else '?')+'teamID='+team,data=data,method=method,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:code,text=r.status,r.read()
    except urllib.error.HTTPError as e:code,text=e.code,e.read()
    try:payload=json.loads(text or b'null')
    except ValueError:payload=text.decode(errors='replace')[:500]
    return code,payload

def need(code,payload,what):
    if code>=300:sys.exit(f'{what} failed: HTTP {code} {json.dumps(payload,ensure_ascii=False)[:400]}')
    return payload

def releases(count=10):
    return need(*call('GET',f'/v1/projects/{APP}/releases?page=1&count={count}'),'list releases')

def wait(timeout=900):
    start=time.time()
    while time.time()-start<timeout:
        rel=releases(1)['releases'][0];state=rel.get('state')
        print(f"{int(time.time()-start):4d}s {rel.get('tag','')} {state}",flush=True)
        if state in SETTLED:return state
        time.sleep(10)
    return 'TIMEOUT'

def deploy(message,no_wait):
    config=json.loads((ROOT/'liara.json').read_text());archive=ROOT/'dist'/'bazargard-source.tar.gz'
    subprocess.run([str(ROOT/'scripts'/'liara-package.sh'),str(archive)],check=True)
    boundary='----bazargard'+uuid.uuid4().hex
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="source.tar.gz"\r\n'
          'Content-Type: application/gzip\r\n\r\n').encode()+archive.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
    source=need(*call('POST',f'/v2/projects/{APP}/sources',raw=body,ctype='multipart/form-data; boundary='+boundary,timeout=600),'upload')['sourceID']
    release={'sourceID':source,'type':config.get('platform','docker'),'port':int(config.get('port',8080)),'message':message}
    if config.get('disks'):release['disks']=config['disks']
    out=need(*call('POST',f'/v2/projects/{APP}/releases',release),'create release')
    print('release',out.get('releaseID',out))
    if not no_wait:
        state=wait();print(state)
        if state!='READY':sys.exit(1)

def rollback(target):
    data=releases(20);current=data.get('currentRelease')
    if not target:
        ready=[r for r in data['releases'] if r.get('state')=='READY' and r.get('type') in ('DEPLOY','ENV_UPDATE') and r['_id']!=current]
        if not ready:sys.exit('no earlier READY release to roll back to')
        target=ready[0]['_id'];print('rolling back to',ready[0].get('tag'),target)
    code,payload=call('POST',f'/v1/releases/{target}/rollback')
    need(code,payload,'rollback');print('rollback accepted; polling');print(wait())

def status():
    project=need(*call('GET',f'/v1/projects/{APP}'),'project')['project']
    print(json.dumps({k:project.get(k) for k in ('project_id','status','scale','planID','bundlePlanID','isDeployed','defaultSubdomain')},ensure_ascii=False))
    code,summary=call('GET',f'/v1/projects/{APP}/metrics/summary')
    if code<300:
        for kind in ('cpuUsage','memoryUsage'):
            for item in summary.get(kind) or []:print(kind,item.get('value',[None,None])[1])
        for disk in summary.get('disksUsage') or []:print('disk',disk.get('name'),disk.get('usage'),'/',disk.get('size'))

def logs(minutes,search):
    query=f'?start={int(time.time())-60*minutes}&direction=forward'+(f'&search={urllib.request.quote(search)}' if search else '')
    data=need(*call('GET',f'/v2/projects/{APP}/logs'+query,timeout=60),'logs')
    lines=[]
    for group in data.get('data') or []:
        for ts,entry in group.get('values') or []:
            try:entry=json.loads(entry).get('_entry',entry)
            except ValueError:pass
            lines.append((int(ts),entry))
    for ts,entry in sorted(lines):print(time.strftime('%H:%M:%S',time.localtime(ts//10**9)),entry)

def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter);sub=p.add_subparsers(dest='cmd',required=True)
    d=sub.add_parser('deploy');d.add_argument('--message',default='deploy');d.add_argument('--no-wait',action='store_true')
    sub.add_parser('wait');r=sub.add_parser('releases');r.add_argument('--count',type=int,default=10)
    b=sub.add_parser('rollback');b.add_argument('release_id',nargs='?')
    sub.add_parser('status');l=sub.add_parser('logs');l.add_argument('--minutes',type=int,default=30);l.add_argument('--search')
    for name in ('restart','start','stop'):sub.add_parser(name)
    a=p.parse_args()
    if a.cmd=='deploy':deploy(a.message,a.no_wait)
    elif a.cmd=='wait':print(wait())
    elif a.cmd=='releases':
        data=releases(a.count)
        for rel in data['releases']:print('*' if rel['_id']==data.get('currentRelease') else ' ',rel.get('tag'),rel['_id'],rel.get('type'),rel.get('state'),rel.get('createdAt'))
    elif a.cmd=='rollback':rollback(a.release_id)
    elif a.cmd=='status':status()
    elif a.cmd=='logs':logs(a.minutes,a.search)
    elif a.cmd=='restart':print(need(*call('POST',f'/v1/projects/{APP}/actions/restart'),'restart') or 'restarting')
    else:print(need(*call('POST',f'/v1/projects/{APP}/actions/scale',{'scale':1 if a.cmd=='start' else 0}),a.cmd) or a.cmd)

if __name__=='__main__':main()
