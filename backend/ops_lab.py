"""A real, deliberately small website for isolated deployment/recovery exercises."""
import argparse,json,time,traceback
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--port',type=int,required=True);a=p.parse_args()
 class Site(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_GET(self):
   started=time.monotonic();code=200
   try:
    c=json.loads(Path(a.config).read_text());fault=c.get('fault','none')
    if fault=='missing_config' or not c.get('site_title'):raise KeyError('SITE_TITLE is missing')
    if fault=='bad_release':raise RuntimeError('BAD_RELEASE: v2 application startup contract failed')
    if fault=='slow':time.sleep(.7)
    body={'site':c['site_title'],'version':c.get('version','v1'),'status':'ok','message':'سایت آزمایشی واقعاً روی Arvan اجرا می‌شود'}
   except Exception as e:
    code=500;body={'status':'error','error':type(e).__name__,'message':str(e)}
    print(json.dumps({'at':time.time(),'level':'error','error':type(e).__name__,'message':str(e)},ensure_ascii=False),flush=True)
   raw=json.dumps(body,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
   print(json.dumps({'at':time.time(),'level':'access','status':code,'latency_ms':round((time.monotonic()-started)*1000,1)},ensure_ascii=False),flush=True)
 ThreadingHTTPServer(('127.0.0.1',a.port),Site).serve_forever()

if __name__=='__main__':main()
