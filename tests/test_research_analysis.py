import json
import unittest
from pathlib import Path

from eventguard.research_analysis import (
    ROOT, _enrich, _frontier, assert_frozen_algorithm, describe, holm_adjust,
    load_frozen_runs, wilcoxon_exact,
)


class FrozenResearchAnalysisTests(unittest.TestCase):
    def test_algorithm_hashes_are_frozen(self):
        assert_frozen_algorithm()

    def test_locked_evaluation_matrix(self):
        rows = load_frozen_runs()
        self.assertEqual(len(rows), 8000)
        self.assertEqual({r['seed'] for r in rows}, set(range(31, 131)))

    def test_summary_statistics(self):
        values = describe([1, 2, 3, 4, 5])
        self.assertEqual(values['n'], 5)
        self.assertEqual(values['mean'], 3)
        self.assertEqual(values['median'], 3)
        self.assertGreater(values['std'], 0)
        self.assertLess(values['ci95_low'], 3)
        self.assertGreater(values['ci95_high'], 3)

    def test_critical_copy_cost_uses_class_specific_traffic(self):
        row = load_frozen_runs()[0]
        self.assertEqual(row['strategy'], 'FIXED_1')
        enriched = _enrich(row)
        self.assertAlmostEqual(enriched['data_copies_per_critical_event'], 1.0)
        self.assertAlmostEqual(enriched['data_copies_per_delivered_critical'], 1.0)

    def test_wilcoxon_signed_rank_exact_and_all_ties(self):
        result = wilcoxon_exact([1, 2, 3])
        self.assertEqual(result['n_nonzero'], 3)
        self.assertAlmostEqual(result['p_two_sided'], .25)
        self.assertEqual(result['rank_biserial'], 1)
        ties = wilcoxon_exact([0, 0, 0])
        self.assertEqual((ties['p_two_sided'], ties['rank_biserial']), (1, 0))
        mixed = wilcoxon_exact([1, -1, 0])
        self.assertEqual(mixed['n_nonzero'], 2)
        self.assertEqual(mixed['rank_biserial'], 0)
        self.assertEqual(mixed['p_two_sided'], 1)

    def test_holm_adjustment(self):
        result = holm_adjust([.01, .03, .04])
        self.assertEqual([round(x, 2) for x in result], [.03, .06, .06])

    def test_pareto_frontier_is_within_condition(self):
        points = [
            {'strategy': 'A', 'total_bytes_transmitted_mean': 10, 'critical_event_delivery_ratio_mean': .8},
            {'strategy': 'B', 'total_bytes_transmitted_mean': 20, 'critical_event_delivery_ratio_mean': .9},
            {'strategy': 'C', 'total_bytes_transmitted_mean': 25, 'critical_event_delivery_ratio_mean': .85},
        ]
        front, dominated = _frontier(points)
        self.assertEqual({p['strategy'] for p in front}, {'A', 'B'})
        self.assertEqual({p['strategy'] for p in dominated}, {'C'})

    def test_manifests_and_four_copy_exclusion(self):
        root = json.loads((ROOT / 'results/experiment_manifest.json').read_text())
        sensitivity = json.loads((ROOT / 'results/sensitivity/experiment_manifest.json').read_text())
        self.assertEqual(root['algorithm_version'], 'EventGuard-v1')
        self.assertEqual(root['seed_range']['evaluation'], [31, 130])
        self.assertEqual(sensitivity['run_count'], 11000)
        self.assertIn('not simulated', sensitivity['max_redundancy_4_status'])
        report = (ROOT / 'results/sensitivity/report.md').read_text()
        self.assertIn('Unsupported in frozen v1', report)


if __name__ == '__main__':
    unittest.main()
