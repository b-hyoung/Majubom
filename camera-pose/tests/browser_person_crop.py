"""Prescribed detection and CLIP evidence, not pretrained human recognition."""
import os
import shutil
from playwright.sync_api import sync_playwright
from test_person_crop import CropRecordTests


def main():
    case=CropRecordTests();case.setUp()
    try:
        case.enable_fallback();case.workspace.person_crop_status={'available':True,'reason':None}
        gid=case.start_pair()
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('BEDPOSE_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(case.server.url)
            page.wait_for_function("()=>document.getElementById('fused-label').textContent==='누움'")
            page.locator('#posture-details > summary').click()
            page.locator('#decision-evidence summary').click()
            page.wait_for_function("()=>document.getElementById('decision-evidence-views').textContent.includes('사람 검출로 보완')")
            page.wait_for_function("()=>document.getElementById('clip-note').textContent.includes('사람 검출 영역으로 보완')")
            page.locator('#add-source').click()
            assert page.locator('#person-fallback').is_enabled()
            assert not page.locator('#person-fallback').is_checked()
            page.locator('#dialog-close').click()
            page.evaluate('setGridMode(true)')
            page.wait_for_function("()=>document.getElementById('decision-evidence-views').textContent.includes('사람 검출로 보완')")
            case.request('/api/group',{'action':'stop','group_id':gid})
            case.wait(lambda x:x['groups'][0]['phase']=='completed')
            page.evaluate('setGridMode(false)')
            page.wait_for_function("()=>!document.getElementById('select-target').disabled")
            page.locator('#select-target').click()
            page.locator('#target-dialog').wait_for(state='visible')
            page.locator('#target-confirm').click()
            page.locator('#target-dialog').wait_for(state='hidden')
            assert not errors,errors
            browser.close()
        print('PASS: optional crop toggle, recovered crop provenance, single/grid display and verified group completion')
    finally:case.doCleanups()

if __name__=='__main__':main()
