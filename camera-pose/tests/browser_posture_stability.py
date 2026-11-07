"""Prescribed camera outputs exercise separate stable/raw presentation."""
import os
import shutil
from playwright.sync_api import sync_playwright
from test_multiview_posture import MultiviewWebTests
from bed_pose.posture_stability import Stabilizer


def main():
    case=MultiviewWebTests();case.setUp()
    try:
        original=case.workspace.factory
        def factory(*args,**kwargs):
            engine=original(*args,**kwargs)
            engine.stability=Stabilizer({'confirm_ms':300,'min_samples':3,'max_gap_ms':300,'expire_ms':500})
            return engine
        case.workspace.factory=factory
        gid=case.start_pair()
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('BEDPOSE_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(case.server.url)
            page.wait_for_function("()=>document.getElementById('stable-label').textContent==='누움'")
            for s in case.workspace.sessions.values():s.worker.engine.body_state_provider.runtime.direction='side'
            page.wait_for_function("()=>document.getElementById('posture-value').textContent==='옆으로 누움'")
            page.wait_for_function("()=>['전환 확인 중','자세 확인 중','새 통합 관측 대기'].includes(document.getElementById('stable-label').textContent)")
            page.wait_for_function("()=>document.getElementById('stable-label').textContent==='옆으로 누움'")
            page.evaluate('setGridMode(true)')
            page.wait_for_function("()=>document.getElementById('stable-label').textContent==='옆으로 누움'")
            for s in case.workspace.sessions.values():s.worker.paused=True
            page.wait_for_function("()=>document.getElementById('stable-label').textContent==='입력 만료'")
            for s in case.workspace.sessions.values():s.worker.paused=False
            page.wait_for_function("()=>document.getElementById('stable-label').textContent==='옆으로 누움'")
            page.locator('#add-source').click();page.locator('#stability-settings').evaluate('(el)=>el.open=true')
            assert page.locator('#stability-confirm').input_value()=='500'
            page.locator('#dialog-close').click()
            assert not errors,errors
            browser.close()
        case.request('/api/group',{'action':'stop','group_id':gid})
        case.wait(lambda x:x['groups'][0]['phase']=='completed')
        print('PASS: raw/stable separation, transition confirmation, grid, no-input expiry/recovery, configurable settings and group completion')
    finally:
        for s in case.workspace.sessions.values():s.worker.paused=False
        case.doCleanups()

if __name__=='__main__':main()
