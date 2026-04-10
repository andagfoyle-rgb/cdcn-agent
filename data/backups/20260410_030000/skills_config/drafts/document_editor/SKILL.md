# document_editor

**Name:** `document_editor`

**Description:** Opens, reads in full, and edits existing documents from the CDCN archive. Unlike search_archive which returns fragments, this skill provides complete document content and supports saving modifications.

**USE WHEN:**
- You need to read an entire document's content (not just search matches)
- You need to reformat, condense, or amend an existing document
- You need to save a modified version of a document
- You need to create a new document from existing content

**CALL FORMAT:**
```
document_editor(
    action="read" | "save",
    document_path="<relative path from data/>",
    content="<new/modified content>" (required for save),
    output_path="<optional save location>" (for save),
    create_backup=true | false (default: true)
)
```

**LIMITATIONS:**
- Only accesses files within the `data/` directory
- Text files only (UTF-8 encoded); binary files cannot be processed
- Supported formats: `.txt`, `.md`, `.json`, `.csv`, `.yml`, `.yaml`, `.html`, `.xml`
- Does not perform modifications itself—returns content for the caller to edit, then saves the result