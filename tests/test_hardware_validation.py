import unittest
from unittest.mock import patch

from eventguard.hardware_validation import (frozen_guard, plan, test_run_state_isolation as check_isolation)


class FakeReader:
    def __init__(self, role):
        self.role = role
        self.commands = []

    def drain(self):
        return []

    def write(self, command):
        self.commands.append(command)


class HardwareValidationTests(unittest.TestCase):
    def test_frozen_core(self):
        self.assertEqual(frozen_guard()['algorithm_version'], 'EventGuard-v1')

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
            if token == 'E220_READY': return [(0, f'ROLE,{reader.role},1,17,16'), (0, 'E220_READY')]
            raise AssertionError(token)

        with patch('eventguard.hardware_validation._lines', side_effect=reply):
            result = check_isolation(sensor, gateway, {})
        self.assertEqual(sensor.commands, ['RESET', 'STATUS'])
        self.assertEqual(gateway.commands, ['RESET', 'STATUS'])
        self.assertIn('sequence', result['reset_contract'])


if __name__ == '__main__':
    unittest.main()
