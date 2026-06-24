import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../src/components/ChatPanel.jsx", import.meta.url), "utf8");

// sendMessage 函数体（到下一个声明 handleSelectFiles 为止）
const sendStart = src.indexOf("const sendMessage = async");
const sendEnd = src.indexOf("const handleSelectFiles");
const sendMessageBody = src.slice(sendStart, sendEnd);

test("sendMessage 点发送即乐观清空输入框（在 startStream 之前）", () => {
  const clearIdx = sendMessageBody.indexOf("setInput('')");
  const sendIdx = sendMessageBody.indexOf("startStream(");
  assert.ok(clearIdx > -1, "sendMessage 必须乐观清空 setInput('')");
  assert.ok(sendIdx > -1, "sendMessage 必须调用 startStream");
  assert.ok(clearIdx < sendIdx, "乐观清空必须发生在 startStream 之前（点发送即清，不等回答结束）");
});

test("sendMessage 失败恢复走双重守卫：发送序号 + 输入框仍空，绝不覆盖新输入/新发送", () => {
  // 每次发送自增序号
  assert.match(sendMessageBody, /const sendSeq = \+\+sendSeqRef\.current/, "每次发送需自增序号 token");
  // 恢复 helper 先校验序号（防旧的被中止发送盖回已被新发送清空的输入框）
  assert.match(sendMessageBody, /if \(sendSeqRef\.current !== sendSeq\) return/, "恢复前需校验仍是最近一次发送");
  // 再校验输入框仍空（防 abort 后覆盖用户新打的字）
  assert.match(sendMessageBody, /setInput\(prev => prev === '' \? trimmedInput : prev\)/, "仅输入框仍空才回填");
  // 两条失败路径都经同一守卫 helper
  const calls = sendMessageBody.match(/restoreInputForRetry\(\)/g) || [];
  assert.ok(calls.length >= 2, "上传失败与发送失败两条路径都需调用 restoreInputForRetry()");
  // 不得出现无守卫的直接回填
  assert.doesNotMatch(sendMessageBody, /setInput\(trimmedInput\)/, "禁止无守卫的 setInput(trimmedInput)");
});

test("startStream 成功分支不再负责清空输入框（清空职责已上移 sendMessage）", () => {
  const branchStart = src.indexOf("if (!streamFailed && renderUserBubble)");
  const branchEnd = src.indexOf("} else if (streamFailed && renderUserBubble)");
  assert.ok(branchStart > -1 && branchEnd > branchStart, "应保留成功分支结构");
  const successBranch = src.slice(branchStart, branchEnd);
  assert.doesNotMatch(successBranch, /setInput/, "成功分支不得再 setInput（避免双重清空职责）");
});

test("整个 ChatPanel 里 setInput('') 只此一处（乐观清空是唯一来源）", () => {
  const matches = src.match(/setInput\(''\)/g) || [];
  assert.equal(matches.length, 1, "setInput('') 应只在 sendMessage 乐观清空处出现一次");
});
