from datetime import datetime
from pathlib import Path
import json
import math
import mimetypes
import re
import shutil
import uuid
from typing import Iterable, Optional


class SkillEngine:
    """咨询技能工作流引擎"""

    CORE_CONTEXT_FILES = [
        ("当前项目概览", "plan/project-overview.md"),
        ("当前项目进度", "plan/progress.md"),
        ("阶段门禁", "plan/stage-gates.md"),
        ("项目备注", "plan/notes.md"),
    ]

    FORMAL_PLAN_FILES = {
        "project-overview.md",
        "progress.md",
        "stage-gates.md",
        "notes.md",
        "outline.md",
        "research-plan.md",
        "references.md",
        "tasks.md",
        "review.md",
        "data-log.md",
        "analysis-notes.md",
        "review-checklist.md",
        "presentation-plan.md",
        "delivery-log.md",
    }
    STAGE_CHECKPOINTS_FILENAME = "stage_checkpoints.json"
    STAGE_CHECKPOINT_KEYS = {
        "outline_confirmed_at",
        "review_started_at",
        "review_passed_at",
        "presentation_ready_at",
        "delivery_archived_at",
    }
    MIGRATION_MARKER_KEY = "__migrated_at"
    _CASCADE_ORDER = [
        "outline_confirmed_at",
        "review_started_at",
        "review_passed_at",
        "presentation_ready_at",
        "delivery_archived_at",
    ]
    assert set(_CASCADE_ORDER) == STAGE_CHECKPOINT_KEYS, (
        "_CASCADE_ORDER must cover exactly STAGE_CHECKPOINT_KEYS"
    )
    _EXPECTED_LENGTH_LINE_PATTERN = re.compile(r"预期篇幅[^\n]*?[:：]\s*([^\n(（]+)")
    _EXPECTED_LENGTH_HEADING_PATTERN = re.compile(
        r"^##\s*预期篇幅\s*\n\s*([^\n(（]+)",
        re.MULTILINE,
    )
    _DL_ENTRY_PATTERN = re.compile(r"^#{3,4}\s*\*{0,2}\s*\[(DL-[^\]]+)\]", re.MULTILINE)
    _DL_REFERENCE_PATTERN = re.compile(r"\[(DL-[^\]]+)\]")
    _MARKDOWN_STRIP_PATTERNS = [
        (re.compile(r"```[\s\S]*?```"), ""),
        (re.compile(r"`[^`]*`"), ""),
        (re.compile(r"!\[[^\]]*\]\([^)]*\)"), ""),
        (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
        (re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE), ""),
        (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), ""),
        (re.compile(r"^\s*\d+\.\s+", re.MULTILINE), ""),
        (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
        (re.compile(r"\*([^*]+)\*"), r"\1"),
        (re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE), ""),
    ]
    _EVIDENCE_MARKERS = (
        re.compile(r"https?://"),
        re.compile(r"material:[a-zA-Z0-9\-]+"),
        re.compile(r"^(访谈|调研)[:：]", re.MULTILINE),
    )
    _SELF_SIGNATURE_PATTERNS = [
        re.compile(r"审查人\s*[:：]\s*(咨询报告写作助手|AI|助手|Claude|GPT|ChatGPT|gemini|模型)"),
    ]
    _PREMATURE_REVIEW_VERDICT_PATTERNS = [
        re.compile(r"审查结论\s*[:：]"),
        re.compile(r"建议通过"),
        re.compile(r"审查通过"),
    ]
    _ARCHIVE_CLAIM_PATTERNS = [
        re.compile(r"(项目状态|交付状态)[^\n]*?[:：]?\s*(已完成|已交付|已归档|已结束)"),
    ]
    # Broadened from spec's `客户反馈` to `反馈` to catch variants like `**反馈 A**`
    # that the literal spec regex would miss. Covers all feedback headings, not only
    # customer-facing ones, which also closes a real bypass vector.
    _DELIVERY_PLACEHOLDER_INLINE = re.compile(
        r"-\s*\[x\][^\n]*反馈[^\n]*[(（]?\s*(待记录|待补充|暂无)\s*[)）]?"
    )
    _DELIVERY_BLOCK_RE = re.compile(
        r"-\s*\[x\][^\n]*反馈[^\n]*\n(?P<body>(?:[^\n]*(?:\n|$)){0,5})",
        re.MULTILINE,
    )
    _PLACEHOLDER_WORDS_RE = re.compile(r"[(（]?\s*(待记录|待补充|暂无)\s*[)）]?")
    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
    TEXT_SUFFIXES = {".md", ".txt", ".csv"}
    STAGE_CHECKLIST_ITEMS = {
        "S0": [
            "\u9700\u6c42\u8bbf\u8c08\u5b8c\u6210",
            "\u8303\u56f4\u754c\u5b9a\u660e\u786e",
            "project-overview.md \u521b\u5efa",
            "\u4ea4\u4ed8\u5f62\u5f0f\u786e\u8ba4",
        ],
        "S1": [
            "notes.md \u66f4\u65b0",
            "references.md \u66f4\u65b0",
            "\u5206\u6790\u6846\u67b6\u786e\u5b9a",
            "outline.md \u5b8c\u6210",
            "research-plan.md \u5b8c\u6210",
        ],
        "S2": [
            "data-log.md \u66f4\u65b0",
            "\u4e00\u624b/\u4e8c\u624b\u8d44\u6599\u6761\u76ee\u5f55\u5165",
            "\u8bbf\u8c08\u6216\u8c03\u7814\u8bb0\u5f55\u6c89\u6dc0",
        ],
        "S3": [
            "analysis-notes.md \u521b\u5efa/\u66f4\u65b0",
            "\u5173\u952e\u53d1\u73b0\u63d0\u70bc",
            "\u7ed3\u8bba\u4e0e\u5047\u8bbe\u533a\u5206\u6e05\u695a",
        ],
        "S4": [
            "\u62a5\u544a\u7ed3\u6784\u786e\u5b9a",
            "report_draft_v1.md / content/report.md / content/draft.md / content/final-report.md / output/final-report.md \u4efb\u4e00\u5f62\u6210\u6709\u6548\u8349\u7a3f",
            "\u5404\u7ae0\u8282\u5185\u5bb9\u6301\u7eed\u5b8c\u5584",
            "\u6267\u884c\u6458\u8981\u4e0e\u56fe\u8868\u540c\u6b65\u66f4\u65b0",
        ],
        "S5": [
            "review-checklist.md \u5b8c\u6210",
            "review.md \u8bb0\u5f55\u4fee\u8ba2\u610f\u89c1",
            "\u4e8b\u5b9e\u3001\u903b\u8f91\u4e0e\u8bed\u8a00\u8d28\u91cf\u5ba1\u67e5\u5b8c\u6210",
        ],
        "S6": [
            "\u4ec5\u5f53 project-overview.md \u4e2d\u4ea4\u4ed8\u5f62\u5f0f = \u62a5\u544a+\u6f14\u793a \u65f6\u542f\u7528",
            "presentation-plan.md \u5b8c\u6210",
            "PPT / \u8bb2\u7a3f / Q&A \u51c6\u5907",
        ],
        "S7": [
            "delivery-log.md \u66f4\u65b0",
            "\u5ba2\u6237\u53cd\u9988\u6536\u96c6",
            "\u540e\u7eed\u52a8\u4f5c\u4e0e\u5f52\u6863\u8bb0\u5f55",
        ],
    }
    STAGE_ORDER = ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7")
    STAGE_TITLES = {
        "S0": "项目启动",
        "S1": "研究设计",
        "S2": "资料采集",
        "S3": "分析沉淀",
        "S4": "报告撰写",
        "S5": "质量审查",
        "S6": "演示准备",
        "S7": "交付归档",
    }
    REPORT_DRAFT_CANDIDATES = (
        "report_draft_v1.md",
        "content/report.md",
        "content/draft.md",
        "content/final-report.md",
        "output/final-report.md",
    )

    def _stage_checkpoints_path(self, project_path):
        return Path(project_path) / self.STAGE_CHECKPOINTS_FILENAME

    def _load_stage_checkpoints(self, project_path) -> dict[str, str]:
        raw = self._read_raw_stage_checkpoints(project_path)
        return {
            key: value
            for key, value in raw.items()
            if key in self.STAGE_CHECKPOINT_KEYS and isinstance(value, str)
        }

    def _read_raw_stage_checkpoints(self, project_path) -> dict:
        checkpoints_path = self._stage_checkpoints_path(project_path)
        if not checkpoints_path.exists():
            return {}
        try:
            data = json.loads(checkpoints_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _write_raw_stage_checkpoints(self, project_path, data):
        checkpoints_path = self._stage_checkpoints_path(project_path)
        checkpoints_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _backfill_stage_checkpoints_if_missing(self, project_path):
        checkpoints_path = self._stage_checkpoints_path(project_path)
        if checkpoints_path.exists():
            return

        # No historical confirm event exists; migration records that backfill happened.
        timestamp = datetime.now().isoformat(timespec="seconds")
        migrated = {self.MIGRATION_MARKER_KEY: timestamp}
        stage_gates_path = Path(project_path) / "plan" / "stage-gates.md"
        if stage_gates_path.exists():
            stage_text = stage_gates_path.read_text(encoding="utf-8")
            current_stage = self._extract_stage_code(stage_text)
            if current_stage and self._stage_index(current_stage) >= self._stage_index("S2"):
                migrated["outline_confirmed_at"] = timestamp

        checkpoints_path.write_text(
            json.dumps(migrated, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _clear_stage_checkpoint_cascade(self, project_path, key):
        if key not in self._CASCADE_ORDER:
            raise ValueError(f"unknown cascade key: {key}")

        start = self._CASCADE_ORDER.index(key)
        checkpoints = self._load_stage_checkpoints(project_path)
        changed = False
        for cascade_key in self._CASCADE_ORDER[start:]:
            if cascade_key in checkpoints:
                del checkpoints[cascade_key]
                changed = True

        if changed:
            raw = self._read_raw_stage_checkpoints(project_path)
            marker = raw.get(self.MIGRATION_MARKER_KEY)
            payload = dict(checkpoints)
            if marker:
                payload[self.MIGRATION_MARKER_KEY] = marker
            self._write_raw_stage_checkpoints(project_path, payload)

    def _save_stage_checkpoint(self, project_path, key):
        if key not in self.STAGE_CHECKPOINT_KEYS:
            raise ValueError(f"Unsupported stage checkpoint key: {key}")

        raw = self._read_raw_stage_checkpoints(project_path)
        existing = raw.get(key)
        if isinstance(existing, str):
            return existing

        timestamp = datetime.now().isoformat(timespec="seconds")
        raw[key] = timestamp
        self._write_raw_stage_checkpoints(project_path, raw)
        return timestamp

    def _clear_stage_checkpoint(self, project_path, key):
        if key not in self.STAGE_CHECKPOINT_KEYS:
            raise ValueError(f"Unsupported stage checkpoint key: {key}")

        raw = self._read_raw_stage_checkpoints(project_path)
        if key not in raw:
            return

        raw.pop(key, None)
        self._write_raw_stage_checkpoints(project_path, raw)

    def _resolve_length_targets(self, project_path):
        overview_path = project_path / "plan" / "project-overview.md"
        expected = 3000
        fallback_used = True
        if overview_path.exists():
            text = overview_path.read_text(encoding="utf-8")
            for pattern in (
                self._EXPECTED_LENGTH_LINE_PATTERN,
                self._EXPECTED_LENGTH_HEADING_PATTERN,
            ):
                match = pattern.search(text)
                if not match:
                    continue
                nums = re.findall(r"\d+", match.group(1))
                if nums:
                    expected = max(int(n) for n in nums)
                    fallback_used = False
                    break
        # Plan §9.3: minimum data-log entries scale with expected report length.
        data_log_min = min(12, math.ceil(expected / 1000 * 1.3))
        # Plan §9.3: analysis citations also scale, capped to keep S3 practical.
        analysis_refs_min = min(8, math.ceil(expected / 1000 * 0.8))
        return {
            "expected_length": expected,
            "data_log_min": max(3, data_log_min),
            "analysis_refs_min": max(2, analysis_refs_min),
            # Plan §9.3: drafts below 70% of target length are not review-ready.
            "report_word_floor": int(expected * 0.7),
            "fallback_used": fallback_used,
        }

    def _count_valid_data_log_sources(self, project_path):
        data_log = project_path / "plan" / "data-log.md"
        if not data_log.exists():
            return 0
        text = data_log.read_text(encoding="utf-8")
        entries = list(self._DL_ENTRY_PATTERN.finditer(text))
        valid = 0
        for idx, match in enumerate(entries):
            start = match.end()
            end = entries[idx + 1].start() if idx + 1 < len(entries) else len(text)
            body = text[start:end]
            if any(pattern.search(body) for pattern in self._EVIDENCE_MARKERS):
                valid += 1
        return valid

    def _has_enough_data_log_sources(self, project_path, min_count):
        return self._count_valid_data_log_sources(project_path) >= min_count

    def _count_analysis_refs(self, project_path):
        analysis = project_path / "plan" / "analysis-notes.md"
        data_log = project_path / "plan" / "data-log.md"
        if not analysis.exists() or not data_log.exists():
            return 0
        dl_ids = {
            m.group(1)
            for m in self._DL_ENTRY_PATTERN.finditer(
                data_log.read_text(encoding="utf-8")
            )
        }
        refs = {
            m.group(1)
            for m in self._DL_REFERENCE_PATTERN.finditer(
                analysis.read_text(encoding="utf-8")
            )
        }
        return len(refs & dl_ids)

    def _has_enough_analysis_refs(self, project_path, min_refs):
        return self._count_analysis_refs(project_path) >= min_refs

    def __init__(self, projects_dir: Path, skill_dir: Path):
        self.projects_dir = projects_dir
        self.skill_dir = skill_dir
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.projects_dir / "registry.json"

    def create_project(
        self,
        project_info_or_name=None,
        project_type: str | None = None,
        theme: str | None = None,
        target_audience: str | None = None,
        deadline: str | None = None,
        expected_length: str | None = None,
        notes: str = "",
        workspace_dir: str | None = None,
        initial_material_paths: list[str] | None = None,
        **extra_kwargs,
    ) -> dict:
        """Create new project."""
        payload = self._normalize_create_payload(
            project_info_or_name,
            project_type=project_type,
            theme=theme,
            target_audience=target_audience,
            deadline=deadline,
            expected_length=expected_length,
            notes=notes,
            workspace_dir=workspace_dir,
            initial_material_paths=initial_material_paths,
            **extra_kwargs,
        )

        payload = dict(payload)
        payload["workspace_dir"] = payload.get("workspace_dir") or str(
            self._default_workspace_dir(payload["name"])
        )
        return self._create_workspace_project(payload)

    def _default_workspace_dir(self, project_name: str) -> Path:
        return (self.projects_dir / project_name).resolve()

    def _create_workspace_project(self, payload: dict) -> dict:
        registry = self._load_registry()
        workspace_path = Path(payload["workspace_dir"]).expanduser().resolve()
        if not workspace_path.exists():
            workspace_path.mkdir(parents=True)
        if not workspace_path.is_dir():
            raise ValueError("工作目录无效")
            raise ValueError("宸ヤ綔鐩綍鏃犳晥")

        project_dir = workspace_path / ".consulting-report"
        if project_dir.exists():
            raise ValueError("该工作目录已经初始化过项目")
            raise ValueError("工作目录无效")

        for project in registry["projects"]:
            if Path(project["workspace_dir"]).resolve() == workspace_path:
                raise ValueError("该工作目录已经初始化过项目")

        project_dir.mkdir(parents=True)
        self._initialize_project_structure(project_dir)
        self._populate_v2_plan_files(
            project_path=project_dir,
            name=payload["name"],
            project_type=payload["project_type"],
            theme=payload["theme"],
            target_audience=payload["target_audience"],
            deadline=payload["deadline"],
            expected_length=payload["expected_length"],
            notes=payload["notes"],
        )

        now = datetime.now().isoformat(timespec="seconds")
        project_record = {
            "id": self._new_id("proj"),
            "name": payload["name"],
            "project_type": payload["project_type"],
            "theme": payload["theme"],
            "target_audience": payload["target_audience"],
            "deadline": payload["deadline"],
            "expected_length": payload["expected_length"],
            "notes": payload["notes"],
            "workspace_dir": str(workspace_path),
            "project_dir": str(project_dir),
            "created_at": now,
            "updated_at": now,
        }

        registry["projects"].append(project_record)
        self._save_registry(registry)
        self._save_materials(project_record, [])

        if payload["initial_material_paths"]:
            self.add_materials(
                project_record["id"],
                payload["initial_material_paths"],
                added_via="project_create",
            )

        return project_record

    def _initialize_project_structure(self, project_path: Path):
        plan_path = project_path / "plan"
        content_path = project_path / "content"
        output_path = project_path / "output"
        imported_materials_path = project_path / "materials" / "imported"

        plan_path.mkdir(parents=True, exist_ok=True)
        content_path.mkdir(exist_ok=True)
        output_path.mkdir(exist_ok=True)
        imported_materials_path.mkdir(parents=True, exist_ok=True)

        template_dir = self.skill_dir / "plan-template"
        for template_name in sorted(self.FORMAL_PLAN_FILES):
            template_file = template_dir / template_name
            if template_file.exists():
                shutil.copy(template_file, plan_path / template_name)

    def _populate_v2_plan_files(
        self,
        project_path: Path,
        name: str,
        project_type: str,
        theme: str,
        target_audience: str,
        deadline: str,
        expected_length: str,
        notes: str,
    ):
        today = datetime.now().strftime("%Y-%m-%d")
        replacements = {
            "[填写项目名称]": name,
            "[战略咨询/市场研究/尽职调查/运营优化]": project_type,
            "[描述客户背景、行业环境、当前面临的挑战]": theme,
            "[具体、可衡量的项目目标]": f"面向{target_audience}形成{expected_length}规模的咨询报告初稿",
            "[填写目标读者]": target_audience,
            "[例如：3000字 / 5000-8000字]": expected_length,
            "[填写负责人]": "",
            "[填写报告主题]": theme,
            "[填写客户名称]": "",
        }

        overview_path = project_path / "plan" / "project-overview.md"
        if overview_path.exists():
            content = overview_path.read_text(encoding="utf-8")
            for source, target in replacements.items():
                content = content.replace(source, target)
            content = content.replace("**交付时间**: [YYYY-MM-DD]", f"**交付时间**: {deadline}")
            content = content.replace("**开始日期**: [YYYY-MM-DD]", f"**开始日期**: {today}")
            content = content.replace("**截止日期**: [YYYY-MM-DD]", f"**截止日期**: {deadline}")
            if target_audience and "目标读者" not in content:
                content += f"\n\n## 目标读者\n{target_audience}\n"
            if expected_length and "预期篇幅" not in content:
                content += f"\n## 预期篇幅\n{expected_length}\n"
            overview_path.write_text(content, encoding="utf-8")

        progress_path = project_path / "plan" / "progress.md"
        if progress_path.exists():
            content = progress_path.read_text(encoding="utf-8")
            content = content.replace("[S0/S1/S2/S3/S4/S5/S6/S7]", "S0")
            content = content.replace("[进行中/已完成/待开始/阻塞]", "进行中")
            content = content.replace("[YYYY-MM-DD]", datetime.now().strftime("%Y-%m-%d"), 1)
            progress_path.write_text(content, encoding="utf-8")

        stage_gates_path = project_path / "plan" / "stage-gates.md"
        if stage_gates_path.exists():
            content = stage_gates_path.read_text(encoding="utf-8")
            content = content.replace("**阶段**: [S0/S1/S2/S3/S4/S5/S6/S7]", "**阶段**: S0")
            content = content.replace("**状态**: [进行中/已完成/待开始/阻塞]", "**状态**: 进行中")
            content = content.replace("**更新日期**: [YYYY-MM-DD]", f"**更新日期**: {today}")
            stage_gates_path.write_text(content, encoding="utf-8")

        notes_path = project_path / "plan" / "notes.md"
        if notes_path.exists() and notes:
            content = notes_path.read_text(encoding="utf-8")
            content += f"\n\n## 初始化备注\n{notes}\n"
            notes_path.write_text(content, encoding="utf-8")

    def get_project_record(self, project_ref: str) -> Optional[dict]:
        registry = self._load_registry()
        for project in registry["projects"]:
            if project["id"] == project_ref or project["name"] == project_ref:
                return dict(project)
        return None

    def get_project_path(self, project_ref: str) -> Optional[Path]:
        project_record = self.get_project_record(project_ref)
        if project_record:
            project_path = Path(project_record["project_dir"])
            return project_path if project_path.exists() else None
        return None

    def list_projects(self) -> list:
        registry = self._load_registry()
        return [
            {
                **project,
                "path": project["project_dir"],
            }
            for project in registry["projects"]
        ]

    def list_materials(self, project_ref: str) -> list[dict]:
        project_record = self.get_project_record(project_ref)
        if not project_record:
            return []
        return self._load_materials(project_record)

    def add_materials(self, project_ref: str, material_paths: Iterable[str], added_via: str) -> list[dict]:
        project_record = self.get_project_record(project_ref)
        if not project_record:
            raise ValueError(f"项目 {project_ref} 不存在")

        project_path = Path(project_record["project_dir"]).resolve()
        workspace_root = Path(project_record["workspace_dir"]).resolve()
        materials = self._load_materials(project_record)
        added_materials: list[dict] = []

        for raw_path in material_paths:
            source_path = Path(raw_path).expanduser().resolve()
            if not source_path.exists() or not source_path.is_file():
                raise ValueError(f"材料不存在: {raw_path}")
                raise ValueError(f"鏉愭枡涓嶅瓨鍦? {raw_path}")

            workspace_relative = self._workspace_relative_path(source_path, workspace_root)
            if workspace_relative is not None:
                duplicate = self._find_existing_workspace_material(materials, workspace_relative)
                if duplicate:
                    added_materials.append(duplicate)
                    continue
                stored_rel_path = workspace_relative
                source_type = "workspace"
                original_path = ""
            else:
                duplicate = self._find_existing_imported_material(materials, source_path)
                if duplicate:
                    added_materials.append(duplicate)
                    continue
                destination_rel = self._build_imported_destination(project_path, source_path.name)
                destination_path = project_path / destination_rel
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination_path)
                stored_rel_path = self._to_posix(destination_rel)
                source_type = "imported"
                original_path = str(source_path)

            mime_type, _ = mimetypes.guess_type(source_path.name)
            material = {
                "id": self._new_id("mat"),
                "display_name": source_path.name,
                "media_kind": self._detect_media_kind(source_path),
                "source_type": source_type,
                "stored_rel_path": stored_rel_path,
                "original_path": original_path,
                "added_via": added_via,
                "file_type": source_path.suffix.lstrip(".").lower(),
                "mime_type": mime_type or "application/octet-stream",
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            materials.append(material)
            added_materials.append(material)

        self._save_materials(project_record, materials)
        self._touch_project(project_record["id"])
        return added_materials

    def remove_material(self, project_ref: str, material_id: str):
        project_record = self.get_project_record(project_ref)
        if not project_record:
            raise ValueError(f"项目 {project_ref} 不存在")

        materials = self._load_materials(project_record)
        target = next((item for item in materials if item["id"] == material_id), None)
        if not target:
            raise ValueError("材料不存在")

        if target["source_type"] == "imported":
            imported_path = Path(project_record["project_dir"]) / target["stored_rel_path"]
            if imported_path.exists():
                imported_path.unlink()

        materials = [item for item in materials if item["id"] != material_id]
        self._save_materials(project_record, materials)
        self._touch_project(project_record["id"])

    def delete_project(self, project_ref: str):
        project_record = self.get_project_record(project_ref)
        if project_record:
            project_path = Path(project_record["project_dir"])
            if project_path.exists():
                shutil.rmtree(project_path)
            registry = self._load_registry()
            registry["projects"] = [item for item in registry["projects"] if item["id"] != project_record["id"]]
            self._save_registry(registry)
            return


        raise ValueError(f"项目 {project_ref} 不存在")

    def read_file(self, project_ref: str, file_path: str) -> str:
        """璇诲彇椤圭洰鏂囦欢"""
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        full_path = self._resolve_project_path(project_path, file_path)
        if not full_path.exists():
            raise ValueError(f"文件 {file_path} 不存在")

        return full_path.read_text(encoding="utf-8")

    def write_file(self, project_ref: str, file_path: str, content: str):
        """鍐欏叆椤圭洰鏂囦欢"""
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        normalized_path = self.validate_plan_write(project_ref, file_path)
        full_path = self._resolve_project_path(project_path.resolve(), normalized_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    def normalize_file_path(self, project_ref: str, file_path: str) -> str:
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        project_root = project_path.resolve()
        full_path = self._resolve_project_path(project_root, file_path)
        normalized_path = self._to_posix(full_path.relative_to(project_root))
        return self._canonicalize_plan_markdown_path(normalized_path)

    def is_formal_plan_file(self, file_path: str) -> bool:
        normalized_path = self._canonicalize_plan_markdown_path(self._to_posix(file_path).lstrip("/"))
        if not self._is_plan_markdown_path(normalized_path):
            return False
        return normalized_path.split("/", 1)[1] in self.FORMAL_PLAN_FILES

    def evidence_gate_satisfied(self, project_ref: str) -> bool:
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")
        return self._has_effective_notes(project_path) and self._has_effective_references(project_path)

    def validate_plan_write(self, project_ref: str, file_path: str) -> str:
        normalized_path = self.normalize_file_path(project_ref, file_path)

        if not self._is_plan_markdown_path(normalized_path):
            return normalized_path

        if self._is_backend_owned_stage_tracking_file(normalized_path):
            raise ValueError(
                f"`{normalized_path}` is backend-generated. Update the substantive project files instead of writing stage tracking files directly."
            )

        if not self.is_formal_plan_file(normalized_path):
            raise ValueError(
                f"`{normalized_path}` is not an official plan file. Use only the registered `plan/*.md` files and never invent unofficial files such as `plan/gate-control.md`."
            )

        if self._requires_pre_outline_evidence(normalized_path) and not self.evidence_gate_satisfied(project_ref):
            raise ValueError(
                "Before writing `plan/outline.md` or `plan/research-plan.md`, update `notes.md` and `references.md` and satisfy the minimum 2-source rule."
            )

        return normalized_path

    def _delivery_log_has_placeholder_feedback(self, content: str) -> bool:
        if self._DELIVERY_PLACEHOLDER_INLINE.search(content):
            return True
        for match in self._DELIVERY_BLOCK_RE.finditer(content):
            body = match.group("body")
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("- [") or stripped.startswith("#"):
                    break
                if self._PLACEHOLDER_WORDS_RE.search(line):
                    return True
        return False

    def validate_self_signature(self, normalized_path: str, content: str, checkpoints: dict) -> str | None:
        """Return an error message if `content` violates self-signature / premature-verdict /
        archive-claim rules for the given plan file path, else None.

        Interception is auto-disabled when the corresponding checkpoint stamp is present
        (spec §12.3): `review_passed_at` disables review-checklist.md interception; and
        `delivery_archived_at` disables delivery-log.md interception. The auto-disable
        lets the UI-driven advance flow write whatever it needs without false positives
        after the user has already confirmed the stage via the right-side workspace.

        Returns:
            str error message for the caller to surface in the chat stream AND return
            as the tool error, or None if the write is allowed.
        """
        if normalized_path == "plan/review-checklist.md":
            if "review_passed_at" in checkpoints:
                return None
            for pattern in self._SELF_SIGNATURE_PATTERNS:
                if pattern.search(content):
                    return (
                        "review-checklist.md 的\"审查人\"字段必须由真实用户签字，"
                        "请保留\"审查人：[待用户确认]\"让用户在 UI 上签字。"
                    )
            if "review_started_at" not in checkpoints:
                for pattern in self._PREMATURE_REVIEW_VERDICT_PATTERNS:
                    if pattern.search(content):
                        return (
                            "review-checklist.md 的\"审查结论 / 建议通过\"字段必须在用户点击"
                            "\"完成撰写，开始审查\"按钮之后再写入。当前审查尚未开始，"
                            "请保留为空或\"[待审查]\"，并告知用户需要他们先点按钮进入审查阶段。"
                        )
        if normalized_path == "plan/delivery-log.md":
            if "delivery_archived_at" in checkpoints:
                return None
            for pattern in self._ARCHIVE_CLAIM_PATTERNS:
                if pattern.search(content):
                    return (
                        "delivery-log.md 声明\"已归档/已交付\"需要用户点击 UI 的\"归档结束项目\"按钮。"
                        "请把状态保持为\"待归档\"，并告知用户需要他们点按钮。"
                    )
            if self._delivery_log_has_placeholder_feedback(content):
                return (
                    "delivery-log.md 勾选\"客户反馈\"需要真实反馈内容，"
                    "请保留为未勾选，等用户补齐反馈后再勾。"
                )
        return None

    def is_protected_stage_checkpoints_path(self, normalized_path: str) -> bool:
        """Return True if `normalized_path` points to `stage_checkpoints.json` (the user-
        confirmation truth source). The model must never be able to directly write this
        file via `write_file` — only the checkpoint endpoints (via `record_stage_checkpoint`)
        may mutate it.

        Comparison must be case-insensitive because Windows filesystems are case-insensitive
        by default: `Stage_Checkpoints.json` and `STAGE_CHECKPOINTS.JSON` all resolve to
        the same file. Backslashes are normalized to forward slashes before the basename
        extraction so Windows-style relative paths (`plan\\..\\Stage_Checkpoints.json`)
        are also blocked.
        """
        if not normalized_path:
            return False
        tail = normalized_path.replace("\\", "/").rsplit("/", 1)[-1]
        return tail.casefold() == self.STAGE_CHECKPOINTS_FILENAME.casefold()

    def read_material_file(self, project_ref: str, material_id: str) -> str:
        project_record = self.get_project_record(project_ref)
        if not project_record:
            raise ValueError(f"项目 {project_ref} 不存在")

        material = self.get_material(project_ref, material_id)
        if material["media_kind"] == "image_like":
            raise ValueError("当前暂不支持读取该材料")

        material_path = self.get_material_path(project_ref, material_id)
        suffix = material_path.suffix.lower()

        if suffix in self.TEXT_SUFFIXES:
            return material_path.read_text(encoding="utf-8")
        if suffix == ".docx":
            return self._read_docx(material_path)
        if suffix == ".xlsx":
            return self._read_xlsx(material_path)
        if suffix == ".pdf":
            return self._read_pdf(material_path)

        try:
            return material_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"当前暂不支持读取 {suffix} 材料") from exc

    def get_material(self, project_ref: str, material_id: str) -> dict:
        material = next((item for item in self.list_materials(project_ref) if item["id"] == material_id), None)
        if not material:
            raise ValueError("材料不存在")
        return material

    def get_material_path(self, project_ref: str, material_id: str) -> Path:
        project_record = self.get_project_record(project_ref)
        if not project_record:
            raise ValueError(f"项目 {project_ref} 不存在")

        material = self.get_material(project_ref, material_id)
        if material["source_type"] == "workspace":
            return (Path(project_record["workspace_dir"]) / material["stored_rel_path"]).resolve()
        return (Path(project_record["project_dir"]) / material["stored_rel_path"]).resolve()

    def get_workspace_summary(self, project_ref: str) -> dict:
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        self._backfill_stage_checkpoints_if_missing(project_path)
        stage_state = self._infer_stage_state(project_path)
        project_record = self.get_project_record(project_ref) or {}
        tracking_state = self._sync_stage_tracking_files(project_path)
        materials = self.list_materials(project_ref)

        checkpoints = stage_state.get("checkpoints", {})
        next_stage_hint = None
        if "review_passed_at" in checkpoints:
            next_stage_hint = "S6" if self._delivery_mode_requires_presentation(project_path) else "S7"

        stalled_since = None
        if stage_state["stage_code"] in ("S2", "S3"):
            last_write = self._last_evidence_write_at(project_path)
            if last_write is not None:
                elapsed = datetime.now() - last_write
                if elapsed.total_seconds() >= 30 * 60:
                    stalled_since = last_write.isoformat(timespec="seconds")

        length_targets = stage_state.get("length_targets", {})

        return {
            "stage_code": stage_state["stage_code"],
            "status": stage_state.get("stage_status", tracking_state["status"]),
            "completed_items": stage_state["completed_items"],
            "skipped_items": stage_state.get("skipped_items", []),
            "next_actions": tracking_state["next_actions"],
            "workspace_dir": project_record.get("workspace_dir", ""),
            "project_dir": str(project_path),
            "materials": materials,
            "checkpoints": checkpoints,
            "length_targets": length_targets,
            "length_fallback_used": length_targets.get("fallback_used", False),
            "quality_progress": self._build_quality_progress(project_path, stage_state),
            "flags": stage_state.get("flags", {}),
            "next_stage_hint": next_stage_hint,
            "stalled_since": stalled_since,
            "word_count": self._current_report_word_count(project_path),
            "delivery_mode": self._extract_delivery_mode(project_path),
        }

    def build_project_context(self, project_ref: str) -> str:
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        self._sync_stage_tracking_files(project_path)
        sections = []
        for title, relative_path in self.CORE_CONTEXT_FILES:
            content = self._read_optional(project_ref, relative_path)
            if content:
                sections.append(f"## {title}\n{content}")

        if self._is_effective_plan_file(project_path, "tasks.md"):
            tasks_content = self._read_plan_file(project_path, "tasks.md")
            if tasks_content:
                sections.append(f"## 当前阶段任务\n{tasks_content}")

        materials = self.list_materials(project_ref)
        if materials:
            material_lines = [
                f"- {material['id']} | {material['display_name']} | {material['source_type']} | {material['file_type']}"
                for material in materials
            ]
            sections.append("## 可用项目材料\n" + "\n".join(material_lines))

        return "\n\n".join(sections)

    def get_script_path(self, script_name: str) -> str:
        script_path = self.skill_dir / "scripts" / script_name
        if not script_path.exists():
            raise ValueError(f"脚本 {script_name} 不存在")
        return str(script_path)

    def ensure_output_dir(self, project_ref: str) -> str:
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        output_dir = project_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        return str(output_dir)

    def get_primary_report_path(self, project_ref: str) -> str:
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        output_report = project_path / "output" / "final-report.md"
        if output_report.exists():
            return str(output_report)

        content_dir = project_path / "content"
        preferred_names = ["final-report.md", "report.md", "draft.md"]
        for name in preferred_names:
            candidate = content_dir / name
            if candidate.exists():
                return str(candidate)

        content_files = sorted(content_dir.glob("*.md"))
        if len(content_files) == 1:
            return str(content_files[0])

        report_like_files = [path for path in content_files if "report" in path.stem.lower()]
        if len(report_like_files) == 1:
            return str(report_like_files[0])

        if content_files:
            raise ValueError("存在多个内容文件，无法确定主报告，请先生成 final-report.md")

        raise ValueError("没有可检查或导出的报告草稿")

    def _ensure_stage_gates_state(self, project_path: Path) -> str:
        return self._sync_stage_tracking_files(project_path)["stage_gates_text"]
        stage_gates_path = project_path / "plan" / "stage-gates.md"
        if not stage_gates_path.exists():
            template_path = self.skill_dir / "plan-template" / "stage-gates.md"
            if template_path.exists():
                shutil.copy(template_path, stage_gates_path)
            else:
                return ""

        stage_state = self._infer_stage_state(project_path)
        stage_code = stage_state["stage_code"]
        if not stage_code:
            return stage_gates_path.read_text(encoding="utf-8")

        original_content = stage_gates_path.read_text(encoding="utf-8")
        content = original_content
        content = re.sub(r"\*\*闃舵\*\*:\s*[^\n]+", f"**闃舵**: {stage_code}", content)
        content = re.sub(r"\*\*状态\*\*:\s*[^\n]+", "**状态**: 进行中", content)
        content = re.sub(
            r"\*\*鏇存柊鏃ユ湡\*\*:\s*[^\n]+",
            f"**鏇存柊鏃ユ湡**: {datetime.now().strftime('%Y-%m-%d')}",
            content,
        )
        for task in self._tracked_stage_items():
            content = self._set_stage_gate_item_state(content, task, " ")
        for task in stage_state["completed_items"]:
            content = self._set_stage_gate_item_state(content, task, "x")
        for task in stage_state["skipped_items"]:
            content = self._set_stage_gate_item_state(content, task, "/")

        if content != original_content:
            stage_gates_path.write_text(content, encoding="utf-8")
        return content

    def _sync_stage_tracking_files(self, project_path: Path) -> dict:
        stage_state = self._infer_stage_state(project_path)
        stage_code = stage_state["stage_code"] or "S0"
        completed_items = list(stage_state["completed_items"])
        skipped_items = list(stage_state["skipped_items"])
        next_actions = [
            item
            for item in self.STAGE_CHECKLIST_ITEMS.get(stage_code, [])
            if item not in completed_items and item not in skipped_items
        ]
        status = "进行中"
        stage_gates_path = project_path / "plan" / "stage-gates.md"
        existing_stage_gates = stage_gates_path.read_text(encoding="utf-8") if stage_gates_path.exists() else ""
        manual_lines = self._extract_manual_stage_gate_lines(existing_stage_gates)
        stage_gates_text = self._render_stage_gates_markdown(
            stage_code,
            status,
            completed_items,
            skipped_items,
            manual_lines,
        )
        progress_text = self._render_progress_markdown(stage_code, status, next_actions, completed_items)
        tasks_text = self._render_tasks_markdown(stage_code, next_actions)

        self._write_tracking_file(stage_gates_path, stage_gates_text)
        self._write_tracking_file(project_path / "plan" / "progress.md", progress_text)
        self._write_tracking_file(project_path / "plan" / "tasks.md", tasks_text)

        return {
            "stage_code": stage_code,
            "status": status,
            "completed_items": completed_items,
            "skipped_items": skipped_items,
            "next_actions": next_actions,
            "stage_gates_text": stage_gates_text,
        }

    def _extract_delivery_mode(self, project_path: Path) -> str:
        """Parse 交付形式 from plan/project-overview.md."""
        overview_path = project_path / "plan" / "project-overview.md"
        if not overview_path.exists():
            return "仅报告"
        text = overview_path.read_text(encoding="utf-8")
        match = re.search(r"交付形式[^\n]*?[:：]\s*([^\n]+)", text)
        if not match:
            return "仅报告"
        value = match.group(1).strip()
        return "报告+演示" if "演示" in value else "仅报告"

    def _current_report_word_count(self, project_path: Path) -> int:
        counts = []
        for candidate in self.REPORT_DRAFT_CANDIDATES:
            draft_text = self._read_project_file(project_path, candidate)
            if not draft_text or self._is_template_stub(draft_text):
                continue
            counts.append(self._count_words(draft_text))
        return max(counts) if counts else 0

    def _last_evidence_write_at(self, project_path: Path) -> datetime | None:
        candidates = [
            project_path / "plan" / "notes.md",
            project_path / "plan" / "references.md",
            project_path / "plan" / "data-log.md",
            project_path / "plan" / "analysis-notes.md",
        ]
        mtimes = [path.stat().st_mtime for path in candidates if path.exists()]
        if not mtimes:
            return None
        return datetime.fromtimestamp(max(mtimes))

    def _build_quality_progress(self, project_path: Path, stage_state: dict) -> dict | None:
        stage = stage_state["stage_code"]
        targets = stage_state.get("length_targets", {})
        if stage == "S2":
            return {
                "label": "有效来源条目",
                "current": self._count_valid_data_log_sources(project_path),
                "target": targets.get("data_log_min", 0),
            }
        if stage == "S3":
            return {
                "label": "分析证据引用",
                "current": self._count_analysis_refs(project_path),
                "target": targets.get("analysis_refs_min", 0),
            }
        return None

    def record_stage_checkpoint(self, project_id: str, key: str, action: str) -> dict:
        from backend.chat import _get_project_request_lock

        project_path = self.get_project_path(project_id)
        if project_path is None:
            raise ValueError(f"项目不存在: {project_id}")
        if action not in ("set", "clear"):
            raise ValueError(f"未知 action: {action}")

        lock = _get_project_request_lock(project_id)
        with lock:
            if action == "set":
                timestamp = self._save_stage_checkpoint(project_path, key)
                self._sync_stage_tracking_files(project_path)
                return {"status": "ok", "key": key, "timestamp": timestamp}
            self._clear_stage_checkpoint_cascade(project_path, key)
            self._sync_stage_tracking_files(project_path)
            return {"status": "ok", "key": key, "cleared": True}

    def _write_tracking_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if existing != content:
            path.write_text(content, encoding="utf-8")

    def _extract_manual_stage_gate_lines(self, markdown_text: str) -> list[str]:
        manual_lines: list[str] = []
        tracked_items = set(self._tracked_stage_items())
        for raw_line in markdown_text.splitlines():
            stripped = raw_line.strip()
            match = re.match(r"^- \[(?: |x|X|/)\] (.+)$", stripped)
            if not match:
                continue
            task = match.group(1).strip()
            if task in tracked_items:
                continue
            manual_lines.append(stripped)
        return manual_lines

    def _render_stage_gates_markdown(
        self,
        stage_code: str,
        status: str,
        completed_items: list[str],
        skipped_items: list[str],
        manual_lines: list[str] | None = None,
    ) -> str:
        lines = [
            "# 项目阶段与门禁",
            "",
            "## 当前阶段",
            "",
            f"**阶段**: {stage_code}",
            f"**状态**: {status}",
            f"**更新日期**: {datetime.now().strftime('%Y-%m-%d')}",
            "",
            "## 阶段进度",
            "",
        ]
        for stage in self.STAGE_ORDER:
            lines.append(f"### {stage} {self.STAGE_TITLES.get(stage, stage)}")
            for item in self.STAGE_CHECKLIST_ITEMS[stage]:
                state = "x" if item in completed_items else "/" if item in skipped_items else " "
                lines.append(f"- [{state}] {item}")
            lines.append("")
        if manual_lines:
            lines.append("## 手工补充记录")
            lines.append("")
            lines.extend(manual_lines)
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _render_progress_markdown(
        self,
        stage_code: str,
        status: str,
        next_actions: list[str],
        completed_items: list[str],
    ) -> str:
        current_task = next_actions[0] if next_actions else "当前阶段任务已完成，等待推进下一阶段。"
        completed_summary = " / ".join(completed_items[-3:]) if completed_items else "-"
        next_summary = " / ".join(next_actions[:3]) if next_actions else "-"
        lines = [
            "# 项目进度追踪",
            "",
            "## 当前状态",
            f"**阶段**: {stage_code}",
            f"**状态**: {status}",
            f"**当前任务**: {current_task}",
            f"**更新日期**: {datetime.now().strftime('%Y-%m-%d')}",
            "",
            "## 执行摘要",
            f"- 已完成: {completed_summary}",
            f"- 下一步: {next_summary}",
        ]
        return "\n".join(lines).strip() + "\n"

    def _render_tasks_markdown(self, stage_code: str, next_actions: list[str]) -> str:
        lines = [
            "# 任务清单",
            "",
            "## 当前阶段",
            "",
            f"**阶段**: {stage_code}",
            f"**阶段目标**: {self.STAGE_TITLES.get(stage_code, stage_code)}",
            "",
            "## 当前阶段待办",
        ]
        if next_actions:
            lines.extend(f"- [ ] {item}" for item in next_actions)
        else:
            lines.append("- [x] 当前阶段待办已清空，可推进下一阶段。")
        return "\n".join(lines).strip() + "\n"

    def _infer_stage_progress(self, project_path: Path) -> tuple[str, list[str]]:
        stage_state = self._infer_stage_state(project_path)
        return stage_state["stage_code"], stage_state["completed_items"]

    def _has_meaningful_outline(self, project_path: Path) -> bool:
        return self._has_effective_outline(project_path)

    def _infer_stage_state(self, project_path: Path) -> dict:
        targets = self._resolve_length_targets(project_path)
        checkpoints = self._load_stage_checkpoints(project_path)

        project_overview_ready = self._is_effective_plan_file(project_path, "project-overview.md")
        notes_ready = self._has_effective_notes(project_path)
        references_ready = self._has_effective_references(project_path)
        outline_ready = self._has_effective_outline(project_path)
        research_plan_ready = self._has_effective_research_plan(project_path)

        data_log_quality_ok = self._has_enough_data_log_sources(project_path, targets["data_log_min"])
        analysis_quality_ok = self._has_enough_analysis_refs(project_path, targets["analysis_refs_min"])

        report_ready = self._has_effective_report_draft(project_path, min_words=targets["report_word_floor"])
        review_checklist_ready = self._has_effective_review_checklist(project_path)
        presentation_ready = self._has_effective_presentation_plan(project_path)
        delivery_ready = self._has_effective_delivery_log(project_path)
        presentation_required = self._delivery_mode_requires_presentation(project_path)

        outline_confirmed = "outline_confirmed_at" in checkpoints
        review_started = "review_started_at" in checkpoints
        review_passed = "review_passed_at" in checkpoints
        presentation_done = "presentation_ready_at" in checkpoints
        delivery_archived = "delivery_archived_at" in checkpoints

        stage_zero_complete = project_overview_ready
        stage_one_complete = (
            stage_zero_complete
            and notes_ready
            and references_ready
            and outline_ready
            and research_plan_ready
            and outline_confirmed
        )
        stage_two_complete = stage_one_complete and data_log_quality_ok
        stage_three_complete = stage_two_complete and analysis_quality_ok
        stage_four_complete = stage_three_complete and report_ready and review_started
        stage_five_complete = stage_four_complete and review_checklist_ready and review_passed
        stage_six_complete = stage_five_complete and (
            (presentation_ready and presentation_done) if presentation_required else True
        )
        stage_seven_complete = stage_six_complete and delivery_ready and delivery_archived

        if not stage_zero_complete:
            stage_code = "S0"
            stage_status = "进行中"
        elif not stage_one_complete:
            stage_code = "S1"
            stage_status = "进行中"
        elif not stage_two_complete:
            stage_code = "S2"
            stage_status = "进行中"
        elif not stage_three_complete:
            stage_code = "S3"
            stage_status = "进行中"
        elif not stage_four_complete:
            stage_code = "S4"
            stage_status = "进行中"
        elif not stage_five_complete:
            stage_code = "S5"
            stage_status = "进行中"
        elif presentation_required and not stage_six_complete:
            stage_code = "S6"
            stage_status = "进行中"
        elif not stage_seven_complete:
            stage_code = "S7"
            stage_status = "进行中"
        else:
            stage_code = "done"
            stage_status = "已归档"

        # *_ready means effective file content; *_confirmed/started/passed/done/archived means a user checkpoint.
        flags = {
            "project_overview_ready": project_overview_ready,
            "notes_ready": notes_ready,
            "references_ready": references_ready,
            "outline_ready": outline_ready,
            "research_plan_ready": research_plan_ready,
            "data_log_ready": data_log_quality_ok,
            "analysis_ready": analysis_quality_ok,
            "report_ready": report_ready,
            "review_checklist_ready": review_checklist_ready,
            "review_notes_ready": self._has_effective_review_notes(project_path),
            "review_ready": review_checklist_ready and review_passed,
            "presentation_ready": presentation_ready,
            "delivery_ready": delivery_ready and delivery_archived,
            "presentation_required": presentation_required,
            "outline_confirmed": outline_confirmed,
            "review_started": review_started,
            "review_passed": review_passed,
            "presentation_done": presentation_done,
            "delivery_archived": delivery_archived,
        }
        return {
            "stage_code": stage_code,
            "stage_status": stage_status,
            "completed_items": self._build_completed_items(stage_code, flags),
            "skipped_items": self._build_skipped_items(stage_code, flags),
            "checkpoints": checkpoints,
            "length_targets": targets,
            "flags": flags,
        }

    def _build_completed_items(self, stage_code: str, flags: dict) -> list[str]:
        completed: list[str] = []
        stage_index = self._stage_index(stage_code)
        for stage in self.STAGE_ORDER[:stage_index]:
            if stage == "S6" and not flags["presentation_required"]:
                continue
            if stage == "S5":
                completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][0])
                if flags["review_notes_ready"]:
                    completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][1])
                completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][2])
                continue
            completed.extend(self.STAGE_CHECKLIST_ITEMS[stage])

        if stage_code == "S0":
            if flags["project_overview_ready"]:
                completed.append(self.STAGE_CHECKLIST_ITEMS["S0"][2])
        elif stage_code == "S1":
            if flags["notes_ready"]:
                completed.append(self.STAGE_CHECKLIST_ITEMS["S1"][0])
            if flags["references_ready"]:
                completed.append(self.STAGE_CHECKLIST_ITEMS["S1"][1])
            if flags["outline_ready"] or flags["research_plan_ready"]:
                completed.append(self.STAGE_CHECKLIST_ITEMS["S1"][2])
            if flags["outline_ready"]:
                completed.append(self.STAGE_CHECKLIST_ITEMS["S1"][3])
            if flags["research_plan_ready"]:
                completed.append(self.STAGE_CHECKLIST_ITEMS["S1"][4])
        elif stage_code == "S3" and flags["analysis_ready"]:
            completed.extend(self.STAGE_CHECKLIST_ITEMS["S3"])
        elif stage_code == "S4" and flags["report_ready"]:
            completed.extend(self.STAGE_CHECKLIST_ITEMS["S4"])
        elif stage_code == "S5":
            if flags["review_checklist_ready"]:
                completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][0])
            if flags["review_notes_ready"]:
                completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][1])
            if flags["review_checklist_ready"]:
                completed.append(self.STAGE_CHECKLIST_ITEMS["S5"][2])
        elif stage_code == "S6":
            completed.append(self.STAGE_CHECKLIST_ITEMS["S6"][0])
            if flags["presentation_ready"]:
                completed.extend(self.STAGE_CHECKLIST_ITEMS["S6"][1:])
        elif stage_code == "S7" and flags["delivery_ready"]:
            if flags["presentation_required"] and flags["presentation_ready"]:
                completed.extend(self.STAGE_CHECKLIST_ITEMS["S6"])
            completed.extend(self.STAGE_CHECKLIST_ITEMS["S7"])

        return list(dict.fromkeys(completed))

    def _build_skipped_items(self, stage_code: str, flags: dict) -> list[str]:
        if not flags["presentation_required"] and stage_code in {"S7", "done"}:
            return list(self.STAGE_CHECKLIST_ITEMS["S6"])
        return []

    def _stage_index(self, stage_code: str) -> int:
        if stage_code == "done":
            return len(self.STAGE_ORDER)
        return self.STAGE_ORDER.index(stage_code)

    def _tracked_stage_items(self) -> list[str]:
        tracked_items: list[str] = []
        for stage in self.STAGE_ORDER:
            tracked_items.extend(self.STAGE_CHECKLIST_ITEMS[stage])
        return tracked_items

    def _set_stage_gate_item_state(self, content: str, task: str, state: str) -> str:
        pattern = rf"- \[(?: |x|X|/)\] {re.escape(task)}"
        return re.sub(pattern, f"- [{state}] {task}", content)

    def _is_plan_markdown_path(self, normalized_path: str) -> bool:
        candidate = self._to_posix(normalized_path).lstrip("/").lower()
        return candidate.startswith("plan/") and candidate.endswith(".md")

    def _requires_pre_outline_evidence(self, normalized_path: str) -> bool:
        return self._canonicalize_plan_markdown_path(normalized_path) in {
            "plan/outline.md",
            "plan/research-plan.md",
        }

    def _is_backend_owned_stage_tracking_file(self, normalized_path: str) -> bool:
        return self._canonicalize_plan_markdown_path(normalized_path) in {
            "plan/stage-gates.md",
            "plan/progress.md",
            "plan/tasks.md",
        }

    def _canonicalize_plan_markdown_path(self, normalized_path: str) -> str:
        candidate = self._to_posix(normalized_path).lstrip("/")
        if not self._is_plan_markdown_path(candidate):
            return candidate
        return candidate.lower()

    def _is_effective_plan_file(self, project_path: Path, file_name: str) -> bool:
        text = self._read_plan_file(project_path, file_name)
        if not text:
            return False
        if self._is_template_content(text, file_name):
            return False
        return self._has_substantive_body(text)

    def _read_plan_file(self, project_path: Path, file_name: str) -> str:
        file_path = project_path / "plan" / file_name
        if not file_path.exists():
            return ""
        return file_path.read_text(encoding="utf-8").strip()

    def _read_project_file(self, project_path: Path, relative_path: str) -> str:
        file_path = project_path / relative_path
        if not file_path.exists():
            return ""
        return file_path.read_text(encoding="utf-8").strip()

    def _is_template_content(self, text: str, file_name: str) -> bool:
        template_path = self.skill_dir / "plan-template" / file_name
        if not template_path.exists():
            return False
        template_text = template_path.read_text(encoding="utf-8").strip()
        return self._normalize_text(text) == self._normalize_text(template_text)

    def _has_substantive_body(self, text: str) -> bool:
        return self._count_substantive_body_lines(text) > 0

    def _count_substantive_body_lines(self, text: str) -> int:
        count = 0
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped == "---":
                continue
            if stripped.startswith("#"):
                continue
            if stripped.startswith("<!--") and stripped.endswith("-->"):
                continue
            if stripped.startswith("|"):
                continue
            candidate = stripped[2:].strip() if stripped.startswith("- ") else stripped
            candidate = re.sub(r"\*\*([^*]+)\*\*\s*[:：]\s*", "", candidate).strip()
            if self._is_substantive_field_value(candidate):
                count += 1
        return count

    def _is_substantive_field_value(self, value: str) -> bool:
        candidate = re.sub(r"\*\*", "", (value or "").strip())
        if not candidate or candidate in {"-", "|"}:
            return False
        if re.search(r"\[[ xX/]\]", candidate):
            return False
        if candidate.startswith("[") and candidate.endswith("]"):
            return False
        if re.fullmatch(r"\d+\.", candidate):
            return False
        if re.fullmatch(r"[|\-:\s]+", candidate):
            return False
        if candidate.endswith(":") or candidate.endswith("："):
            return False
        return True

    def _has_effective_notes(self, project_path: Path) -> bool:
        notes_text = self._read_plan_file(project_path, "notes.md")
        if not notes_text or self._is_template_content(notes_text, "notes.md"):
            return False
        heading_count = len(re.findall(r"^(?:##+|###)\s+", notes_text, flags=re.MULTILINE))
        labeled_line_count = self._count_substantive_labeled_lines(notes_text)
        substantive_line_count = self._count_substantive_body_lines(notes_text)
        return substantive_line_count >= 2 and (heading_count >= 2 or labeled_line_count >= 2)

    def _has_effective_references(self, project_path: Path) -> bool:
        references_text = self._read_plan_file(project_path, "references.md")
        if not references_text or self._is_template_content(references_text, "references.md"):
            return False
        return self._count_reference_evidence(references_text) >= 2

    def _count_reference_evidence(self, references_text: str) -> int:
        count = 0
        for raw_line in references_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line == "---":
                continue
            if line.startswith("- "):
                entry = line[2:].strip()
                if self._looks_like_reference_evidence(entry):
                    count += 1
                    continue
            numbered_match = re.match(r"^\d+\.\s+(.+)$", line)
            if numbered_match:
                entry = numbered_match.group(1).strip()
                if self._looks_like_reference_evidence(entry):
                    count += 1
                    continue
            if line.startswith("**") and ":" in line:
                _, value = line.split(":", 1)
                value = value.strip()
                if self._looks_like_reference_evidence(value):
                    count += 1
                    continue
            if "http://" in line or "https://" in line:
                count += 1
        return count

    def _normalize_reference_evidence_value(self, value: str) -> str:
        candidate = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 \2", (value or "").strip())
        return re.sub(r"^\[[^\]]+\]\s*", "", candidate).strip()

    def _looks_like_reference_evidence(self, value: str) -> bool:
        candidate = self._normalize_reference_evidence_value(value)
        if not self._is_substantive_field_value(candidate):
            return False

        lowered = candidate.lower()
        placeholder_tokens = (
            "tbd",
            "todo",
            "placeholder",
            "source name",
            "待补",
            "待确认",
            "来源名称",
            "示例来源",
        )
        if any(token in lowered for token in placeholder_tokens):
            return False

        if "[" in candidate or "]" in candidate:
            return False

        return True

    def _has_effective_outline(self, project_path: Path) -> bool:
        outline_text = self._read_plan_file(project_path, "outline.md")
        if not outline_text or self._is_template_content(outline_text, "outline.md"):
            return False
        section_count = len(re.findall(r"^(?:##+\s+|[0-9]+\.\s+)", outline_text, flags=re.MULTILINE))
        return section_count >= 2 and self._has_substantive_body(outline_text)

    def _has_effective_research_plan(self, project_path: Path) -> bool:
        research_plan_text = self._read_plan_file(project_path, "research-plan.md")
        if not research_plan_text or self._is_template_content(research_plan_text, "research-plan.md"):
            return False
        section_count = len(
            re.findall(r"^(?:##+\s+|[0-9]+\.\s+)", research_plan_text, flags=re.MULTILINE)
        )
        required_patterns = [
            r"research methods?",
            r"data sources?",
            r"execution steps?",
            r"研究方法",
            r"数据来源",
            r"执行步骤",
        ]
        structural_patterns = [
            r"research objectives?",
            r"research questions?",
            r"phase plan",
            r"key assumptions?",
            r"研究目标",
            r"研究问题",
            r"核心研究问题",
            r"阶段安排",
            r"关键假设",
        ]
        has_named_sections = any(
            re.search(pattern, research_plan_text, flags=re.IGNORECASE)
            for pattern in required_patterns
        )
        has_plan_structure = any(
            re.search(pattern, research_plan_text, flags=re.IGNORECASE)
            for pattern in structural_patterns
        )
        return (has_named_sections or (section_count >= 2 and has_plan_structure)) and self._has_substantive_body(
            research_plan_text
        )

    def _has_effective_data_log(self, project_path: Path) -> bool:
        data_log_text = self._read_plan_file(project_path, "data-log.md")
        if not data_log_text or self._is_template_content(data_log_text, "data-log.md"):
            return False
        seen_table_separator = False
        for raw_line in data_log_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("|"):
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if len(cells) < 4:
                    continue
                if re.fullmatch(r"[-:\s]+", "".join(cells)):
                    seen_table_separator = True
                    continue
                if not seen_table_separator:
                    continue
                if self._is_substantive_field_value(cells[3]) and any(
                    self._is_substantive_field_value(cell) for cell in cells[:3]
                ):
                    return True
                continue
            if line.startswith("- ") and self._is_substantive_field_value(line[2:].strip()):
                return True
        return False

    def _has_effective_analysis_notes(self, project_path: Path) -> bool:
        analysis_text = self._read_plan_file(project_path, "analysis-notes.md")
        if not analysis_text or self._is_template_content(analysis_text, "analysis-notes.md"):
            return False
        insight_heading_count = len(re.findall(r"^(?:##+)\s+", analysis_text, flags=re.MULTILINE))
        labeled_line_count = self._count_substantive_labeled_lines(analysis_text)
        return insight_heading_count >= 1 and labeled_line_count >= 3 and self._has_substantive_body(analysis_text)

    def _has_effective_review_checklist(self, project_path: Path) -> bool:
        review_text = self._read_plan_file(project_path, "review-checklist.md")
        if not review_text or self._is_template_content(review_text, "review-checklist.md"):
            return False
        total_items = re.findall(r"^\s*-\s+\[[ xX/]\]\s+.+\S$", review_text, flags=re.MULTILINE)
        checked_items = re.findall(r"^\s*-\s+\[[xX]\]\s+.+\S$", review_text, flags=re.MULTILINE)
        return len(total_items) >= 3 and len(checked_items) == len(total_items)

    def _has_effective_review_notes(self, project_path: Path) -> bool:
        review_text = self._read_plan_file(project_path, "review.md")
        if not review_text or self._is_template_content(review_text, "review.md"):
            return False
        substantive_review_item = re.search(
            r"^\s*(?:-\s+(?!\[[ xX/]\])[^\s].*|\d+\.[ \t]+[^\s].*)$",
            review_text,
            flags=re.MULTILINE,
        )
        substantive_labeled_line = self._count_substantive_labeled_lines(review_text) >= 2
        return bool(substantive_review_item or substantive_labeled_line) and self._has_substantive_body(review_text)

    def _count_substantive_labeled_lines(self, text: str) -> int:
        count = 0
        plain_text = text.replace("**", "")
        for raw_line in plain_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.search(r"^[^:：]+[:：]\s*(.+)$", line)
            if match and self._is_substantive_field_value(match.group(1)):
                count += 1
        return count

    def _has_effective_presentation_plan(self, project_path: Path) -> bool:
        presentation_text = self._read_plan_file(project_path, "presentation-plan.md")
        if not presentation_text or self._is_template_content(presentation_text, "presentation-plan.md"):
            return False
        required_patterns = [r"\bppt\b", r"\bq&a\b", r"\bnarrative\b", r"演示", r"讲稿"]
        return (
            any(re.search(pattern, presentation_text, flags=re.IGNORECASE) for pattern in required_patterns)
            and self._has_substantive_body(presentation_text)
        )

    def _has_effective_delivery_log(self, project_path: Path) -> bool:
        delivery_text = self._read_plan_file(project_path, "delivery-log.md")
        if not delivery_text or self._is_template_content(delivery_text, "delivery-log.md"):
            return False
        required_patterns = [r"\bdelivery date\b", r"\bshared\b", r"\bsent\b", r"\bfeedback\b", r"交付", r"反馈"]
        return (
            any(re.search(pattern, delivery_text, flags=re.IGNORECASE) for pattern in required_patterns)
            and self._has_substantive_body(delivery_text)
        )

    def _count_words(self, content: str) -> int:
        text = content
        for pattern, repl in self._MARKDOWN_STRIP_PATTERNS:
            text = pattern.sub(repl, text)
        stripped = re.sub(r"[\s\u3000]+", "", text)
        return len(stripped)

    def _is_template_stub(self, text: str) -> bool:
        return not self._has_substantive_body(text)

    def _has_effective_report_draft(self, project_path: Path, min_words: int = 0) -> bool:
        for candidate in self.REPORT_DRAFT_CANDIDATES:
            draft_text = self._read_project_file(project_path, candidate)
            if not draft_text or self._is_template_stub(draft_text):
                continue
            if min_words and self._count_words(draft_text) < min_words:
                continue
            return True
        return False

    def _delivery_mode_requires_presentation(self, project_path: Path) -> bool:
        return self._extract_delivery_mode(project_path) == "报告+演示"

    def get_skill_prompt(self) -> str:
        """鑾峰彇Skill瀹氫箟"""
        skill_file = self.skill_dir / "SKILL.md"
        sections = [skill_file.read_text(encoding="utf-8")]

        lifecycle_file = self.skill_dir / "modules" / "consulting-lifecycle.md"
        if lifecycle_file.exists():
            sections.append("## Consulting Lifecycle Guidance\n" + lifecycle_file.read_text(encoding="utf-8"))

        return "\n\n".join(sections)

    def get_template(self, project_type: str) -> str:
        """鑾峰彇鎶ュ憡妯℃澘"""
        template_file = self.skill_dir / "templates" / f"{project_type}.md"
        if template_file.exists():
            return template_file.read_text(encoding="utf-8")
        return ""

    def _normalize_create_payload(self, project_info_or_name, **kwargs) -> dict:
        if hasattr(project_info_or_name, "model_dump"):
            payload = project_info_or_name.model_dump()
        elif isinstance(project_info_or_name, dict):
            payload = dict(project_info_or_name)
        elif project_info_or_name is None:
            payload = dict(kwargs)
        else:
            payload = {"name": project_info_or_name, **kwargs}

        payload.setdefault("notes", "")
        payload.setdefault("workspace_dir", kwargs.get("workspace_dir"))
        payload.setdefault("initial_material_paths", kwargs.get("initial_material_paths") or [])
        required_fields = [
            "name",
            "project_type",
            "theme",
            "target_audience",
            "deadline",
            "expected_length",
        ]
        missing = [field for field in required_fields if not payload.get(field)]
        if missing:
            raise ValueError(f"缺少项目字段: {', '.join(missing)}")
        return payload

    def _load_registry(self) -> dict:
        if not self.registry_path.exists():
            return {"projects": []}
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def _save_registry(self, registry: dict):
        self.registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _touch_project(self, project_id: str):
        registry = self._load_registry()
        for project in registry["projects"]:
            if project["id"] == project_id:
                project["updated_at"] = datetime.now().isoformat(timespec="seconds")
                break
        self._save_registry(registry)

    def _materials_path(self, project_record: dict) -> Path:
        return Path(project_record["project_dir"]) / "materials.json"

    def _load_materials(self, project_record: dict) -> list[dict]:
        materials_path = self._materials_path(project_record)
        if not materials_path.exists():
            return []
        return json.loads(materials_path.read_text(encoding="utf-8"))

    def _save_materials(self, project_record: dict, materials: list[dict]):
        self._materials_path(project_record).write_text(
            json.dumps(materials, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _workspace_relative_path(self, source_path: Path, workspace_root: Path) -> Optional[str]:
        if self._is_within(source_path, workspace_root):
            return self._to_posix(source_path.relative_to(workspace_root))
        return None

    def _find_existing_workspace_material(self, materials: list[dict], stored_rel_path: str) -> Optional[dict]:
        return next(
            (
                item for item in materials
                if item["source_type"] == "workspace" and item["stored_rel_path"] == stored_rel_path
            ),
            None,
        )

    def _find_existing_imported_material(self, materials: list[dict], source_path: Path) -> Optional[dict]:
        source_str = str(source_path)
        return next(
            (
                item for item in materials
                if item["source_type"] == "imported" and item.get("original_path") == source_str
            ),
            None,
        )

    def _build_imported_destination(self, project_path: Path, file_name: str) -> Path:
        imported_root = project_path / "materials" / "imported"
        candidate_name = file_name
        stem = Path(file_name).stem
        suffix = Path(file_name).suffix
        counter = 1
        while (imported_root / candidate_name).exists():
            candidate_name = f"{stem}-{counter}{suffix}"
            counter += 1
        return Path("materials") / "imported" / candidate_name

    def _detect_media_kind(self, source_path: Path) -> str:
        return "image_like" if source_path.suffix.lower() in self.IMAGE_SUFFIXES else "text_like"

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def _to_posix(self, path_value) -> str:
        if isinstance(path_value, Path):
            return path_value.as_posix()
        return str(path_value).replace("\\", "/")

    def _resolve_project_path(self, project_path: Path, file_path: str) -> Path:
        full_path = (project_path / file_path).resolve()
        if not self._is_within(full_path, project_path.resolve()):
            raise ValueError("非法的文件路径")
        return full_path

    def _is_within(self, path: Path, base: Path) -> bool:
        try:
            path.resolve().relative_to(base.resolve())
            return True
        except ValueError:
            return False

    def _read_optional(self, project_ref: str, file_path: str) -> str:
        try:
            return self.read_file(project_ref, file_path)
        except ValueError:
            return ""

    def _extract_stage_code(self, progress_text: str) -> str:
        match = re.search(r"\*\*阶段\*\*:\s*([A-Z]\d)", progress_text)
        return match.group(1) if match else ""

    def _extract_stage_status(self, progress_text: str) -> str:
        match = re.search(r"\*\*状态\*\*:\s*([^\n]+)", progress_text)
        return match.group(1).strip() if match else ""

    def _extract_checked_items(self, markdown_text: str) -> list[str]:
        return [
            match.group(1).strip()
            for match in re.finditer(r"- \[x\]\s+(.+)", markdown_text, flags=re.IGNORECASE)
        ]

    def _extract_open_items(self, markdown_text: str) -> list[str]:
        return [
            match.group(1).strip()
            for match in re.finditer(r"- \[ \]\s+(.+)", markdown_text)
        ]

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _read_docx(self, material_path: Path) -> str:
        from docx import Document

        document = Document(material_path)
        lines = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
        return "\n".join(lines).strip()

    def _read_xlsx(self, material_path: Path) -> str:
        import openpyxl

        workbook = openpyxl.load_workbook(material_path, data_only=True, read_only=True)
        sections = []
        for sheet in workbook.worksheets:
            rows = []
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                values = ["" if value is None else str(value) for value in row]
                if any(values):
                    rows.append(" | ".join(values))
                if row_index >= 50:
                    break
            sections.append(f"## {sheet.title}\n" + "\n".join(rows))
        return "\n\n".join(section for section in sections if section.strip()).strip()

    def _read_pdf(self, material_path: Path) -> str:
        from pypdf import PdfReader

        reader = PdfReader(str(material_path))
        sections = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                sections.append(f"## 第{index}页\n{text}")
        if sections:
            return "\n\n".join(sections)
        raise ValueError("PDF 未提取到文本，当前版本暂不支持扫描版 PDF。")
