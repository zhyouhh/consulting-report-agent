import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
const src = readFileSync(new URL('../src/components/Sidebar.jsx', import.meta.url), 'utf-8');

test('Sidebar account block renders quota label', () => {
  assert.match(src, /quotaLabel/, 'Sidebar 账号块应显示今日额度');
});
