import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from backend.search_state import SearchStateStore, normalize_search_query


class SearchStateTests(unittest.TestCase):
    def test_normalize_query_collapses_spacing_and_case(self):
        self.assertEqual(
            normalize_search_query("  OpenAI   GPT-5！！ "),
            "openai gpt-5",
        )
        self.assertEqual(
            normalize_search_query("  猪猪侠   2025！！ "),
            "猪猪侠 2025",
        )

    def test_l1_memory_cache_hits_before_disk_lookup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SearchStateStore(
                runtime_state_path=Path(tmpdir) / "state.json",
                cache_path=Path(tmpdir) / "cache.json",
            )
            store.put_cache(
                "猪猪侠 2025",
                project_id="demo",
                value={"provider": "serper"},
                memory_ttl_seconds=60,
                project_ttl_seconds=300,
            )

            with mock.patch.object(store, "_load_file_cache", side_effect=AssertionError("should not hit disk")):
                hit = store.get_cache("猪猪侠 2025", project_id="demo")

        self.assertEqual(hit["provider"], "serper")

    def test_l2_file_cache_backfills_l1_after_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_state_path = Path(tmpdir) / "state.json"
            cache_path = Path(tmpdir) / "cache.json"
            first = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            first.put_cache(
                "猪猪侠 2025",
                project_id="demo",
                value={"provider": "serper"},
                memory_ttl_seconds=60,
                project_ttl_seconds=300,
            )

            second = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            hit = second.get_cache("猪猪侠 2025", project_id="demo")

        self.assertEqual(hit["provider"], "serper")
        self.assertTrue(second._memory_cache)

    def test_l1_cache_miss_after_memory_ttl_but_l2_still_hits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_state_path = Path(tmpdir) / "state.json"
            cache_path = Path(tmpdir) / "cache.json"
            store = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            with mock.patch("backend.search_state.time.time", return_value=1000):
                store.put_cache(
                    "猪猪侠 2025",
                    project_id="demo",
                    value={"provider": "serper"},
                    memory_ttl_seconds=60,
                    project_ttl_seconds=300,
                )

            with mock.patch("backend.search_state.time.time", return_value=1105):
                hit = store.get_cache("猪猪侠 2025", project_id="demo")

        self.assertEqual(hit["provider"], "serper")
        self.assertTrue(store._memory_cache)

    def test_cache_is_scoped_by_project_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SearchStateStore(
                runtime_state_path=Path(tmpdir) / "state.json",
                cache_path=Path(tmpdir) / "cache.json",
            )
            store.put_cache(
                "猪猪侠",
                project_id="a",
                value={"provider": "serper"},
                memory_ttl_seconds=60,
                project_ttl_seconds=300,
            )

            miss = store.get_cache("猪猪侠", project_id="b")

        self.assertIsNone(miss)

    def test_provider_cooldown_persists_to_runtime_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_state_path = Path(tmpdir) / "state.json"
            cache_path = Path(tmpdir) / "cache.json"
            with mock.patch("backend.search_state.time.time", return_value=1000):
                store = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
                store.mark_provider_failure("serper", error_type="rate_limited", cooldown_seconds=180)

            reloaded = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            state = reloaded.get_provider_state("serper")

        self.assertEqual(state.last_error_type, "rate_limited")
        self.assertEqual(state.cooldown_until, 1180)
        self.assertEqual(state.consecutive_failures, 1)

    def test_mark_provider_success_clears_consecutive_failures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_state_path = Path(tmpdir) / "state.json"
            cache_path = Path(tmpdir) / "cache.json"
            with mock.patch("backend.search_state.time.time", return_value=1000):
                store = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
                store.mark_provider_failure("serper", error_type="timeout", cooldown_seconds=180)

            with mock.patch("backend.search_state.time.time", return_value=1200):
                store.mark_provider_success("serper")

            state = store.get_provider_state("serper")

        self.assertEqual(state.consecutive_failures, 0)
        self.assertIsNone(state.cooldown_until)
        self.assertIsNone(state.last_error_type)
        self.assertEqual(state.last_success_at, 1200)

    def test_l2_file_cache_misses_after_project_ttl_expiry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_state_path = Path(tmpdir) / "state.json"
            cache_path = Path(tmpdir) / "cache.json"
            store = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            with mock.patch("backend.search_state.time.time", return_value=1000):
                store.put_cache(
                    "猪猪侠 2025",
                    project_id="demo",
                    value={"provider": "serper"},
                    memory_ttl_seconds=60,
                    project_ttl_seconds=300,
                )

            with mock.patch("backend.search_state.time.time", return_value=1401):
                miss = store.get_cache("猪猪侠 2025", project_id="demo")

        self.assertIsNone(miss)

    def test_l2_backfill_does_not_extend_cache_beyond_project_ttl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_state_path = Path(tmpdir) / "state.json"
            cache_path = Path(tmpdir) / "cache.json"
            store = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            with mock.patch("backend.search_state.time.time", return_value=1000):
                store.put_cache(
                    "猪猪侠 2025",
                    project_id="demo",
                    value={"provider": "serper"},
                    memory_ttl_seconds=60,
                    project_ttl_seconds=300,
                )

            with mock.patch("backend.search_state.time.time", return_value=1299):
                hit = store.get_cache("猪猪侠 2025", project_id="demo")

            with mock.patch("backend.search_state.time.time", return_value=1301):
                miss = store.get_cache("猪猪侠 2025", project_id="demo")

        self.assertEqual(hit["provider"], "serper")
        self.assertIsNone(miss)

    def test_provider_failures_merge_across_store_instances(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_state_path = Path(tmpdir) / "state.json"
            cache_path = Path(tmpdir) / "cache.json"
            first = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            second = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)

            with mock.patch("backend.search_state.time.time", return_value=1000):
                first.mark_provider_failure("serper", error_type="rate_limited", cooldown_seconds=180)

            with mock.patch("backend.search_state.time.time", return_value=1010):
                second.mark_provider_failure("serper", error_type="timeout", cooldown_seconds=180)

            reloaded = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            state = reloaded.get_provider_state("serper")

        self.assertEqual(state.consecutive_failures, 2)
        self.assertEqual(state.last_error_type, "timeout")

    def test_file_cache_entries_merge_across_store_instances(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_state_path = Path(tmpdir) / "state.json"
            cache_path = Path(tmpdir) / "cache.json"
            first = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            second = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)

            with mock.patch("backend.search_state.time.time", return_value=1000):
                first.put_cache(
                    "猪猪侠 2025",
                    project_id="demo",
                    value={"provider": "serper"},
                    memory_ttl_seconds=60,
                    project_ttl_seconds=300,
                )

            with mock.patch("backend.search_state.time.time", return_value=1010):
                second.put_cache(
                    "OpenAI news",
                    project_id="demo",
                    value={"provider": "brave"},
                    memory_ttl_seconds=60,
                    project_ttl_seconds=300,
                )

            reloaded = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            with mock.patch("backend.search_state.time.time", return_value=1020):
                first_hit = reloaded.get_cache("猪猪侠 2025", project_id="demo")
                second_hit = reloaded.get_cache("OpenAI news", project_id="demo")

        self.assertEqual(first_hit["provider"], "serper")
        self.assertEqual(second_hit["provider"], "brave")

    def test_try_acquire_search_slot_is_atomic_across_threads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SearchStateStore(
                runtime_state_path=Path(tmpdir) / "state.json",
                cache_path=Path(tmpdir) / "cache.json",
            )
            barrier = threading.Barrier(2)
            results = []

            def worker(project_id: str):
                barrier.wait()
                results.append(
                    store.try_acquire_search_slot(
                        project_id=project_id,
                        project_window_seconds=300,
                        project_limit=10,
                        global_window_seconds=60,
                        global_limit=1,
                    )
                )

            first_thread = threading.Thread(target=worker, args=("project-a",))
            second_thread = threading.Thread(target=worker, args=("project-b",))
            first_thread.start()
            second_thread.start()
            first_thread.join(timeout=2)
            second_thread.join(timeout=2)

        self.assertEqual(results.count(None), 1)
        self.assertEqual(results.count("global_minute"), 1)

    def test_concurrent_provider_failure_updates_are_serialized(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_state_path = Path(tmpdir) / "state.json"
            cache_path = Path(tmpdir) / "cache.json"
            first = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            second = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            first_ready = threading.Event()
            second_started = threading.Event()
            allow_first_write = threading.Event()
            original_write = first._write_json_object

            def blocked_first_write(path, payload):
                if path == runtime_state_path and not first_ready.is_set():
                    first_ready.set()
                    self.assertTrue(allow_first_write.wait(timeout=2))
                return original_write(path, payload)

            def first_target():
                with mock.patch("backend.search_state.time.time", return_value=1000):
                    first.mark_provider_failure("serper", error_type="rate_limited", cooldown_seconds=180)

            def second_target():
                second_started.set()
                with mock.patch("backend.search_state.time.time", return_value=1010):
                    second.mark_provider_failure("serper", error_type="timeout", cooldown_seconds=180)

            with mock.patch.object(first, "_write_json_object", side_effect=blocked_first_write):
                first_thread = threading.Thread(target=first_target)
                second_thread = threading.Thread(target=second_target)
                first_thread.start()
                self.assertTrue(first_ready.wait(timeout=2))
                second_thread.start()
                self.assertTrue(second_started.wait(timeout=2))
                allow_first_write.set()
                first_thread.join(timeout=2)
                second_thread.join(timeout=2)

            reloaded = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            state = reloaded.get_provider_state("serper")

        self.assertEqual(state.consecutive_failures, 2)
        self.assertEqual(state.last_error_type, "timeout")

    def test_concurrent_file_cache_updates_are_serialized(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_state_path = Path(tmpdir) / "state.json"
            cache_path = Path(tmpdir) / "cache.json"
            first = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            second = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            first_ready = threading.Event()
            second_started = threading.Event()
            allow_first_write = threading.Event()
            original_write = first._write_json_object

            def blocked_first_write(path, payload):
                if path == cache_path and not first_ready.is_set():
                    first_ready.set()
                    self.assertTrue(allow_first_write.wait(timeout=2))
                return original_write(path, payload)

            def first_target():
                with mock.patch("backend.search_state.time.time", return_value=1000):
                    first.put_cache(
                        "猪猪侠 2025",
                        project_id="demo",
                        value={"provider": "serper"},
                        memory_ttl_seconds=60,
                        project_ttl_seconds=300,
                    )

            def second_target():
                second_started.set()
                with mock.patch("backend.search_state.time.time", return_value=1010):
                    second.put_cache(
                        "OpenAI news",
                        project_id="demo",
                        value={"provider": "brave"},
                        memory_ttl_seconds=60,
                        project_ttl_seconds=300,
                    )

            with mock.patch.object(first, "_write_json_object", side_effect=blocked_first_write):
                first_thread = threading.Thread(target=first_target)
                second_thread = threading.Thread(target=second_target)
                first_thread.start()
                self.assertTrue(first_ready.wait(timeout=2))
                second_thread.start()
                self.assertTrue(second_started.wait(timeout=2))
                allow_first_write.set()
                first_thread.join(timeout=2)
                second_thread.join(timeout=2)

            reloaded = SearchStateStore(runtime_state_path=runtime_state_path, cache_path=cache_path)
            with mock.patch("backend.search_state.time.time", return_value=1020):
                first_hit = reloaded.get_cache("猪猪侠 2025", project_id="demo")
                second_hit = reloaded.get_cache("OpenAI news", project_id="demo")

        self.assertEqual(first_hit["provider"], "serper")
        self.assertEqual(second_hit["provider"], "brave")


if __name__ == "__main__":
    unittest.main()
