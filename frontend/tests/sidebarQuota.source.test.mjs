import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
const src = readFileSync(new URL('../src/components/Sidebar.jsx', import.meta.url), 'utf-8');

test('Sidebar account block renders quota label', () => {
  assert.match(src, /quotaLabel/, 'Sidebar 账号块应显示今日额度');
});

// Codex quality 轨：source-guard 加固——不只查 quotaLabel（注释/未用 import 也会过），
// 同时锁住实际 wiring：条件渲染守 daily_cap_yuan、取 today_cost_yuan，且 quotaLabel 真被调用。
test('Sidebar quota wiring references real me fields and invokes quotaLabel', () => {
  assert.match(src, /import\s*\{[^}]*quotaLabel[^}]*\}\s*from\s*['"][^'"]*quotaFormat/, '应从 quotaFormat 导入 quotaLabel');
  assert.match(src, /daily_cap_yuan/, '应按 daily_cap_yuan 条件渲染额度行');
  assert.match(src, /today_cost_yuan/, '应取 today_cost_yuan 作已用额度');
  assert.match(src, /quotaLabel\s*\(/, 'quotaLabel 应被实际调用，而非仅出现在注释/import');
});
