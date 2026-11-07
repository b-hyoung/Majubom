"""Chromium with prescribed CLIP similarities; not pretrained recognition testing."""
import os
import shutil
from test_clip_posture import ClipWebTests
from playwright.sync_api import sync_playwright


def main():
    case=ClipWebTests();case.setUp()
    try:
        sid=case.add_video()
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('BEDPOSE_CHROMIUM') or shutil.which('chromium'),
                                      headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(case.server.url)
            page.wait_for_function("()=>document.getElementById('posture-value').textContent==='누움'")
            page.locator('#posture-details > summary').click()
            page.locator('#decision-evidence summary').click()
            assert page.locator('#decision-evidence-views p').count()==1
            assert '단일 카메라 근거' in page.locator('#decision-evidence-note').inner_text()
            assert '상태 기여 100%' in page.locator('#decision-evidence-views').inner_text()
            page.locator('#clip-evidence summary').click()
            page.wait_for_function("()=>document.getElementById('clip-input-image').naturalWidth===224")
            assert float(page.locator('#clip-lying').inner_text())>.9
            assert '정답 확률이 아닙니다' in page.locator('#clip-note').inner_text()
            assert '가장 높은 항목' in page.locator('#clip-policy').inner_text()
            page.locator('#add-source').click();assert page.locator('#use-clip').is_checked()
            assert not page.locator('#use-silhouette').is_checked()
            assert not page.locator('#use-clip').is_disabled();page.locator('#dialog-close').click()
            page.locator('#start').click();case.wait(lambda s:s['sessions'][0]['closed'])
            page.wait_for_function("()=>document.getElementById('clip-evidence').hidden")
            assert page.locator('#clip-input-image').get_attribute('src') is None
            page.locator('#open-records').click();page.locator('.records-item').first.click()
            page.wait_for_function("()=>document.querySelector('#records-frames tr')?.textContent.includes('누움')")
            assert not errors,errors
            browser.close()
        print('PASS: CLIP toggle, actual input preview, uncalibrated scores, EOF cleanup and record display; prescribed test similarities')
    finally:case.doCleanups()


if __name__=='__main__':main()
