import os,shutil,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_record_image import ImageTests
from playwright.sync_api import sync_playwright
c=ImageTests();c.setUp()
try:
 root,asset,session=c.measured_file();video=c.workspace.assets[asset]['path']
 with sync_playwright() as p:
  b=p.chromium.launch(executable_path=os.environ.get('BEDPOSE_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
  page=b.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(c.server.url);page.locator('#open-records').click();page.locator('[data-record="'+root.name+'"]').click()
  page.wait_for_function('()=>recordBrowser.page?.frames.length>0')
  page.locator('#record-video-file').set_input_files(str(video))
  page.wait_for_function("()=>document.getElementById('record-image-canvas').style.display==='block'")
  assert '원본 프레임 1' in page.locator('#record-image-message').inner_text()
  assert '원본 대응 확인' in page.locator('#record-image-message').inner_text()
  page.locator('#record-image-details summary').click()
  assert 'posture_assessment' in page.locator('#record-image-evidence').inner_text()
  last=page.locator('#records-frames tr').last;last.locator('button').click()
  page.wait_for_function('()=>recordImage.frame===recordBrowser.page.frames.at(-1).frame_id&&document.getElementById("record-image-message").textContent.includes("원본 대응 확인")')
  for width in [390,1440,2560]:
   page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(100)
   assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth+1')
  page.evaluate('()=>{void showStoredImage(0);resetRecordImage(false)}');page.wait_for_timeout(200)
  assert page.evaluate('()=>document.getElementById("record-image-canvas").style.display==="none"')
  page.locator('#records-close').click();page.wait_for_function('()=>recordImage.asset===null')
  assert not errors,errors;b.close()
 print('PASS: original upload, exact first/last frame, saved evidence, three widths, close cleanup, no JS errors')
finally:c.cleanup();c.temp.cleanup()
