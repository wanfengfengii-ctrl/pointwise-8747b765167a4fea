import sqlite3

from archive.publish import finalize
from archive.receipts import read_first_receipt
from archive.seal import TailSeal
from custody.device import DevicePageUnavailable, ReleaseOutcome
from custody.evidence import list_page_evidence
from custody.resume import resume_page
from reading.framing import Dialect, InvalidReading
from registry.identity import Conflict, InvalidMaterial, PendingReel
from review.handover import handover_list
from review.report import get_report
from tests.support import (
    BATCH, DEVICE, DOUBLE_ORIGINS, DOUBLE_SHAPE, SINGLE_SHAPE, SIX_ONES,
    WorkdayCase, observe, page_origins, report_shape,
)


class TailSealTests(WorkdayCase):
    def test_late_dialect(self):
        observed = {}
        origins = {}
        expected_origins = {
            'single': [[['P1', 0, 2]], [['P2', 0, 2]], [['P2', 2, 4]]],
            'double': DOUBLE_ORIGINS,
        }
        for dialect, expected in ((Dialect.SINGLE, SINGLE_SHAPE), (Dialect.DOUBLE, DOUBLE_SHAPE)):
            reel_id = dialect.value
            with self.subTest(reel=reel_id):
                manifest = self.stage(reel_id, SIX_ONES)
                self.assert_pending(reel_id)
                self.take_all(manifest)
                with self.assertRaises(PendingReel):
                    finalize(self.session, reel_id)
                self.assert_pending(reel_id)
                with self.assertRaises(InvalidMaterial):
                    finalize(self.session, reel_id, dialect)
                first = finalize(self.session, reel_id, TailSeal(reel_id, manifest.page_ids, dialect))
                self.assertEqual(report_shape(first), expected)
                self.assertEqual(page_origins(first.snapshot.records), expected_origins[reel_id])
                self.assertEqual(first, get_report(self.session, reel_id))
                observed[reel_id] = report_shape(first)
                origins[reel_id] = page_origins(first.snapshot.records)
        if len(observed) == 2:
            observe('v3', {'pages': SIX_ONES, 'selected': ['single', 'double']},
                    {'single': SINGLE_SHAPE, 'double': DOUBLE_SHAPE}, observed,
                    expected_origins, origins,
                    ['custody/coverage.py', 'archive/admission.py', 'reading/records.py', 'reading/samples.py'],
                    'six 01 bytes give three 2-byte frames or two 3-byte frames')

    def test_resume_after_release(self):
        manifest = self.stage('resumed', SIX_ONES)
        seal = TailSeal('resumed', manifest.page_ids, Dialect.DOUBLE)
        self.session.device.schedule_release_loss(DEVICE, 'resumed', 'P1')
        self.assertIs(self.deliver('resumed', 'P1'), ReleaseOutcome.UNKNOWN)
        self.assert_pending('resumed')
        self.reopen()
        with self.assertRaises(DevicePageUnavailable):
            self.session.device.current_page(DEVICE, 'resumed', 'P1')
        current_page = self.session.device.current_page(DEVICE, 'resumed', 'P2').page_id
        self.assertEqual(current_page, 'P2')
        self.assertIs(self.session.device.release_status(DEVICE, 'resumed', 'P1'), ReleaseOutcome.RELEASED)
        with self.assertRaises(PendingReel):
            self.deliver('resumed', 'P2')
        confirmation = resume_page(self.session, 'resumed', 'P1')
        self.assertIs(confirmation, ReleaseOutcome.RELEASED)
        self.assertIs(self.deliver('resumed', 'P2'), ReleaseOutcome.RELEASED)
        self.assert_pending('resumed')
        first = finalize(self.session, 'resumed', seal)
        actual = report_shape(first)
        self.assertEqual(actual, DOUBLE_SHAPE,
                         f'WRONG_DIALECT_REPORT actual={actual!r} expected={DOUBLE_SHAPE!r}')
        self.assertEqual(page_origins(first.snapshot.records), DOUBLE_ORIGINS)

        straight = self.stage('straight', SIX_ONES)
        self.take_all(straight)
        uninterrupted = finalize(self.session, 'straight', TailSeal('straight', straight.page_ids, Dialect.DOUBLE))
        self.assertEqual(report_shape(uninterrupted), actual)
        self.assertEqual(uninterrupted.snapshot.records, first.snapshot.records)
        self.assertNotEqual(uninterrupted.receipt.receipt_id, first.receipt.receipt_id)

        self.reopen(online=False)
        replay = finalize(self.session, 'resumed', seal)
        self.assertEqual(replay, first)
        self.assertEqual(get_report(self.session, 'resumed'), first)
        with self.assertRaises(Conflict):
            finalize(self.session, 'resumed', TailSeal('resumed', manifest.page_ids, Dialect.SINGLE))
        self.assertEqual(read_first_receipt(self.session.ledger, 'resumed'), first.receipt)
        observe('v4', {'pages': SIX_ONES, 'lost_reply': 'P1', 'next_page': current_page,
                       'tail_dialect': 'double'}, DOUBLE_SHAPE, actual,
                {'exit': 'released', 'origins': DOUBLE_ORIGINS, 'same_receipt': True},
                {'exit': confirmation.value, 'origins': page_origins(replay.snapshot.records),
                 'same_receipt': replay.receipt == first.receipt},
                ['custody/device.py', 'custody/resume.py', 'custody/coverage.py', 'archive/admission.py'],
                'first double record consumes P1[0,2) plus P2[0,1); independent uninterrupted reel agrees')

    def test_retry_when_exit_did_not_apply_and_device_is_temporarily_unavailable(self):
        manifest = self.stage('retry', SIX_ONES)
        self.session.device.schedule_release_loss(DEVICE, 'retry', 'P1', phase='before')
        self.assertIs(self.deliver('retry', 'P1'), ReleaseOutcome.UNKNOWN)
        self.assertEqual(self.session.device.current_page(DEVICE, 'retry', 'P1').page_id, 'P1')
        self.reopen(online=False)
        self.assertIs(resume_page(self.session, 'retry', 'P1'), ReleaseOutcome.UNKNOWN)
        self.assert_pending('retry')
        self.reopen()
        self.assertIs(self.session.device.release_status(DEVICE, 'retry', 'P1'), ReleaseOutcome.CURRENT)
        self.assertIs(resume_page(self.session, 'retry', 'P1'), ReleaseOutcome.RELEASED)
        self.assertEqual(len(list_page_evidence(self.session.ledger, 'retry')), 1)
        self.assertIs(self.deliver('retry', 'P2'), ReleaseOutcome.RELEASED)
        self.assertEqual(report_shape(finalize(self.session, 'retry',
                                              TailSeal('retry', manifest.page_ids, Dialect.DOUBLE))),
                         DOUBLE_SHAPE)

    def test_reject_invalid_selection(self):
        manifest = self.stage('order', SIX_ONES)
        good = TailSeal('order', manifest.page_ids, Dialect.DOUBLE)
        self.assertIs(self.deliver('order', 'P1'), ReleaseOutcome.RELEASED)
        with self.assertRaises(PendingReel):
            finalize(self.session, 'order', good)
        missing = self.assert_pending('order')
        self.assertIs(self.deliver('order', 'P2'), ReleaseOutcome.RELEASED)
        rejected = {'missing_page': missing}
        for label, bad in (
            ('wrong_reel', TailSeal('different', manifest.page_ids, Dialect.DOUBLE)),
            ('manifest_order', TailSeal('order', ('P2', 'P1'), Dialect.DOUBLE)),
            ('manifest_count', TailSeal('order', ('P1',), Dialect.DOUBLE)),
        ):
            with self.subTest(label=label):
                with self.assertRaises(InvalidMaterial):
                    finalize(self.session, 'order', bad)
                rejected[label] = self.assert_pending('order')
        self.assertEqual(report_shape(finalize(self.session, 'order', good)), DOUBLE_SHAPE)

        invalid = self.stage('truncated', (('only', '010101'),))
        self.take_all(invalid)
        with self.assertRaises(InvalidReading) as failure:
            finalize(self.session, 'truncated', TailSeal('truncated', invalid.page_ids, Dialect.SINGLE))
        self.assertEqual(failure.exception.offset, 2)
        rejected['selected_invalid'] = self.assert_pending('truncated')
        expected = {label: {'snapshot': None, 'receipt': None} for label in (
            'missing_page', 'wrong_reel', 'manifest_order', 'manifest_count', 'selected_invalid',
        )}
        self.assertEqual(rejected, expected)
        observe('v5', {'physical_order': ['P1', 'P2'], 'reversed_seal': ['P2', 'P1'],
                       'single_payload': '010101'}, expected, rejected, {'invalid_at': 2},
                {'invalid_at': failure.exception.offset},
                ['archive/seal.py', 'archive/admission.py', 'archive/publish.py'],
                'ordered identities must match; a second single-byte record starts at 2 and lacks its sample')

    def test_bad_marker_and_unavailable_payload_prevent_publication(self):
        invalid = self.stage('bad-marker', (('P', '01020304'),))
        self.take_all(invalid)
        with self.assertRaises(InvalidReading) as failure:
            finalize(self.session, 'bad-marker', TailSeal('bad-marker', invalid.page_ids, Dialect.DOUBLE))
        self.assertEqual(failure.exception.offset, 3)
        self.assert_pending('bad-marker')

        missing = self.stage('lost-payload', SIX_ONES)
        self.take_all(missing)
        with self.session.ledger as db:
            db.execute("UPDATE custody_pages SET payload = NULL WHERE reel_id = 'lost-payload' AND page_id = 'P1'")
        with self.assertRaisesRegex(PendingReel, 'original payload unavailable'):
            finalize(self.session, 'lost-payload', TailSeal('lost-payload', missing.page_ids, Dialect.DOUBLE))
        self.assert_pending('lost-payload')

    def test_publish_rollback(self):
        old = self.stage('old', SIX_ONES)
        self.take_all(old)
        first_old = finalize(self.session, 'old', TailSeal('old', old.page_ids, Dialect.DOUBLE))
        new = self.stage('new', SIX_ONES)
        self.take_all(new)
        new_seal = TailSeal('new', new.page_ids, Dialect.DOUBLE)
        evidence_before = list_page_evidence(self.session.ledger, 'new')
        with self.session.ledger as db:
            db.execute('''
                CREATE TRIGGER fail_new_receipt BEFORE INSERT ON archive_receipts
                WHEN NEW.reel_id = 'new' AND EXISTS (
                    SELECT 1 FROM archive_origins WHERE reel_id = NEW.reel_id
                )
                BEGIN SELECT RAISE(ABORT, 'receipt ledger rejected new'); END
            ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt ledger rejected new'):
            finalize(self.session, 'new', new_seal)
        self.reopen(online=False)
        pending = self.assert_pending('new')
        self.assertEqual(list_page_evidence(self.session.ledger, 'new'), evidence_before)
        self.assertEqual(get_report(self.session, 'old'), first_old)
        handover = handover_list(self.session, BATCH)
        self.assertEqual((handover.ready, handover.pending), (('old',), ('new',)))
        db = self.session.ledger
        with db:
            for table in ('archive_rows', 'archive_origins'):
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE reel_id = ?',
                                            ('new',)).fetchone()[0], 0)
            db.execute('DROP TRIGGER fail_new_receipt')
        signed = finalize(self.session, 'new', new_seal)
        self.reopen(online=False)
        self.assertEqual(report_shape(signed), DOUBLE_SHAPE)
        self.assertEqual(finalize(self.session, 'new', new_seal), signed)
        self.assertEqual(get_report(self.session, 'new'), signed)
        self.assertNotEqual(signed.receipt, first_old.receipt)
        self.assertEqual(get_report(self.session, 'old'), first_old)
        self.assertEqual(handover_list(self.session, BATCH).ready, ('old', 'new'))
        count = self.session.ledger.execute(
            'SELECT count(*) FROM archive_receipts WHERE reel_id = ?', ('new',),
        ).fetchone()[0]
        self.assertEqual(count, 1)
        observe('v6', {'fault': 'SQLite rejects the new receipt after origins are inserted'},
                {'snapshot': None, 'receipt': None}, pending,
                {'pending_after_failure': ['new'], 'receipts_after_retry': 1},
                {'pending_after_failure': list(handover.pending), 'receipts_after_retry': count},
                ['archive/publish.py', 'archive/store.py', 'archive/receipts.py', 'review/handover.py'],
                'one SQLite transaction covers report, origins and first receipt')
