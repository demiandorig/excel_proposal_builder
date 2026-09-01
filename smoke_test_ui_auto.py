"""
End-to-end UI smoke test using Playwright.
Drives through all 5 steps and saves screenshots to ./screenshots/
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

SCREENSHOTS = Path(__file__).parent / "screenshots"
SCREENSHOTS.mkdir(exist_ok=True)

NOTION_TEXT = """Requested by: Camilo Arias
Salesperson market: Los Angeles
Salesperson email: camilo.arias@entravision.com
Request type: Renewal Proposal Request
CC: manager@entravision.com

Client name: Fronteras Del Norte
Client website: https://fronterasdelnorte.com
Agency name:
Agency fee:

Start date: 2026-06-01
End date: 2026-09-30
Monthly budget: $7,500
Total months: 4
Campaign goal: Awareness

Geographic target: Los Angeles DMA + San Diego DMA
Demo target: A18-49 Hispanic
Behavioral target: Spanish-language TV viewers

Products selected: Paid Search, Meta Ads on Entravision Pages, eDigital Display, CTV Premier

Additional comments: Renewal with modest budget increase. Keep the same mix as last flight.
"""

def shot(page, name):
    path = str(SCREENSHOTS / f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  screenshot → {path}")

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=400)
        page = browser.new_page(viewport={"width": 1400, "height": 900})

        print("\n=== Step 1: Load app ===")
        page.goto("http://127.0.0.1:8000")
        page.wait_for_load_state("networkidle")
        shot(page, "01_landing")

        print("\n=== Step 1: Paste Notion text ===")
        page.fill("#notion-input", NOTION_TEXT)
        shot(page, "02_notion_pasted")
        page.click("#parse-btn")
        page.wait_for_selector(".step.active[data-step='2']", timeout=10000)
        shot(page, "03_step2_review")

        print("\n=== Step 2: Review parsed fields ===")
        time.sleep(0.5)
        shot(page, "04_step2_fields")

        # Click Next / Continue to step 3
        next_btn = page.query_selector("#step-2 .btn-primary, #step-2 button.next, #step-2 [data-action='next']")
        if next_btn:
            next_btn.click()
        else:
            # Try the step nav
            page.click(".step-nav .step[data-step='3']")
        page.wait_for_selector(".step.active[data-step='3'], #step-3.active, #step-3:not(.hidden)", timeout=8000)
        shot(page, "05_step3_curate")

        print("\n=== Step 3: Line items / suggest mix ===")
        suggest_btn = page.query_selector("#suggest-btn, [data-action='suggest'], button:has-text('Suggest')")
        if suggest_btn:
            suggest_btn.click()
            time.sleep(1)
        shot(page, "06_step3_suggested")

        # Move to step 4
        next_btn = page.query_selector("#step-3 .btn-primary, #step-3 button.next, #step-3 [data-action='next']")
        if next_btn:
            next_btn.click()
            page.wait_for_selector(".step.active[data-step='4'], #step-4:not(.hidden)", timeout=8000)
        shot(page, "07_step4_avails")

        print("\n=== Step 4: Avails (skip — leave blank) ===")
        time.sleep(0.3)
        next_btn = page.query_selector("#step-4 .btn-primary, #step-4 button.next, #step-4 [data-action='next']")
        if next_btn:
            next_btn.click()
            page.wait_for_selector(".step.active[data-step='5'], #step-5:not(.hidden)", timeout=8000)
        shot(page, "08_step5_generate")

        print("\n=== Step 5: Generate ===")
        time.sleep(0.3)
        gen_btn = page.query_selector("#generate-btn, [data-action='generate'], button:has-text('Generate')")
        if gen_btn:
            gen_btn.click()
            # Wait for download link or success message
            page.wait_for_selector(".download-link, .success, [data-proposal-id], a[download]", timeout=20000)
            time.sleep(0.5)
        shot(page, "09_step5_generated")

        print("\n=== Done. All screenshots saved to ./screenshots/ ===")
        time.sleep(3)
        browser.close()

if __name__ == "__main__":
    run()
