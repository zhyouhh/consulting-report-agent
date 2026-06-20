import test from "node:test";
import assert from "node:assert/strict";

import {
  buildChatRequest,
  buildTransientAttachmentsPayload,
  conversionStatusChip,
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

test("buildChatRequest trims messageText by default", () => {
  assert.deepEqual(
    buildChatRequest({
      projectId: "proj-1",
      messageText: "  请整理这份纪要  ",
    }),
    {
      project_id: "proj-1",
      message_text: "请整理这份纪要",
    },
  );
});

test("buildChatRequest preserves selected materials when provided", () => {
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

test("buildChatRequest omits empty optional fields", () => {
  const req = buildChatRequest({
    projectId: "proj-1",
    messageText: "  请继续  ",
    attachedMaterialIds: [],
    transientAttachments: [],
  });

  assert.equal(Object.prototype.hasOwnProperty.call(req, "attached_material_ids"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(req, "transient_attachments"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(req, "system_trigger"), false);
});

test("buildChatRequest includes transient attachments when provided", () => {
  assert.deepEqual(
    buildChatRequest({
      projectId: "proj-1",
      messageText: "  请看截图  ",
      attachedMaterialIds: ["mat-1"],
      transientAttachments: [
        {
          name: "bug.png",
          mime_type: "image/png",
          data_url: "data:image/png;base64,AAAA",
        },
      ],
    }),
    {
      project_id: "proj-1",
      message_text: "请看截图",
      attached_material_ids: ["mat-1"],
      transient_attachments: [
        {
          name: "bug.png",
          mime_type: "image/png",
          data_url: "data:image/png;base64,AAAA",
        },
      ],
    },
  );
});

test("buildChatRequest with systemTrigger allows empty messageText", () => {
  const req = buildChatRequest({
    projectId: "demo",
    messageText: "",
    systemTrigger: "independent_review_done",
  });

  assert.equal(req.message_text, "");
  assert.equal(req.system_trigger, "independent_review_done");
});

test("buildChatRequest omits system_trigger when null", () => {
  const req = buildChatRequest({ projectId: "demo", messageText: "hi", systemTrigger: null });

  assert.equal(Object.prototype.hasOwnProperty.call(req, "system_trigger"), false);
});

test("buildChatRequest threads run-bound trigger metadata verbatim", () => {
  const req = buildChatRequest({
    projectId: "demo",
    messageText: "",
    systemTrigger: "independent_review_done",
    triggerMetadata: { run_id: "run-abc-123", report_mtime_ns: "1760000000123456789" },
  });

  assert.equal(req.system_trigger, "independent_review_done");
  assert.equal(req.run_id, "run-abc-123");
  assert.equal(req.report_mtime_ns, "1760000000123456789");
});

test("buildChatRequest keeps report_mtime_ns a string without precision loss (>2^53)", () => {
  // A real nanosecond mtime exceeds Number.MAX_SAFE_INTEGER; it must NOT be coerced to a
  // Number, which would silently round it and break the backend run-bound check.
  const bigMtime = "1760000000123456789";
  const req = buildChatRequest({
    projectId: "demo",
    messageText: "",
    systemTrigger: "independent_review_done",
    triggerMetadata: { run_id: "run-x", report_mtime_ns: bigMtime },
  });

  assert.equal(typeof req.report_mtime_ns, "string");
  assert.equal(req.report_mtime_ns, bigMtime);
  // The exact string survives; the same value through Number() would have changed.
  assert.notEqual(String(Number(bigMtime)), bigMtime);
});

test("buildChatRequest omits metadata fields when triggerMetadata is null", () => {
  const req = buildChatRequest({
    projectId: "demo",
    messageText: "",
    systemTrigger: "lint_report_done",
    triggerMetadata: null,
  });

  assert.equal(Object.prototype.hasOwnProperty.call(req, "run_id"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(req, "report_mtime_ns"), false);
});

test("buildChatRequest carries client_message_id on a normal user turn", () => {
  const req = buildChatRequest({
    projectId: "proj-1",
    messageText: "  请看截图  ",
    clientMessageId: "bubble-123",
  });

  assert.equal(req.client_message_id, "bubble-123");
  assert.equal(req.message_text, "请看截图");
});

test("buildChatRequest omits client_message_id when absent", () => {
  const req = buildChatRequest({ projectId: "proj-1", messageText: "hi" });
  assert.equal(Object.prototype.hasOwnProperty.call(req, "client_message_id"), false);
});

test("buildChatRequest never sends client_message_id on a system_trigger turn", () => {
  const req = buildChatRequest({
    projectId: "demo",
    messageText: "",
    systemTrigger: "independent_review_done",
    clientMessageId: "bubble-should-be-dropped",
  });

  assert.equal(req.system_trigger, "independent_review_done");
  assert.equal(Object.prototype.hasOwnProperty.call(req, "client_message_id"), false);
});

test("buildTransientAttachmentsPayload preserves the pending attachment id", () => {
  const payload = buildTransientAttachmentsPayload([
    { id: "att-1", name: "bug.png", mime_type: "image/png", data_url: "data:image/png;base64,AAAA" },
    { id: "att-2", name: "chart.png", mime_type: "image/png", data_url: "data:image/png;base64,BBBB" },
  ]);

  assert.deepEqual(payload, [
    { id: "att-1", name: "bug.png", mime_type: "image/png", data_url: "data:image/png;base64,AAAA" },
    { id: "att-2", name: "chart.png", mime_type: "image/png", data_url: "data:image/png;base64,BBBB" },
  ]);
});

test("conversionStatusChip returns a neutral 未解析 chip for not_parsed (and missing)", () => {
  // N6 Fix1: spec §8 requires every material show a status; not_parsed gets a muted chip.
  const notParsed = conversionStatusChip({ conversion_status: "not_parsed" });
  assert.notEqual(notParsed, null);
  assert.equal(notParsed.label, "未解析");
  assert.equal(notParsed.tone, "not_parsed");

  // Missing / undefined status falls back to the same neutral chip.
  assert.equal(conversionStatusChip({}).label, "未解析");
  assert.equal(conversionStatusChip().label, "未解析");
});

test("conversionStatusChip labels parsed and failed", () => {
  assert.deepEqual(conversionStatusChip({ conversion_status: "parsed" }), {
    label: "已解析",
    tone: "parsed",
    title: null,
  });
  assert.deepEqual(
    conversionStatusChip({ conversion_status: "failed", conversion_reason: "文件损坏" }),
    { label: "失败", tone: "failed", title: "文件损坏" },
  );
});
