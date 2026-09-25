import unittest

from eventguard.analysis import confidence_interval, paired_t_test
from eventguard.faults import LossPlan
from eventguard.importance import ImportanceClassifier
from eventguard.model import GroundTruth, Importance, LinkState, LossModel, Sample, Strategy
from eventguard.protocol import AckPacket, DataPacket, DuplicateTracker, ProtocolError, crc16_ccitt
from eventguard.simulator import run_reference
from eventguard.strategy import LinkQualityEstimator, choose_redundancy
from eventguard.trace import generate_trace, trace_fingerprint


class TraceTests(unittest.TestCase):
    def test_trace_is_deterministic_and_contains_all_phases_and_labels(self):
        left = generate_trace(42, 4)
        right = generate_trace(42, 4)
        self.assertEqual(left, right)
        self.assertEqual(trace_fingerprint(left), trace_fingerprint(right))
        self.assertEqual({row.phase for row in left}, {"STABLE", "SLOW_CHANGE", "RAPID_CHANGE", "STABLE_WAIT",
                         "CRITICAL_SOIL", "RECOVERY_SOIL", "CRITICAL_MULTI", "RECOVERY", "STABLE_FINAL"})
        self.assertEqual({row.truth for row in left}, set(GroundTruth))
        self.assertNotEqual(trace_fingerprint(left), trace_fingerprint(generate_trace(43, 4)))


class ImportanceTests(unittest.TestCase):
    def sample(self, index, timestamp, values):
        return Sample(index, timestamp, *values, GroundTruth.NORMAL, "TEST")

    def test_stable_normal_and_multivariate_step_critical(self):
        classifier = ImportanceClassifier()
        self.assertEqual(classifier.classify(self.sample(0, 0, (24, 48, 120, 60)))[0], Importance.NORMAL)
        self.assertEqual(classifier.classify(self.sample(1, 1000, (24.02, 48.1, 121, 60.1)))[0], Importance.NORMAL)
        level, score = classifier.classify(self.sample(2, 2000, (31, 70, 1100, 30)))
        self.assertGreater(score, 2.4)
        self.assertEqual(level, Importance.CRITICAL)

    def test_trace_critical_ground_truth_starts_on_value_change(self):
        samples = generate_trace(11, 4)
        classifier = ImportanceClassifier()
        classifications = {}
        for sample in samples:
            label, score = classifier.classify(sample)
            if sample.phase in ("CRITICAL_SOIL", "CRITICAL_MULTI") and sample.phase not in classifications:
                classifications[sample.phase] = (sample.truth, label, score)
        self.assertEqual(classifications["CRITICAL_SOIL"][1], Importance.CRITICAL)
        self.assertEqual(classifications["CRITICAL_MULTI"][1], Importance.CRITICAL)


class ProtocolTests(unittest.TestCase):
    def test_packet_round_trip_and_crc(self):
        packet = DataPacket(1, 257, 19, 2, 1, 3, 0x12345678, -1525, 5875, 640, 731)
        wire = packet.encode()
        self.assertEqual(len(wire), 26)
        self.assertEqual(DataPacket.decode(wire), packet)
        self.assertEqual(crc16_ccitt(b"123456789"), 0x29B1)
        corrupted = bytearray(wire); corrupted[16] ^= 0x20
        with self.assertRaises(ProtocolError):
            DataPacket.decode(bytes(corrupted))

    def test_ack_round_trip_and_crc_error(self):
        ack = AckPacket(1, 10, 9, 0, 2)
        encoded = ack.encode()
        self.assertEqual(len(encoded), 13)
        self.assertEqual(AckPacket.decode(encoded), ack)
        with self.assertRaises(ProtocolError):
            AckPacket.decode(encoded[:-1] + bytes([encoded[-1] ^ 1]))

    def test_deduplication_counts_duplicates_and_out_of_order(self):
        tracker = DuplicateTracker()
        self.assertTrue(tracker.accept(1, 2))
        self.assertFalse(tracker.accept(1, 2))
        self.assertTrue(tracker.accept(1, 1))
        self.assertEqual(tracker.duplicates, 1)
        self.assertEqual(tracker.out_of_order, 1)


class FaultTests(unittest.TestCase):
    def test_random_loss_decisions_are_seeded_and_kind_separated(self):
        first = LossPlan(123, .2, LossModel.RANDOM, 100, 3)
        second = LossPlan(123, .2, LossModel.RANDOM, 100, 3)
        other = LossPlan(124, .2, LossModel.RANDOM, 100, 3)
        self.assertEqual(first._data, second._data)
        self.assertEqual(first._ack, second._ack)
        self.assertNotEqual(first._data, other._data)
        self.assertNotEqual(first._data, first._ack)

    def test_burst_loss_contains_configured_contiguous_run(self):
        for length in (2, 3, 5):
            plan = LossPlan(37, .20, LossModel.BURST, 100, length)
            self.assertEqual(sum(plan._data), round(300 * .2))
            self.assertTrue(any(all(plan._data[i:i + length]) for i in range(len(plan._data) - length + 1)))


class StrategyAndLinkTests(unittest.TestCase):
    def test_baselines_and_eventguard_bounded_adaptation(self):
        self.assertEqual(choose_redundancy(Strategy.NO_PROTECTION, Importance.CRITICAL, LinkState.BAD), 1)
        self.assertEqual(choose_redundancy(Strategy.FIXED_REDUNDANCY, Importance.NORMAL, LinkState.BAD), 2)
        self.assertEqual(choose_redundancy(Strategy.EVENTGUARD, Importance.CRITICAL, LinkState.GOOD), 3)
        self.assertEqual(choose_redundancy(Strategy.EVENTGUARD, Importance.IMPORTANT, LinkState.DEGRADED), 3)
        self.assertEqual(choose_redundancy(Strategy.EVENTGUARD, Importance.CRITICAL, LinkState.BAD, maximum=3), 3)

    def test_link_estimator_uses_ack_window_and_consecutive_failures(self):
        estimator = LinkQualityEstimator(window=4)
        self.assertEqual(estimator.state, LinkState.GOOD)
        estimator.observe(True); estimator.observe(True); estimator.observe(True)
        estimator.observe(False)
        self.assertEqual(estimator.state, LinkState.DEGRADED)
        estimator.observe(False)
        self.assertEqual(estimator.state, LinkState.DEGRADED)
        estimator.observe(False)
        self.assertEqual(estimator.state, LinkState.BAD)
        estimator.observe(True); estimator.observe(True); estimator.observe(True); estimator.observe(True)
        self.assertEqual(estimator.state, LinkState.GOOD)

    def test_strategy_runs_share_same_trace_and_loss_calendar(self):
        samples = generate_trace(11, 3)
        plans = [LossPlan(11, .2, LossModel.RANDOM, len(samples), 3) for _ in Strategy]
        self.assertTrue(all(plan._data == plans[0]._data and plan._ack == plans[0]._ack for plan in plans))
        outputs = [run_reference(samples, __import__("eventguard.model", fromlist=["RunConfig"]).RunConfig(strategy, .2, LossModel.RANDOM, 11)) for strategy in Strategy]
        self.assertTrue(all(output["metrics"]["logical_packets"] == len(samples) for output in outputs))
        self.assertNotEqual(outputs[0]["metrics"]["total_bytes_transmitted"], outputs[1]["metrics"]["total_bytes_transmitted"])

    def test_fixed_baseline_sends_exact_configured_copies_even_when_ack_arrives(self):
        samples = generate_trace(11, 2)
        no_protection = run_reference(samples, __import__("eventguard.model", fromlist=["RunConfig"]).RunConfig(
            Strategy.NO_PROTECTION, 0, LossModel.RANDOM, 11))
        fixed = run_reference(samples, __import__("eventguard.model", fromlist=["RunConfig"]).RunConfig(
            Strategy.FIXED_REDUNDANCY, 0, LossModel.RANDOM, 11, fixed_redundancy=2))
        self.assertEqual(no_protection["metrics"]["physical_data_transmissions"], len(samples))
        self.assertEqual(fixed["metrics"]["physical_data_transmissions"], 2 * len(samples))
        self.assertEqual(fixed["metrics"]["overall_delivery_ratio"], 1.0)


class MetricsTests(unittest.TestCase):
    def test_parser_uses_gateway_dedup_and_ack_and_injection_records(self):
        from eventguard.host import _parse_metrics
        samples = generate_trace(11, 2)
        sensor_lines = [(1.0, "TX,0,0,0,2,26"), (1.01, "ACK,0,0,0,DROP"),
                        (1.1, "TX,0,0,1,2,26"), (1.11, "ACK,0,0,1,OK")]
        gateway_lines = [(1.005, "RX,0,0,0,NEW"), (1.006, "DELIVER,0,0"),
                         (1.007, "ACK_TX,0,0,0,13"), (1.105, "RX,0,0,1,DUP"), (1.106, "ACK_TX,0,0,1,13")]
        config = __import__("eventguard.model", fromlist=["RunConfig"]).RunConfig(Strategy.EVENTGUARD, .05, LossModel.RANDOM, 11)
        metrics = _parse_metrics(samples, [], sensor_lines, gateway_lines, 1, 2, config, "hash")
        self.assertEqual(metrics["physical_data_transmissions"], 2)
        self.assertEqual(metrics["ack_count"], 2)
        self.assertEqual(metrics["ack_injected_drops"], 1)
        self.assertEqual(metrics["duplicate_packets"], 1)
        self.assertEqual(metrics["total_bytes_transmitted"], 2 * 26 + 2 * 13)

    def test_confidence_interval_and_paired_t_test(self):
        mean, std, median, low, high = confidence_interval([1, 2, 3, 4, 5])
        self.assertEqual((mean, median), (3, 3))
        self.assertLess(low, mean); self.assertGreater(high, mean); self.assertGreater(std, 0)
        result = paired_t_test([.9, .8, .7], [.5, .5, .5])
        self.assertGreater(result["mean_difference"], 0)
        self.assertLess(result["p_two_sided"], .1)


if __name__ == "__main__":
    unittest.main()
