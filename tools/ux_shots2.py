import io, os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8012"
PW = io.open("D:/code/safeorbit安轨/output/ux/pw.txt").read().strip()
OUT = "D:/code/safeorbit安轨/reports/ux"
os.makedirs(OUT, exist_ok=True)

def login(page):
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_timeout(1000)
    if page.locator("#landing-enter").is_visible():
        page.click("#landing-enter"); page.wait_for_timeout(700)
    page.fill("#login-user", "uxreview")
    page.fill("#login-pass", PW)
    page.click("#login-button")
    page.wait_for_timeout(3000)
    return not page.locator("#login").is_visible()

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    print("login:", login(page))

    page.click('summary:has-text("最近记录")')
    page.wait_for_timeout(900)
    page.screenshot(path=os.path.join(OUT, "08-trace-list.png"))
    print("row count:", page.locator("#traces tr").count())

    row = page.locator("#traces tr").first
    row.click()
    page.wait_for_timeout(2200)
    page.screenshot(path=os.path.join(OUT, "09-trace-detail.png"))
    for v, n in [("resources", "10-view-resources"), ("permissions", "11-view-permissions"),
                 ("decisions", "12-view-decisions"), ("impact", "13-view-impact")]:
        try:
            page.click('.view-tab[data-view="%s"]' % v)
            page.wait_for_timeout(700)
            page.screenshot(path=os.path.join(OUT, n + ".png"))
        except Exception as e:
            print("view fail", v, repr(e)[:60])

    m = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                      is_mobile=True, has_touch=True)
    mp = m.new_page()
    print("mobile login:", login(mp))
    mp.screenshot(path=os.path.join(OUT, "15-mobile-console.png"))
    mp.click('summary:has-text("最近记录")'); mp.wait_for_timeout(900)
    mp.screenshot(path=os.path.join(OUT, "16-mobile-list.png"))
    b.close()
