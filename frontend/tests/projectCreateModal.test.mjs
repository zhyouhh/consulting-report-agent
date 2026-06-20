import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { prepareProjectCreatePayload } from "../src/utils/projectCreatePayload.js";

const modalSource = readFileSync(
  new URL("../src/components/ProjectCreateModal.jsx", import.meta.url),
  "utf-8",
);

test("ProjectCreateModal removes legacy title, name, notes, and target-audience fields and uses a date input", () => {
  assert.doesNotMatch(modalSource, /新建咨询项目/);
  assert.doesNotMatch(modalSource, /项目名称/);
  assert.doesNotMatch(modalSource, /已有材料或备注/);
  // 报告面向对象（高层/中层/执行）选择器已移除。
  assert.doesNotMatch(modalSource, /target_audience|高层决策者|中层管理者|执行团队/);
  assert.match(modalSource, /type="date"/);
  assert.match(modalSource, /onCreate\(prepareProjectCreatePayload\(formData\)\)/);
});

test("prepareProjectCreatePayload derives the project name from the theme and keeps ISO deadline", () => {
  const payload = prepareProjectCreatePayload({
    workspace_dir: "D:\\workspace",
    project_type: "strategy-consulting",
    theme: "  超人起飞  ",
    deadline: "2026-04-02",
    expected_length: "5000字",
    notes: "",
    initial_material_paths: ["D:\\workspace\\brief.md"],
  });

  assert.equal(payload.name, "超人起飞");
  assert.equal(payload.theme, "超人起飞");
  assert.equal(payload.deadline, "2026-04-02");
  assert.equal(payload.notes, "");
  // 目标读者字段已移除，payload 不再携带。
  assert.equal(payload.target_audience, undefined);
  assert.deepEqual(payload.initial_material_paths, ["D:\\workspace\\brief.md"]);
});

test("prepareProjectCreatePayload preserves a meaningful theme as the project display name", () => {
  const payload = prepareProjectCreatePayload({
    workspace_dir: "D:\\workspace",
    project_type: "strategy-consulting",
    theme: "AI 战略 / 2026!",
    deadline: "2026-04-02",
    expected_length: "5000字",
    notes: "",
    initial_material_paths: [],
  });

  assert.equal(payload.name, "AI 战略 / 2026!");
  assert.equal(payload.theme, "AI 战略 / 2026!");
});

test("prepareProjectCreatePayload rejects themes that cannot yield a meaningful project name", () => {
  assert.throws(
    () =>
      prepareProjectCreatePayload({
        workspace_dir: "D:\\workspace",
        project_type: "strategy-consulting",
        theme: "---",
        deadline: "2026-04-02",
        expected_length: "5000字",
        notes: "",
        initial_material_paths: [],
      }),
    /有效的报告主题/,
  );
});
