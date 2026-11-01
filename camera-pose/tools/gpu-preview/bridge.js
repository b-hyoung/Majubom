import {FilesetResolver, PoseLandmarker} from './vision_bundle.mjs';
let task, files, options, canvas;
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
  async reset() { if (task) task.close(); task = await PoseLandmarker.createFromOptions(files, options); },
  estimate({rgb, width, height, timestamp}) {
    const bytes = Uint8Array.from(atob(rgb), c => c.charCodeAt(0));
    return this.estimateBytes({rgb:bytes,width,height,timestamp});
  },
  estimateBytes({rgb:bytes, width, height, timestamp}) {
    if (bytes.length !== width * height * 3) throw Error('Invalid RGB frame length');
    const data = new Uint8ClampedArray(width * height * 4);
    for (let i = 0, j = 0; i < bytes.length; i += 3, j += 4) {
      data[j] = bytes[i]; data[j + 1] = bytes[i + 1]; data[j + 2] = bytes[i + 2]; data[j + 3] = 255;
    }
    if (!canvas || canvas.width !== width || canvas.height !== height) canvas = new OffscreenCanvas(width, height);
    canvas.getContext('2d').putImageData(new ImageData(data, width, height), 0, 0);
    try {
      const result = task.detectForVideo(canvas, timestamp);
      return {landmarks: result.landmarks, worldLandmarks: result.worldLandmarks};
    } catch (error) {
      return {error: String(error), sdkCallStarted: true};
    }
  },
  close() {if (task) task.close(); task = null;}
};
