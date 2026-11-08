"""Real Chromium regression: python tests/browser_preview_recovery.py.

Requires optional developer Playwright + Chromium; uses generated files/test SDK.
"""
import os
import shutil
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from test_preview_quality import QualityWebTests
from playwright.sync_api import sync_playwright


def check(browser,case,grid):
    page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    damaged=[]
    def corrupt(route):
        response=route.fetch();data=response.json()
        if data and len(damaged)<(2 if grid else 1):
            data['image']='data:image/jpeg;base64,broken';damaged.append(data['sequence'])
        route.fulfill(response=response,json=data)
    page.route('**/api/view/*',corrupt)
    if grid:page.add_init_script("sessionStorage.setItem('bedpose-grid','true')")
    page.goto(case.server.url)
    if grid:page.evaluate('setGridMode(true)')
    page.wait_for_function("()=>!serverGone&&(gridMode?[...gridTiles.values()].some(t=>t.view?.decode_retry_at):view?.decode_retry_at)")
    assert page.evaluate('()=>!serverGone')
    page.wait_for_function("()=>!serverGone&&(gridMode?[...gridTiles.values()].every(t=>t.picture):!!picture)")
    # A stale snapshot can arrive before the already recovered view response.
    page.evaluate("""()=>{const s=current();s.preview_error='older failure';s.sequence=view.sequence-1;render();} """)
    assert page.evaluate('()=>!!picture&&!serverGone')
    # Repeat with equal sequence: a successful decoded response wins.
    page.evaluate("()=>{current().sequence=view.sequence;render()}")
    assert page.evaluate('()=>!!picture')
    if grid:assert page.evaluate('()=>[...gridTiles.values()].every(t=>t.picture)')
    assert page.evaluate('()=>view.frame_id===0')
    assert not errors,errors
    page.close()


def main():
    case=QualityWebTests();case.setUp()
    try:
        case.paused_file();case.paused_file()
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('BEDPOSE_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
            check(browser,case,False);check(browser,case,True)
            browser.close()
        print('PASS: decode failure, same-sequence retry, stale-state recovery, single/grid, no JS errors')
    finally:case.cleanup();case.temp.cleanup()

if __name__=='__main__':main()
