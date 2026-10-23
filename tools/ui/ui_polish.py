"""UI regression check for the polish round (desktop + mobile):
legacy dialogs restyled, seller follow-up kit, follow-up tracker, compare-12 prices,
reviews panel surviving a failed poll. Screenshots in  (default /tmp/ui)."""
import asyncio,sys,json
from playwright.async_api import async_playwright
import os
SHOTS=os.getenv('UI_SHOTS','/tmp/ui');os.makedirs(SHOTS,exist_ok=True)
BASE=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8611/'
CHROME='/opt/bazargard/home/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell'
problems=[]
def check(ok,msg):
    print(('OK  ' if ok else 'FAIL')+' '+msg,flush=True)
    if not ok:problems.append(msg)

async def modal_ok(page,name,shot):
    m=page.locator('.modal').last
    await m.wait_for(timeout=8000)
    over=await m.evaluate('e=>e.scrollWidth-e.clientWidth')
    check(over<=2,f'{name}: no horizontal overflow ({over}px)')
    await page.screenshot(path=f'{SHOTS}/polish-{shot}.png')

async def close_modal(page):
    await page.locator('.modal .modal-head .icon-btn').last.click();await page.wait_for_timeout(300)

async def run(b,name,vp):
    ctx=await b.new_context(viewport=vp,locale='fa-IR');page=await ctx.new_page()
    errs=[];page.on('pageerror',lambda e:errs.append(str(e)[:200]))
    await page.goto(BASE);await page.wait_for_timeout(2000)
    await page.goto(BASE+'#/results');await page.wait_for_timeout(600)
    box=page.locator('input[aria-label="جست‌وجوی محصول"]:visible').first
    await box.fill('هدفون بی سیم');await box.press('Enter');await page.wait_for_selector('.offer-actions .btn-primary',timeout=20000);await page.wait_for_timeout(1000)
    # legacy dialogs from the offer card menu
    for label,shot in (('هزینه و اقساط','costs'),('افزودن به سبد چندقلمی',None),('درخواست تأیید و نگهداری','request')):
        await page.locator('.offer .more button[aria-label="کارهای بیشتر"]:visible').first.click();await page.wait_for_timeout(300)
        await page.locator('[role=menuitem]',has_text=label).first.click();await page.wait_for_timeout(900)
        if shot:await modal_ok(page,label,f'{name}-{shot}');await close_modal(page)
    # seller follow-up kit
    await page.locator('.offer-actions button',has_text='پرسش از فروشنده').first.click();await page.wait_for_timeout(1200)
    await modal_ok(page,'follow-up','%s-followup'%name)
    tabs=await page.locator('.modal .tabs button').all_inner_texts()
    check(tabs==['پیگیری واقعی','ثبت پاسخ فروشنده','شبیه‌سازی (آزمایشی)'],f'follow-up tabs {tabs}')
    share=await page.locator('.modal .followup-actions a').evaluate_all('els=>els.map(e=>e.href.split(":")[0]+":"+e.textContent.trim())')
    check(any('wa.me' in s or s.startswith('https:واتس') for s in share) and any(s.startswith('sms:') for s in share),f'share links {share}')
    check(await page.locator('.modal .path-explain .sim').count()==1,'real vs simulated explained')
    await page.locator('.modal .tabs button',has_text='شبیه‌سازی').click();await page.wait_for_timeout(400)
    await page.screenshot(path=f'{SHOTS}/polish-{name}-simulation.png');await close_modal(page)
    # plan + tracker + plan dialogs
    for i in range(2):
        await page.locator('.offer-actions .btn-primary').nth(i).click();await page.wait_for_timeout(300)
    await page.evaluate("location.hash='#/plan'");await page.wait_for_timeout(3500)
    check(await page.locator('.followup-tracker').count()==1,'follow-up tracker on plan page')
    sel=page.locator('.followup-tracker select').first
    await sel.select_option('asked');await page.wait_for_timeout(800)
    await page.reload();await page.wait_for_timeout(2500);await page.evaluate("location.hash='#/plan'");await page.wait_for_timeout(2500)
    check(await page.locator('.followup-tracker select').first.input_value()=='asked','tracker status persisted')
    await page.screenshot(path=f'{SHOTS}/polish-{name}-plan.png',full_page=True)
    await page.locator('.plan-page .page-head button',has_text='مهلت').click();await page.wait_for_timeout(800)
    await modal_ok(page,'trip timing','%s-mission'%name);await close_modal(page)
    await page.locator('.plan-summary button',has_text='حالت سفر').click();await page.wait_for_timeout(1200)
    await modal_ok(page,'trip mode','%s-street'%name);await close_modal(page)
    # compare first 12 from the all-results tab
    await page.evaluate("location.hash='#/results'");await page.wait_for_timeout(800)
    await page.locator('button',has_text='همه نتایج').first.click();await page.wait_for_timeout(2500)
    chip=page.locator('button.chip',has_text='مقایسه').first
    if await chip.count():
        await chip.click();await page.wait_for_selector('.modal tr',timeout=30000);await page.wait_for_timeout(1500)
        prices=await page.evaluate("""()=>{const row=[...document.querySelectorAll('.modal tr')].find(r=>/قیمت/.test(r.cells?.[0]?.textContent||''));return row?[...row.cells].slice(1).map(c=>c.textContent.trim()):[]}""")
        filled=[p for p in prices if p and 'موجود نیست' not in p and p!='—']
        check(len(prices)>=10 and len(filled)>=len(prices)-1,f'compare-12 prices filled {len(filled)}/{len(prices)}')
        await page.screenshot(path=f'{SHOTS}/polish-{name}-compare12.png');await close_modal(page)
    else:check(False,'compare chip on all-results tab')
    # reviews panel survives a failed poll
    pid=await page.evaluate("()=>{const a=document.querySelector('.offer a[href*=\"#/product/\"]');return a?a.getAttribute('href').split('/').pop():null}")
    if not pid:
        pid=json.loads(await page.evaluate("async()=>JSON.stringify((await (await fetch('api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:localStorage.getItem('hamrah-session-v1'),query:'هدفون بی سیم'})})).json()).offers[0].product_id)"))
    failed=[0]
    async def flaky(route):
        if failed[0]<1:failed[0]+=1;await route.abort()
        else:await route.continue_()
    await page.route('**/reviews?*',flaky)
    await page.evaluate(f"location.hash='#/product/{pid}'");await page.wait_for_timeout(2500);await page.locator('.reviews-panel').first.scroll_into_view_if_needed();await page.wait_for_timeout(3500)
    text=await page.locator('.reviews-panel').first.inner_text() if await page.locator('.reviews-panel').count() else ''
    check(failed[0]==1 and 'کامل نشد' not in text,f'reviews panel keeps polling after a failed request (aborted={failed[0]}, panel={text[:60]!r})')
    await page.screenshot(path=f'{SHOTS}/polish-{name}-reviews.png')
    check(not errs,f'{name}: no page errors {errs[:2]}')
    check(await page.evaluate('document.documentElement.scrollWidth')<=vp['width']+1,f'{name}: no horizontal page scroll')
    await ctx.close()

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(executable_path=CHROME,args=['--no-sandbox'])
        for name,vp in (('desk',{'width':1440,'height':900}),('mob',{'width':390,'height':844})):
            try:await run(b,name,vp)
            except Exception as e:check(False,f'{name}: {type(e).__name__}: {str(e)[:300]}')
        await b.close()
    print('PROBLEMS',len(problems));sys.exit(1 if problems else 0)
asyncio.run(main())
