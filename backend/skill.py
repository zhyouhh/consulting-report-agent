from datetime import datetime
from pathlib import Path
import re
import shutil
from typing import Optional


class SkillEngine:
    """Skill流程引擎"""

    CORE_CONTEXT_FILES = [
        ("当前项目概览", "plan/project-overview.md"),
        ("当前项目进度", "plan/progress.md"),
        ("阶段门禁", "plan/stage-gates.md"),
        ("项目备注", "plan/notes.md"),
    ]

    def __init__(self, projects_dir: Path, skill_dir: Path):
        self.projects_dir = projects_dir
        self.skill_dir = skill_dir
        self.projects_dir.mkdir(exist_ok=True)

    def create_project(
        self,
        name: str,
        project_type: str,
        theme: str,
        target_audience: str,
        deadline: str,
        expected_length: str,
        notes: str = "",
    ) -> Path:
        """创建新项目"""
        project_path = self.projects_dir / name
        if project_path.exists():
            raise ValueError(f"项目 {name} 已存在")

        plan_path = project_path / "plan"
        content_path = project_path / "content"
        output_path = project_path / "output"
        plan_path.mkdir(parents=True)
        content_path.mkdir(parents=True)
        output_path.mkdir(parents=True)

        template_dir = self.skill_dir / "plan-template"
        for template_file in template_dir.glob("*.md"):
            shutil.copy(template_file, plan_path / template_file.name)

        self._populate_v2_plan_files(
            project_path=project_path,
            name=name,
            project_type=project_type,
            theme=theme,
            target_audience=target_audience,
            deadline=deadline,
            expected_length=expected_length,
            notes=notes,
        )
        return project_path

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

    def get_project_path(self, name: str) -> Optional[Path]:
        """获取项目路径"""
        project_path = self.projects_dir / name
        return project_path if project_path.exists() else None

    def list_projects(self) -> list:
        """列出所有项目"""
        projects = []
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            overview_file = project_dir / "plan" / "project-overview.md"
            legacy_file = project_dir / "plan" / "project-info.md"
            if overview_file.exists() or legacy_file.exists():
                projects.append({
                    "name": project_dir.name,
                    "path": str(project_dir)
                })
        return projects

    def read_file(self, project_name: str, file_path: str) -> str:
        """读取项目文件"""
        project_path = self.get_project_path(project_name)
        if not project_path:
            raise ValueError(f"项目 {project_name} 不存在")

        full_path = self._resolve_project_path(project_path, file_path)
        if not full_path.exists():
            raise ValueError(f"文件 {file_path} 不存在")

        return full_path.read_text(encoding="utf-8")

    def write_file(self, project_name: str, file_path: str, content: str):
        """写入项目文件"""
        project_path = self.get_project_path(project_name)
        if not project_path:
            raise ValueError(f"项目 {project_name} 不存在")

        full_path = self._resolve_project_path(project_path, file_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    def get_workspace_summary(self, project_name: str) -> dict:
        if not self.get_project_path(project_name):
            raise ValueError(f"项目 {project_name} 不存在")

        stage_gates_text = self._read_optional(project_name, "plan/stage-gates.md")

        return {
            "stage_code": self._extract_stage_code(stage_gates_text),
            "status": self._extract_stage_status(stage_gates_text),
            "completed_items": self._extract_checked_items(stage_gates_text),
            "next_actions": self._extract_open_items(stage_gates_text)[:3],
        }

    def build_project_context(self, project_name: str) -> str:
        sections = []
        for title, relative_path in self.CORE_CONTEXT_FILES:
            content = self._read_optional(project_name, relative_path)
            if content:
                sections.append(f"## {title}\n{content}")
        return "\n\n".join(sections)

    def get_script_path(self, script_name: str) -> str:
        script_path = self.skill_dir / "scripts" / script_name
        if not script_path.exists():
            raise ValueError(f"脚本 {script_name} 不存在")
        return str(script_path)

    def ensure_output_dir(self, project_name: str) -> str:
        project_path = self.get_project_path(project_name)
        if not project_path:
            raise ValueError(f"项目 {project_name} 不存在")

        output_dir = project_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        return str(output_dir)

    def get_primary_report_path(self, project_name: str) -> str:
        project_path = self.get_project_path(project_name)
        if not project_path:
            raise ValueError(f"项目 {project_name} 不存在")

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

    def _resolve_project_path(self, project_path: Path, file_path: str) -> Path:
        full_path = (project_path / file_path).resolve()
        if not full_path.is_relative_to(project_path.resolve()):
            raise ValueError("非法的文件路径")
        return full_path

    def _read_optional(self, project_name: str, file_path: str) -> str:
        try:
            return self.read_file(project_name, file_path)
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
