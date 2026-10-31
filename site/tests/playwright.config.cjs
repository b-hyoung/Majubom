const { defineConfig } = require('@playwright/test');
const path = require('node:path');

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: 'dashboard.spec.cjs',
  outputDir: path.join(__dirname, 'artifacts', 'test-results'),
  timeout: 30000,
  expect: { timeout: 6000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  reporter: [
    ['list'],
    ['json', { outputFile: path.join(__dirname, 'artifacts', 'results.json') }],
  ],
  use: {
    baseURL: process.env.MAJUBOM_TEST_URL || 'http://127.0.0.1:5001',
    channel: process.env.PLAYWRIGHT_CHANNEL || (process.platform === 'win32' ? 'chrome' : undefined),
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: 'ko-KR',
    timezoneId: 'Asia/Seoul',
    reducedMotion: 'reduce',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
