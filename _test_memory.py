"""Section 6 — Memory System simulation."""
import asyncio, tempfile
from pathlib import Path

async def run():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        import app.config as cfg
        cfg.settings.memory_path = str(Path(tmp) / 'memory')

        from app.skills.memory import MemorySkill

        ms = MemorySkill()

        # 1. write_current_task / read_current_task
        r = ms.write_current_task('Draft the quarterly report')
        assert r.success, f'write_current_task failed: {r.error}'
        r2 = ms.read_current_task()
        assert r2.success
        assert 'quarterly report' in (r2.output or '')
        print(f'Current task write/read: PASSED (output={r2.output[:40]}...)')

        # 2. write_journal / read_recent_journal
        r = ms.write_journal(date='2026-03-19', content='Worked on funding applications today.')
        assert r.success
        r2 = ms.read_recent_journal(n_days=3)
        assert r2.success
        print(f'Journal write/read: PASSED')

        # 3. log_session_summary
        r = ms.log_session_summary(session_id='abc123', summary='Discussed board meeting agenda.')
        assert r.success
        print(f'Session summary logged: PASSED')

        # 4. write_prefetch_cache / read_prefetch_cache
        r = ms.write_prefetch_cache('## Q: charity number\n\nAnswer: SCO012345')
        assert r.success
        r2 = ms.read_prefetch_cache()
        assert r2.success
        assert 'SCO012345' in (r2.output or '')
        print(f'Prefetch cache write/read: PASSED')

        # 5. get_system_prompt (returns something from soul/agents files)
        prompt = await ms.get_system_prompt()
        assert isinstance(prompt, str)
        print(f'System prompt assembled: {len(prompt)} chars')

        # 6. update_memory section
        from app.skills.memory import LONG_TERM_MEMORY_FILE, MEMORY_DIR
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        if not LONG_TERM_MEMORY_FILE.exists():
            LONG_TERM_MEMORY_FILE.write_text('# CDCN Agent Memory\n\n## Key decisions\n\nInitial entry.\n')
        r = ms.update_memory(section='Key decisions', content='Approved new funding strategy.')
        assert r.success
        print(f'Memory update: PASSED')

        # 7. Dream journal
        r = ms.write_dream_journal(date='2026-03-19', content='Overnight consolidation complete.')
        assert r.success
        print(f'Dream journal: PASSED')

        print('\nSECTION 6 Memory System: ALL PASSED')

asyncio.run(run())
