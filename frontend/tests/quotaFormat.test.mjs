import { test } from 'node:test';
import assert from 'node:assert';
import { formatYuan, quotaLabel, quotaRatio } from '../src/utils/quotaFormat.js';

test('formatYuan renders 2 decimals with ¥', () => {
  assert.equal(formatYuan(1.5), '¥1.50');
  assert.equal(formatYuan(0), '¥0.00');
});

test('quotaLabel shows used / cap', () => {
  assert.equal(quotaLabel(1.5, 5), '今日 ¥1.50 / ¥5.00');
});

test('quotaRatio clamps 0..1', () => {
  assert.equal(quotaRatio(2, 5), 0.4);
  assert.equal(quotaRatio(10, 5), 1);
  assert.equal(quotaRatio(1, 0), 0); // cap=0 防除零
});
