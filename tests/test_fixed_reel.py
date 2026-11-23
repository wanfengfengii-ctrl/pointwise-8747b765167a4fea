from dataclasses import replace
import sqlite3

from archive.publish import finalize
from custody.device import ReleaseOutcome, page_checksum
from custody.evidence import list_page_evidence, load_manifest
from custody.receive import receive_page
from reading.framing import Dialect
from registry.identity import Conflict, InvalidMaterial, PendingReel, RegistrationMode
from registry.intake import find_registered_reel, register
from registry.session import initialize
from review.report import get_report
from tests.support import BATCH, DEVICE, WorkdayCase, observe, page_origins, report_shape


class FixedReelTests(WorkdayCase):
    def test_publish_known_dialect(self):
        pages = (('P9', '010702'), ('P3', '0304'))
        manifest = self.stage('fixed', pages, mode=RegistrationMode.FIXED_SINGLE)
        first_registration = find_registered_reel(self.session.ledger, 'fixed')
        repeated = register(self.session, 'fixed', DEVICE, RegistrationMode.FIXED_SINGLE)
        self.assertEqual(repeated, first_registration)
        self.assertEqual(repeated.batch_id, BATCH)
        first_page = self.session.device.current_page(DEVICE, 'fixed', 'P9')
        self.take_all(manifest)

        first = finalize(self.session, 'fixed', Dialect.SINGLE)
        expected = {'dialect': 'single', 'values': [7, 7], 'ranges': [[0, 2], [2, 5]],
                    'total': 14, 'byte_count': 5}
        actual = report_shape(first)
        self.assertEqual(actual, expected)
        expected_origins = [[['P9', 0, 2]], [['P9', 2, 3], ['P3', 0, 2]]]
        self.assertEqual(page_origins(first.snapshot.records), expected_origins)
        self.assertEqual(first.receipt.reel_id, 'fixed')
        self.assertGreater(first.receipt.receipt_id, 0)
        self.assertEqual(first, finalize(self.session, 'fixed', Dialect.SINGLE))
        self.assertIs(receive_page(self.session, 'fixed', 'P9', first_page), ReleaseOutcome.RELEASED)
        changed = bytes.fromhex('010802')
        with self.assertRaises(Conflict):
            receive_page(self.session, 'fixed', 'P9', replace(
                first_page, payload=changed, checksum=page_checksum(changed),
            ))
        retrieved = get_report(self.session, 'fixed')
        self.assertEqual(retrieved, first)
        observe('v1', {'pages': pages, 'registered_dialect': 'single'}, expected, actual,
                {'origins': expected_origins, 'same_receipt': True},
                {'origins': page_origins(retrieved.snapshot.records),
                 'same_receipt': retrieved.receipt == first.receipt},
                ['custody/coverage.py', 'reading/records.py', 'archive/publish.py', 'review/report.py'],
                '01 consumes 07, then 02 consumes 03 and 04; the second row crosses P9/P3')

    def test_initialize_preserves_registration_pages_and_archive(self):
        manifest = self.stage('kept', (('P', '0107'),), mode=RegistrationMode.FIXED_SINGLE)
        previous = find_registered_reel(self.session.ledger, 'kept')
        self.take_all(manifest)
        pages_before = list_page_evidence(self.session.ledger, 'kept')
        published = finalize(self.session, 'kept', Dialect.SINGLE)
        initialize(self.session)
        self.reopen(batch='evening')
        initialize(self.session)
        replay = register(self.session, 'kept', DEVICE, RegistrationMode.FIXED_SINGLE)
        self.assertEqual(replay, previous)
        self.assertEqual(replay.batch_id, BATCH)
        self.assertEqual(list_page_evidence(self.session.ledger, 'kept'), pages_before)
        self.assertIs(self.session.device.release_status(DEVICE, 'kept', 'P'), ReleaseOutcome.RELEASED)
        self.assertEqual(get_report(self.session, 'kept'), published)

    def test_registration_conflict_keeps_original_device_and_mode(self):
        manifest = self.stage('reserved', (('P', '0107'),), mode=RegistrationMode.FIXED_SINGLE)
        original = find_registered_reel(self.session.ledger, 'reserved')
        self.reopen(batch='evening')
        for device, mode in (('other-reader', RegistrationMode.FIXED_SINGLE),
                             (DEVICE, RegistrationMode.FIXED_DOUBLE),
                             (DEVICE, RegistrationMode.TAIL)):
            with self.subTest(device=device, mode=mode):
                with self.assertRaises(Conflict):
                    register(self.session, 'reserved', device, mode)
                self.assertEqual(find_registered_reel(self.session.ledger, 'reserved'), original)
        self.take_all(manifest)
        self.assertEqual(finalize(self.session, 'reserved', Dialect.SINGLE).snapshot.total, 7)

    def test_reject_wrong_page(self):
        manifest = self.stage('source', (('P1', '0107'),), mode=RegistrationMode.FIXED_SINGLE)
        self.stage('other-reel', (('P1', '0108'),), mode=RegistrationMode.FIXED_SINGLE)
        self.stage('other-device', (('P1', '0109'),), mode=RegistrationMode.FIXED_SINGLE,
                   device_id='reader-b')
        current = self.session.device.current_page(DEVICE, 'source', 'P1')
        foreign = self.session.device.current_page('reader-b', 'other-device', 'P1')
        for target, material in (
            ('source', replace(current, payload=b'\x01\x08')),
            ('source', replace(current, ticket=current.ticket + 10)),
            ('source', foreign),
            ('other-reel', current),
        ):
            with self.subTest(target=target, ticket=material.ticket):
                with self.assertRaises(InvalidMaterial):
                    receive_page(self.session, target, 'P1', material)
                self.assertEqual(list_page_evidence(self.session.ledger, target), ())
                self.assertIsNone(load_manifest(self.session.ledger, target))
                self.assertEqual(self.session.device.current_page(DEVICE, 'source', 'P1'), current)
                self.assertEqual(self.session.device.current_page('reader-b', 'other-device', 'P1'), foreign)
        outcome = self.session.device.release_status(DEVICE, 'source', 'P1')
        self.assertIs(outcome, ReleaseOutcome.CURRENT)
        wrong_reel_count = len(list_page_evidence(self.session.ledger, 'other-reel'))
        self.take_all(manifest)
        self.assertEqual(finalize(self.session, 'source', Dialect.SINGLE).snapshot.total, 7)
        observe('v2', {'current_reel': 'source', 'other_reel': 'other-reel', 'shared_page_id': 'P1'},
                {'wrong_reel_intake_count': 0}, {'wrong_reel_intake_count': wrong_reel_count},
                {'original_status': 'current'}, {'original_status': outcome.value},
                ['custody/device.py', 'custody/receive.py', 'custody/evidence.py'],
                'physical current ticket belongs to source on reader-a')

    def test_intake_failure_keeps_device_current(self):
        self.stage('blocked', (('P1', '0107'),), mode=RegistrationMode.FIXED_SINGLE)
        db = self.session.ledger
        with db:
            db.execute('''
                CREATE TRIGGER block_intake BEFORE INSERT ON custody_pages
                WHEN NEW.reel_id = 'blocked'
                BEGIN SELECT RAISE(ABORT, 'intake write rejected'); END
            ''')
        current = self.session.device.current_page(DEVICE, 'blocked', 'P1')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'intake write rejected'):
            self.deliver('blocked', 'P1')
        self.assertIsNone(load_manifest(db, 'blocked'))
        self.assertEqual(list_page_evidence(db, 'blocked'), ())
        self.assertEqual(self.session.device.current_page(DEVICE, 'blocked', 'P1'), current)
        with db:
            db.execute('DROP TRIGGER block_intake')
        self.assertIs(self.deliver('blocked', 'P1'), ReleaseOutcome.RELEASED)
        self.assertEqual(finalize(self.session, 'blocked', Dialect.SINGLE).snapshot.total, 7)

    def test_empty_coverage_requires_manifest_and_distinguishes_empty_page(self):
        register(self.session, 'empty', DEVICE, RegistrationMode.FIXED_SINGLE)
        with self.assertRaises(PendingReel):
            finalize(self.session, 'empty', Dialect.SINGLE)
        self.assert_pending('empty')
        self.session.device.load_reel(DEVICE, 'empty', ())
        empty = finalize(self.session, 'empty', Dialect.SINGLE)
        self.assertEqual((empty.snapshot.byte_count, empty.snapshot.records, empty.snapshot.total), (0, (), 0))
        self.stage('zero-page', (('P', ''),), mode=RegistrationMode.FIXED_SINGLE)
        with self.assertRaises(PendingReel):
            finalize(self.session, 'zero-page', Dialect.SINGLE)
        self.assertIs(self.deliver('zero-page', 'P'), ReleaseOutcome.RELEASED)
        zero_page = finalize(self.session, 'zero-page', Dialect.SINGLE)
        self.assertEqual((zero_page.snapshot.byte_count, zero_page.snapshot.records, zero_page.snapshot.total),
                         (0, (), 0))
