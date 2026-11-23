from archive.publish import finalize
from archive.receipts import read_first_receipt
from archive.seal import TailSeal
from reading.framing import Dialect
from registry.identity import InvalidMaterial, NotFound, PendingReel, RegistrationMode
from registry.intake import register
from review.handover import handover_list
from review.reconcile import audit_range
from review.report import get_report
from tests.support import (
    BATCH, DEVICE, DOUBLE_ORIGINS, DOUBLE_SHAPE, SIX_ONES, WorkdayCase,
    observe, page_origins, report_shape,
)


class ReviewTests(WorkdayCase):
    def sign_double(self, reel_id):
        manifest = self.stage(reel_id, SIX_ONES)
        self.take_all(manifest)
        return finalize(self.session, reel_id, TailSeal(reel_id, manifest.page_ids, Dialect.DOUBLE))

    def test_offline_original_report(self):
        first = self.sign_double('archived')
        self.assertEqual(report_shape(first), DOUBLE_SHAPE)
        self.assertEqual(page_origins(first.snapshot.records), DOUBLE_ORIGINS)
        self.reopen(batch='evening', online=False)
        self.assertIsNone(self.session.device)
        retrieved = get_report(self.session, 'archived')
        self.assertEqual(retrieved, first)
        self.assertEqual(retrieved.receipt, read_first_receipt(self.session.ledger, 'archived'))
        self.assertEqual(page_origins(retrieved.snapshot.records), DOUBLE_ORIGINS)
        with self.assertRaises(NotFound):
            get_report(self.session, 'unregistered')
        observe('v7', {'device_online': False, 'reel_id': 'archived'}, DOUBLE_SHAPE,
                report_shape(retrieved), {'origins': DOUBLE_ORIGINS, 'same_receipt': True},
                {'origins': page_origins(retrieved.snapshot.records),
                 'same_receipt': retrieved.receipt == first.receipt},
                ['archive/store.py', 'archive/receipts.py', 'review/report.py'],
                'first archived receipt and two hand-mapped page slices remain readable after reopening')

    def test_range_and_handover(self):
        first = self.sign_double('z-ready')
        register(self.session, 'z-pending', DEVICE, RegistrationMode.TAIL)
        self.sign_double('a-ready')
        register(self.session, 'a-pending', DEVICE, RegistrationMode.TAIL)
        self.reopen(batch='other-batch')
        other = self.sign_double('other-ready')
        self.reopen(batch='evening', online=False)

        rows = audit_range(self.session, 'z-ready', 2, 3)
        self.assertEqual(rows, first.snapshot.records[:1])
        self.assertEqual([(row.start, row.end, row.value) for row in rows], [(0, 3, 2)])
        self.assertEqual(page_origins(rows), DOUBLE_ORIGINS[:1])
        adjacent = audit_range(self.session, 'z-ready', 3, 4)
        self.assertEqual(adjacent, first.snapshot.records[1:])
        self.assertEqual([(row.start, row.end, row.value) for row in adjacent], [(3, 6, 2)])
        self.assertEqual(audit_range(self.session, 'z-ready', 2, 2), ())
        self.assertEqual(audit_range(self.session, 'z-ready', 6, 6), ())
        self.assertEqual(audit_range(self.session, 'z-ready', 0, 6), first.snapshot.records)
        for start, end in ((-1, 2), (4, 3), (0, 7), (0.0, 1), (True, 2)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(InvalidMaterial):
                    audit_range(self.session, 'z-ready', start, end)
        with self.assertRaises(NotFound):
            audit_range(self.session, 'unknown', 0, 0)
        with self.assertRaises(PendingReel):
            audit_range(self.session, 'z-pending', 0, 0)

        handover = handover_list(self.session, BATCH)
        self.assertEqual(handover.ready, ('z-ready', 'a-ready'))
        self.assertEqual(handover.pending, ('z-pending', 'a-pending'))
        self.assertEqual(get_report(self.session, handover.ready[0]), first)
        for reel_id in handover.pending:
            self.assert_pending(reel_id)
        separate = handover_list(self.session, 'other-batch')
        self.assertEqual((separate.ready, separate.pending), (('other-ready',), ()))
        self.assertEqual(get_report(self.session, separate.ready[0]), other)
        with self.assertRaises(NotFound):
            handover_list(self.session, 'absent')

        observe('v8', {'window': [2, 3], 'batch': BATCH}, {'rows': [[0, 3, 2]]},
                {'rows': [[row.start, row.end, row.value] for row in rows]},
                {'origins': DOUBLE_ORIGINS[:1], 'ready': ['z-ready', 'a-ready'],
                 'pending': ['z-pending', 'a-pending']},
                {'origins': page_origins(rows), 'ready': list(handover.ready),
                 'pending': list(handover.pending)},
                ['review/ranges.py', 'review/reconcile.py', 'registry/worklist.py', 'review/handover.py'],
                'positive-length overlap with [2,3) picks [0,3); registration order controls each list')

    def test_origin_ignores_empty_physical_pages(self):
        manifest = self.stage('empty-slices', (('E1', ''), ('P1', '0101'), ('E2', ''),
                                             ('P2', '01010101'), ('E3', '')))
        self.take_all(manifest)
        first = finalize(self.session, 'empty-slices', TailSeal('empty-slices', manifest.page_ids, Dialect.DOUBLE))
        self.assertEqual(report_shape(first), DOUBLE_SHAPE)
        self.assertEqual(page_origins(first.snapshot.records), DOUBLE_ORIGINS)
        self.reopen(online=False)
        self.assertEqual(get_report(self.session, 'empty-slices'), first)
        self.assertEqual(audit_range(self.session, 'empty-slices', 2, 3), first.snapshot.records[:1])
