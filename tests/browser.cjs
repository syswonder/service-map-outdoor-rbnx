// Run against scripts/integration.py --serve (synthetic data only).
const { chromium } = require('@playwright/test');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    page.setDefaultTimeout(7000);
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    const base=process.env.SCENE_URL || 'http://127.0.0.1:50109';
    await page.goto(base+'/outdoor');
    await page.waitForSelector('#od-roads path',{state:'attached'});
    const before=await (await page.request.get(base+'/api/outdoor/state')).json();
    assert.equal(before.is_active,true);
    assert.equal(before.map.example_data,true,'Browser test requires the synthetic example');
    const view=await page.locator('#od-svg').getAttribute('viewBox');
    for (const mode of ['capture','merge','plan','view']) {
      await page.locator(`[data-mode="${mode}"]`).click();
      assert.equal(await page.locator('#od-svg').getAttribute('viewBox'),view);
      assert(await page.locator('#od-panel h2').textContent());
    }
    await page.locator('#od-gait').uncheck();
    assert.equal(await page.locator('#od-roads path[style*="#bb9fe9"]').count(),0);
    await page.locator('#od-gait').check();
    await page.locator('#od-quality-toggle').uncheck();
    assert.equal(await page.locator('#od-quality path').count(),0);
    await page.locator('#od-quality-toggle').check();
    const map=before.map, roads=map.network.roads;
    const points=roads.flatMap(r=>r.points);
    const start=points.reduce((a,b)=>a.x<b.x?a:b), goal=points.reduce((a,b)=>a.y>b.y?a:b);
    const response=await page.request.post(base+'/api/outdoor/plan_route',{data:{map_id:map.map_id,version:map.version,start,goal}});
    const route=await response.json();
    assert.equal(route.ok,true,JSON.stringify(route));
    assert(route.length_m>4,JSON.stringify(route));
    const reverse=await (await page.request.post(base+'/api/outdoor/plan_route',{data:{map_id:map.map_id,version:map.version,start:goal,goal:start}})).json();
    assert(Math.abs(reverse.length_m-route.length_m)<1e-6);
    await page.screenshot({path:process.env.SCENE_SCREENSHOT || '/tmp/outdoor-scene.png',fullPage:true});
    await page.locator('.scene-types a').first().click();
    const after=await (await page.request.get(base+'/api/outdoor/state')).json();
    assert.equal(after.binding.map_id,before.binding.map_id);
    assert.equal(after.binding.version,before.binding.version);
    if (process.env.SCENE_FIXTURE_CONTROL) {
      const fs = require('node:fs');
      await page.goto(base+'/outdoor');
      await page.waitForSelector('#od-roads path',{state:'attached'});
      await page.locator('[data-mode="capture"]').click();
      await page.locator('[data-command="append"]').click();
      await page.waitForSelector('[data-command="pause"]');
      assert(await page.locator('[data-mode="view"]').isDisabled());
      const blocked=await page.request.post(base+'/api/maps/load',{data:{map_id:map.map_id,map_type:'outdoor'}});
      assert.equal(blocked.status(),409);
      await page.locator('[data-command="pause"]').click();
      await page.waitForSelector('[data-command="resume"]');
      await page.locator('[data-command="resume"]').click();
      await page.waitForSelector('[data-command="pause"]');
      const control=process.env.SCENE_FIXTURE_CONTROL;
      fs.writeFileSync(control+'.browser.tmp',JSON.stringify({x:2,y:5}));
      fs.renameSync(control+'.browser.tmp',control);
      await page.waitForTimeout(2100);
      await page.locator('[data-command="stop"]').click();
      await page.waitForSelector('[data-command="preview-merge"]');
      await page.locator('[data-command="preview-merge"]').click();
      await page.waitForSelector('[data-command="apply"]');
      assert(await page.locator('[data-command="apply"]').isDisabled());
      await page.locator('[data-mode="capture"]').click();
      page.once('dialog',dialog=>dialog.accept());
      await page.locator('[data-command="discard"]').click();
      await page.waitForSelector('[data-command="append"]');
    }
    await page.goto(base+'/regions?map_type=outdoor');
    await page.waitForSelector('iframe');
    assert((await page.locator('iframe').getAttribute('src')).includes('map_type=outdoor'));
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,checks:['four modes preserve view','gait and quality layers','branch forward/reverse planning','tabs do not load maps','outdoor annotation editor',...(process.env.SCENE_FIXTURE_CONTROL?['capture pause/resume and map-load guard','unreviewed merge cannot submit']:[])],length_m:route.length_m}));
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exit(1)});
