// Private stdio RPC; RGB frames and model stay in local memory.
const http = require('node:http'), fs = require('node:fs'), path = require('node:path');
const crypto = require('node:crypto'), readline = require('node:readline');
const {chromium} = require('playwright-core');
const root = __dirname, taskDir = path.join(root, 'node_modules', '@mediapipe', 'tasks-vision');
const token = crypto.randomBytes(24).toString('hex');
let model, server, browser, page, currentFrame;
const paths = new Map([
  ['/bridge.html', [path.join(root, 'bridge.html'), 'text/html']],
  ['/bridge.js', [path.join(root, 'bridge.js'), 'text/javascript']],
  ['/vision_bundle.mjs', [path.join(taskDir, 'vision_bundle.mjs'), 'text/javascript']],
]);
for (const name of ['vision_wasm_internal.js', 'vision_wasm_internal.wasm', 'vision_wasm_nosimd_internal.js', 'vision_wasm_nosimd_internal.wasm'])
  paths.set('/wasm/' + name, [path.join(taskDir, 'wasm', name), name.endsWith('.js') ? 'text/javascript' : 'application/wasm']);

async function init(msg) {
  if (browser) throw Error('Already initialized');
  if (require(path.join(taskDir, 'package.json')).version !== '0.10.35') throw Error('Expected @mediapipe/tasks-vision 0.10.35');
  model = Buffer.from(msg.model, 'base64');
  server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const prefix = '/' + token;
    if (!url.pathname.startsWith(prefix + '/')) {res.writeHead(404); return res.end();}
    const sub = url.pathname.slice(prefix.length);
    if (sub === '/infer' && req.method === 'POST') {
      const width=Number(req.headers['x-width']),height=Number(req.headers['x-height']),timestamp=Number(req.headers['x-timestamp']);
      if (!page || currentFrame || !Number.isInteger(width) || !Number.isInteger(height) || width<1 || height<1 || width*height>4096*2160 || !Number.isSafeInteger(timestamp)) {res.writeHead(400);return res.end();}
      try {
        const chunks=[];let size=0;
        for await (const chunk of req) {size+=chunk.length;if(size>width*height*3)throw Error('Frame too large');chunks.push(chunk);}
        if(size!==width*height*3)throw Error('Incomplete RGB frame');
        currentFrame=Buffer.concat(chunks);
        const value=await page.evaluate(async meta => {
          const rgb=new Uint8Array(await (await fetch('./frame.bin')).arrayBuffer());
          return window.bridge.estimateBytes({...meta,rgb});
        },{width,height,timestamp});
        res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));
      } catch(error) {res.writeHead(500);res.end(JSON.stringify({error:String(error),sdkCallStarted:false}));}
      finally {currentFrame=null;}
      return;
    }
    if(req.method!=='GET'){res.writeHead(405);return res.end();}
    if(sub==='/frame.bin' && currentFrame){res.writeHead(200,{'Content-Type':'application/octet-stream','Cache-Control':'no-store'});return res.end(currentFrame);}
    if (sub === '/model.task') {res.writeHead(200, {'Content-Type':'application/octet-stream','Cache-Control':'no-store'}); return res.end(model);}
    const entry = paths.get(sub);
    if (!entry) {res.writeHead(404); return res.end();}
    res.setHeader('Content-Type', entry[1]);
    const s = fs.createReadStream(entry[0]); s.on('error', () => {if (!res.headersSent) res.writeHead(404); res.end();}); s.pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const candidates = [process.env.CHROME_PATH, path.join(process.env.PROGRAMFILES || '', 'Google/Chrome/Application/chrome.exe'),
    path.join(process.env['PROGRAMFILES(X86)'] || '', 'Microsoft/Edge/Application/msedge.exe')];
  const executablePath = candidates.find(p => p && fs.existsSync(p));
  if (!executablePath) throw Error('Chrome/Edge not found; set CHROME_PATH');
  browser = await chromium.launch({executablePath, headless:true,
    args:['--force_high_performance_gpu','--use-angle=d3d11','--disable-software-rasterizer','--disable-background-networking']});
  page = await browser.newPage();
  page.on('console', message => console.error(message.text()));
  await page.goto(`http://127.0.0.1:${server.address().port}/${token}/bridge.html`);
  await page.waitForFunction(() => !!window.bridge);
  const details=await page.evaluate(config => window.bridge.init(config), msg.options);
  return {...details,inference_url:`http://127.0.0.1:${server.address().port}/${token}/infer`};
}
async function close() {
  if (browser) {try {if (page) await page.evaluate(() => window.bridge.close());} catch (_) {} await browser.close();}
  if (server) await new Promise(resolve => server.close(resolve));
  browser = page = server = model = null;
}
let serial = Promise.resolve();
readline.createInterface({input:process.stdin, crlfDelay:Infinity}).on('line', line => {
  serial = serial.then(async () => {
    let msg;
    try {
      msg = JSON.parse(line);
      let value;
      if (msg.op === 'init') value = await init(msg);
      else if (msg.op === 'estimate') value = await page.evaluate(x => window.bridge.estimate(x), msg);
      else if (msg.op === 'reset') value = await page.evaluate(() => window.bridge.reset());
      else if (msg.op === 'close') {await close(); value = null;}
      else throw Error('Unknown operation');
      console.log(JSON.stringify({id:msg.id, ok:true, value}));
    } catch (error) {console.log(JSON.stringify({id:msg && msg.id, ok:false, error:String(error)}));}
  });
}).on('close', () => {serial.finally(close).finally(() => process.exit());});
