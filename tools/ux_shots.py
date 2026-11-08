import io, os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8012"
PW = io.open("D:/code/safeorbit安轨/output/ux/pw.txt").read().strip()
OUT = "D:/code/safeorbit安轨/reports/ux"
os.makedirs(OUT, exist_ok=True)

def shot(page, name):
    page.screenshot(path=os.path.join(OUT, name + ".png"))
    print("shot", name)

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_timeout(1200)
    shot(page, "01-landing")

    if page.locator("#landing-enter").is_visible():
        page.click("#landing-enter")
        page.wait_for_timeout(900)
    shot(page, "02-login")

    page.fill("#login-user", "uxreview")
    page.fill("#login-pass", PW)
    page.click("#login-button")
    page.wait_for_timeout(3000)
    print("login overlay visible:", page.locator("#login").is_visible())
    shot(page, "03-traces")

    if page.locator("#login").is_visible():
        print("login msg:", page.locator("#login-msg").inner_text())
    else:
        for tab, name in [("policies", "04-policies"), ("dispositions", "05-dispositions"),
                          ("codeaudit", "06-codeaudit"), ("agents", "07-agents")]:
            try:
                page.click('.tab[data-tab="%s"]' % tab)
                page.wait_for_timeout(1600)
                shot(page, name)
            except Exception as e:
                print("tab fail", tab, repr(e)[:80])

        page.click('.tab[data-tab="traces"]'); page.wait_for_timeout(1500)
        rows = page.locator("tbody tr")
        print("rows", rows.count())
        if rows.count():
            rows.first.click(); page.wait_for_timeout(2000)
            shot(page, "08-trace-detail")
            for v, n in [("resources", "09-view-resources"), ("permissions", "10-view-permissions"),
                         ("decisions", "11-view-decisions"), ("impact", "12-view-impact")]:
                try:
                    page.click('.view-tab[data-view="%s"]' % v)
                    page.wait_for_timeout(700); shot(page, n)
                except Exception as e:
                    print("view fail", v, repr(e)[:60])

    m = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                      is_mobile=True, has_touch=True)
    mp = m.new_page()
    mp.goto(BASE + "/", wait_until="networkidle"); mp.wait_for_timeout(900)
    mp.screenshot(path=os.path.join(OUT, "13-mobile-landing.png"))
    if mp.locator("#landing-enter").is_visible():
        mp.click("#landing-enter"); mp.wait_for_timeout(800)
    if mp.locator("#login-user").count() and mp.locator("#login").is_visible():
        mp.fill("#login-user", "uxreview"); mp.fill("#login-pass", PW)
        mp.click("#login-button"); mp.wait_for_timeout(3000)
    mp.screenshot(path=os.path.join(OUT, "14-mobile-traces.png"))
    print("console errors:", errs[:8])
    b.close()
