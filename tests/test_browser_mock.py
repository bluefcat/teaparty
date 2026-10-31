"""Renderer interaction smoke test; no outbound requests or model weights."""
import json, re
from pathlib import Path
from playwright.sync_api import sync_playwright
base=Path(__file__).parent/'image_library'/'web_assets'
html=(base/'index.html').read_text().replace('%%MORI_TOKEN%%','local-test-token')
html=re.sub(r'<link rel="stylesheet" href="/style.css">','',html)
html=html.replace('<script src="/app.js" defer></script>','')
css=(base/'style.css').read_text()
js=(base/'app.js').read_text()
images=[{'id':i+1,'name':f'test-{i:02}.png','path':f'/tmp/test-{i:02}.png','width':200,'height':300+i*9,
'error':'','tags':['test'], 'status':'ready','mtime':0,'size':333,'hash':str(i),'duplicate':False,'colors':[]} for i in range(36)]
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1350,'height':720})
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content(html)
 page.add_style_tag(content=css)
 page.evaluate("history.pushState=function(){};history.replaceState=function(){}")
 page.evaluate('''(images)=>{window._uploaded=false;window.fetch=async function(url,opts={}){
  let data;
  if(url==='/api/state'){data={total:window._uploaded?37:36,folders:[],settings:{},scan:{running:false,revision:0,summary:'',completed:0,total:0,folder:'',device:'',status:''}}}
  else if(url==='/api/search'){data={total:window._uploaded?37:36,images:window._uploaded?[...images, {...images[0],id:100}]:images,tags:[],resolved_tags:[],message:''}}
  else if(url==='/api/upload_image'){window._uploaded=true;data={ok:true,folder:'/tmp/managed_images/단일 이미지'}}
  else{data={ok:true,folders:[],total:36,images:[],tags:[],resolved_tags:[],similar:[]}}
  return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
 }}''',images)
 page.add_script_tag(content=js)
 page.wait_for_selector('.pin',timeout=12000)
 assert page.locator('#addFolder').is_visible()
 assert page.locator('#addImage').is_visible()
 assert page.locator('.pin').count()==36
 page.screenshot(path='/mnt/data/TEA_PARTY_v9_header_preview.png')
 before=page.locator('.topbar').bounding_box()['y']
 page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
 page.wait_for_timeout(300)
 after=page.locator('.topbar').bounding_box()['y']
 print('sticky header top:',before,after)
 assert before>15 and abs(after)<15
 page.locator('#addImage').click()
 page.locator('#imageFileInput').set_input_files({'name':'sample.png','mimeType':'image/png','buffer':b'fakeimage'})
 page.wait_for_function('window._uploaded===true',timeout=12000)
 page.wait_for_timeout(200)
 assert page.locator('#sideCount').inner_text()=='37'
 assert not errors,errors
 print('PASS: sticky topbar, upload flow, 36 image cards, no JS errors')
 browser.close()
