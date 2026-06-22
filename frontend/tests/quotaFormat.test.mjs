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

// Codex quality 轨：导出 util 的边界契约——formatYuan/quotaRatio 对非 finite 输入恒安全。
test('formatYuan coerces non-finite/non-number to 0', () => {
  for (const bad of [NaN, Infinity, -Infinity, null, undefined, '3']) {
    assert.equal(formatYuan(bad), '¥0.00');
  }
  assert.equal(formatYuan(-1.5), '¥-1.50'); // 负数是合法 finite，按原样显示
});

test('quotaRatio never returns NaN and stays within [0,1]', () => {
  const cases = [
    [NaN, 5], [Infinity, 5], [-Infinity, 5], [null, 5], [undefined, 5], ['3', 5],
    [1, 0], [1, -5], [1, NaN], [1, Infinity], [1, null], [1, undefined],
    [-2, 5], [10, 5], [3, 5],
  ];
  for (const [u, c] of cases) {
    const r = quotaRatio(u, c);
    assert.ok(!Number.isNaN(r), `quotaRatio(${String(u)},${String(c)}) 不应为 NaN`);
    assert.ok(r >= 0 && r <= 1, `quotaRatio(${String(u)},${String(c)})=${r} 应落在 [0,1]`);
  }
  assert.equal(quotaRatio(NaN, 5), 0);   // 非 finite used → 0
  assert.equal(quotaRatio('3', 5), 0);   // 与 formatToken 一致：字符串非 finite → 0（不隐式转换）
  assert.equal(quotaRatio(-2, 5), 0);    // 负 used clamp 到 0
});
