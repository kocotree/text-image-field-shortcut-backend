from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from services.state import MemoryStateStore


class MemoryStateStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1_000.0
        self.store = MemoryStateStore(clock=lambda: self.now)

    def test_value_and_counter_expire(self) -> None:
        self.assertTrue(self.store.set("value", "ready", 10))
        self.assertEqual(self.store.increment("counter", 10), 1)
        self.assertEqual(self.store.increment("counter", 10), 2)

        self.now += 10

        self.assertIsNone(self.store.get("value"))
        self.assertIsNone(self.store.get("counter"))

    def test_lock_can_be_reacquired_after_expiration(self) -> None:
        self.assertTrue(self.store.acquire_lock("probe", 5))
        self.assertFalse(self.store.acquire_lock("probe", 5))

        self.now += 5

        self.assertTrue(self.store.acquire_lock("probe", 5))

    def test_rolling_window_discards_expired_events(self) -> None:
        self.assertEqual(self.store.record_event("fallback", 10), 1)
        self.now += 5
        self.assertEqual(self.store.record_event("fallback", 10), 2)
        self.now += 5
        self.assertEqual(self.store.record_event("fallback", 10), 2)

    def test_increment_is_thread_safe(self) -> None:
        store = MemoryStateStore()

        def increment_many(_worker: int) -> None:
            for _ in range(100):
                store.increment("counter", 60)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(increment_many, range(8)))

        self.assertEqual(store.get("counter"), "800")

    def test_delete_clears_values_locks_and_events(self) -> None:
        self.store.set("value", "ready", 10)
        self.store.acquire_lock("lock", 10)
        self.store.record_event("events", 10)

        self.assertTrue(self.store.delete("value", "lock", "events"))

        self.assertIsNone(self.store.get("value"))
        self.assertTrue(self.store.acquire_lock("lock", 10))
        self.assertEqual(self.store.record_event("events", 10), 1)


if __name__ == "__main__":
    unittest.main()
