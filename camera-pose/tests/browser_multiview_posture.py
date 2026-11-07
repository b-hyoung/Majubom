"""Two prescribed CLIP camera streams, no physical camera/accuracy claims."""
import os
import shutil
from playwright.sync_api import sync_playwright
from test_multiview_posture import MultiviewWebTests


def main():
    case=MultiviewWebTests();case.setUp()
    try:
        gid=case.start_pair()
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('BEDPOSE_CHROMIUM') or shutil.which('chromium'),
                                      headless=True,args=['--no-sandbox'])
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(case.server.url)
            page.locator('#fused-posture').wait_for(state='visible')
            page.wait_for_function("()=>document.getElementById('fused-label').textContent==='누움'")
            page.locator('#posture-details > summary').click()
            assert '두 시점 영상 점수 통합' in page.locator('#fused-detail').inner_text()
            assert '통합 점수' in page.locator('#fused-detail').inner_text()
            page.locator('#decision-evidence summary').click()
            assert page.locator('#decision-evidence-views p').count()==2
            assert '상태 기여 50%' in page.locator('#decision-evidence-views').inner_text()
            assert '불일치 없음' in page.locator('#decision-evidence-note').inner_text()
            # Keep a unique direction winner below the existing acceptance threshold.
            members=case.workspace.groups[gid].members
            for member in members:
                runtime=member.worker.engine.body_state_provider.runtime
                def weak_direction(image):
                    return {'lying':.6,'sitting':0.,'standing':0.,'other':0.,
                            'supine':.05,'side':0.,'prone':0.},10.
                runtime.similarities=weak_direction
            page.wait_for_function("()=>snapshot.groups[0].posture_assessment?.fusion_evidence?.direction_scores?.supine < .5")
            assert page.locator('#fused-label').inner_text()=='누움'
            assert '최고 점수 선택' in page.locator('#fused-detail').inner_text()
            for member in members:
                del member.worker.engine.body_state_provider.runtime.similarities
            page.wait_for_function("()=>document.getElementById('fused-label').textContent==='누움'")
            # Change only prescribed model output in one stream, not image accuracy.
            member=case.workspace.groups[gid].members[1]
            member.worker.engine.body_state_provider.runtime.direction='side'
            page.wait_for_function("()=>document.getElementById('decision-evidence-note').textContent.includes('시점 간 불일치: 누운 방향')")
            page.evaluate('setGridMode(true)')
            page.wait_for_function("()=>document.getElementById('decision-evidence-note').textContent.includes('시점 간 불일치')")
            assert not errors,errors
            browser.close()
        case.request('/api/group',{'action':'stop','group_id':gid})
        case.wait(lambda x:x['groups'][0]['phase']=='completed')
        print('PASS: live fused result, contribution count, scores, single/grid and recording completion')
    finally:case.doCleanups()

if __name__=='__main__':main()
