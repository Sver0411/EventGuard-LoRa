import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from eventguard.hardware_validation import (_gateway_end_counter_issues, frozen_guard, plan,
                                            _is_status_role_line,
                                            _ensure_uart_diag,
                                            test_run_state_isolation as check_isolation)
from eventguard.host import _parse_metrics, _run_config, load_config
from eventguard.trace import generate_trace, trace_fingerprint


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
            for offset in range(0, expected, 3 if stage == 'smoke' else 4 if stage == 'stage1' else 5):
                group = order[offset:offset + (3 if stage == 'smoke' else 4 if stage == 'stage1' else 5)]
                self.assertEqual(group[0][3], 'EVENTGUARD')
                self.assertEqual(len({(m, r, s) for m, r, s, _, _ in group}), 1)

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
