"""Narrow desktop/sidebar regression for TEA PARTY v12.2."""
import runpy
from pathlib import Path
from playwright.sync_api import sync_playwright

source = runpy.run_path(str(Path(__file__).with_name('test_responsive_ui.py')))


def test_sidebar_overflow_and_controls():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1366,'height':820})
        try:
            errors = source['setup'](page)
            for width in (1366,1100,1050,900,800,600,390,320):
                page.set_viewport_size({'width':width,'height':820})
                page.wait_for_timeout(80)
                page.locator('#sideCount').evaluate('(el)=>{el.textContent="13,460";}')
                detail = page.evaluate('''()=>{
                  const r=s=>document.querySelector(s).getBoundingClientRect();
                  const wide=window.innerWidth>800;
                  return {wide,brandRight:r('.brand strong').right,sidebarRight:r('.sidebar').right,
                    statRight:r('.stat-number span').right,statLeft:r('.stat-number span').left,
                    statParentRight:r('.library-stats').right,settingHeight:r('#sideSettings').height,
                    activeBackground:getComputedStyle(document.querySelector('.nav-item.active')).backgroundColor,
                    activeIcon:getComputedStyle(document.querySelector('.nav-item.active svg')).stroke,
                    overflow:document.documentElement.scrollWidth>window.innerWidth};
                }''')
                assert not detail['overflow'],(width,detail)
                if detail['wide']:
                    assert detail['brandRight']<=detail['sidebarRight']+1,(width,detail)
                    assert detail['statLeft']>=0 and detail['statRight']<=detail['statParentRight']+1,(width,detail)
                else:
                    assert detail['settingHeight']>=44,(width,detail)
                    assert detail['activeBackground']!='rgba(0, 0, 0, 0)',(width,detail)
                    assert detail['activeIcon']=='rgb(255, 255, 255)',(width,detail)
            assert not errors,errors
        finally:
            browser.close()
