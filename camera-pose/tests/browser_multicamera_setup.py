"""Ordinary UI camera additions/group setup with prescribed streams; no hardware claim."""
import os
import shutil
from playwright.sync_api import sync_playwright
from test_multiview_posture import MultiviewWebTests
from bed_pose.live_sources import SyntheticSource


def main():
    case=MultiviewWebTests();case.setUp()
    original=case.workspace.factory
    class Camera:
        def __init__(self,index):self.index=index;self.frames=SyntheticSource(interval=.03)
        def open(self):return dict(self.frames.open(),kind='camera',camera_index=self.index)
        def read(self):return self.frames.read()
        def close(self):self.frames.close()
    def factory(source,*args,**kwargs):return original(Camera(source.index),*args,**kwargs)
    case.workspace.factory=factory
    with case.workspace.lock:
        case.workspace.scan_state={'busy':False,'indices':[0,1,2],'error':None,
            'devices':[{'index':i,'name':'Test Camera '+str(i)} for i in range(3)]}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('BEDPOSE_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(case.server.url)
            page.locator('#multi-camera-mode').click()
            assert page.locator('#multi-camera-setup').is_visible()
            assert page.locator('#group-start').is_disabled()
            page.locator('#multi-add-camera').click()
            page.locator('#camera-index').select_option('0')
            page.locator('#connect-more').click()
            page.wait_for_function("()=>snapshot.sessions.length===1 && !connectingInput")
            assert page.locator('#source-dialog').is_visible()
            page.wait_for_function("()=>document.querySelector('#camera-index option[value=\"0\"]').disabled")
            assert page.locator('#camera-index').input_value()=='1'
            page.locator('#connect-input').click()
            page.wait_for_function("()=>snapshot.sessions.length===2 && snapshot.sessions.every(s=>s.frame_id!==null)")
            page.wait_for_function("()=>gridTiles.size===2 && !document.getElementById('camera-grid').hidden")
            assert not page.locator('#source-dialog').is_visible()
            assert page.locator('#group-start').is_disabled()
            assert page.evaluate('snapshot.groups.length')==0
            page.locator('#multi-configure-group').click()
            ids=page.evaluate('snapshot.sessions.map(s=>s.id)')
            for sid in ids:
                frame=next(s for s in case.workspace.snapshot()['sessions'] if s['id']==sid)['frame_id']
                status,_=case.control(sid,'region',box=[0,0,640,360],selected_frame_id=frame)
                assert status==200
            case.wait(lambda x:all(s['region'] is not None for s in x['sessions']))
            page.wait_for_function("()=>snapshot.sessions.every(s=>s.region)")
            assert page.locator('#group-start').is_disabled()
            page.locator('#beds-confirmed').check();page.locator('#group-skew').fill('1000')
            page.locator('#group-record-video').check()
            page.locator('#group-start').click()
            page.wait_for_function("()=>document.getElementById('fused-label').textContent==='누움'")
            page.locator('#group-stop').click()
            page.wait_for_function("()=>snapshot.groups[0].phase==='completed'")
            from bed_pose.io_utils import load_json
            from pathlib import Path
            members=page.evaluate('snapshot.groups[0].members')
            assert all(load_json(Path(m['run_directory'])/'manifest.json')['video_recording'] for m in members)
            assert all((Path(m['run_directory'])/'analysis_video.mkv').exists() for m in members)
            # Third camera adds a view; it is never silently fused into the pair.
            page.locator('#add-camera').click();page.locator('#camera-index').select_option('2')
            page.locator('#connect-input').click()
            page.wait_for_function("()=>snapshot.sessions.length===3 && gridTiles.size===3")
            assert page.evaluate('snapshot.groups[0].members.length')==2
            assert '3대 연결됨' in page.locator('#multi-camera-status').inner_text()
            third=page.evaluate("snapshot.sessions.find(s=>s.camera_index===2).id")
            page.locator('.camera-tile[data-id="'+third+'"]').click()
            page.locator('#remove-input').click()
            page.wait_for_function("()=>snapshot.sessions.length===2")
            assert '2대 연결됨' in page.locator('#multi-camera-status').inner_text()
            # Switching to individual view never closes either camera.
            page.locator('#single-camera-mode').click()
            assert page.evaluate('snapshot.sessions.length')==2
            assert not page.locator('#multi-camera-setup').is_visible()
            page.locator('#multi-camera-mode').click()
            for width in (390,1280):
                page.set_viewport_size({'width':width,'height':900})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert not errors,errors
            browser.close()
        print('PASS: normal UI adds 1/2/3 cameras, prevents duplicate selection, confirms bed regions before fusion, removes camera and preserves inputs across modes')
    finally:case.doCleanups()

if __name__=='__main__':main()
