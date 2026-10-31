"""Optional Playwright smoke test, run as a script (requires Chromium)."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

base=Path(__file__).resolve().parents[1]/'image_library/web_assets'
html=(base/'index.html').read_text(encoding='utf8').replace('%%MORI_TOKEN%%','test-token')
html=html.replace('<link rel="stylesheet" href="/style.css">','').replace('<script src="/app.js" defer></script>','')
css=(base/'style.css').read_text(encoding='utf8')
js=(base/'app.js').read_text(encoding='utf8')
rows=[{'id':i+1,'name':f'photo-{i}.png','path':f'/tmp/managed_images/photo-{i}.png',
       'width':120,'height':180+i*3,'tags':['tag'], 'status':'ready','mtime':1,
       'size':100,'hash':str(i),'duplicate':False,'colors':[]} for i in range(45)]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1360,'height':800})
    errors=[]
    page.on('pageerror',lambda exc:errors.append(str(exc)))
    page.set_content(html)
    page.add_style_tag(content=css)
    # A set_content document has an opaque about:blank origin; native history
    # pushState is exercised in the real HTTP server, not this isolated mock.
    page.evaluate('history.pushState=()=>{};history.replaceState=()=>{}')
    page.evaluate('''(rows) => {
      window.fetch=async function(url,opts={}) {
        let data={};
        if(url==='/api/state')data={total:rows.length,settings:{},scan:{running:false,revision:0,summary:'',completed:0,total:0,folder:'',device:'',status:''},events:[],errors:[]};
        else if(url==='/api/search')data={total:rows.length,images:rows,tags:[['tag',rows.length]],resolved_tags:[],message:''};
        else if(url.startsWith('/api/detail'))data={image:rows[0],similar:rows.slice(1,8)};
        else data={ok:true};
        return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
      };
    }''',rows)
    page.add_script_tag(content=js)
    page.wait_for_selector('.pin')
    assert page.locator('.pin').count()==45
    assert page.locator('[data-nav="folders"]').count()==0
    assert page.locator('#folderSelect').count()==0
    assert page.locator('#addFolder').is_visible()
    assert page.locator('#addImage').is_visible()
    assert page.locator('.stat-caption').inner_text()=='프로그램 내부 저장소'
    page.screenshot(path='/mnt/data/TEA_PARTY_v11_flat_gallery.png')
    page.locator('#addFolder').click()
    assert page.locator('#folderModal').is_visible()
    assert '하나의 라이브러리' in page.locator('#folderModal').inner_text()
    page.locator('[data-close="folder"]').first.click()
    page.locator('[data-nav="colors"]').click()
    assert page.locator('#colorDrawer').evaluate('(el)=>el.classList.contains("open")')
    page.locator('[data-close="color"]').first.click()
    page.locator('.pin').first.click()
    page.wait_for_selector('#detailName:text("photo-0.png")')
    assert page.locator('#detailModal').is_visible()
    assert not errors, errors
    print('PASS: 45 thumbnails, no folders, folder import dialog, color drawer and image detail; 0 JS errors')
    browser.close()
