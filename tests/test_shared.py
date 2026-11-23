from archive.publish import finalize, issue_shared
from archive.receipts import FirstReceipt
from archive.seal import TailSeal
from reading.framing import Dialect
from registry.identity import InvalidMaterial, NotFound, PendingReel, RegistrationMode
from registry.intake import register
from registry.session import open_session
from review.reconcile import reconcile_shared
from review.report import get_report
from tests.support import DEVICE, SIX_ONES, WorkdayCase, observe, page_origins


SINGLE_THREE = (('S1', '0200'), ('S2', '01020100020001'))
SINGLE_ZERO = (('Z1', '01000101'), ('Z2', '0101'))
DOUBLE_TWO = (('D1', '0102'), ('D2', '00010200'))
DOUBLE_THREE_ONE = (('T1', '010300'), ('T2', '010100'))
SINGLE_COPY = (('C1', '010101010101'),)


class SharedPayloadTests(WorkdayCase):
    def sign(self, reel_id, pages, mode, dialect):
        manifest = self.stage(reel_id, pages, mode=mode)
        self.take_all(manifest)
        return finalize(self.session, reel_id, dialect)

    def sign_pair(self, single_pages=SINGLE_THREE, double_pages=DOUBLE_TWO,
                  single_id='single-three', double_id='double-two'):
        single = self.sign(single_id, single_pages, RegistrationMode.FIXED_SINGLE, Dialect.SINGLE)
        double = self.sign(double_id, double_pages, RegistrationMode.FIXED_DOUBLE, Dialect.DOUBLE)
        return single, double

    def test_issue_three_and_two(self):
        single, double = self.sign_pair()
        package = issue_shared(self.session, 'single-three', 'double-two')
        self.assertEqual(package['payload'], bytes.fromhex('010101010101'))
        self.assertEqual(package['receipts'], (single.receipt, double.receipt))
        self.assertIsInstance(package['receipts'][0], FirstReceipt)
        self.assertEqual(get_report(self.session, 'single-three'), single)
        self.assertEqual(get_report(self.session, 'double-two'), double)
        counts = self.session.ledger.execute(
            'SELECT (SELECT count(*) FROM archive_receipts), '
            '(SELECT count(*) FROM archive_rows), (SELECT count(*) FROM archive_origins)'
        ).fetchone()
        self.assertEqual(counts, (2, 5, 7))  # three single rows + two double rows

        self.reopen(batch='evening', online=False)
        offline = issue_shared(self.session, 'single-three', 'double-two')
        self.assertEqual(offline, package)
        single_rows, double_rows = reconcile_shared(
            offline, get_report(self.session, 'single-three'), get_report(self.session, 'double-two'))
        self.assertEqual([(start, end) for (start, end), _ in single_rows],
                         [(0, 2), (2, 4), (4, 6)])
        self.assertEqual([(start, end) for (start, end), _ in double_rows], [(0, 3), (3, 6)])
        self.assertEqual(page_origins([row for _, row in single_rows])[0],
                         [['S1', 0, 2], ['S2', 0, 1]])
        self.assertEqual(page_origins([row for _, row in double_rows])[0],
                         [['D1', 0, 2], ['D2', 0, 1]])
        observe('v9', {'single': 'single-three', 'double': 'double-two'},
                {'payload': '010101010101', 'wire': [[0, 6], [0, 6]]},
                {'payload': package['payload'].hex(),
                 'wire': [[single_rows[0][0][0], single_rows[-1][0][1]],
                          [double_rows[0][0][0], double_rows[-1][0][1]]]},
                {'single_origins': [['S1', 0, 2], ['S2', 0, 1]],
                 'double_origins': [['D1', 0, 2], ['D2', 0, 1]]},
                {'single_origins': page_origins([single_rows[0][1]])[0],
                 'double_origins': page_origins([double_rows[0][1]])[0]},
                ['archive/publish.py', 'review/reconcile.py', 'reading/framing.py'],
                'six 01 bytes read as three single rows or two double rows; origins come from the signed reports')

    def test_tail_seals_and_retired_device(self):
        single_manifest = self.stage('tail-single', SIX_ONES)
        self.take_all(single_manifest)
        single = finalize(self.session, 'tail-single',
                          TailSeal('tail-single', single_manifest.page_ids, Dialect.SINGLE))
        double_manifest = self.stage('tail-double', SIX_ONES)
        self.take_all(double_manifest)
        double = finalize(self.session, 'tail-double',
                          TailSeal('tail-double', double_manifest.page_ids, Dialect.DOUBLE))
        self.session.close()
        # Reopen without a device ledger: both machines are retired.
        self.session = open_session(self.ledger, 'night')
        self.assertIsNone(self.session.device)
        package = issue_shared(self.session, 'tail-single', 'tail-double')
        self.assertEqual(package['receipts'], (single.receipt, double.receipt))
        self.assertEqual(package['payload'], bytes.fromhex('010101010101'))
        single_rows, double_rows = reconcile_shared(
            package, get_report(self.session, 'tail-single'), get_report(self.session, 'tail-double'))
        self.assertEqual([row.value for _, row in single_rows], [1, 1, 1])
        self.assertEqual([row.value for _, row in double_rows], [2, 2])
        self.assertEqual([(start, end) for (start, end), _ in single_rows],
                         [(0, 2), (2, 4), (4, 6)])
        self.assertEqual([(start, end) for (start, end), _ in double_rows], [(0, 3), (3, 6)])

    def test_issue_zero_and_two(self):
        self.sign_pair(SINGLE_ZERO, DOUBLE_TWO, single_id='single-zero')
        package = issue_shared(self.session, 'single-zero', 'double-two')
        self.assertEqual(package['payload'], bytes.fromhex('0200000200010101'))
        single_rows, double_rows = reconcile_shared(
            package, get_report(self.session, 'single-zero'), get_report(self.session, 'double-two'))
        self.assertEqual([(start, end) for (start, end), _ in single_rows],
                         [(0, 3), (3, 6), (6, 8)])
        self.assertEqual([(start, end) for (start, end), _ in double_rows], [(0, 5), (5, 8)])
        self.assertEqual([row.value for _, row in single_rows], [0, 1, 1])
        self.assertEqual([row.value for _, row in double_rows], [2, 2])
        self.assertEqual([(row.start, row.end) for _, row in single_rows],
                         [(0, 2), (2, 4), (4, 6)])
        self.assertEqual([(row.start, row.end) for _, row in double_rows], [(0, 3), (3, 6)])

    def test_empty_both_sides_and_empty_one_side(self):
        self.sign('empty-single', (('ES', ''),), RegistrationMode.FIXED_SINGLE, Dialect.SINGLE)
        self.sign('empty-double', (('ED', ''),), RegistrationMode.FIXED_DOUBLE, Dialect.DOUBLE)
        package = issue_shared(self.session, 'empty-single', 'empty-double')
        self.assertEqual(package, {
            'receipts': (get_report(self.session, 'empty-single').receipt,
                         get_report(self.session, 'empty-double').receipt),
            'payload': b'',
        })
        self.assertEqual(reconcile_shared(package, get_report(self.session, 'empty-single'),
                                          get_report(self.session, 'empty-double')), ((), ()))

        self.sign_pair(SINGLE_THREE, DOUBLE_TWO)
        for single_id, double_id in (('empty-single', 'double-two'),
                                     ('single-three', 'empty-double')):
            with self.subTest(single=single_id, double=double_id):
                with self.assertRaises(InvalidMaterial):
                    issue_shared(self.session, single_id, double_id)

    def test_no_common_encoding(self):
        self.sign_pair(SINGLE_ZERO, DOUBLE_THREE_ONE, single_id='single-zero',
                       double_id='double-three-one')
        with self.assertRaises(InvalidMaterial):
            issue_shared(self.session, 'single-zero', 'double-three-one')

    def test_unknown_and_pending(self):
        self.sign_pair()
        register(self.session, 'waiting', DEVICE, RegistrationMode.FIXED_SINGLE)
        with self.assertRaises(NotFound):
            issue_shared(self.session, 'no-such-reel', 'double-two')
        with self.assertRaises(NotFound):
            issue_shared(self.session, 'single-three', 'no-such-reel')
        with self.assertRaises(PendingReel):
            issue_shared(self.session, 'waiting', 'double-two')
        with self.assertRaises(PendingReel):
            issue_shared(self.session, 'single-three', 'waiting')

    def test_wrong_side_and_same_reel(self):
        self.sign_pair()
        with self.assertRaises(InvalidMaterial):
            issue_shared(self.session, 'double-two', 'single-three')
        with self.assertRaises(InvalidMaterial):
            issue_shared(self.session, 'single-three', 'single-three')
        with self.assertRaises(InvalidMaterial):
            issue_shared(self.session, 'double-two', 'double-two')

    def test_reconcile_rejects_wrong_payload(self):
        self.sign_pair()
        package = issue_shared(self.session, 'single-three', 'double-two')
        single_report = get_report(self.session, 'single-three')
        double_report = get_report(self.session, 'double-two')
        original = double_report.snapshot
        bad_payloads = {
            'original_double_payload': bytes.fromhex('010200010200'),
            'truncated': package['payload'][:-1],
            'bad_marker': bytes.fromhex('010103010101'),
            'extra_row': package['payload'] + bytes.fromhex('0101'),
            'wrong_rows': bytes.fromhex('0200000200020202'),
        }
        for label, payload in bad_payloads.items():
            with self.subTest(label=label):
                with self.assertRaises(InvalidMaterial):
                    reconcile_shared({'receipts': package['receipts'], 'payload': payload},
                                     single_report, double_report)
        self.assertEqual(get_report(self.session, 'double-two').snapshot, original)

    def test_reconcile_rejects_misbinding(self):
        self.sign_pair()
        self.sign('single-three-copy', SINGLE_COPY, RegistrationMode.FIXED_SINGLE, Dialect.SINGLE)
        package = issue_shared(self.session, 'single-three', 'double-two')
        single_report = get_report(self.session, 'single-three')
        double_report = get_report(self.session, 'double-two')
        copy_report = get_report(self.session, 'single-three-copy')
        self.assertEqual([row.value for row in copy_report.snapshot.records],
                         [row.value for row in single_report.snapshot.records])
        self.assertNotEqual(copy_report.receipt, single_report.receipt)

        swapped = (package['receipts'][1], package['receipts'][0])
        for label, receipts, single, double in (
            ('copy_single', package['receipts'], copy_report, double_report),
            ('copy_double', package['receipts'], single_report, copy_report),
            ('swapped_receipts', swapped, single_report, double_report),
            ('single_as_double', package['receipts'], double_report, single_report),
        ):
            with self.subTest(label=label):
                with self.assertRaises(InvalidMaterial):
                    reconcile_shared({'receipts': receipts, 'payload': package['payload']},
                                     single, double)

    def test_reconcile_rejects_duplicate_reports(self):
        self.sign_pair()
        package = issue_shared(self.session, 'single-three', 'double-two')
        double_report = get_report(self.session, 'double-two')
        # Both slots filled with the double publication: wrong side for the
        # first slot, but also not two independent reels.
        with self.assertRaises(InvalidMaterial):
            reconcile_shared(package, double_report, double_report)
        single_report = get_report(self.session, 'single-three')
        with self.assertRaises(InvalidMaterial):
            reconcile_shared(package, single_report, single_report)

    def test_reconcile_rejects_bad_shape(self):
        self.sign_pair()
        package = issue_shared(self.session, 'single-three', 'double-two')
        single_report = get_report(self.session, 'single-three')
        double_report = get_report(self.session, 'double-two')
        bad = {
            'not_mapping': ['x', 'y'],
            'extra_key': {'receipts': package['receipts'], 'payload': package['payload'],
                          'other': 1},
            'payload_not_bytes': {'receipts': package['receipts'], 'payload': 3},
            'receipts_not_tuple': {'receipts': list(package['receipts']), 'payload': b''},
            'receipts_short': {'receipts': package['receipts'][:1], 'payload': b''},
            'receipts_not_receipt': {'receipts': (1, 2), 'payload': b''},
        }
        for label, value in bad.items():
            with self.subTest(label=label):
                with self.assertRaises(InvalidMaterial):
                    reconcile_shared(value, single_report, double_report)
        with self.assertRaises(InvalidMaterial):
            reconcile_shared(package, single_report, None)


