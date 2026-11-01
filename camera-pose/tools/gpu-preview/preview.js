import {FilesetResolver, PoseLandmarker} from './vision_bundle.mjs';
const camera = document.getElementById('camera');
const start = document.getElementById('start'), stop = document.getElementById('stop');
const video = document.getElementById('video'), canvas = document.getElementById('view');
const status = document.getElementById('status');
let stream, landmarker, request, lastVideoTime = -1, generation = 0, videoCallback = false;

async function cameras() {
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput');
  const selected = camera.value;
  camera.replaceChildren(...devices.map((d, i) => new Option(d.label || `카메라 ${i + 1}`, d.deviceId)));
  if (devices.some(d => d.deviceId === selected)) camera.value = selected;
}

function shutdown() {
  generation++;
  if (videoCallback) video.cancelVideoFrameCallback(request);
  else cancelAnimationFrame(request);
  if (stream) stream.getTracks().forEach(track => track.stop());
  stream = null; video.srcObject = null;
  if (landmarker) landmarker.close();
  landmarker = null; lastVideoTime = -1; start.disabled = false; stop.disabled = true;
}

start.onclick = async () => {
  start.disabled = true; const run = ++generation;
  try {
    const gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) throw new Error('WebGL 2를 사용할 수 없습니다.');
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    const renderer = debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : '확인할 수 없음';
    if (!debug) throw new Error('GPU 렌더러를 확인할 수 없습니다. 브라우저의 GPU 설정을 확인하세요.');
    if (/swiftshader|software|llvmpipe|basic render|\bwarp\b/i.test(renderer)) throw new Error(`소프트웨어 렌더러 감지: ${renderer}`);
    status.textContent = 'GPU 모델을 불러오는 중...';
    const files = await FilesetResolver.forVisionTasks('./wasm');
    const task = await PoseLandmarker.createFromOptions(files, {baseOptions: {modelAssetPath: './model.task', delegate: 'GPU'}, runningMode: 'VIDEO', numPoses: 1});
    if (run !== generation) {task.close(); return;} landmarker = task;
    const incoming = await navigator.mediaDevices.getUserMedia({video: camera.value ? {deviceId: {exact: camera.value}} : true, audio: false});
    if (run !== generation) {incoming.getTracks().forEach(track => track.stop()); return;} stream = incoming;
    video.srcObject = stream; await video.play(); await cameras(); stop.disabled = false;
    let frames = 0, began = performance.now();
    videoCallback = typeof video.requestVideoFrameCallback === 'function';
    const schedule = () => {request = videoCallback ? video.requestVideoFrameCallback(draw) : requestAnimationFrame(draw);};
    const draw = () => {
      try {
        if (video.readyState >= 2 && video.currentTime !== lastVideoTime) {
          lastVideoTime = video.currentTime;
          const result = landmarker.detectForVideo(video, performance.now());
          if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
            canvas.width = video.videoWidth; canvas.height = video.videoHeight;
          }
          const ctx = canvas.getContext('2d'); ctx.drawImage(video, 0, 0);
          ctx.fillStyle = '#55eeb0';
          for (const pose of result.landmarks) for (const p of pose) {
            if (p.visibility < 0.3) continue;
            ctx.beginPath(); ctx.arc(p.x * canvas.width, p.y * canvas.height, 3, 0, Math.PI * 2); ctx.fill();
          }
          frames++;
          status.textContent = `실행: MediaPipe GPU\n렌더러: ${renderer}\n해상도: ${canvas.width} × ${canvas.height} · 검출: ${result.landmarks.length}명 · 평균 처리: ${(frames / ((performance.now()-began)/1000)).toFixed(1)} FPS`;
        }
        schedule();
      } catch (error) {shutdown(); status.textContent = `처리 실패: ${error.message}`;}
    };
    schedule();
  } catch (error) {shutdown(); status.textContent = `시작 실패: ${error.message}`;}
};
stop.onclick = () => {shutdown(); status.textContent = '카메라를 중지했습니다.';};
window.addEventListener('pagehide', shutdown);
cameras().catch(() => {});
