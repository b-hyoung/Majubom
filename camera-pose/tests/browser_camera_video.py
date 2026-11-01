"""Select video recording and open a matching saved frame with synthetic input."""
import os
import shutil
from playwright.sync_api import sync_playwright
from test_web_workspace import WebTests


def main():
    case=WebTests();case.setUp()
    try:
        sid=case.demo()
        status,_=case.control(sid,'region',box=[10,20,200,300],selected_frame_id=0)
        assert status==200
        case.wait(lambda x:x['sessions'][0]['region'] is not None)
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('BEDPOSE_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(case.server.url)
            page.locator('#record-video').check()
            page.locator('#start').click()
            page.wait_for_function("()=>snapshot.sessions[0].recording && snapshot.sessions[0].recorded_frames>=3")
            assert '녹화합니다' in page.locator('#measure-note').inner_text()
            page.locator('#stop').click()
            page.wait_for_function("()=>snapshot.sessions[0].integrity==='verified'")
            page.locator('#open-records').click()
            page.locator('#records-list button').first.click()
            page.locator('#records-frames button').first.click()
            page.wait_for_function("()=>document.getElementById('record-image-canvas').style.display==='block'")
            assert '영상과 로그의 프레임 대응 확인' in page.locator('#record-image-message').inner_text()
            assert not errors,errors
            browser.close()
        print('PASS: optional video selection, recording, saved log frame and matching image')
    finally:case.doCleanups()

if __name__=='__main__':main()
