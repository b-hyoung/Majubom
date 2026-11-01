"""Chromium UI regression for independent shape/region records; synthetic SDK."""
import os
import shutil
from test_separated_posture import SeparationWebTests
from playwright.sync_api import sync_playwright


def main():
    case=SeparationWebTests();case.setUp()
    try:
        sid=case.body_input()
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('BEDPOSE_CHROMIUM') or shutil.which('chromium'),
                                      headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
            page=browser.new_page();errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(case.server.url)
            page.wait_for_function("()=>document.getElementById('posture-value').textContent==='정면 신체 형태'")
            page.locator('#posture-details > summary').click()
            assert page.locator('#separated-results').is_visible()
            # Long reasons must not collapse metric labels into word/character columns.
            for width in (390, 1024, 1280, 1920):
                page.set_viewport_size({'width':width,'height':900})
                page.evaluate("document.getElementById('body-state-value').textContent='독립 영상 판단 근거가 연결되지 않았습니다. 촬영 시점 또는 몸통 방향 불확실'")
                sizes=page.locator('#separated-results dt').evaluate_all('(nodes)=>nodes.map(n=>n.getBoundingClientRect().width)')
                assert all(w>=105 for w in sizes),sizes
                assert page.locator('#separated-results').evaluate('(n)=>n.scrollWidth<=n.clientWidth')
            page.set_viewport_size({'width':1280,'height':900})

            assert '영역 미지정' in page.locator('#bed-relation-value').inner_text()
            assert page.locator('#start').is_enabled()
            page.locator('#start').click()
            case.wait(lambda x:x['sessions'][0]['recorded_frames']>=2)
            _,view=case.request('/api/view/'+sid)
            assert case.control(sid,'region',box=[1,1,50,50],selected_frame_id=view['frame_id'])[0]==200
            page.wait_for_function("()=>document.getElementById('bed-relation-value').textContent==='영상상 영역 밖'")
            assert page.locator('#posture-value').inner_text()=='정면 신체 형태'
            page.locator('#stop').click()
            case.wait(lambda x:x['sessions'][0]['integrity']=='verified')
            page.locator('#open-records').click()
            page.locator('.records-item').first.click()
            page.wait_for_function("()=>document.querySelector('#records-frames tr')?.textContent.includes('정면 형태')")
            assert '침대 관계' in page.locator('.records-table thead').inner_text()
            page.locator('#records-close').click()
            page.evaluate('setGridMode(true)')
            page.wait_for_function("()=>document.getElementById('posture-value').textContent==='정면 신체 형태'")
            assert not errors,errors
            browser.close()
        print('PASS: no-region recording, separate body/region display, region edits, stored results, single/grid, no JS errors')
    finally:
        case.cleanup();case.temp.cleanup()


if __name__=='__main__':main()
