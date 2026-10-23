"""UI check of «تازه‌سازی قیمت» against a running instance. usage: ui_fresh.py BASE [--chat] [--web]"""
import asyncio,json,os,re,sys,time
from playwright.async_api import async_playwright
BASE=sys.argv[1];CHAT='--chat' in sys.argv;WEB='--web' in sys.argv
OUT=os.getenv('UI_SHOTS','/tmp/ui-shots');os.makedirs(OUT,exist_ok=True)
CHROME=os.getenv('CHROME','/opt/bazargard/home/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell')
ok=True
def check(name,cond,extra=''):
    global ok;ok&=bool(cond);print(('PASS ' if cond else 'FAIL ')+name,str(extra)[:300],flush=True)
async def search(page,q):
    await page.evaluate("location.hash='#/results'");await page.wait_for_timeout(1200)
    box=page.locator('input[aria-label="جست‌وجوی محصول"]:visible').first;await box.fill(q);await box.press('Enter');await page.wait_for_timeout(5000)
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(executable_path=CHROME,args=['--no-sandbox'])
        ctx=await b.new_context(viewport={'width':1440,'height':900},locale='fa-IR',timezone_id='Asia/Tehran')
        page=await ctx.new_page();errs=[];page.on('pageerror',lambda e:errs.append(str(e)[:200]))
        await page.goto(BASE+'#/results');await page.wait_for_timeout(3000)
        await search(page,'کیو سی وای T13 ANC 2')
        card=page.locator('.offer-grid article.offer',has_text='آودیس ماهان').first
        est=await page.locator('.offer-estimate').count()
        check('stale cards show «برآورد امروز» with a confidence label',est>0,(await page.locator('.offer-estimate').first.inner_text()) if est else '')
        await card.locator('button:has-text("افزودن به برنامه")').click();await page.wait_for_timeout(800)
        ab=page.locator('.offer-grid .ask-price')
        if await ab.count():
            await ab.first.click();await page.wait_for_timeout(2500)
            check('stale card button opens the one-tap link card',await page.locator('.fr-link').count());await page.keyboard.press('Escape');await page.wait_for_timeout(500)
        else:check('stale card has the ask button',False)
        await page.screenshot(path=f'{OUT}/fr-01-estimate.png')
        url=None
        if CHAT:
            await page.evaluate("location.hash='#/chat'");await page.wait_for_timeout(800)
            box=page.locator('textarea[aria-label="پیام به بازارگرد"]:visible').first
            await box.fill('قیمت کیو سی وای T13 ANC 2 در آودیس ماهان قدیمیه؛ همین الان از فروشنده بپرس.');await box.press('Enter')
            t=time.time()
            while time.time()-t<120 and not await page.locator('.fr-link').count():await page.wait_for_timeout(1000)
            check('agent prepares the one-tap link (panel)',await page.locator('.fr-link').count(),round(time.time()-t))
            while time.time()-t<150 and await page.locator('button.send .spin').count():await page.wait_for_timeout(1000)
            bub=page.locator('.bubble.markdown');n=await bub.count();print('REPLY',(await bub.nth(n-1).inner_text())[:500].replace('\n',' | ') if n else '-')
            await page.screenshot(path=f'{OUT}/fr-02-agent-link.png')
            if await page.locator('.fr-link').count():url=await page.locator('.fr-preview').get_attribute('href');await page.keyboard.press('Escape')
        if not url:
            await page.evaluate("location.hash='#/plan'");await page.wait_for_timeout(5000)
            await page.locator('.plan-stop',has_text='آودیس ماهان').first.locator('button:has-text("تأیید قیمت و موجودی")').click();await page.wait_for_timeout(1500)
            m=page.locator('.modal').first;await m.locator('button:has-text("لینک یک‌لمسی تازه‌سازی قیمت")').click();await page.wait_for_timeout(2500)
            msg=await m.locator('.followup-msg textarea').input_value();url=re.search(r'https?://\S+\?shop=\S+',msg).group(0)
            check('contact modal adds the shop link to the message','?shop=' in url);await page.screenshot(path=f'{OUT}/fr-02-contact-link.png');await page.keyboard.press('Escape')
        # seller side, same browser for the check (a phone in reality)
        seller=await ctx.new_page();await seller.set_viewport_size({'width':390,'height':844});seller.on('pageerror',lambda e:errs.append(str(e)[:200]))
        await seller.goto(url);await seller.wait_for_timeout(3000)
        rows=seller.locator('.fr-items li');check('seller page lists the shop offers',await rows.count()>0,await rows.count())
        row=seller.locator('.fr-items li',has_text='T13 ANC 2').first
        check('the demanded item comes first with its reason','T13' in await rows.first.inner_text(),(await rows.first.inner_text())[:120].replace('\n',' '))
        await seller.screenshot(path=f'{OUT}/fr-03-seller.png')
        await row.locator('button:has-text("قیمت جدید")').click();await row.locator('input').fill('2690000');await row.locator('button:has-text("ثبت قیمت")').click();await seller.wait_for_timeout(2500)
        check('seller update confirmed on the page','تأییدشده امروز' in await row.inner_text(),(await row.inner_text())[:160].replace('\n',' '))
        check('no horizontal scroll on the seller page',await seller.evaluate('document.documentElement.scrollWidth')<=390)
        await seller.screenshot(path=f'{OUT}/fr-04-seller-done.png');await seller.close()
        await page.evaluate("location.hash='#/results'");await page.wait_for_timeout(800);await page.evaluate("location.hash='#/plan'");await page.wait_for_timeout(6000)
        stop=page.locator('.plan-stop',has_text='آودیس ماهان').first
        txt=await stop.inner_text()
        check('plan shows the new price with «قیمت تأییدشده امروز»','قیمت تأییدشده امروز' in txt and '۲٬۶۹۰٬۰۰۰' in txt,txt[:200].replace('\n',' '))
        trk=page.locator('select[aria-label="وضعیت پیگیری آودیس ماهان"]')
        check('tracker marks the stop confirmed via the price link',await trk.count() and await trk.input_value()=='confirmed')
        await stop.scroll_into_view_if_needed();await page.screenshot(path=f'{OUT}/fr-05-plan.png')
        await search(page,'کیو سی وای T13 ANC 2')
        c2=page.locator('.offer-grid article.offer',has_text='آودیس ماهان').first
        check('result card shows the confirmed badge and basis',('قیمت تأییدشده امروز' in await c2.inner_text()) and ('قیمت تأییدشدهٔ فروشنده' in await c2.inner_text()))
        await page.evaluate("fetch('api/bootstrap').then(r=>r.json()).then(d=>fetch('api/offers/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:d.sessions[0].id,offer_ids:['offer_3724c5eeb5508a2c4076a6d5']})}))");await page.wait_for_timeout(1500)  # the charger at زون تل شاپ in a plan
        await page.goto(BASE+'?workspace=freshness');await page.wait_for_timeout(5000)
        check('freshness board shows queue and stats',await page.locator('.fr-queue li').count()>0 and await page.locator('.fr-stats div').count()==6,await page.locator('.fr-queue li').count())
        await page.screenshot(path=f'{OUT}/fr-06-board.png')
        await page.click('button:has-text("درخواست خودکار")');await page.wait_for_timeout(4000)
        check('auto-ask sends to the top shops',await page.locator('.fr-sent p').count()>0,(await page.locator('.fr-sent').inner_text())[:200].replace('\n',' | ') if await page.locator('.fr-sent').count() else '')
        if WEB:
            w=page.locator('.fr-queue li',has_text='زون تل شاپ').first
            if await w.count():
                t=time.time();await w.locator('button:has-text("بررسی از سایت فروشگاه")').click()
                while time.time()-t<150 and not await w.locator('.fr-web').count():await page.wait_for_timeout(2000)
                check('web check returns a verdict',await w.locator('.fr-web').count(),(await w.locator('.fr-web').inner_text() if await w.locator('.fr-web').count() else '')+' %ds'%(time.time()-t))
            else:check('web check row present',False)
        await page.screenshot(path=f'{OUT}/fr-07-board-after.png')
        check('no page errors',not errs,errs[:3])
        await b.close()
    print('ALL PASS' if ok else 'SOME CHECKS FAILED')
asyncio.run(main())
