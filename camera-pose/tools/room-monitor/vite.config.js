import { defineConfig } from 'vite';
export default defineConfig({
  base: '/monitor-assets/',
  build: {
    outDir: '../../src/bed_pose/static/monitor', emptyOutDir: true,
    rollupOptions: { output: { entryFileNames: 'monitor.js', assetFileNames: 'monitor.[ext]', inlineDynamicImports: true } }
  }
});
