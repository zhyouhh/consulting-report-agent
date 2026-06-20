import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = () => readFileSync(path.join(__dirname, "../src/components/FilePreviewPanel.jsx"), "utf-8");

test("imports fileTree + fileEditState utils", () => {
  const s = src();
  assert.match(s, /from ['"]\.\.\/utils\/fileTree['"]/);
  assert.match(s, /buildFileTree/);
  assert.match(s, /from ['"]\.\.\/utils\/fileEditState['"]/);
  assert.match(s, /guardLeave/);
});

test("renders grouped tree via buildFileTree", () => {
  assert.match(src(), /buildFileTree\(files,\s*currentStage\)/);
});

test("edit button only for editable current file and only in preview", () => {
  const s = src();
  assert.match(s, /currentEditable/);
  assert.match(s, /!inEdit && currentEditable/);
});

test("edit mode renders a raw textarea bound to edit.draft", () => {
  const s = src();
  assert.match(s, /<textarea/);
  assert.match(s, /value=\{edit\.draft\}/);
  assert.match(s, /editDraft\(prev, e\.target\.value\)/);
});

test("save posts draft with base_mtime_ns and handles 409 reload", () => {
  const s = src();
  assert.match(s, /onSaveFile\(currentFile, snapshot\.draft, snapshot\.baseMtimeNs\)/);
  assert.match(s, /result\?\.conflict/);
  assert.match(s, /reloadAfterConflict/);
});

test("attemptLeave decides via guardLeave; saving blocks; dirty defers to dialog", () => {
  const s = src();
  assert.match(s, /const attemptLeave = useCallback/);
  assert.match(s, /guardLeave\(editRef\.current\)/);
  assert.match(s, /正在保存/);                        // block path
  assert.match(s, /setLeaveDialog\(\{ action \}\)/);  // confirm path 挂起 action
});

test("v1 leave dialog has three buttons 保存/放弃修改/取消 (deferred-action)", () => {
  const s = src();
  assert.match(s, /role="dialog"/);
  assert.match(s, /handleLeaveSave/);
  assert.match(s, /handleLeaveDiscard/);
  assert.match(s, /放弃修改/);
  // 保存按钮：存成功后才执行挂起的离开动作
  assert.match(s, /const handleLeaveSave = useCallback/);
  assert.match(s, /await doSave\(\)/);
  assert.match(s, /dialog\.action\?\.\(\)/);
});

test("exposes attemptLeave + isEditing on the imperative handle", () => {
  const s = src();
  assert.match(s, /useImperativeHandle/);
  assert.match(s, /attemptLeave: \(action\) => attemptLeave\(action\)/);
  // isEditing lets WorkspacePanel.loadFiles skip content reload mid-edit (BLOCKER 3)
  assert.match(s, /isEditing:/);
  // 旧的同步 confirmDiscardIfDirty 接口不得回潜（已统一到延后动作 attemptLeave）
  assert.doesNotMatch(s, /confirmDiscardIfDirty/);
});

test("edit textarea binds edit.draft, not the content prop (refresh can't clobber edits)", () => {
  const s = src();
  // 编辑态用独立 edit.draft 状态；refreshToken 刷新 content prop 不会覆盖正在编辑的内容。
  assert.match(s, /value=\{edit\.draft\}/);
});

test("registers best-effort beforeunload while dirty/saving", () => {
  const s = src();
  assert.match(s, /beforeunload/);
});

test("switching file is guarded via attemptLeave", () => {
  const s = src();
  assert.match(s, /handleSelectFile/);
  assert.match(s, /attemptLeave\(\(\) => onSelectFile\?\.\(path\)\)/);
});

test("shows review_stale advisory on the draft", () => {
  const s = src();
  assert.match(s, /reviewStale/);
  assert.match(s, /建议重新审查/);
});

test("tree/preview split is state-driven + draggable, not a fixed max-h-64 (N4)", () => {
  const s = src();
  // 文件树高度由 treePct 状态驱动（默认三七分），不再写死 max-h-64
  assert.match(s, /from ['"]\.\.\/utils\/filePanelLayout['"]/);
  assert.match(s, /const \[treePct, setTreePct\] = useState\(DEFAULT_TREE_PCT\)/);
  assert.match(s, /style=\{\{ height: `\$\{treePct\}%` \}\}/);
  assert.doesNotMatch(s, /max-h-64/);
  // 可拖动分隔条
  assert.match(s, /const startTreeResize = useCallback/);
  assert.match(s, /onMouseDown=\{startTreeResize\}/);
  assert.match(s, /cursor-row-resize/);
  assert.match(s, /computeTreePct\(ev\.clientY/);
  // 拖动监听必须有卸载兜底清理，避免中途卸载泄漏 + setState-after-unmount
  assert.match(s, /resizeCleanupRef/);
  assert.match(s, /useEffect\(\(\) => \(\) => resizeCleanupRef\.current\?\.\(\), \[\]\)/);
});

test("enter-edit invalidated by any allowed leave during async reload via selection epoch (codex BLOCKER 2)", () => {
  const s = src();
  assert.match(s, /const selectionSeqRef = useRef/);
  assert.match(s, /const targetPath = currentFile/);
  // attemptLeave 的 allow 分支自增序号（含尚未 commit 的异步切换）；进入编辑 await 后比序号
  assert.match(s, /selectionSeqRef\.current \+= 1/);
  assert.match(s, /selectionSeqRef\.current !== seq/);
  // 不能再退回比 currentFile（看不到 pending navigation）
  assert.doesNotMatch(s, /currentFileRef/);
});

test("toolbar save and leave-dialog save share confirmReloadCurrent on 409 (codex NIT 3)", () => {
  const s = src();
  assert.match(s, /const confirmReloadCurrent = useCallback/);
  // 两处 409 分支都复用同一 reload 决策
  assert.match(s, /await confirmReloadCurrent\(\)/);
});

test("three-button dialog closes on Escape = 取消 (codex NIT 1)", () => {
  const s = src();
  assert.match(s, /['"]Escape['"]/);
  assert.match(s, /closeLeaveDialog\(\)/);
});
