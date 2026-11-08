"""Real Chromium, prescribed provider decisions; not image recognition testing."""
import os
import shutil
from test_body_state import BodyStateWebTests
from playwright.sync_api import sync_playwright


def main():
    case=BodyStateWebTests();case.setUp()
    try:
        sid=case.state_input()
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('BEDPOSE_CHROMIUM') or shutil.which('chromium'),
                                      headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(case.server.url)
            page.wait_for_function("()=>document.getElementById('posture-value').textContent==='누움'")
            page.locator('#posture-details > summary').click()
            assert page.locator('#body-state-value').inner_text()=='누움'
            assert '영역 미지정' in page.locator('#bed-relation-value').inner_text()
            assert page.locator('#context-candidate-value').inner_text()=='누움'
            page.locator('#start').click();case.wait(lambda s:s['sessions'][0]['recorded_frames']>=2)
            case.provider.decision.update(state='standing',lying_direction='unknown')
            page.wait_for_function("()=>document.getElementById('posture-value').textContent==='서 있음'")
            assert page.locator('#context-candidate-value').inner_text()=='확인 불가'
            page.locator('#stop').click();case.wait(lambda s:s['sessions'][0]['integrity']=='verified')
            page.locator('#open-records').click();page.locator('.records-item').first.click()
            page.wait_for_function("()=>document.querySelector('#records-frames tr')?.textContent.includes('누움')")
            assert '신체 상태' in page.locator('.records-table thead').inner_text()
            page.locator('#records-close').click();page.evaluate('setGridMode(true)')
            page.wait_for_function("()=>document.getElementById('posture-value').textContent==='서 있음'")
            assert not errors,errors
            browser.close()
        print('PASS: separate image-state predictions, no bed required, standing rejection, record display, grid and no JS errors')
    finally:
        case.cleanup();case.temp.cleanup()


if __name__=='__main__':main()
