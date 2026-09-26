"""Tests for the separate post-hoc host-only diagnostic allocator."""
from __future__ import annotations

import inspect
import unittest

from tools.run_posthoc_importance_matched_budget import (
    allocate_importance_matched_budget,
    ordering_key,
)


class ImportanceMatchedBudgetTests(unittest.TestCase):
    def test_case_a_base_budget_matches_importance_only(self):
        labels = ["NORMAL", "IMPORTANT", "CRITICAL", "IMPORTANT"]
        self.assertEqual(allocate_importance_matched_budget(labels, 8, 31), [1, 2, 3, 2])

    def test_case_b_extra_goes_to_important_first(self):
        labels = ["NORMAL", "IMPORTANT", "NORMAL", "IMPORTANT", "CRITICAL"]
        result = allocate_importance_matched_budget(labels, 10, 31)
        self.assertEqual(sum(result), 10)
        self.assertEqual(result[0], 1)
        self.assertEqual(result[2], 1)
        self.assertEqual(result[4], 3)
        self.assertEqual(result[1] + result[3], 5)

    def test_case_c_normal_round_one_after_important(self):
        labels = ["NORMAL", "IMPORTANT", "NORMAL", "CRITICAL"]
        result = allocate_importance_matched_budget(labels, 9, 31)
        self.assertEqual(result[1], 3)
        self.assertEqual(sorted((result[0], result[2])), [1, 2])

    def test_case_d_normal_round_two_follows_complete_round_one(self):
        labels = ["NORMAL", "IMPORTANT", "NORMAL", "CRITICAL"]
        result = allocate_importance_matched_budget(labels, 11, 31)
        self.assertEqual(result[1], 3)
        self.assertEqual(sorted((result[0], result[2])), [2, 3])

    def test_case_e_maximum_budget_respects_cap(self):
        labels = ["NORMAL", "IMPORTANT", "CRITICAL", "NORMAL"]
        result = allocate_importance_matched_budget(labels, 12, 31)
        self.assertEqual(result, [3, 3, 3, 3])
        with self.assertRaises(ValueError):
            allocate_importance_matched_budget(labels, 13, 31)

    def test_case_f_repeated_seed_and_input_are_deterministic(self):
        labels = ["NORMAL", "IMPORTANT", "NORMAL", "IMPORTANT", "NORMAL"]
        a = allocate_importance_matched_budget(labels, 10, 37)
        b = allocate_importance_matched_budget(labels, 10, 37)
        self.assertEqual(a, b)
        self.assertEqual(ordering_key(37, 0, "NORMAL"), ordering_key(37, 0, "NORMAL"))

    def test_case_g_link_history_cannot_affect_allocation(self):
        self.assertEqual(tuple(inspect.signature(allocate_importance_matched_budget).parameters),
                         ("predicted_classes", "budget", "seed"))
        labels = ["IMPORTANT", "NORMAL", "IMPORTANT", "NORMAL"]
        for ignored_link_history in ([], ["GOOD"] * 54, ["BAD"] * 54):
            self.assertEqual(
                allocate_importance_matched_budget(labels, 9, 32),
                allocate_importance_matched_budget(labels, 9, 32),
                ignored_link_history,
            )
        self.assertFalse({"link", "link_state", "loss_plan", "truth"} &
                         set(allocate_importance_matched_budget.__code__.co_names))

    def test_case_h_ack_outcomes_cannot_affect_allocation(self):
        labels = ["NORMAL", "IMPORTANT", "NORMAL", "IMPORTANT"]
        expected = allocate_importance_matched_budget(labels, 9, 40)
        for ignored_ack_outcomes in ([], [True] * 54, [False] * 54):
            self.assertEqual(allocate_importance_matched_budget(labels, 9, 40),
                             expected, ignored_ack_outcomes)
        self.assertFalse({"ack", "ack_history", "accepted_ack", "calendar"} &
                         set(allocate_importance_matched_budget.__code__.co_names))


if __name__ == "__main__":
    unittest.main()
