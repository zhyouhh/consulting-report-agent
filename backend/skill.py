from datetime import datetime
from pathlib import Path
import json
import mimetypes
import re
import shutil
import uuid
from typing import Iterable, Optional


class SkillEngine:
    """Skill流程引擎"""

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
    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
    TEXT_SUFFIXES = {".md", ".txt", ".csv"}

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
        """创建新项目。"""
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

        if payload["workspace_dir"]:
            return self._create_workspace_project(payload)
        return self._create_legacy_project(payload)

    def _create_workspace_project(self, payload: dict) -> dict:
        registry = self._load_registry()
        workspace_path = Path(payload["workspace_dir"]).expanduser().resolve()
        if not workspace_path.exists():
            workspace_path.mkdir(parents=True)
        if not workspace_path.is_dir():
            raise ValueError("工作目录无效")

        project_dir = workspace_path / ".consulting-report"
        if project_dir.exists():
            raise ValueError("该工作目录已经初始化过项目")

        for project in registry["projects"]:
            if Path(project["workspace_dir"]).resolve() == workspace_path:
                raise ValueError("该工作目录已被其他项目占用")

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

    def _create_legacy_project(self, payload: dict) -> dict:
        """兼容旧测试和旧调用。"""
        project_path = self.projects_dir / payload["name"]
        if project_path.exists():
            raise ValueError(f"项目 {payload['name']} 已存在")

        self._initialize_project_structure(project_path)
        self._populate_v2_plan_files(
            project_path=project_path,
            name=payload["name"],
            project_type=payload["project_type"],
            theme=payload["theme"],
            target_audience=payload["target_audience"],
            deadline=payload["deadline"],
            expected_length=payload["expected_length"],
            notes=payload["notes"],
        )
        return {
            "id": payload["name"],
            "name": payload["name"],
            "project_type": payload["project_type"],
            "theme": payload["theme"],
            "target_audience": payload["target_audience"],
            "deadline": payload["deadline"],
            "expected_length": payload["expected_length"],
            "notes": payload["notes"],
            "workspace_dir": str(project_path),
            "project_dir": str(project_path),
        }

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
        }

        overview_path = project_path / "plan" / "project-overview.md"
        if overview_path.exists():
            content = overview_path.read_text(encoding="utf-8")
            for source, target in replacements.items():
                content = content.replace(source, target)
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
            content = content.replace("[进行中/已完成/待开始]", "进行中")
            content = content.replace("[YYYY-MM-DD]", datetime.now().strftime("%Y-%m-%d"), 1)
            progress_path.write_text(content, encoding="utf-8")

        stage_gates_path = project_path / "plan" / "stage-gates.md"
        if stage_gates_path.exists():
            content = stage_gates_path.read_text(encoding="utf-8")
            content = content.replace("**阶段**: [S0/S1/S2/S3/S4/S5/S6/S7]", "**阶段**: S0")
            content = content.replace("**状态**: [进行中/已完成/待开始]", "**状态**: 进行中")
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

        legacy_path = self.projects_dir / project_ref
        return legacy_path if legacy_path.exists() else None

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

        legacy_path = self.get_project_path(project_ref)
        if legacy_path and legacy_path.exists():
            shutil.rmtree(legacy_path)
            return

        raise ValueError(f"项目 {project_ref} 不存在")

    def read_file(self, project_ref: str, file_path: str) -> str:
        """读取项目文件"""
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        full_path = self._resolve_project_path(project_path, file_path)
        if not full_path.exists():
            raise ValueError(f"文件 {file_path} 不存在")

        return full_path.read_text(encoding="utf-8")

    def write_file(self, project_ref: str, file_path: str, content: str):
        """写入项目文件"""
        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        full_path = self._resolve_project_path(project_path, file_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    def read_material_file(self, project_ref: str, material_id: str) -> str:
        project_record = self.get_project_record(project_ref)
        if not project_record:
            raise ValueError(f"项目 {project_ref} 不存在")

        material = self.get_material(project_ref, material_id)
        if material["media_kind"] == "image_like":
            raise ValueError("图片材料不支持文本提取，请在聊天时直接附带给模型。")

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
        project_record = self.get_project_record(project_ref)
        if not project_record and not self.get_project_path(project_ref):
            raise ValueError(f"项目 {project_ref} 不存在")

        project_path = self.get_project_path(project_ref)
        if not project_path:
            raise ValueError(f"项目 {project_ref} 不存在")

        stage_gates_text = self._ensure_stage_gates_state(project_path)
        stage_code = self._extract_stage_code(stage_gates_text)
        status = self._extract_stage_status(stage_gates_text)
        materials = self.list_materials(project_ref)

        return {
            "stage_code": stage_code,
            "status": status,
            "completed_items": self._extract_checked_items(stage_gates_text),
            "next_actions": self._extract_open_items(stage_gates_text)[:3],
            "workspace_dir": project_record["workspace_dir"] if project_record else "",
            "project_dir": project_record["project_dir"] if project_record else str(self.get_project_path(project_ref)),
            "materials": [
                {
                    "id": material["id"],
                    "display_name": material["display_name"],
                    "source_type": material["source_type"],
                    "media_kind": material["media_kind"],
                    "file_type": material["file_type"],
                }
                for material in materials
            ],
        }

    def build_project_context(self, project_ref: str) -> str:
        sections = []
        for title, relative_path in self.CORE_CONTEXT_FILES:
            content = self._read_optional(project_ref, relative_path)
            if content:
                sections.append(f"## {title}\n{content}")

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
        stage_gates_path = project_path / "plan" / "stage-gates.md"
        if not stage_gates_path.exists():
            template_path = self.skill_dir / "plan-template" / "stage-gates.md"
            if template_path.exists():
                shutil.copy(template_path, stage_gates_path)
            else:
                return ""

        stage_code, completed_keys = self._infer_stage_progress(project_path)
        if not stage_code:
            return stage_gates_path.read_text(encoding="utf-8")

        content = stage_gates_path.read_text(encoding="utf-8")
        content = re.sub(r"\*\*阶段\*\*:\s*[^\n]+", f"**阶段**: {stage_code}", content)
        content = re.sub(r"\*\*状态\*\*:\s*[^\n]+", "**状态**: 进行中", content)
        content = re.sub(
            r"\*\*更新日期\*\*:\s*[^\n]+",
            f"**更新日期**: {datetime.now().strftime('%Y-%m-%d')}",
            content,
        )
        for task in completed_keys:
            content = content.replace(f"- [ ] {task}", f"- [x] {task}")
            content = content.replace(f"- [/] {task}", f"- [x] {task}")

        stage_gates_path.write_text(content, encoding="utf-8")
        return content

    def _infer_stage_progress(self, project_path: Path) -> tuple[str, list[str]]:
        completed = ["project-overview.md 创建"]
        report_candidates = [
            project_path / "report_draft_v1.md",
            project_path / "content" / "report.md",
            project_path / "content" / "draft.md",
            project_path / "output" / "final-report.md",
        ]

        if any(path.exists() for path in report_candidates):
            completed.extend(["报告结构确定"])
            return "S4", completed

        if self._has_meaningful_outline(project_path):
            completed.extend(["分析框架确定"])
            return "S1", completed

        return "S0", completed

    def _has_meaningful_outline(self, project_path: Path) -> bool:
        outline_path = project_path / "plan" / "outline.md"
        if not outline_path.exists():
            return False

        outline_text = outline_path.read_text(encoding="utf-8").strip()
        if not outline_text:
            return False

        template_path = self.skill_dir / "plan-template" / "outline.md"
        if not template_path.exists():
            return True

        template_text = template_path.read_text(encoding="utf-8").strip()
        return self._normalize_text(outline_text) != self._normalize_text(template_text)

    def get_skill_prompt(self) -> str:
        """获取Skill定义"""
        skill_file = self.skill_dir / "SKILL.md"
        return skill_file.read_text(encoding="utf-8")

    def get_template(self, report_type: str) -> str:
        """获取报告模板"""
        template_file = self.skill_dir / "templates" / f"{report_type}.md"
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
