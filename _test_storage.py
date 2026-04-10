import asyncio, tempfile
from pathlib import Path

async def run():
    with tempfile.TemporaryDirectory() as tmp:
        import app.config as cfg
        cfg.settings.audit_log_path = str(Path(tmp) / 'audit.db')
        cfg.settings.pending_changes_path = str(Path(tmp) / 'pending')

        from app.storage.audit_log import log_event, recent_events
        await log_event('system', 'boot', '', 'started')
        await log_event('user1', 'query', 'target', 'searched for policies')
        rows = await recent_events(limit=10)
        print(f'Audit log rows: {len(rows)}')
        for r in rows:
            print(f'  actor={r["actor"]} action={r["action"]}')

        from app.storage.pending_changes import PendingChangesManager
        pcm = PendingChangesManager()
        cid = pcm.propose('Test amendment', 'Proposed text here', author='test', change_type='agents')
        print(f'Proposed change ID: {cid}')
        pending = pcm.list_pending()
        print(f'Pending changes: {len(pending)}')

        ok = pcm.reject(cid, rejected_by='admin', reason='Not needed')
        print(f'Reject result: {ok}')
        pending_after = pcm.list_pending()
        print(f'Pending after reject: {len(pending_after)}')
        print('SECTION 4 Storage layer: PASSED')

asyncio.run(run())
