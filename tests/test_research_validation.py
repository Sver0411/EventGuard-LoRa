import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from statistics import mean

from eventguard.faults import LossPlan
from eventguard.host import load_config
from eventguard.model import Importance, LinkState, LossModel, RunConfig, Strategy
from eventguard.simulator import budget_allocation, run_reference
from eventguard.strategy import LinkQualityEstimator
from eventguard.trace import generate_trace
from eventguard.validation import classifier_diagnostics

ROOT = Path(__file__).resolve().parents[1]
C_STRATEGY = {Strategy.NO_PROTECTION: 0, Strategy.FIXED_REDUNDANCY: 1, Strategy.EVENTGUARD: 2,
              Strategy.FIXED_1: 3, Strategy.FIXED_2: 4, Strategy.FIXED_3: 5,
              Strategy.IMPORTANCE_ONLY: 6, Strategy.LINK_ONLY: 7,
              Strategy.UNIFORM_BUDGET: 8, Strategy.RANDOM_BUDGET: 9}
C_MODEL = {LossModel.RANDOM_COPY: 0, LossModel.BURST_COPY: 1, LossModel.BURST_SAMPLE: 2}


class ResearchDesignTests(unittest.TestCase):
    def test_no_zero_interval_trace(self):
        for density in (2, 3, 6, 10):
            rows = generate_trace(17, density)
            self.assertTrue(all(b.timestamp_ms > a.timestamp_ms for a, b in zip(rows, rows[1:])))
            self.assertEqual({b.timestamp_ms - a.timestamp_ms for a, b in zip(rows, rows[1:])}, {10000})

    def test_importance_confusion_matrix_reasonable(self):
        d = classifier_diagnostics(tuple(range(31, 61)), 6, load_config())
        self.assertLess(d['false_critical_rate'], .05)
        self.assertGreater(d['per_class']['CRITICAL']['recall'], .85)
        self.assertGreater(d['per_class']['CRITICAL']['precision'], .75)
        self.assertGreater(d['macro_f1'], .8)

    def test_link_state_uses_first_copy_outcomes(self):
        estimator = LinkQualityEstimator(window=4)
        estimator.observe_copy(False, True)
        estimator.observe_copy(True, False)
        estimator.observe_copy(True, False)
        self.assertEqual(estimator.copy_attempts, 3)
        self.assertEqual(estimator.copy_ack_success, 2)
        self.assertEqual(estimator.copy_failures, 1)
        self.assertEqual(estimator.success_ratio, 0)
        self.assertEqual(estimator.state, LinkState.BAD)

    def test_link_state_good_degraded_bad(self):
        e = LinkQualityEstimator(window=4)
        for ok in (True, True, True): e.observe_copy(ok, True)
        self.assertEqual(e.state, LinkState.GOOD)
        e.observe_copy(False, True)
        self.assertEqual(e.state, LinkState.DEGRADED)
        e.observe_copy(False, True)
        e.observe_copy(False, True)
        self.assertEqual(e.state, LinkState.BAD)

    def test_link_estimator_strategy_independence(self):
        samples = generate_trace(39, 6)
        states = []
        first = []
        for strategy in (Strategy.FIXED_1, Strategy.FIXED_3, Strategy.EVENTGUARD):
            outcome = run_reference(samples, RunConfig(strategy, .2, LossModel.RANDOM_COPY, 39))
            states.append([e['link_state'] for e in outcome['events']])
            first.append([e['first_copy_ack_accepted'] for e in outcome['events']])
        self.assertEqual(states[0], states[1]); self.assertEqual(states[1], states[2])
        self.assertEqual(first[0], first[1]); self.assertEqual(first[1], first[2])

    def test_loss_calendar_strategy_independent(self):
        for model in (LossModel.RANDOM_COPY, LossModel.BURST_COPY, LossModel.BURST_SAMPLE):
            calendars = [LossPlan(42, .2, model, 54) for _ in Strategy]
            self.assertTrue(all(c._data == calendars[0]._data and c._ack == calendars[0]._ack for c in calendars))

    def test_burst_sample_fairness(self):
        p = LossPlan(42, .2, LossModel.BURST_SAMPLE, 54)
        for i in range(54):
            for kind in ('DATA', 'ACK'):
                self.assertEqual(len({p.drops(kind, i, copy) for copy in range(3)}), 1)
        self.assertTrue(any(all(p.drops('DATA', i + j, 0) for j in range(3)) for i in range(52)))

    def test_effective_loss_rate_close_across_strategies(self):
        for model in (LossModel.RANDOM_COPY, LossModel.BURST_SAMPLE):
            differences = []
            for seed in range(31, 61):
                samples = generate_trace(seed, 6)
                eg = run_reference(samples, RunConfig(Strategy.EVENTGUARD, .2, model, seed))['metrics']
                fixed = run_reference(samples, RunConfig(Strategy.FIXED_1, .2, model, seed))['metrics']
                self.assertEqual(eg['first_copy_data_drop_rate'], fixed['first_copy_data_drop_rate'])
                differences.append(eg['effective_data_drop_rate'] - fixed['effective_data_drop_rate'])
            self.assertLess(abs(mean(differences)), .05)

    def test_exact_budget_allocation_does_not_read_event_labels(self):
        samples = generate_trace(31, 6)
        config = RunConfig(Strategy.EVENTGUARD, .2, LossModel.RANDOM_COPY, 31)
        budget = run_reference(samples, config)['metrics']['physical_data_transmissions']
        for strategy in (Strategy.UNIFORM_BUDGET, Strategy.RANDOM_BUDGET):
            allocation = budget_allocation(strategy, len(samples), budget, 31)
            self.assertEqual(sum(allocation), budget)
            changed = [replace(s, truth=s.truth, temperature=s.temperature + 100) for s in samples]
            self.assertEqual(allocation, budget_allocation(strategy, len(changed), budget, 31))
            outcome = run_reference(samples, replace(config, strategy=strategy))
            self.assertEqual(outcome['metrics']['physical_data_transmissions'], budget)


class CParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / 'parity'
        subprocess.run(['cc', '-std=c11', '-O2', '-I' + str(ROOT / 'firmware/common'),
                        str(ROOT / 'tools/parity_harness.c'), str(ROOT / 'firmware/common/faults.c'),
                        str(ROOT / 'firmware/common/importance.c'), str(ROOT / 'firmware/common/strategy.c'),
                        '-lm', '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def _compare(self, strategy, model, seed, rate):
        samples = generate_trace(seed, 6)
        config = RunConfig(strategy, rate, model, seed, critical_threshold=3.0)
        outcome = run_reference(samples, config)
        budget = run_reference(samples, replace(config, strategy=Strategy.EVENTGUARD))['metrics']['physical_data_transmissions']
        data = ''.join(f'{s.sample_id},{s.timestamp_ms},{s.temperature},{s.humidity},{s.light},{s.soil_moisture}\n' for s in samples)
        result = subprocess.run([str(self.binary), str(seed), str(rate), str(C_MODEL[model]),
                                 str(C_STRATEGY[strategy]), str(budget), str(len(samples)), '3'],
                                input=data, text=True, capture_output=True, check=True)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), len(samples))
        plan = LossPlan(seed, rate, model, len(samples))
        for i, (line, event) in enumerate(zip(lines, outcome['events'])):
            fields = line.split(',')
            self.assertEqual(int(fields[0]), Importance[event['importance']])
            self.assertEqual(int(fields[1]), event['redundancy_selected'])
            self.assertEqual(int(fields[2]), LinkState[event['link_state']])
            self.assertAlmostEqual(float(fields[3]), event['importance_score'], places=3)
            for copy in range(3):
                self.assertEqual(int(fields[4 + 2 * copy]), plan.drops('DATA', i, copy))
                self.assertEqual(int(fields[5 + 2 * copy]), plan.drops('ACK', i, copy))

    def test_python_c_importance_parity(self):
        for seed in (31, 47, 59):
            self._compare(Strategy.EVENTGUARD, LossModel.RANDOM_COPY, seed, .2)

    def test_python_c_policy_fault_and_budget_parity(self):
        for model in (LossModel.RANDOM_COPY, LossModel.BURST_COPY, LossModel.BURST_SAMPLE):
            for strategy in C_STRATEGY:
                self._compare(strategy, model, 37, .2)
