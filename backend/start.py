"""Portable single-worker entry point (SQLite and in-process assistant jobs).

On a fresh persistent disk the catalogue is downloaded once from CATALOG_URL
(gzip, bearer token, sha256-checked) before the app imports it. While that runs a
tiny placeholder answers 503 on the port so the platform sees a live process.
"""
import gzip,hashlib,os,shutil,threading,urllib.error,urllib.request
import obs,resilience
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import uvicorn

class Warming(BaseHTTPRequestHandler):
    def do_GET(self):
        body='{"status":"warming_up","detail":"downloading catalogue"}'.encode()
        self.send_response(503);self.send_header('Content-Type','application/json');self.send_header('Retry-After','30');self.end_headers();self.wfile.write(body)
    def log_message(self,*a):pass

def bootstrap_catalogue(port):
    data=Path(os.getenv('DATA_DIR','data'));target=data/'catalog.db';url=os.getenv('CATALOG_URL','')
    if target.exists() or not url:return
    data.mkdir(parents=True,exist_ok=True)
    placeholder=ThreadingHTTPServer(('0.0.0.0',port),Warming);threading.Thread(target=placeholder.serve_forever,daemon=True).start()
    try:
        gz=data/'catalog.db.gz.part';digest=hashlib.sha256()
        req=urllib.request.Request(url,headers={'Authorization':'Bearer '+os.getenv('CATALOG_TOKEN','')})
        obs.log('catalog.seed','info','downloading catalogue')
        def download():
            # Restarted from scratch on each attempt; network errors are transient.
            digest=hashlib.sha256()
            try:
                with urllib.request.urlopen(req,timeout=120) as r,open(gz,'wb') as out:
                    while chunk:=r.read(1<<20):digest.update(chunk);out.write(chunk)
            except urllib.error.HTTPError as e:
                if e.code in resilience.TRANSIENT_STATUS:raise resilience.Retryable('HTTP %d'%e.code)
                raise resilience.NonRetryable('HTTP %d'%e.code)
            except (urllib.error.URLError,OSError) as e:raise resilience.Retryable(type(e).__name__)
            return digest
        digest=resilience.call_sync('catalog_seed',download,attempts=4,base=5.0,cap=60.0)
        expected=os.getenv('CATALOG_SHA256','')
        if expected and digest.hexdigest()!=expected:gz.unlink();raise RuntimeError('catalogue checksum mismatch')
        part=data/'catalog.db.part'
        with gzip.open(gz,'rb') as src,open(part,'wb') as dst:shutil.copyfileobj(src,dst,1<<20)
        part.replace(target);gz.unlink();obs.log('catalog.seed','info','catalogue ready',bytes=target.stat().st_size)
    finally:
        placeholder.shutdown();placeholder.server_close()

if __name__=='__main__':
    port=int(os.getenv('PORT','8080'))
    if not 1<=port<=65535:raise ValueError('PORT must be between 1 and 65535')
    obs.setup();obs.log('app.start','info','starting',port=port)
    bootstrap_catalogue(port)
    # JSON logs only (no plain access log); SIGTERM drains and persists queued work.
    uvicorn.run('server:app',host='0.0.0.0',port=port,workers=1,proxy_headers=True,forwarded_allow_ips=os.getenv('FORWARDED_ALLOW_IPS','127.0.0.1'),access_log=False,timeout_graceful_shutdown=25)
