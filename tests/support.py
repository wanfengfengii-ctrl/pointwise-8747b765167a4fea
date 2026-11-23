import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from archive.receipts import read_first_receipt
from archive.store import load_snapshot
from custody.device import ReleaseOutcome
from custody.receive import receive_page
from registry.identity import PendingReel, RegistrationMode
from registry.intake import register
from registry.session import open_session
from review.report import get_report


DEVICE = 'reader-a'
BATCH = 'day-1'
SIX_ONES = (('P1', '0101'), ('P2', '01010101'))
SINGLE_SHAPE = {'dialect': 'single', 'values': [1, 1, 1],
                'ranges': [[0, 2], [2, 4], [4, 6]], 'total': 3, 'byte_count': 6}
DOUBLE_SHAPE = {'dialect': 'double', 'values': [2, 2],
                'ranges': [[0, 3], [3, 6]], 'total': 4, 'byte_count': 6}
DOUBLE_ORIGINS = [[['P1', 0, 2], ['P2', 0, 1]], [['P2', 1, 4]]]


def report_shape(report):
    snapshot = report.snapshot
    return {'dialect': snapshot.dialect.value, 'values': [row.value for row in snapshot.records],
            'ranges': [[row.start, row.end] for row in snapshot.records],
            'total': snapshot.total, 'byte_count': snapshot.byte_count}


def page_origins(records):
    return [[[origin.page_id, origin.start, origin.end] for origin in row.sources]
            for row in records]


def observe(case_id, input_data, expected, actual, downstream_expected, downstream_actual,
            code_paths, oracle):
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': case_id, 'input': input_data, 'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': code_paths, 'oracle': oracle,
    }, ensure_ascii=False, sort_keys=True), flush=True)


class WorkdayCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='reel-test-')
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)
        self.ledger = path / 'exchange.sqlite'
        self.device_ledger = path / 'device.sqlite'
        self.session = open_session(self.ledger, BATCH, device_path=self.device_ledger)
        self.addCleanup(self.close_session)

    def close_session(self):
        self.session.close()

    def reopen(self, *, batch=BATCH, online=True):
        self.close_session()
        self.session = open_session(self.ledger, batch,
                                    device_path=self.device_ledger if online else None)

    def stage(self, reel_id, pages, *, mode=RegistrationMode.TAIL, device_id=DEVICE):
        register(self.session, reel_id, device_id, mode)
        return self.session.device.load_reel(device_id, reel_id,
                                             ((key, bytes.fromhex(data)) for key, data in pages))

    def deliver(self, reel_id, page_id, *, device_id=DEVICE):
        page = self.session.device.current_page(device_id, reel_id, page_id)
        return receive_page(self.session, reel_id, page_id, page)

    def take_all(self, manifest):
        for page_id in manifest.page_ids:
            self.assertIs(self.deliver(manifest.reel_id, page_id, device_id=manifest.device_id),
                          ReleaseOutcome.RELEASED)

    def assert_pending(self, reel_id):
        with self.assertRaises(PendingReel):
            get_report(self.session, reel_id)
        evidence = {'snapshot': load_snapshot(self.session.ledger, reel_id),
                    'receipt': read_first_receipt(self.session.ledger, reel_id)}
        self.assertEqual(evidence, {'snapshot': None, 'receipt': None})
        return evidence
