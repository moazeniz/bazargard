"""Evidence screenshots for docs/EVIDENCE.md (desktop + mobile) from a running app."""
import asyncio,sys
from playwright.async_api import async_playwright
BASE=sys.argv[1];OUT=sys.argv[2]
CHROME='/opt/bazargard/home/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell'
async def shot(page,name):await page.screenshot(path=f'{OUT}/{name}.png');print('saved',name,flush=True)
async def run(b,tag,vp,chat):
    ctx=await b.new_context(viewport=vp,locale='fa-IR');page=await ctx.new_page();errs=[];page.on('pageerror',lambda e:errs.append(str(e)[:150]))
    await page.goto(BASE);await page.wait_for_timeout(2500)
    if chat:
        box=page.locator('textarea[aria-label="پیام به بازارگرد"]:visible').first
        await box.fill('یه هدفون بی‌سیم تا ۳ میلیون می‌خوام');await box.press('Enter')
        await page.wait_for_function("()=>document.querySelectorAll('.message.assistant, .msg.assistant, [data-role=assistant]').length>0 || /\\[گزینه/.test(document.body.innerText)",timeout=90000)
        await page.wait_for_timeout(2500);await shot(page,f'{tag}-chat-answer')
    await page.goto(BASE+'#/results');await page.wait_for_timeout(800)
    box=page.locator('input[aria-label="جست‌وجوی محصول"]:visible').first
    await box.fill('هدفون بی سیم');await box.press('Enter');await page.wait_for_selector('.offer-actions .btn-primary',timeout=30000);await page.wait_for_timeout(1200)
    await shot(page,f'{tag}-results')
    await page.locator('button',has_text='همه نتایج').first.click();await page.wait_for_timeout(2500)
    await page.locator('button.chip',has_text='مقایسه').first.click();await page.wait_for_selector('.modal tr',timeout=30000);await page.wait_for_timeout(2500)
    await shot(page,f'{tag}-comparison');await page.locator('.modal .modal-head .icon-btn').last.click();await page.wait_for_timeout(400)
    await page.evaluate("location.hash='#/results'");await page.wait_for_timeout(800)
    await page.locator('button',has_text='پیشنهاد بازارگرد').first.click();await page.wait_for_timeout(800)
    await page.locator('.offer-actions button',has_text='پرسش از فروشنده').first.click();await page.wait_for_timeout(1500)
    await shot(page,f'{tag}-seller-followup');await page.locator('.modal .modal-head .icon-btn').last.click();await page.wait_for_timeout(300)
    for i in range(2):await page.locator('.offer-actions .btn-primary').nth(i).click();await page.wait_for_timeout(300)
    await page.evaluate("location.hash='#/plan'");await page.wait_for_timeout(3500);await page.screenshot(path=f'{OUT}/{tag}-plan.png',full_page=True);print('saved plan')
    await page.evaluate("location.hash='#/map'");await page.wait_for_timeout(3000);await shot(page,f'{tag}-map')
    await page.evaluate("location.hash='#/chat'");await page.wait_for_timeout(600)
    opener=page.locator('button.rail-item',has_text='داده‌ها')
    if await opener.count() and await opener.first.is_visible():await opener.first.click()
    else:
        await page.locator('button[aria-label*="منو"], .mobile-bar button').first.click();await page.wait_for_timeout(500)
        await page.locator('button',has_text='درباره داده‌ها').first.click()
    await page.wait_for_selector('.data-facts .df-tile',timeout=30000);await page.wait_for_timeout(800);await shot(page,f'{tag}-about-data')
    print(tag,'page errors',errs);await ctx.close()
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(executable_path=CHROME,args=['--no-sandbox'])
        await run(b,'desktop',{'width':1440,'height':900},True)
        await run(b,'mobile',{'width':390,'height':844},False)
        await b.close()
asyncio.run(main())
