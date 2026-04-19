import json
from pathlib import Path
from backend.skill import SkillEngine


def test_load_stage_checkpoints_returns_empty_when_file_missing(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
    project_dir = tmp_path / 'proj-x'
    project_dir.mkdir()
    assert engine._load_stage_checkpoints(project_dir) == {}


def test_save_stage_checkpoint_is_idempotent_on_repeat_set(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
    project_dir = tmp_path / 'proj-x'
    project_dir.mkdir()
    first = engine._save_stage_checkpoint(project_dir, 'outline_confirmed_at')
    second = engine._save_stage_checkpoint(project_dir, 'outline_confirmed_at')
    assert first == second


def test_clear_stage_checkpoint_removes_key(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
    project_dir = tmp_path / 'proj-x'
    project_dir.mkdir()
    engine._save_stage_checkpoint(project_dir, 'review_started_at')
    engine._clear_stage_checkpoint(project_dir, 'review_started_at')
    assert 'review_started_at' not in engine._load_stage_checkpoints(project_dir)


def test_save_checkpoint_preserves_migration_marker(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
    project_dir = tmp_path / 'proj-x'
    project_dir.mkdir()
    import json as _json
    (project_dir / 'stage_checkpoints.json').write_text(
        _json.dumps({'__migrated_at': '2026-04-17T12:00:00'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    engine._save_stage_checkpoint(project_dir, 'outline_confirmed_at')
    raw = _json.loads((project_dir / 'stage_checkpoints.json').read_text(encoding='utf-8'))
    assert raw.get('__migrated_at') == '2026-04-17T12:00:00'
    assert 'outline_confirmed_at' in raw


def test_clear_checkpoint_preserves_migration_marker(tmp_path):
    engine = SkillEngine(projects_dir=tmp_path, skill_dir=Path('skill'))
    project_dir = tmp_path / 'proj-x'
    project_dir.mkdir()
    import json as _json
    (project_dir / 'stage_checkpoints.json').write_text(
        _json.dumps({'__migrated_at': '2026-04-17T12:00:00', 'outline_confirmed_at': '2026-04-17T12:01:00'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    engine._clear_stage_checkpoint(project_dir, 'outline_confirmed_at')
    raw = _json.loads((project_dir / 'stage_checkpoints.json').read_text(encoding='utf-8'))
    assert raw.get('__migrated_at') == '2026-04-17T12:00:00'
    assert 'outline_confirmed_at' not in raw


def load_tests(loader, tests, pattern):
    import tempfile
    import unittest

    def wrap(test_func):
        def case():
            with tempfile.TemporaryDirectory() as tmp_dir:
                test_func(Path(tmp_dir))

        case.__name__ = test_func.__name__
        return unittest.FunctionTestCase(case, description=test_func.__name__)

    suite = unittest.TestSuite()
    for test_func in (
        test_load_stage_checkpoints_returns_empty_when_file_missing,
        test_save_stage_checkpoint_is_idempotent_on_repeat_set,
        test_clear_stage_checkpoint_removes_key,
        test_save_checkpoint_preserves_migration_marker,
        test_clear_checkpoint_preserves_migration_marker,
    ):
        suite.addTest(wrap(test_func))
    return suite
