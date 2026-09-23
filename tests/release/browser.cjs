/* Optional real-browser smoke. All web resources are local fixtures; no paid calls.
 * NEWS_PLAYWRIGHT_MODULE can point to an existing Playwright installation. */
const {chromium} = require(process.env.NEWS_PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const ROOT=path.resolve(__dirname,'../..');
(async()=>{
  const {ownerPage}=await import('../../cloudflare/news-scheduler/http.mjs');
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const results=[];
  try {
    for(const mobile of [false,true]) {
      const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1280,height:900}});
      // Exercise denied localStorage fallback in a real browser on both layouts.
      await context.addInitScript(()=>{Object.defineProperty(window,'localStorage',{get(){throw new Error('storage denied');}});});
      let checks=0, posts=0, polls=0, exactReturn=false;
      let edition=JSON.parse(fs.readFileSync(path.join(ROOT,'tests/fixtures/contracts/healthy-edition.json')));
      const errors=[];
      const page=await context.newPage(); page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/*',async route=>{
        const req=route.request(), url=new URL(req.url());
        if(url.hostname==='worker.example.test') {
          if(url.pathname==='/' && req.method()==='GET') return route.fulfill({contentType:'text/html',body:ownerPage('https://pages.example.test/News/',url.searchParams.get('return'))});
          if(url.pathname==='/refresh' && req.method()==='POST') {
            assert.equal(req.headers()['origin'],'https://worker.example.test');
            assert.equal(req.headers()['x-requested-with'],'news-refresh');posts++;
            return route.fulfill({status:202,json:{status:'accepted',job_id:'job_browser',request_id:'req_browser',status_url:'/refresh/job_browser'}});
          }
          if(url.pathname==='/refresh/job_browser') {
            polls++; edition={...edition,request_ids:['req_browser']};
            return route.fulfill({json:{status:'succeeded',request_id:'req_browser',job_id:'job_browser'}});
          }
          return route.fulfill({status:404,body:'not found'});
        }
        if(url.hostname!=='pages.example.test') return route.abort();
        if(url.searchParams.get('request')==='req_browser' && url.searchParams.get('job')==='job_browser') exactReturn=true;
        if(url.pathname.endsWith('/data/briefing.json')) {checks++;return route.fulfill({json:edition});}
        if(url.pathname.endsWith('/data/past/index.json')) return route.fulfill({json:[]});
        const name=url.pathname.split('/').at(-1)||'index.html';
        const file=path.join(ROOT,'site',name);
        if(!fs.existsSync(file)) return route.fulfill({status:404,body:'not found'});
        let body=fs.readFileSync(file,'utf8');
        // Production leaves the owner link disabled; this is stage-3 UI simulation.
        if(name==='index.html') body=body.replace('name="news-owner-url" content=""','name="news-owner-url" content="https://worker.example.test/"');
        return route.fulfill({contentType:name.endsWith('.js')?'text/javascript':'text/html',body});
      });
      await page.goto('https://pages.example.test/News/');
      await page.getByRole('button',{name:'Check for updates'}).waitFor();
      const before=checks;
      await page.getByRole('button',{name:'Check for updates'}).click();
      await page.waitForFunction(()=>/latest published briefing|No newer briefing published yet/.test(document.body.innerText));
      assert.ok(checks>before); assert.equal(posts,0);
      await page.getByRole('link',{name:'Fetch new briefing',exact:true}).click();
      await page.getByRole('button',{name:'Fetch new briefing',exact:true}).waitFor();
      assert.equal(posts,0); // A GET/login/page load cannot spend.
      await page.getByRole('button',{name:'Fetch new briefing',exact:true}).press('Enter');
      await page.waitForFunction(()=>document.body.innerText.includes('New briefing ready'));
      await page.getByRole('button',{name:'Check for updates'}).waitFor();
      assert.equal(posts,1);assert.equal(polls,1);
      assert.equal(exactReturn,true);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      assert.deepEqual(errors,[]);
      results.push({layout:mobile?'390x844':'1280x900',blockedStorage:true,checkOnlyPosts:0,ownerPosts:posts,exactReturn:true,pageErrors:errors.length});
      await context.close();
    }
    console.log(JSON.stringify({browser:browser.version(),status:'pass',boundary:'local UI simulation; Access edge and live publication unverified',results},null,2));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
