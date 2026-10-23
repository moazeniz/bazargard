"""UI check of the developer console (desktop side/bottom dock + mobile sheet) against a DEV_CONSOLE=1 instance.

Usage: DEV_SECRETS=a,b,c python tools/ui/ui_devconsole.py http://127.0.0.1:8611/
No login: the console is an on/off toggle (remembered per browser). Sends ONE chat message (one real
model run). DEV_SECRETS (keys, gate/admin tokens) come from the environment and are never printed;
the check fails if any of them shows up in the page or the exported trace.
"""
import asyncio,json,os,sys,time
from playwright.async_api import async_playwright
BASE=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8611/'
OUT=os.getenv('UI_SHOTS','/tmp/ui-shots')
SECRETS=[s for s in os.getenv('DEV_SECRETS','').split(',') if len(s)>=6]
CHROME=os.getenv('CHROME','/opt/bazargard/home/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell')
def leak(text):return sum(1 for s in SECRETS if s in text)

async def main():
  ok=True
  def check(name,cond,extra=''):
    nonlocal ok;ok&=bool(cond);print(('PASS ' if cond else 'FAIL ')+name,extra,flush=True)
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path=CHROME,args=['--no-sandbox'])
    ctx=await b.new_context(viewport={'width':1440,'height':900},locale='fa-IR',accept_downloads=True);page=await ctx.new_page()
    errs=[];page.on('pageerror',lambda e:errs.append(str(e)[:200]))
    await page.goto(BASE+'#/chat');await page.wait_for_timeout(2500)
    rail=page.locator('.rail .rail-item:has-text("کنسول")')
    check('desktop rail shows «کنسول» above گفت‌وگوها, console off by default',await rail.is_visible() and await rail.get_attribute('aria-checked')=='false' and await page.locator('.devc').count()==0 and await page.evaluate("(()=>{const r=[...document.querySelectorAll('.rail .rail-item')].map(b=>b.innerText.trim());return r.indexOf('کنسول')>=0&&r.indexOf('کنسول')<r.indexOf('گفت‌وگوها')})()"))
    await rail.click();await page.wait_for_selector('.devc-filters',timeout=5000)
    check('rail toggle turns it on with no login form',await page.locator('.devc-login, input[type=password]').count()==0 and await rail.get_attribute('aria-checked')=='true')
    await page.reload();await page.wait_for_selector('.devc-filters',timeout=8000)
    check('on/off choice survives a reload',await page.locator('.rail .rail-item:has-text("کنسول")').get_attribute('aria-checked')=='true')
    await rail.click();await page.wait_for_timeout(300);off=await page.locator('.devc').count()==0
    await page.reload();await page.wait_for_timeout(2500);still_off=await page.locator('.devc').count()==0
    await page.keyboard.press('Control+Backquote');await page.wait_for_selector('.devc-filters',timeout=5000)
    check('toggle off persists; ctrl+` turns it back on',off and still_off)
    box=page.locator('textarea[aria-label="پیام به بازارگرد"]:visible').first
    await box.fill('هدفون بلوتوث زیر دو میلیون تومان');await box.press('Enter')
    started=time.time();seen=[];status=''
    while time.time()-started<120:
      await page.wait_for_timeout(700)
      status=(await page.locator('.devc-run .devc-status').first.inner_text()) if await page.locator('.devc-run').count() else ''
      seen.append((round(time.time()-started,1),status,await page.locator('.devc-run').first.locator('.devc-ev').count() if status else 0))
      if status=='کامل' and not await page.locator('button.send .spin').count():break
    running=[n for _,s,n in seen if s and s!='کامل']
    check('run finished',status=='کامل','after %.0fs'%(time.time()-started))
    check('timeline updated live while running',len(set(running))>=2,'event counts while running: %s'%sorted(set(running)))
    await page.wait_for_timeout(1500)
    run=page.locator('.devc-run').first
    kinds={k:await run.locator('.devc-ev.k-'+k).count() for k in ('run','guard','model','tool','queue','external')}
    check('model rounds, tool calls and guard decision present',kinds['model']>=1 and kinds['tool']>=1 and kinds['guard']>=1,kinds)
    model=await run.locator('.devc-ev.k-model').first.inner_text();tool=await run.locator('.devc-ev.k-tool:has-text("search")').first.inner_text() if await run.locator('.devc-ev.k-tool:has-text("search")').count() else await run.locator('.devc-ev.k-tool').first.inner_text()
    check('model row shows provider/model/tokens',('in ' in model and 'out ' in model),model.replace('\n',' | ')[:220])
    check('tool row shows label, query and hits',('«' in tool and 'نتیجه' in tool),tool.replace('\n',' | ')[:260])
    totals=await page.inner_text('.devc-totals');check('session totals + time breakdown',('مدل' in totals and 'ابزار' in totals and 'سایر' in totals),totals.replace('\n',' | ')[:260])
    await page.screenshot(path=f'{OUT}/devc-desk-side.png')
    await page.click('.devc-filters button:has-text("ابزار")');await page.wait_for_timeout(300)
    visible=await run.locator('.devc-ev').count();tools_only=await run.locator('.devc-ev.k-tool').count()
    check('filter by type',visible==tools_only and visible>=1,'%d/%d'%(tools_only,visible))
    await run.locator('.devc-ev .devc-actions button').first.click();await page.wait_for_selector('.devc-json')
    j=await page.inner_text('.devc-json');check('JSON expands',('tool' in j and 'args' in j),j.replace('\n',' ')[:160])
    await page.screenshot(path=f'{OUT}/devc-desk-tool-json.png')
    async with page.expect_download() as dl:await page.click('button[aria-label="خروجی JSON همه اجراها"]')
    path=await (await dl.value).path();data=json.load(open(path))
    check('export downloads the session trace as JSON',data['runs'] and data['runs'][0]['events'],'%d runs, %d events'%(len(data['runs']),len(data['runs'][0]['events'])))
    blob=json.dumps(data,ensure_ascii=False)+await page.content()
    check('no secrets/gate token/system prompt in the trace or page',leak(blob)==0 and '"system"' not in json.dumps(data))
    await page.click('.devc-filters button:has-text("همه")');await page.click('button[aria-label="تغییر جای کنسول"]');await page.wait_for_timeout(400)
    check('bottom drawer dock',await page.locator('.devc-bottom').count()==1)
    await page.screenshot(path=f'{OUT}/devc-desk-bottom.png')
    # Mobile: same browser (same chats), full-screen sheet from the top-bar icon; the sidebar switch still exists.
    await page.click('button[aria-label="خاموش کردن کنسول"]')
    m=await ctx.new_page();m.on('pageerror',lambda e:errs.append(str(e)[:200]));await m.set_viewport_size({'width':390,'height':844})
    await m.goto(BASE+'#/chat');await m.wait_for_timeout(2500)
    icon=m.locator('.mobile-bar button[aria-label^="کنسول توسعه‌دهنده"]')
    check('mobile top bar shows the console icon',await icon.is_visible() and await icon.get_attribute('aria-checked')=='false')
    await m.screenshot(path=f'{OUT}/devc-mob-topbar.png')
    await m.click('button[aria-label="گفت‌وگوها و تنظیمات"]');switch=m.locator('.drawer-foot [role=switch]')
    check('sidebar on/off switch present',await switch.count()==1 and 'خاموش' in await switch.inner_text())
    await switch.click();await m.wait_for_selector('.devc-run',timeout=8000);await m.click('button[aria-label="خاموش کردن کنسول"]');await m.wait_for_timeout(300)
    await icon.click();await m.wait_for_selector('.devc-run',timeout=8000)
    check('top-bar icon toggles the sheet',await icon.get_attribute('aria-checked')=='true')
    rect=await m.evaluate("(()=>{const r=document.querySelector('.devc').getBoundingClientRect();return [r.width,r.height]})()")
    check('mobile full-screen sheet',rect[0]>=389 and rect[1]>=843,rect)
    check('mobile has no horizontal scroll',await m.evaluate('document.documentElement.scrollWidth')<=390)
    await m.screenshot(path=f'{OUT}/devc-mob-sheet.png')
    await m.locator('.devc-ev.k-tool .devc-actions button').first.click();await m.wait_for_timeout(300);await m.locator('.devc-json').first.scroll_into_view_if_needed()
    await m.screenshot(path=f'{OUT}/devc-mob-json.png')
    check('no page errors',not errs,errs[:3])
    await b.close()
  print('ALL PASS' if ok else 'SOME CHECKS FAILED',flush=True)
asyncio.run(main())
