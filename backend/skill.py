from pathlib import Path
from datetime import datetime
import shutil
from typing import Optional


class SkillEngine:
    """Skill流程引擎"""

    def __init__(self, projects_dir: Path, skill_dir: Path):
        self.projects_dir = projects_dir
        self.skill_dir = skill_dir
        self.projects_dir.mkdir(exist_ok=True)

    def create_project(self, name: str, report_type: str, theme: str, target_audience: str) -> Path:
        """创建新项目"""
        project_path = self.projects_dir / name
        if project_path.exists():
            raise ValueError(f"项目 {name} 已存在")

        # 创建目录结构
        (project_path / "plan").mkdir(parents=True)
        (project_path / "content").mkdir(parents=True)
        (project_path / "output").mkdir(parents=True)

        # 复制模板文件
        template_dir = self.skill_dir / "plan-template"
        for template_file in template_dir.glob("*.md"):
            shutil.copy(template_file, project_path / "plan" / template_file.name)

        # 填写项目信息
        self._write_project_info(project_path, name, report_type, theme, target_audience)

        return project_path

    def _write_project_info(self, project_path: Path, name: str, report_type: str,
                           theme: str, target_audience: str):
        """填写项目基本信息"""
        info_file = project_path / "plan" / "project-info.md"
        content = info_file.read_text(encoding="utf-8")

        # 替换占位符
        content = content.replace("<!-- 专题研究报告 | 体系规划方案 | 实施工作方案 | 管理制度 -->", report_type)
        content = content.replace("## 报告主题\n\n", f"## 报告主题\n{theme}\n\n")
        content = content.replace("<!-- 高层决策者 | 中层管理者 | 执行团队 | 外部客户 -->", target_audience)
        content = content.replace("## 创建时间\n<!-- 自动填充 -->",
                                f"## 创建时间\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        content = content.replace("## 当前状态\n<!-- 大纲设计中 | 内容撰写中 | 质量审查中 | 已完成 -->",
                                "## 当前状态\n大纲设计中")

        info_file.write_text(content, encoding="utf-8")

    def get_project_path(self, name: str) -> Optional[Path]:
        """获取项目路径"""
        project_path = self.projects_dir / name
        return project_path if project_path.exists() else None

    def list_projects(self) -> list:
        """列出所有项目"""
        projects = []
        for project_dir in self.projects_dir.iterdir():
            if project_dir.is_dir():
                info_file = project_dir / "plan" / "project-info.md"
                if info_file.exists():
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

        # 安全检查：防止路径遍历攻击
        full_path = (project_path / file_path).resolve()
        if not full_path.is_relative_to(project_path.resolve()):
            raise ValueError("非法的文件路径")

        if not full_path.exists():
            raise ValueError(f"文件 {file_path} 不存在")

        return full_path.read_text(encoding="utf-8")

    def write_file(self, project_name: str, file_path: str, content: str):
        """写入项目文件"""
        project_path = self.get_project_path(project_name)
        if not project_path:
            raise ValueError(f"项目 {project_name} 不存在")

        # 安全检查：防止路径遍历攻击
        full_path = (project_path / file_path).resolve()
        if not full_path.is_relative_to(project_path.resolve()):
            raise ValueError("非法的文件路径")

        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

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
