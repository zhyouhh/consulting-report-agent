import test from "node:test";
import assert from "node:assert/strict";

import {
  buildChatRequest,
  mergeMaterials,
  toggleMaterialSelection,
} from "../src/utils/chatMaterials.js";

test("mergeMaterials keeps existing order and appends new unique materials", () => {
  const existing = [
    { id: "mat-1", display_name: "访谈纪要.txt" },
    { id: "mat-2", display_name: "行业数据.xlsx" },
  ];
  const incoming = [
    { id: "mat-2", display_name: "行业数据.xlsx" },
    { id: "mat-3", display_name: "市场图表.png" },
  ];

  assert.deepEqual(mergeMaterials(existing, incoming), [
    { id: "mat-1", display_name: "访谈纪要.txt" },
    { id: "mat-2", display_name: "行业数据.xlsx" },
    { id: "mat-3", display_name: "市场图表.png" },
  ]);
});

test("toggleMaterialSelection adds an unselected material", () => {
  assert.deepEqual(toggleMaterialSelection(["mat-1"], "mat-2"), ["mat-1", "mat-2"]);
});

test("toggleMaterialSelection removes a selected material", () => {
  assert.deepEqual(toggleMaterialSelection(["mat-1", "mat-2"], "mat-2"), ["mat-1"]);
});

test("buildChatRequest trims message text and preserves selected materials", () => {
  assert.deepEqual(
    buildChatRequest({
      projectId: "proj-1",
      messageText: "  请整理这份纪要  ",
      attachedMaterialIds: ["mat-1", "mat-3"],
    }),
    {
      project_id: "proj-1",
      message_text: "请整理这份纪要",
      attached_material_ids: ["mat-1", "mat-3"],
    },
  );
});
