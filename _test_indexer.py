"""Section 8 — Document Indexer offline simulation."""
import asyncio, tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

async def run():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        import app.config as cfg
        cfg.settings.chroma_path = str(Path(tmp) / 'chroma')
        cfg.settings.watched_folder = str(Path(tmp) / 'docs')

        from app.skills.indexer import (
            IndexerSkill, DocumentIndexerSkill,
            _infer_doc_type, _chunk_elements, _PagedElement,
            SUPPORTED_SUFFIXES
        )

        # 1. doc type inference
        assert _infer_doc_type('agm-minutes-2024.pdf') == 'minutes'
        assert _infer_doc_type('safeguarding_policy.docx') == 'policy'
        assert _infer_doc_type('grant_application.pdf') == 'application'
        assert _infer_doc_type('random_file.txt') == 'other'
        print('Doc type inference: PASSED')

        # 2. chunking
        elements = [_PagedElement(text='A' * 5000, page=1)]
        chunks = _chunk_elements(elements, chunk_chars=2000, overlap_chars=200)
        assert len(chunks) > 1
        print(f'Chunking: PASSED ({len(chunks)} chunks from 5000-char text)')

        # 3. Missing folder
        result = await IndexerSkill().run(folder='/nonexistent/xyz')
        assert not result.success
        assert 'not found' in result.error.lower()
        print('Missing folder error: PASSED')

        # 4. Index a real txt file
        docs_dir = Path(tmp) / 'docs'
        docs_dir.mkdir(parents=True)
        (docs_dir / 'policy_v1.txt').write_text('Community benefit policy text. ' * 100)
        (docs_dir / 'board_minutes_2024.txt').write_text('Minutes of the board meeting. ' * 100)

        with patch('app.skills.indexer.vector_store.add_document', new=AsyncMock()) as mock_add, \
             patch('app.skills.indexer.vector_store.delete_document', new=AsyncMock()):
            result = await IndexerSkill().run(folder=str(docs_dir), force=True)

        assert result.success, f'Indexer failed: {result.error}'
        assert result.metadata['indexed'] == 2, f'Expected 2, got {result.metadata}'
        assert mock_add.called
        print(f'Indexed 2 files: PASSED (indexed={result.metadata["indexed"]})')

        # 5. Skip unchanged files (second run without force)
        with patch('app.skills.indexer.vector_store.add_document', new=AsyncMock()), \
             patch('app.skills.indexer.vector_store.delete_document', new=AsyncMock()):
            result2 = await IndexerSkill().run(folder=str(docs_dir))

        assert result2.metadata['skipped'] == 2
        print(f'Skip unchanged: PASSED (skipped={result2.metadata["skipped"]})')

        # 6. Unsupported extension is ignored
        (docs_dir / 'script.py').write_text('import os')
        with patch('app.skills.indexer.vector_store.add_document', new=AsyncMock()), \
             patch('app.skills.indexer.vector_store.delete_document', new=AsyncMock()):
            result3 = await IndexerSkill().run(folder=str(docs_dir))

        assert result3.metadata.get('indexed', 0) == 0  # .py not indexed
        print('Unsupported extension ignored: PASSED')

        print('\nSECTION 8 Document Indexer: ALL PASSED')

asyncio.run(run())
