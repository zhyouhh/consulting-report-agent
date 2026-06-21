import unittest
from backend.tenant import tenant_project_key
from backend import chat as chat_mod
from backend import independent_review as ir_mod


class CompositeLockKeyTests(unittest.TestCase):
    def test_two_users_distinct_locks(self):
        a = chat_mod._get_project_request_lock(tenant_project_key("uA", "proj-x"))
        b = chat_mod._get_project_request_lock(tenant_project_key("uB", "proj-x"))
        self.assertIsNot(a, b)
        self.assertIs(chat_mod._get_project_request_lock(tenant_project_key("uA", "proj-x")), a)

    def test_review_lock_registry_distinct_per_key(self):
        # 审查锁 registry 的不透明键区分性：不同键→不同锁、同键→同锁。
        # 注意：T6 生产路径的审查侧仍用裸 project_id（整体迁移留 T11）；本测试只验 registry 语义，
        # 不代表生产已用复合键——T11 把审查端点 / chat 读 / 门禁检查一并改复合后才有复合键生产路径。
        a = ir_mod.get_independent_review_lock(tenant_project_key("uA", "proj-x"))
        b = ir_mod.get_independent_review_lock(tenant_project_key("uB", "proj-x"))
        self.assertIsNot(a, b)
        self.assertIs(ir_mod.get_independent_review_lock(tenant_project_key("uA", "proj-x")), a)

    def test_chat_handler_uid_must_match_engine(self):
        # 守卫：handler uid 与 engine uid 不一致 → fail-fast（守卫早于重 init，raise 路径不触发网络/重装配）。
        import os, tempfile
        from pathlib import Path
        from backend.chat import ChatHandler
        from backend.skill import SkillEngine
        from backend.config import Settings
        eng = SkillEngine(Path(os.path.realpath(tempfile.mkdtemp())), Path("."), uid="uA")
        with self.assertRaises(ValueError):
            ChatHandler(Settings(), eng, uid="uB")

    def test_skill_engine_carries_uid_default_local(self):
        from backend.skill import SkillEngine
        import tempfile, os
        eng = SkillEngine(__import__("pathlib").Path(os.path.realpath(tempfile.mkdtemp())), __import__("pathlib").Path("."))
        self.assertEqual(getattr(eng, "uid", None), "local")

    def test_record_stage_checkpoint_uses_composite_key(self):
        import inspect
        from backend.skill import SkillEngine
        src = inspect.getsource(SkillEngine.record_stage_checkpoint)
        self.assertIn("tenant_project_key(self.uid", src)


if __name__ == "__main__":
    unittest.main()
