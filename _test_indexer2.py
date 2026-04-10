"""Section 8 — Document Indexer offline simulation (simplified)."""
import asyncio, tempfile, sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

async def run():
    # Test the pure logic without external dependencies
    from app.skills.indexer import (
        IndexerSkill, DocumentIndexerSkill,
        _infer_doc_type, _chunk_elements, _PagedElement,
        SUPPORTED_SUFFIXES, _doc_id_for_path
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

    # 3. Page tracking across elements
    elements2 = [
        _PagedElement(text='Page 1 content. ' * 100, page=1),
        _PagedElement(text='Page 2 content. ' * 100, page=2),
    ]
    chunks2 = _chunk_elements(elements2, chunk_chars=2000, overlap_chars=100)
    assert chunks2[0][1] == 1
    assert chunks2[-1][1] == 2
    print(f'Page tracking: PASSED')

    # 4. doc_id determinism
    import tempfile, os
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        p = Path(tmp) / 'test.txt'
        p.write_text('hello')
        id1 = _doc_id_for_path(p)
        id2 = _doc_id_for_path(p)
        assert id1 == id2
        assert len(id1) == 20
        print(f'Doc ID determinism: PASSED (id={id1})')

    # 5. Missing folder
    result = await IndexerSkill().run(folder='/nonexistent/xyz123abc')
    assert not result.success
    assert 'not found' in result.error.lower()
    print('Missing folder error: PASSED')

    # 6. Index a real txt file - mock chromadb entirely
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        import app.config as cfg
        cfg.settings.chroma_path = str(Path(tmp) / 'chroma')

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

        # 7. Skip unchanged files
        with patch('app.skills.indexer.vector_store.add_document', new=AsyncMock()), \
             patch('app.skills.indexer.vector_store.delete_document', new=AsyncMock()):
            result2 = await IndexerSkill().run(folder=str(docs_dir))
        assert result2.metadata['skipped'] == 2
        print(f'Skip unchanged: PASSED (skipped={result2.metadata["skipped"]})')

    print('\nSECTION 8 Document Indexer: ALL PASSED')

asyncio.run(run())
