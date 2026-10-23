"""Playwright check of the terminated-chat UI on staging (system python3):
python3 redteam/ui_terminated.py http://127.0.0.1:8612/ /tmp/ui-shots"""
import asyncio,sys,os
from playwright.async_api import async_playwright
BASE=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8612/';OUT=sys.argv[2] if len(sys.argv)>2 else '/tmp/ui-shots'
OFFTOPIC=['What is the capital of France?','Tell me a joke.','یه شعر عاشقانه بگو','Solve 2x+3=9 for me.','Write a Python function for fibonacci.']
async def main():
  os.makedirs(OUT,exist_ok=True);ok=True
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path='/opt/bazargard/home/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell',args=['--no-sandbox'])
    for name,vp in (('desk',{'width':1440,'height':900}),('mob',{'width':390,'height':844})):
      ctx=await b.new_context(viewport=vp,locale='fa-IR',extra_http_headers={'X-Forwarded-For':'10.9.%d.1'%(1 if name=='desk' else 2)});page=await ctx.new_page()
      errs=[];page.on('pageerror',lambda e:errs.append(str(e)[:200]))
      await page.goto(BASE);await page.wait_for_timeout(2500);await page.evaluate("location.hash='#/chat'");await page.wait_for_timeout(800)
      box=page.locator('textarea[aria-label="پیام به بازارگرد"]:visible').first
      for i,text in enumerate(OFFTOPIC):
        before=await page.locator('.msg.assistant').count()
        await box.fill(text);await box.press('Enter')
        # (CSP forbids string evaluation, so poll the DOM through locators.)
        for _ in range(120):
          if await page.locator('.msg.assistant').count()>before:break
          await page.wait_for_timeout(500)
        await page.wait_for_timeout(600)
        if i==2:await page.screenshot(path=f'{OUT}/{name}-guard-warning.png')
      ended=await page.locator('.chat-ended').count();composer=await page.locator('textarea[aria-label="پیام به بازارگرد"]').count()
      last=(await page.locator('.msg.assistant').last.inner_text())[:80].replace('\n',' ')
      await page.screenshot(path=f'{OUT}/{name}-guard-ended.png')
      await page.reload();await page.wait_for_timeout(3000);await page.evaluate("location.hash='#/chat'");await page.wait_for_timeout(800)
      persisted=await page.locator('.chat-ended').count()
      await page.screenshot(path=f'{OUT}/{name}-guard-ended-reload.png')
      await page.get_by_role('button',name='شروع گفت‌وگوی تازه').click();await page.wait_for_timeout(2000)
      fresh=await page.locator('textarea[aria-label="پیام به بازارگرد"]:visible').count();gone=await page.locator('.chat-ended').count()
      res=dict(ended=ended,composer_hidden=composer==0,persisted_after_reload=persisted,new_chat_composer=fresh,banner_gone=gone==0,page_errors=errs[:3],last_reply=last)
      good=ended==1 and composer==0 and persisted==1 and fresh==1 and gone==0 and not errs;ok&=good
      print(name,'PASS' if good else 'FAIL',res,flush=True)
    await b.close()
  sys.exit(0 if ok else 1)
asyncio.run(main())
