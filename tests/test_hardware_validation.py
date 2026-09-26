import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from eventguard.hardware_validation import (_gateway_end_counter_issues, _precompute_stage1_budgets,
                                            _verify_stage1_firmware, frozen_guard, plan,
                                            _is_status_role_line,
                                            _ensure_uart_diag,
                                            _parse_samples, _run_deadline_seconds,
                                            _stage1_physical_noise_gate,
                                            _validate_console_capture,
                                            test_run_state_isolation as check_isolation)
from eventguard.host import SerialLineFramer, _parse_metrics, _run_config, load_config
from eventguard.trace import generate_trace, trace_fingerprint
from eventguard.simulator import run_reference


class FakeReader:
    def __init__(self, role):
        self.role = role
        self.commands = []
        self.pending = []

    def drain(self):
        lines = self.pending
        self.pending = []
        return list(enumerate(lines))

    def write(self, command):
        self.commands.append(command)
        if command == 'STATUS':
            self.pending.extend([f'ROLE,{self.role},1,17,16', 'E220_READY'])


class HardwareValidationTests(unittest.TestCase):
    def test_frozen_core(self):
        self.assertEqual(frozen_guard()['algorithm_version'], 'EventGuard-v1')

    def test_status_requires_explicit_role_response_not_boot_mac_line(self):
        self.assertFalse(_is_status_role_line('ROLE,GATEWAY,c0:4e:30:31:42:9c', 'GATEWAY'))
        self.assertTrue(_is_status_role_line('ROLE,GATEWAY,2,17,16', 'GATEWAY'))

    def test_uart_diagnostic_captured_with_end_is_not_waited_for_again(self):
        reader = FakeReader('SENSOR')
        captured = [(1.0, 'END,36,40,31,0'), (1.1, 'UART_DIAG,frames_completed,40')]
        with patch('eventguard.hardware_validation._lines', side_effect=AssertionError('unexpected extra wait')):
            self.assertEqual(_ensure_uart_diag(reader, captured), captured)

    def test_interleaved_design(self):
        for stage, expected in (('smoke', 12), ('stage1', 160), ('full', 400)):
            order = plan(stage)
            self.assertEqual(len(order), expected)
            self.assertEqual([entry[4] for entry in order], list(range(1, expected + 1)))
            group_size = 3 if stage == 'smoke' else 4 if stage == 'stage1' else 5
            groups = [order[offset:offset + group_size] for offset in range(0, expected, group_size)]
            for group in groups:
                self.assertEqual(len({(m, r, s) for m, r, s, _, _ in group}), 1)
                if stage == 'stage1':
                    self.assertEqual({entry[3] for entry in group},
                                     {'EVENTGUARD', 'IMPORTANCE_ONLY', 'UNIFORM_BUDGET', 'RANDOM_BUDGET'})
                else:
                    self.assertEqual(group[0][3], 'EVENTGUARD')
            if stage == 'stage1':
                self.assertGreater(len({group[0][3] for group in groups}), 1)
                self.assertEqual(plan(stage), order)

    def test_stage1_host_budgets_precomputed_for_all_conditions(self):
        budgets, rows = _precompute_stage1_budgets(load_config())
        self.assertEqual(len(budgets), 40)
        self.assertEqual(len(rows), 40)
        self.assertTrue(all(row['eventguard_reference_data_budget'] > 0 for row in rows))

    def test_stage1_firmware_pair_is_device_recovered_and_hash_locked(self):
        import json
        from eventguard.hardware_validation import ROOT
        study = json.loads((ROOT / 'results/hardware_validation_v2/hardware_manifest.json').read_text())
        images, recovery = _verify_stage1_firmware(study)
        self.assertEqual(set(images), {'sensor', 'gateway'})
        self.assertTrue(recovery['firmware_manifest']['recovered_from_device'])

    def test_stage1_rejects_non_skip_flash_before_any_hardware_operation(self):
        from eventguard.hardware_validation import run
        with self.assertRaisesRegex(RuntimeError, 'build/flash is forbidden'):
            run('stage1', skip_flash=False)

    def test_run_state_isolation(self):
        sensor, gateway = FakeReader('SENSOR'), FakeReader('GATEWAY')

        def reply(reader, token, timeout=8):
            if token == 'RESET,OK': return [(0, 'RESET,OK')]
            raise AssertionError(token)

        with patch('eventguard.hardware_validation._lines', side_effect=reply):
            result = check_isolation(sensor, gateway, {})
        self.assertEqual(sensor.commands, ['RESET', 'STATUS'])
        self.assertEqual(gateway.commands, ['RESET', 'STATUS'])
        self.assertIn('sequence', result['reset_contract'])

    def test_injected_drop_is_not_post_injection_receipt(self):
        sample = generate_trace(31, 6)[:1]
        config = _run_config(load_config(), 'FIXED_2', .20, 'RANDOM_COPY', 31)
        metrics = _parse_metrics(sample, [], [(0, 'TX,0,0,0,2,26')],
                                 [(0, 'DROP,DATA,0,0,0')], 0, 1, config,
                                 trace_fingerprint(sample))
        self.assertEqual(metrics['physical_data_transmissions'], 1)
        self.assertEqual(metrics['physical_data_before_injection'], 1)
        self.assertEqual(metrics['physical_data_received'], 0)
        self.assertEqual(metrics['data_injected_drops'], 1)
        self.assertEqual(metrics['uncontrolled_physical_data_missing'], 0)

    def test_gateway_end_counter_with_data_injection(self):
        metrics = {
            'physical_data_before_injection': 10,
            'physical_data_received': 7,
            'data_injected_drops': 3,
            'delivered_packets': 7,
            'duplicate_packets': 0,
            'ack_count': 7,
            'crc_errors': 0,
        }
        # END fields: expected, s_rx_count, delivery, duplicates, ACK TX, CRC, DATA drop.
        gateway_end = [10, 7, 7, 0, 7, 0, 3, 0, 0]
        self.assertEqual(metrics['physical_data_before_injection'], 10)
        self.assertEqual(metrics['data_injected_drops'], 3)
        self.assertEqual(metrics['physical_data_received'], 7)
        self.assertEqual(_gateway_end_counter_issues(gateway_end, 10, metrics), [])

    def test_one_unplanned_physical_data_loss_is_recorded_not_policy_failure(self):
        from eventguard.model import GroundTruth
        sample = generate_trace(31, 6)[:1]
        config = _run_config(load_config(), 'FIXED_2', 0.0, 'RANDOM_COPY', 31)
        reference = run_reference(sample, config, load_config()['uart_baud'])
        expected = reference['events'][0]
        truth_code = {'NORMAL': 0, 'IMPORTANT': 1, 'CRITICAL': 2}[sample[0].truth.value]
        imp = expected['importance']
        score = expected['importance_score']
        copies = expected['copies_transmitted']
        sensor = [
            (0.1, f'EVT,0,{truth_code},{sample[0].truth.value},{score:.4f},GOOD,{copies}'),
            (0.2, 'TX,0,0,0,2,26'), (0.3, 'ACK,0,0,0,OK'),
            (0.4, 'TX,0,0,1,2,26'), (0.5, 'TIMEOUT,0,1'),
            (0.6, f'SAMPLE,0,{sample[0].truth.value},{imp},{score:.4f},GOOD,2,2,1,500.0'),
        ]
        gateway = [(0.25, 'RX,0,0,0,NEW'), (0.26, 'DELIVER,0,0'), (0.27, 'ACK_TX,0,0,0,13')]
        events, divergences, issues, anomalies = _parse_samples(sample, sensor, gateway, config, reference)
        self.assertEqual(len(events), 1)
        self.assertEqual(divergences, [])
        self.assertEqual(issues, [])
        self.assertEqual(anomalies, [{'kind': 'uncontrolled_physical_data_missing', 'sample_id': 0,
                                      'copy_index': 1, 'planned_drop_opportunity': False}])

    def test_stage1_isolated_physical_noise_allowed_and_repeated_noise_stops(self):
        record = {'run_id': 'uniform-budget', 'execution_order': 2,
                  'status': 'complete', 'loss_model': 'RANDOM_COPY', 'loss_rate': .2, 'seed': 31,
                  'strategy': 'UNIFORM_BUDGET', 'metrics': {'physical_data_transmissions': 144,
                                                            'physical_ack_frames': 110},
                  'physical_anomalies': [{'kind': 'uncontrolled_physical_data_missing',
                                          'sample_id': 25, 'copy_index': 1}]}
        allowed = _stage1_physical_noise_gate(record, [])
        self.assertTrue(allowed['allowed_to_continue'])
        self.assertAlmostEqual(allowed['cumulative_data_missing_rate'], 1 / 144)
        prior = {**record, 'run_id': 'eventguard', 'execution_order': 1, 'strategy': 'EVENTGUARD'}
        repeated = _stage1_physical_noise_gate(record, [prior])
        self.assertFalse(repeated['allowed_to_continue'])
        self.assertTrue(any('repeated across strategies' in reason for reason in repeated['stop_reasons']))

    def test_stage1_physical_noise_gate_excludes_current_and_future_records(self):
        record = {'run_id': 'current', 'execution_order': 3, 'status': 'complete',
                  'loss_model': 'RANDOM_COPY', 'loss_rate': .2, 'seed': 31,
                  'strategy': 'UNIFORM_BUDGET',
                  'metrics': {'physical_data_transmissions': 144, 'physical_ack_frames': 110},
                  'physical_anomalies': [{'kind': 'uncontrolled_physical_data_missing',
                                          'sample_id': 25, 'copy_index': 1}]}
        same_record = dict(record)
        future = {**record, 'run_id': 'future', 'execution_order': 4}
        result = _stage1_physical_noise_gate(record, [same_record, future])
        self.assertTrue(result['allowed_to_continue'])
        self.assertEqual(result['cumulative_data_missing'], 1)
        self.assertAlmostEqual(result['cumulative_data_missing_rate'], 1 / 144)

    def test_stage1_watchdog_includes_three_frozen_send_waits_per_copy(self):
        seconds, components = _run_deadline_seconds(144, 54, 1000)
        self.assertEqual(components['per_copy_worst_case_seconds'], 4.0)
        self.assertEqual(seconds, 633.0)

    def test_serial_line_framer_preserves_split_and_back_to_back_lines(self):
        framer = SerialLineFramer()
        self.assertEqual(framer.feed(b'EVT,1,2,CRIT'), [])
        self.assertEqual(framer.pending_bytes, len(b'EVT,1,2,CRIT'))
        self.assertEqual(framer.feed(b'ICAL\r\nSAMPLE,1,CRITICAL\nTX,1,1,0,2,26\n'),
                         ['EVT,1,2,CRITICAL', 'SAMPLE,1,CRITICAL', 'TX,1,1,0,2,26'])
        self.assertEqual(framer.pending_bytes, 0)

    def test_console_capture_is_checked_against_firmware_counters(self):
        sensor = [(0, 'EVT,0,0,NORMAL,0.1,GOOD,2'), (1, 'TX,0,0,0,2,26'),
                  (2, 'ACK,0,0,0,OK'), (3, 'TX,1,1,0,2,26'), (4, 'ACK,1,1,0,DROP'),
                  (5, 'EVT,1,0,NORMAL,0.1,GOOD,2'),
                  (6, 'SAMPLE,0,NORMAL,NORMAL,0.1,GOOD,2,1,1,100'),
                  (7, 'SAMPLE,1,NORMAL,NORMAL,0.1,GOOD,2,1,0,110'),
                  (8, 'D_ACK_RECEIVED,0,0,0,12'), (9, 'D_ACK_RECEIVED,1,0,1,13'),
                  ('END', 'END,2,2,1,0')]
        gateway = [(0, 'D_RX_FRAME_COMPLETE,0,0,0,10'), (1, 'D_RX_FRAME_COMPLETE,1,0,1,11'),
                   (2, 'RX,0,0,0,NEW'), (3, 'RX,1,1,0,DUP'),
                   (4, 'ACK_TX,0,0,0,13'), (5, 'ACK_TX,1,1,0,13'),
                   ('END', 'END,2,2,2,0,2,0,0,0,0'),
                   ('DIAG', 'UART_DIAG,frames_completed,2')]
        diag = {'sensor': {'frames_completed': 1}, 'gateway': {'frames_completed': 2}}
        diag['sensor']['frames_completed'] = 2
        self.assertEqual(_validate_console_capture(sensor, gateway, ['END,2,2,1,0'],
                                                   ['END,2,2,2,0,2,0,0,0,0'], diag), [])
        missing_tx = [row for row in sensor if row[1] != 'TX,1,1,0,2,26']
        errors = _validate_console_capture(missing_tx, gateway, ['END,2,2,1,0'],
                                           ['END,2,2,2,0,2,0,0,0,0'], diag)
        self.assertTrue(any('console TX capture' in error for error in errors))

    def test_native_e220_stream_parser(self):
        compiler = shutil.which('cc')
        if compiler is None:
            self.skipTest('native C compiler is unavailable')
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='eg-e220-parser-') as temp:
            binary = Path(temp) / 'e220_stream_parser_test'
            subprocess.run([
                compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-I', str(root / 'firmware/common'),
                str(root / 'tests/e220_stream_parser_test.c'),
                str(root / 'firmware/common/e220_stream_parser.c'),
                str(root / 'firmware/common/protocol.c'), '-o', str(binary),
            ], check=True, capture_output=True, text=True)
            completed = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn('all tests passed', completed.stdout)


if __name__ == '__main__':
    unittest.main()
