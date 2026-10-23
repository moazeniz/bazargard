import asyncio,sys
from playwright.async_api import async_playwright
import os
SHOTS=os.getenv('UI_SHOTS','/tmp/ui');os.makedirs(SHOTS,exist_ok=True)
BASE=sys.argv[1] if len(sys.argv)>1 else "http://127.0.0.1:8611/"
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path="/opt/bazargard/home/.cache/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell",args=["--no-sandbox"])
    for name,vp in (('desk',{'width':1440,'height':900}),('mob',{'width':390,'height':844})):
      ctx=await b.new_context(viewport=vp,locale='fa-IR');page=await ctx.new_page()
      errs=[];page.on('pageerror',lambda e:errs.append(str(e)[:200]))
      await page.goto(BASE);await page.wait_for_timeout(2500)
      await page.screenshot(path=f'{SHOTS}/{name}-0-home.png')
      await page.goto(BASE+'#/results');await page.wait_for_timeout(800)
      box=page.locator('input[aria-label="جست‌وجوی محصول"]:visible').first
      await box.fill('هدفون بی سیم');await box.press('Enter');await page.wait_for_selector('.offer-actions .btn-primary',timeout=20000);await page.wait_for_timeout(1500)
      cards=await page.locator('.offer-grid .offer').count();print(name,'result cards',cards,'errors',errs[:2],flush=True)
      await page.screenshot(path=f'{SHOTS}/{name}-1-results.png')
      # add two offers from different shops to the plan
      btns=page.locator('.offer-actions .btn-primary');n=await btns.count()
      for i in range(min(3,n)):
        await page.locator('.offer-actions .btn-primary').first.click();await page.wait_for_timeout(400)
      print(name,'in-plan cards',await page.locator('.offer.in-plan').count(),flush=True)
      # store details from first card
      await page.locator('.offer-shop').first.click();await page.wait_for_timeout(3500)
      print(name,'shop page h1',await page.locator('.shop-hero h1').count(),'offers rows',await page.locator('.offer-row').count(),'hash',await page.evaluate('location.hash'),flush=True)
      await page.screenshot(path=f'{SHOTS}/{name}-2-shop.png',full_page=False)
      await page.evaluate("location.hash='#/map'");await page.wait_for_timeout(3000)
      print(name,'map pins',await page.locator('.leaflet-marker-icon').count(),'cards',await page.locator('.map-card').count(),flush=True)
      await page.screenshot(path=f'{SHOTS}/{name}-3-map.png')
      if await page.locator('.map-card').count():
        await page.locator('.map-card').first.click();await page.wait_for_timeout(1200)
        await page.screenshot(path=f'{SHOTS}/{name}-3b-map-popup.png')
      await page.evaluate("location.hash='#/plan'");await page.wait_for_timeout(3500)
      print(name,'plan stops',await page.locator('.plan-stop').count(),'items',await page.locator('.stop-items li').count(),'sum',(await page.locator('.summary-card').inner_text())[:80].replace(chr(10),' ') if await page.locator('.summary-card').count() else '-',flush=True)
      await page.screenshot(path=f'{SHOTS}/{name}-4-plan.png',full_page=False)
      await page.evaluate("location.hash='#/chat'");await page.wait_for_timeout(800)
      await page.screenshot(path=f'{SHOTS}/{name}-5-chat.png')
      print(name,'scrollWidth',await page.evaluate('document.documentElement.scrollWidth'),'errors',errs[:3],flush=True)
    await b.close()
asyncio.run(main())
