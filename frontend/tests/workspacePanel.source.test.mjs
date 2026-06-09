import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const wsSrc = () => readFileSync(path.join(__dirname, "../src/components/WorkspacePanel.jsx"), "utf-8");
const appSrc = () => readFileSync(path.join(__dirname, "../src/App.jsx"), "utf-8");

test("WorkspacePanel passes structured files straight through (no name/path remap)", () => {
  const s = wsSrc();
  assert.match(s, /setFiles\(res\.data\.files\)/);
  // 旧的 path.split('/').pop().replace('.md','') 映射已删除
  assert.doesNotMatch(s, /\.split\(['"]\/['"]\)\.pop\(\)\.replace/);
});

test("WorkspacePanel has handleSaveFile posting content + base_mtime_ns", () => {
  const s = wsSrc();
  assert.match(s, /const handleSaveFile/);
  assert.match(s, /base_mtime_ns:\s*baseMtimeNs/);
  assert.match(s, /status\s*===?\s*409|status === 409/);
  assert.match(s, /conflict:\s*true/);
  // R2 BLOCKER：成功后立即 setContent（不依赖被 isEditing early-return 跳过的 loadFiles 刷新）
  assert.match(s, /setContent\(nextContent\)/);
});

test("WorkspacePanel has reloadFile re-GETting content + mtime", () => {
  const s = wsSrc();
  assert.match(s, /const reloadFile/);
  assert.match(s, /mtimeNs:\s*res\.data\.mtime_ns/);
});

test("WorkspacePanel reloadFile guards stale project responses (NIT 3)", () => {
  const s = wsSrc();
  assert.match(s, /const reloadFile/);
  assert.match(s, /project switched/);
});

test("WorkspacePanel loadFiles skips content reload while editing (BLOCKER 3)", () => {
  const s = wsSrc();
  assert.match(s, /filePreviewRef\.current\?\.isEditing\?\.\(\)/);
});

test("WorkspacePanel guards tab switch via filePreviewRef.attemptLeave", () => {
  const s = wsSrc();
  assert.match(s, /const handleTabClick/);
  assert.match(s, /filePreviewRef\.current\.attemptLeave\(\(\) => setActiveTab\(next\)\)/);
});

test("WorkspacePanel is forwardRef exposing attemptLeave (forwards to FilePreviewPanel)", () => {
  const s = wsSrc();
  assert.match(s, /forwardRef/);
  assert.match(s, /useImperativeHandle/);
  assert.match(s, /attemptLeave:\s*\(action\)\s*=>/);
  assert.match(s, /fp\.attemptLeave\(action\)/);
  assert.doesNotMatch(s, /confirmDiscardIfDirty/);
});

test("WorkspacePanel passes review_stale + currentStage to FilePreviewPanel", () => {
  const s = wsSrc();
  assert.match(s, /reviewStale=\{Boolean\(workspace\?\.flags\?\.review_stale\)\}/);
  assert.match(s, /currentStage=\{workspace\?\.stage_code\}/);
  assert.match(s, /onSaveFile=\{handleSaveFile\}/);
  assert.match(s, /onReloadFile=\{reloadFile\}/);
});

test("App guards project switch via workspacePanelRef.attemptLeave (deferred proceed)", () => {
  const s = appSrc();
  assert.match(s, /workspacePanelRef/);
  assert.match(s, /ref=\{workspacePanelRef\}/);
  assert.match(s, /wp\.attemptLeave\(proceed\)/);
});

test("App guards workspace-panel toggle (hide unmounts editor) before hiding (R2 BLOCKER)", () => {
  const s = appSrc();
  assert.match(s, /handleToggleWorkspacePanel/);
  assert.match(s, /onToggleWorkspacePanel=\{handleToggleWorkspacePanel\}/);
  // 守卫只在「当前显示且要隐藏」时拦截，dirty 则把隐藏挂起到三按钮弹窗
  assert.match(s, /showWorkspacePanel && wp\?\.attemptLeave/);
});
