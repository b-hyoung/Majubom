// Local-only experimental GPU preview. No recording or measurement integration.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
const os = require('node:os');

const root = __dirname;
const taskDir = path.join(root, 'node_modules', '@mediapipe', 'tasks-vision');
const model = path.resolve(process.argv[2] || path.join(root, '..', '..', 'pose_landmarker_full.task'));
if (!fs.existsSync(path.join(taskDir, 'vision_bundle.mjs')) || !fs.existsSync(model)) {
  console.error('Run npm install in tools/gpu-preview and provide a local .task model path.');
  process.exit(1);
}
const token = crypto.randomBytes(24).toString('hex');
const allowed = new Map([
  ['/', [path.join(root, 'preview.html'), 'text/html; charset=utf-8']],
  ['/preview.js', [path.join(root, 'preview.js'), 'text/javascript; charset=utf-8']],
  ['/vision_bundle.mjs', [path.join(taskDir, 'vision_bundle.mjs'), 'text/javascript; charset=utf-8']],
  ['/model.task', [model, 'application/octet-stream']],
]);
for (const name of ['vision_wasm_internal.js', 'vision_wasm_internal.wasm', 'vision_wasm_nosimd_internal.js', 'vision_wasm_nosimd_internal.wasm'])
  allowed.set('/wasm/' + name, [path.join(taskDir, 'wasm', name), name.endsWith('.js') ? 'text/javascript' : 'application/wasm']);

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const prefix = '/' + token;
  const entry = url.pathname.startsWith(prefix + '/') ? allowed.get(url.pathname.slice(prefix.length)) : null;
  if (req.method !== 'GET' || !entry) {res.writeHead(404); res.end(); return;}
  res.setHeader('Content-Type', entry[1]);
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  const stream = fs.createReadStream(entry[0]);
  stream.on('error', () => {if (!res.headersSent) res.writeHead(404); res.end();});
  stream.pipe(res);
});
server.listen(0, '127.0.0.1', () => {
  const url = `http://127.0.0.1:${server.address().port}/${token}/`;
  console.log(`GPU preview: ${url}`);
  if (process.env.GPU_PREVIEW_NO_LAUNCH === '1') return;
  const candidates = [process.env.CHROME_PATH,
    path.join(process.env.PROGRAMFILES || '', 'Google/Chrome/Application/chrome.exe'),
    path.join(process.env['PROGRAMFILES(X86)'] || '', 'Microsoft/Edge/Application/msedge.exe')];
  const browser = candidates.find(p => p && fs.existsSync(p));
  if (!browser) {console.log('Open this URL in a WebGL 2 capable browser.'); return;}
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'bed-pose-gpu-preview-'));
  const child = spawn(browser, ['--user-data-dir=' + profile, '--force_high_performance_gpu', '--use-angle=d3d11', '--disable-software-rasterizer', '--app=' + url], {stdio: 'ignore'});
  child.on('error', error => console.error('Browser launch failed:', error.message));
  child.on('exit', () => {server.close(); fs.rmSync(profile, {recursive: true, force: true, maxRetries: 5, retryDelay: 100});});
});
