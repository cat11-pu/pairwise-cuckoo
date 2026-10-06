"""Unit tests for cuckoo.core."""

import random
import unittest

from cuckoo.core import (
    EMPTY,
    GROWTH_FACTOR,
    MAX_KICKS,
    MIN_CAPACITY,
    CuckooTable,
    TableFullError,
    stable_hash,
)


class CuckooTableTest(unittest.TestCase):
    """Behaviour and structural invariants of the two-array cuckoo table."""

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def payload(key):
        return "v:%s" % (key,)

    def build(self, keys, **kwargs):
        table = CuckooTable(**kwargs)
        for key in keys:
            table.put(key, self.payload(key))
        return table

    def entries(self, table):
        """Every (key, value) pair sitting in the two arrays, in slot order."""
        found = []
        for slots in table.tables:
            for entry in slots:
                if entry is not None:
                    found.append(entry)
        return found

    def occupied(self, table):
        """How many of the slots hold something."""
        return len(self.entries(table))

    def crowded_keys(self, table, count):
        """Pick `count` distinct keys that share both of their home slots."""
        buckets = {}
        key = 0
        while key < 20000:
            bucket = buckets.setdefault(table.home(key), [])
            bucket.append(key)
            if len(bucket) == count:
                return bucket
            key += 1
        raise AssertionError("no group of %d keys shares both home slots" % count)

    def check_slots(self, table):
        """Every seated key must sit in one of the two slots home() reports."""
        for which, slots in enumerate(table.tables):
            for index, entry in enumerate(slots):
                if entry is None:
                    continue
                key, _ = entry
                self.assertEqual(table.home(key)[which], index,
                                 "a key must sit in the slot its home function reports")

    def check_table(self, table, expected):
        """Every expected key is reachable, seated once, inside the load bound."""
        stored = self.entries(table)
        for slots in table.tables:
            self.assertEqual(len(slots), table.capacity, "each array must cover the whole capacity")
        seated = {}
        for key, value in stored:
            self.assertNotIn(key, seated, "a stored key must not occupy two slots")
            seated[key] = value
        self.assertEqual(set(seated), set(expected), "the slots must hold every stored key")
        self.assertEqual(seated, dict(expected), "a stored key must hold the last value written")
        self.assertEqual(len(table), len(expected), "len must equal the number of stored keys")
        self.assertLessEqual(len(stored) * 2, table.capacity, "the arrays must stay at most half full")
        for key, value in expected.items():
            self.assertEqual(table.get(key), value)
            self.assertIn(key, table)
        self.assertEqual(sorted(table.keys(), key=repr), sorted(expected, key=repr))
        self.assertEqual(sorted(table.values(), key=repr), sorted(expected.values(), key=repr))
        self.check_slots(table)

    # ------------------------------------------------------------------
    # cases
    # ------------------------------------------------------------------
    def test_01_empty_table_and_rejected_keys(self):
        table = CuckooTable()
        self.assertEqual(table.capacity, MIN_CAPACITY)
        self.assertEqual(table.max_kicks, MAX_KICKS)
        self.assertEqual(len(table), 0)
        self.assertEqual(table.kicks, 0)
        self.assertEqual(table.rebuilds, 0)
        self.assertEqual(table.tables, [[EMPTY] * MIN_CAPACITY, [EMPTY] * MIN_CAPACITY])
        self.assertIsNone(table.get("missing"))
        self.assertEqual(table.get("missing", "fallback"), "fallback")
        self.assertNotIn("missing", table)
        self.assertFalse(table.delete("missing"))
        self.assertEqual(table.keys(), [])
        self.assertEqual(table.values(), [])
        self.assertEqual(list(table.items()), [])
        self.assertEqual(list(table), [])
        for bad in (2.5, None, True, (1, 2), b"k"):
            with self.assertRaises(TypeError):
                table.put(bad, 1)
            with self.assertRaises(TypeError):
                table.get(bad)
            with self.assertRaises(TypeError):
                table.delete(bad)
            with self.assertRaises(TypeError):
                bad in table
            with self.assertRaises(TypeError):
                table.home(bad)
        with self.assertRaises(ValueError):
            CuckooTable(capacity=1)
        with self.assertRaises(TypeError):
            CuckooTable(capacity="8")
        with self.assertRaises(ValueError):
            CuckooTable(max_kicks=0)
        with self.assertRaises(TypeError):
            CuckooTable(hashes=(len,))
        small = CuckooTable(capacity=4)
        small.put(3, "three")
        self.assertEqual(small.capacity, 4)
        self.assertEqual(len(small), 1)
        self.assertEqual(small.get(3), "three")
        self.assertEqual(small.get(3, "fallback"), "three")
        self.assertIsNone(small.get(4))
        self.assertIn(3, small)
        first, second = small.home(3)
        self.assertEqual(first, stable_hash(3) % 4, "the first home slot comes from stable_hash")
        self.assertLess(second, small.capacity)
        self.assertEqual(len(small.tables[0]) + len(small.tables[1]), 2 * small.capacity)
        self.assertEqual(self.occupied(small), 1)

    def test_02_put_and_get_mixed_keys(self):
        keys = list(range(0, 60)) + ["key-%d" % index for index in range(20)]
        table = CuckooTable()
        for key in keys:
            table.put(key, self.payload(key))
        self.assertEqual(len(table), len(keys))
        for key in keys:
            self.assertEqual(table.get(key), self.payload(key))
            self.assertIn(key, table)
        self.assertIsNone(table.get("absent"))
        self.assertNotIn("absent", table)
        self.assertNotIn(999, table)
        table.put("none-valued", None)
        self.assertIn("none-valued", table)
        self.assertIsNone(table.get("none-valued"))
        self.assertEqual(len(table), len(keys) + 1)
        expected = {key: self.payload(key) for key in keys}
        expected["none-valued"] = None
        self.check_table(table, expected)

    def test_03_repeated_put_overwrites_the_value(self):
        keys = [5, 12, 5, 27, 5, 12, 31, 27, 5, 40]
        table = CuckooTable()
        for round_index in range(4):
            for key in keys:
                table.put(key, "round%d:%d" % (round_index, key))
        distinct = sorted(set(keys))
        self.assertEqual(len(table), len(distinct))
        for key in distinct:
            self.assertEqual(table.get(key), "round3:%d" % key)
        self.check_table(table, {key: "round3:%d" % key for key in distinct})
        self.assertEqual(self.occupied(table), len(distinct))

    def test_04_both_arrays_are_used_and_stay_reachable(self):
        table = CuckooTable(capacity=8, max_kicks=8)
        group = self.crowded_keys(table, 2)
        for key in group:
            table.put(key, self.payload(key))
        self.assertEqual(len(table), len(group))
        self.assertTrue(any(entry is not None for entry in table.tables[1]),
                        "a key whose first slot is taken must be seated in the second array")
        self.check_table(table, {key: self.payload(key) for key in group})
        for key in group:
            table.put(key, "again:%d" % key)
        self.assertEqual(len(table), len(group))
        self.assertEqual(table.get(group[0]), "again:%d" % group[0])
        self.assertEqual(table.get(group[1]), "again:%d" % group[1])
        self.assertTrue(table.delete(group[0]), "delete of a stored key must report success")
        self.assertNotIn(group[0], table)
        self.assertIsNone(table.get(group[0]))
        self.assertFalse(table.delete(group[0]))
        self.assertIn(group[1], table)
        self.assertEqual(table.get(group[1]), "again:%d" % group[1])
        self.assertEqual(len(table), 1)
        table.put(group[0], self.payload(group[0]))
        self.check_table(table, {group[0]: self.payload(group[0]),
                                 group[1]: "again:%d" % group[1]})

    def test_05_delete_clears_the_slot_it_owns(self):
        table = self.build(list(range(40)))
        victims = [key for key in range(40) if key % 3 == 0]
        for key in victims:
            self.assertTrue(table.delete(key), "delete of a stored key must report success")
        self.assertEqual(len(table), 40 - len(victims))
        self.assertEqual(self.occupied(table), len(table), "a deleted key must leave its slot")
        for key in victims:
            self.assertNotIn(key, table)
            self.assertIsNone(table.get(key))
            self.assertFalse(table.delete(key))
        survivors = {key: self.payload(key) for key in range(40) if key % 3}
        self.check_table(table, survivors)
        for key in victims:
            table.put(key, "again:%d" % key)
        expected = {key: "again:%d" % key if key % 3 == 0 else self.payload(key)
                    for key in range(40)}
        self.check_table(table, expected)
        self.assertEqual(self.occupied(table), len(expected))

    def test_06_crowded_keys_kick_and_stay_reachable(self):
        table = CuckooTable(capacity=8, max_kicks=4)
        group = self.crowded_keys(table, 3)
        for key in group:
            table.put(key, self.payload(key))
        self.assertEqual(len(table), len(group))
        self.assertGreaterEqual(table.kicks + table.rebuilds, 1,
                                "keys that share both home slots must be kicked or rebuilt")
        self.check_table(table, {key: self.payload(key) for key in group})
        for key in group:
            self.assertEqual(table.get(key), self.payload(key))
            self.assertIn(key, table)
        before = table.capacity
        extra = self.crowded_keys(CuckooTable(capacity=before), 1)[0]
        table.put(extra, self.payload(extra))
        self.assertEqual(table.get(extra), self.payload(extra))
        self.assertGreaterEqual(table.capacity, before)
        self.check_table(table, {key: self.payload(key) for key in group + [extra]})

    def test_07_growth_keeps_the_arrays_half_empty(self):
        table = CuckooTable()
        keys = list(range(200))
        for key in keys:
            table.put(key, self.payload(key))
            self.assertLessEqual(len(table) * 2, table.capacity,
                                 "the arrays must grow before they fill up")
        self.assertEqual(len(table), len(keys))
        self.assertGreater(table.capacity, MIN_CAPACITY)
        self.assertGreaterEqual(table.rebuilds, 1)
        self.assertEqual(table.capacity % GROWTH_FACTOR, 0)
        for key in keys:
            self.assertEqual(table.get(key), self.payload(key))
        self.check_table(table, {key: self.payload(key) for key in keys})

    def test_08_a_chain_that_never_settles_grows_or_reports(self):
        table = CuckooTable(capacity=8, max_kicks=2)
        group = self.crowded_keys(table, 3)
        for key in group:
            table.put(key, self.payload(key))
        self.assertEqual(len(table), len(group))
        self.assertGreaterEqual(table.rebuilds, 1,
                                "a chain that long must be answered with bigger arrays")
        self.check_table(table, {key: self.payload(key) for key in group})
        stuck = CuckooTable(capacity=MIN_CAPACITY, hashes=(lambda key: 0, lambda key: 0))
        stuck.put("one", 1)
        stuck.put("two", 2)
        with self.assertRaises(TableFullError):
            stuck.put("three", 3)
        for slots in stuck.tables:
            self.assertEqual(len(slots), stuck.capacity)

    def test_09_mixed_workload_stays_consistent(self):
        keys = list(range(1, 121))
        random.Random(20241006).shuffle(keys)
        table = self.build(keys)
        self.check_table(table, {key: self.payload(key) for key in keys})
        order = list(range(1, 121))
        random.Random(99).shuffle(order)
        for key in order[:60]:
            self.assertTrue(table.delete(key), "delete of a stored key must report success")
        survivors = {key: self.payload(key) for key in sorted(set(range(1, 121)) - set(order[:60]))}
        self.check_table(table, survivors)
        for key in order[60:]:
            self.assertTrue(table.delete(key), "delete of a stored key must report success")
        self.assertEqual(len(table), 0)
        self.assertEqual(table.keys(), [])
        self.assertEqual(self.entries(table), [])
        self.assertFalse(table.delete(order[0]))
        fresh = list(range(1000, 1060))
        for key in fresh:
            table.put(key, self.payload(key))
        self.assertEqual(len(table), len(fresh))
        self.check_table(table, {key: self.payload(key) for key in fresh})
        for key in fresh:
            self.assertIn((key, self.payload(key)), self.entries(table))


if __name__ == "__main__":
    unittest.main()
