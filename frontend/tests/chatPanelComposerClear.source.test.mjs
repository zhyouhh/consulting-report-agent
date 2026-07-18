import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../src/components/ChatPanel.jsx", import.meta.url), "utf8");

// sendMessage 函数体（到下一个声明 handleSelectFiles 为止）
const sendStart = src.indexOf("const sendMessage = async");
const sendEnd = src.indexOf("const handleSelectFiles");
const sendMessageBody = src.slice(sendStart, sendEnd);

test("sendMessage 点发送即乐观清空输入框（在同步 admission 之前）", () => {
  const clearIdx = sendMessageBody.indexOf("setInput('')");
  const sendIdx = sendMessageBody.indexOf("tryStartStream(");
  assert.ok(clearIdx > -1, "sendMessage 必须乐观清空 setInput('')");
  assert.ok(sendIdx > -1, "sendMessage 必须先走同步 tryStartStream admission");
  assert.ok(clearIdx < sendIdx, "乐观清空必须发生在 admission 之前（点发送即清，不等回答结束）");
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

test("startStream 的 turn-end 清理块不负责清空输入框（清空职责已上移 sendMessage）", () => {
  // 材料选择无可见 UI 后，成功/失败两分支已合并为单一 if (renderUserBubble) 清理块。
  // 意图不变：turn-end 清理只清材料选择 / 附件队列，绝不碰 setInput（乐观清空是 sendMessage 的职责）。
  // turn-end 清理块在 isActiveProjectRequest 守卫之后（首个 if (renderUserBubble) 是推用户气泡）。
  const cleanupAnchor = src.indexOf("if (isActiveProjectRequest(requestProjectId)) {");
  assert.ok(cleanupAnchor > -1, "应保留 turn-end 的 isActiveProjectRequest 守卫");
  const branchStart = src.indexOf("if (renderUserBubble)", cleanupAnchor);
  assert.ok(branchStart > -1, "应保留 turn-end 清理块 if (renderUserBubble)");
  const cleanupBlock = src.slice(branchStart, branchStart + 600);
  assert.match(cleanupBlock, /setSelectedMaterialIds\(\[\]\)/, "turn-end 应清空材料选择（失败也清，不留隐形已挂材料）");
  assert.doesNotMatch(cleanupBlock, /setInput/, "turn-end 清理块不得 setInput（避免双重清空职责）");
});

test("整个 ChatPanel 里 setInput('') 只此一处（乐观清空是唯一来源）", () => {
  const matches = src.match(/setInput\(''\)/g) || [];
  assert.equal(matches.length, 1, "setInput('') 应只在 sendMessage 乐观清空处出现一次");
});
