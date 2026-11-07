"""Synthetic camera timing/HTML checks, no physical device or posture accuracy claim."""
import time
import os
import shutil
import numpy as np
from test_web_workspace import WebTests
from bed_pose.latest_camera import LatestCameraSource
from bed_pose.live_engine import LiveEngine
from bed_pose.pose.fake import FakeAdapter
from playwright.sync_api import sync_playwright


def main():
    c=WebTests();c.setUp()
    class Camera:
        index=0
        def open(self):return {'kind':'camera'}
        def read(self):time.sleep(.02);return np.zeros((360,640,3),dtype=np.uint8)
        def close(self):pass
    class Adapter(FakeAdapter):
        def open(self,settings):return dict(super().open(settings),execution_kind='model_inference')
        def estimate(self,model_input):time.sleep(.15);return super().estimate(model_input)
    def factory(source,model,output,options,**kwargs):
        return LiveEngine(LatestCameraSource(Camera()),Adapter(),{'execution_mode':'ordered_sequence'},output)
    c.workspace.factory=factory
    try:
        status,value=c.request('/api/inputs',{'kind':'camera','camera':0,'use_clip':False});assert status==200,value
        sid=value['id']
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('BEDPOSE_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(c.server.url)
            page.locator('#analysis-mode').wait_for(state='visible')
            assert page.locator('#analysis-mode').get_attribute('aria-pressed')=='true'
            assert page.locator('#live-picture').is_hidden()
            assert page.locator('#overlay').is_checked() and page.locator('#overlay').is_enabled()
            c.wait(lambda x:x['sessions'][0].get('frame_id') is not None)
            _,captured=c.request('/api/view/'+sid)
            status,result=c.control(sid,'region',box=[50,40,300,250],selected_frame_id=captured['frame_id'])
            assert status==200,result
            page.wait_for_function("()=>current()?.region!=null && picture!=null")
            # Visible canvas contains both blue bed geometry and green skeleton pixels.
            page.wait_for_function('''()=>{const c=document.getElementById('canvas'),a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let blue=0,green=0;for(let i=0;i<a.length;i+=4){if(a[i+2]>150&&a[i+2]>a[i]*1.5&&a[i+2]>a[i+1]*1.2)blue++;if(a[i+1]>150&&a[i+1]>a[i]*1.4&&a[i+2]>80)green++}return blue>10&&green>10}''')
            calls=page.evaluate('''()=>{draw();const ctx=document.getElementById('canvas').getContext('2d'),original=ctx.drawImage,checked=document.getElementById('overlay').checked;let count=0;ctx.drawImage=function(...args){count++;return original.apply(this,args)};try{draw();draw();const unchanged=count;document.getElementById('overlay').checked=!checked;draw();return [unchanged,count]}finally{ctx.drawImage=original;document.getElementById('overlay').checked=checked;draw()}}''')
            assert calls==[0,1],calls
            for width in (390,1280,1920):
                page.set_viewport_size({'width':width,'height':900})
                assert page.locator('#analysis-mode').is_visible()
                assert page.locator('#live-picture').is_hidden()
            page.set_viewport_size({'width':1280,'height':900})
            page.locator('#latest-mode').click()
            page.locator('#live-picture').wait_for(state='visible')
            page.wait_for_function("()=>document.getElementById('live-picture').naturalWidth===640")
            assert '관절 오버레이 없음' in page.locator('#live-age').inner_text()
            page.locator('#live-bed-overlay').wait_for(state='visible')
            page.wait_for_function("()=>{const c=document.getElementById('live-bed-overlay'),a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let blue=0,green=0;for(let i=0;i<a.length;i+=4){if(a[i+2]>150&&a[i+2]>a[i]*1.5&&a[i+2]>a[i+1]*1.2)blue++;if(a[i+1]>150&&a[i+1]>a[i]*1.4&&a[i+1]>a[i+2]*1.1)green++}return blue>10&&green===0}")
            assert page.evaluate("()=>{const g=rawGeometry;rawGeometry={...g,source_width:g.source_width+1};drawLiveBed();const hidden=document.getElementById('live-bed-overlay').hidden;rawGeometry=g;drawLiveBed();return hidden}")
            for width in (390,1920):
                page.set_viewport_size({'width':width,'height':900})
                page.wait_for_function("()=>!document.getElementById('live-bed-overlay').hidden")
            page.set_viewport_size({'width':1280,'height':900})

            first=c.workspace.raw_view(sid)['capture_frame_id']
            page.wait_for_function("()=>document.getElementById('live-picture').complete")
            c.wait(lambda x:(x['sessions'][0].get('frame_id') or 0)>1)
            c.wait(lambda _:c.workspace.raw_view(sid)['capture_frame_id']>first)
            _,captured=c.request('/api/view/'+sid)
            assert c.control(sid,'region',box=None,selected_frame_id=captured['frame_id'])[0]==200
            page.wait_for_function("()=>document.getElementById('live-bed-overlay').hidden")
            page.locator('#analysis-mode').click();assert page.locator('#live-picture').is_hidden()
            assert page.locator('#live-bed-overlay').is_hidden()
            assert page.locator('#overlay').is_enabled()
            assert not errors,errors
            browser.close()
        print('PASS: camera capture continues during slow inference, independent live image, no stale skeleton overlay, default skeleton/bed pixels, visible mode buttons, responsive layouts and analysis toggle')
    finally:c.cleanup();c.temp.cleanup()

if __name__=='__main__':main()
