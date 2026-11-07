import {FilesetResolver, PoseLandmarker} from './vision_bundle.mjs';
let task, probeTask, files, options, canvas;
window.bridge = {
  async init(config) {
    const gl = document.createElement('canvas').getContext('webgl2');
    const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
    if (!ext) throw Error('Hardware GPU renderer cannot be verified');
    const renderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
    if (/swiftshader|software|llvmpipe|basic render|\bwarp\b/i.test(renderer)) throw Error(`Software renderer: ${renderer}`);
    files = await FilesetResolver.forVisionTasks('./wasm');
    options = {baseOptions: {modelAssetPath: './model.task', delegate: 'GPU'}, runningMode: 'VIDEO',
      numPoses: config.num_poses, minPoseDetectionConfidence: config.min_pose_detection_confidence,
      minPosePresenceConfidence: config.min_pose_presence_confidence,
      minTrackingConfidence: config.min_tracking_confidence};
    task = await PoseLandmarker.createFromOptions(files, options);
    return {renderer};
  },
  async reset({all_tasks=false}={}) { if (task) task.close(); if (all_tasks && probeTask) {probeTask.close();probeTask=null;} task = await PoseLandmarker.createFromOptions(files, options); },
  estimate({rgb, width, height, timestamp, mode}) {
    const bytes = Uint8Array.from(atob(rgb), c => c.charCodeAt(0));
    return this.estimateBytes({rgb:bytes,width,height,timestamp,mode});
  },
  async estimateBytes({rgb:bytes, width, height, timestamp, mode='video'}) {
    if (bytes.length !== width * height * 3) throw Error('Invalid RGB frame length');
    const data = new Uint8ClampedArray(width * height * 4);
    for (let i = 0, j = 0; i < bytes.length; i += 3, j += 4) {
      data[j] = bytes[i]; data[j + 1] = bytes[i + 1]; data[j + 2] = bytes[i + 2]; data[j + 3] = 255;
    }
    if (!canvas || canvas.width !== width || canvas.height !== height) canvas = new OffscreenCanvas(width, height);
    canvas.getContext('2d').putImageData(new ImageData(data, width, height), 0, 0);
    try {
      if (mode !== 'video' && mode !== 'probe') throw Error('Invalid inference mode');
      // IMAGE probes never inherit or corrupt the VIDEO task's tracked ROI.
      if (mode === 'probe' && !probeTask) probeTask = await PoseLandmarker.createFromOptions(files, {...options,runningMode:'IMAGE'});
      const result = mode === 'probe' ? probeTask.detect(canvas) : task.detectForVideo(canvas, timestamp);
      return {landmarks: result.landmarks, worldLandmarks: result.worldLandmarks};
    } catch (error) {
      return {error: String(error), sdkCallStarted: true};
    }
  },
  close() {if (task) task.close();if(probeTask)probeTask.close(); task = probeTask = null;}
};
