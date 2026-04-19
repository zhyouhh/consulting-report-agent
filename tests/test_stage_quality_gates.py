import tempfile
from pathlib import Path

from backend.skill import SkillEngine


def _make_project_with_overview(tmp_path, length_line):
    project_dir = tmp_path / 'proj'
    (project_dir / 'plan').mkdir(parents=True)
    (project_dir / 'plan' / 'project-overview.md').write_text(
        f'# 项目概览\n\n**预期篇幅**: {length_line}\n', encoding='utf-8')
    return project_dir


def _make_project(tmp_path):
    project_dir = tmp_path / 'proj'
    (project_dir / 'plan').mkdir(parents=True, exist_ok=True)
    return project_dir


def _make_project_with_file(tmp_path, rel_path, content):
    project_dir = tmp_path / 'proj'
    target = project_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    return project_dir


def test_resolve_length_targets_parses_plain_integer():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
        project_dir = _make_project_with_overview(tmp_path, '6000字')
        result = engine._resolve_length_targets(project_dir)
        assert result['expected_length'] == 6000
        assert result['data_log_min'] == 8
        assert result['analysis_refs_min'] == 5
        assert result['report_word_floor'] == 4200
        assert result['fallback_used'] is False


def test_resolve_length_targets_parses_range_takes_max():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
        project_dir = _make_project_with_overview(tmp_path, '5000-8000字')
        result = engine._resolve_length_targets(project_dir)
        assert result['expected_length'] == 8000


def test_resolve_length_targets_caps_at_upper_bound():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
        project_dir = _make_project_with_overview(tmp_path, '50000字')
        result = engine._resolve_length_targets(project_dir)
        assert result['data_log_min'] == 12
        assert result['analysis_refs_min'] == 8


def test_resolve_length_targets_defaults_when_unparseable():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
        project_dir = _make_project_with_overview(tmp_path, '待定')
        result = engine._resolve_length_targets(project_dir)
        assert result['expected_length'] == 3000
        assert result['fallback_used'] is True


def test_resolve_length_targets_parses_heading_form():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
        project_dir = _make_project_with_file(
            tmp_path,
            'plan/project-overview.md',
            '# 项目概览\n\n## 预期篇幅\n5000字\n',
        )
        result = engine._resolve_length_targets(project_dir)
        assert result['expected_length'] == 5000
        assert result['fallback_used'] is False


def test_has_enough_data_log_sources_counts_only_entries_with_evidence():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
        project_dir = _make_project(tmp_path)
        (project_dir / 'plan' / 'data-log.md').write_text(
            '### [DL-001]\n来源: https://example.com/a\n\n'
            '### [DL-002]\n素材: material:abc-123\n\n'
            '### [DL-003]\n仅记录，无证据\n',
            encoding='utf-8',
        )
        assert engine._has_enough_data_log_sources(project_dir, min_count=2) is True
        assert engine._has_enough_data_log_sources(project_dir, min_count=3) is False


def test_has_enough_analysis_refs_deduplicates_and_requires_dl_match():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
        project_dir = _make_project(tmp_path)
        (project_dir / 'plan' / 'data-log.md').write_text(
            '### [DL-001]\n来源: https://example.com/a\n\n'
            '### [DL-002]\n来源: https://example.com/b\n\n'
            '### [DL-003]\n来源: https://example.com/c\n',
            encoding='utf-8',
        )
        (project_dir / 'plan' / 'analysis-notes.md').write_text(
            '使用 [DL-001] 支撑结论。\n再次引用 [DL-001]。\n补充 [DL-002]。\n无效引用 [DL-999]。\n',
            encoding='utf-8',
        )
        assert engine._has_enough_analysis_refs(project_dir, min_refs=2) is True
        assert engine._has_enough_analysis_refs(project_dir, min_refs=3) is False


def load_tests(loader, tests, pattern):
    import unittest
    suite = unittest.TestSuite()
    for name, obj in list(globals().items()):
        if name.startswith('test_') and callable(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite
