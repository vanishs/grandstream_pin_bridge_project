// Run with node tests/test_device_users_frontend.cjs (Playwright + Chromium).
const { chromium } = require('playwright');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent('<body></body>');
    await page.evaluate(() => {
      customElements.define('ha-config-device-page', class extends HTMLElement {
        constructor() { super(); this.attachShadow({mode: 'open'}).innerHTML = '<ha-device-info-card></ha-device-info-card>'; }
        updated() { this.originalCalled = true; }
      });
      window.calls = [];
      window.fail = false;
      const host = document.createElement('ha-config-device-page');
      host.deviceId = 'gds';
      host.hass = { language: 'zh-Hans', user: {is_admin: true}, devices: {gds: {manufacturer: 'Grandstream', model: 'GDS3725'}},
        callWS: async (msg) => { window.calls.push(msg); if (window.fail) throw {code: window.fail === true ? 'cannot_connect' : window.fail};
          return {users: [{user_id: '35', username: '<img src=x onerror=alert(1)>'}]}; } };
      document.body.append(host);
    });
    await page.addScriptTag({path: path.resolve(__dirname, '../custom_components/grandstream_pin_bridge/frontend/device-users.js')});
    await page.evaluate(() => document.querySelector('ha-config-device-page').updated(new Map()));
    const card = page.locator('gds-access-users');
    await card.locator('tbody tr').waitFor();
    assert.equal(await card.locator('tbody td').nth(1).textContent(), '<img src=x onerror=alert(1)>');
    assert.equal(await card.locator('img').count(), 0);
    await card.locator('button').click();
    assert.equal(await page.evaluate(() => window.calls.length), 2);
    await page.evaluate(() => { window.fail = true; });
    await card.locator('button').click();
    assert.equal(await card.locator('tbody tr').count(), 0);
    assert.match(await card.locator('p').textContent(), /无法读取设备用户/);
    await page.evaluate(() => { window.fail = 'unknown_error'; });
    await card.locator('button').click();
    assert.match(await card.locator('p').textContent(), /HA 后端处理失败/);
    await page.evaluate(() => { const host = document.querySelector('ha-config-device-page'); host.hass.user.is_admin = false; host.updated(new Map()); });
    assert.equal(await card.count(), 0);
    assert.equal(await page.evaluate(() => document.querySelector('ha-config-device-page').originalCalled), true);
    console.log('PASS: device-page injection, initial load, refresh, text escaping, error clearing, admin visibility');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
