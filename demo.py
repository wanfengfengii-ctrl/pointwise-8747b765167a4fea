from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.publish import finalize, issue_shared
from archive.seal import TailSeal
from custody.device import DevicePageUnavailable, ReleaseOutcome
from custody.receive import receive_page
from custody.resume import resume_page
from reading.framing import Dialect
from registry.identity import ExchangeError, PendingReel, RegistrationMode
from registry.intake import register
from registry.session import open_session
from review.handover import handover_list
from review.reconcile import audit_range, reconcile_shared
from review.report import get_report


def require(actual, expected) -> None:
    if actual != expected:
        raise RuntimeError(f'actual={actual!r}, expected={expected!r}')


def collect_reel(ledger, device_ledger, batch, device_id, material):
    reel_id = material['reel_id']
    mode = RegistrationMode(material['mode'])
    with open_session(ledger, batch, device_path=device_ledger) as session:
        register(session, reel_id, device_id, mode)
        if 'pages' not in material:
            return None
        pages = [(page_id, bytes.fromhex(value)) for page_id, value in material['pages']]
        manifest = session.device.load_reel(device_id, reel_id, pages)
        lost_page = material.get('lose_release_for')
        if lost_page is not None:
            session.device.schedule_release_loss(device_id, reel_id, lost_page)

    for page_id in manifest.page_ids:
        with open_session(ledger, batch, device_path=device_ledger) as session:
            page = session.device.current_page(device_id, reel_id, page_id)
            outcome = receive_page(session, reel_id, page_id, page)
        if page_id == lost_page:
            require(outcome, ReleaseOutcome.UNKNOWN)
            with open_session(ledger, batch, device_path=device_ledger) as session:
                try:
                    session.device.current_page(device_id, reel_id, page_id)
                except DevicePageUnavailable:
                    pass
                else:
                    raise RuntimeError('exited page is still readable')
                require(resume_page(session, reel_id, page_id), ReleaseOutcome.RELEASED)
        else:
            require(outcome, ReleaseOutcome.RELEASED)

    with open_session(ledger, batch) as session:
        try:
            get_report(session, reel_id)
        except PendingReel:
            pass
        else:
            raise RuntimeError('report became visible before publication')
        dialect = Dialect(material['dialect'])
        authorization = TailSeal(reel_id, manifest.page_ids, dialect) if mode is RegistrationMode.TAIL else dialect
        report = finalize(session, reel_id, authorization)
        require(report.snapshot.dialect, dialect)
        return report


def _shared_rows(rows):
    return [{'wire': [start, end],
             'original': [row.start, row.end, row.value],
             'sources': [[source.page_id, source.start, source.end] for source in row.sources]}
            for (start, end), row in rows]


def show_shared(ledger, batch, sample):
    """Issue one shared payload from the sample's signed pairs and check it.

    The device ledger is already closed.  Issuance runs in a read-only
    connection that is then closed, the night shift fetches both reports on its
    own in a second connection that is also closed, and acceptance then runs
    only on the received package and those two reports -- with no device and no
    open archive handle.
    """
    pairs = sample.get('shared_pairs', [])
    issued = {}
    with open_session(ledger, batch) as session:
        for pair in pairs:
            single_id, double_id = pair['single'], pair['double']
            try:
                issued[(single_id, double_id)] = issue_shared(session, single_id, double_id)
            except ExchangeError:
                if not pair.get('expect_reject'):
                    raise
                issued[(single_id, double_id)] = None

    reports = {}
    with open_session(ledger, batch) as session:
        for pair in pairs:
            if issued[(pair['single'], pair['double'])] is None:
                continue
            reports[(pair['single'], pair['double'])] = (
                get_report(session, pair['single']), get_report(session, pair['double']))

    # Both connections are now closed: acceptance is offline.
    for pair in pairs:
        key = (pair['single'], pair['double'])
        package = issued[key]
        if package is None:
            print(json.dumps({'shared': list(key), 'result': 'rejected'}))
            continue
        if pair.get('expect_reject'):
            raise RuntimeError('shared pair was expected to be rejected')
        single_report, double_report = reports[key]
        single_rows, double_rows = reconcile_shared(package, single_report, double_report)
        actual = {'payload': package['payload'].hex(),
                  'single': _shared_rows(single_rows),
                  'double': _shared_rows(double_rows)}
        require(actual, pair['expected'])
        print(json.dumps({
            'shared': list(key),
            'receipts': [asdict(receipt) for receipt in package['receipts']],
            **actual,
        }))


def run_workday(sample, directory):
    ledger = directory / 'exchange.sqlite'
    device_ledger = directory / 'device.sqlite'
    batch = sample['batch_id']
    reports = {}
    for reel in sample['reels']:
        report = collect_reel(ledger, device_ledger, batch, sample['device_id'], reel)
        if report is not None:
            reports[reel['reel_id']] = report

    with open_session(ledger, 'evening') as session:
        for reel in sample['reels']:
            reel_id = reel['reel_id']
            if reel_id not in reports:
                continue
            original = get_report(session, reel_id)
            require(original, reports[reel_id])
            snapshot = original.snapshot
            actual = {'values': [row.value for row in snapshot.records],
                      'ranges': [[row.start, row.end] for row in snapshot.records],
                      'total': snapshot.total}
            require(actual, reel['expected'])
            print(json.dumps({'reel_id': reel_id, 'dialect': snapshot.dialect.value,
                              'receipt': asdict(original.receipt), **actual}))

        audit = sample['audit']
        rows = audit_range(session, audit['reel_id'], *audit['window'])
        actual_rows = json.loads(json.dumps([asdict(row) for row in rows]))
        require(actual_rows, audit['expected'])
        handover = handover_list(session, batch)
        actual_handover = {'ready': list(handover.ready), 'pending': list(handover.pending)}
        require(actual_handover, sample['handover'])
        print(json.dumps({'audit': actual_rows, 'handover': actual_handover}))

    show_shared(ledger, batch, sample)


def main() -> None:
    sample_path = Path(__file__).with_name('samples') / 'workday.json'
    sample = json.loads(sample_path.read_text(encoding='utf-8'))
    with TemporaryDirectory(prefix='reel-demo-') as directory:
        run_workday(sample, Path(directory))


if __name__ == '__main__':
    main()
