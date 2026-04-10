```python
"""
Skill for opening, reading, and editing documents from the CDCN archive.
Provides full document access unlike search_archive which returns fragments.
"""
from app.skills.base import BaseSkill, SkillResult
from pathlib import Path
from datetime import datetime


class DocumentEditorSkill(BaseSkill):
    name = "document_editor"
    description = "Open, read in full, and edit existing documents from the CDCN archive."

    ALLOWED_BASE_DIR = "data"
    SUPPORTED_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".yml", ".yaml", ".html", ".xml"}

    async def run(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "read")

        if action == "read":
            return await self._read_document(kwargs)
        elif action == "save":
            return await self._save_document(kwargs)
        else:
            return SkillResult(
                success=False,
                error=f"Unknown action: '{action}'. Supported actions: 'read', 'save'."
            )

    def _resolve_and_validate_path(self, path_str: str) -> tuple[Path, str | None]:
        """
        Resolve path and validate it's within the allowed base directory.
        Returns (resolved_path, error_message). error_message is None if valid.
        """
        if not path_str:
            return None, "Path is required."

        base_dir = Path(self.ALLOWED_BASE_DIR).resolve()
        full_path = (base_dir / path_str).resolve()

        try:
            full_path.relative_to(base_dir)
        except ValueError:
            return None, f"Access denied: path must be within '{self.ALLOWED_BASE_DIR}/' directory."

        return full_path, None

    def _get_file_metadata(self, path: Path, content: str) -> dict:
        """Extract metadata about a file."""
        stat = path.stat()
        return {
            "size_bytes": stat.st_size,
            "line_count": content.count("\n") + 1 if content else 0,
            "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }

    async def _read_document(self, kwargs: dict) -> SkillResult:
        """Read and return the complete content of a document."""
        path_str = kwargs.get("document_path")

        if not path_str:
            return SkillResult(success=False, error="document_path is required.")

        full_path, error = self._resolve_and_validate_path(path_str)
        if error:
            return SkillResult(success=False, error=error)

        if not full_path.exists():
            return SkillResult(success=False, error=f"Document not found: '{path_str}'")

        if not full_path.is_file():
            return SkillResult(success=False, error=f"Path is not a file: '{path_str}'")

        extension = full_path.suffix.lower()
        warnings = []

        if extension and extension not in self.SUPPORTED_EXTENSIONS:
            warnings.append(f"File extension '{extension}' may not be a recognised text format. Attempting to read anyway.")

        try:
            content = full_path.read_text(encoding="utf-8")
            metadata = self._get_file_metadata(full_path, content)

            result = {
                "content": content,
                "path": path_str,
                "extension": extension or None,
                "size_bytes": metadata["size_bytes"],
                "line_count": metadata["line_count"],
                "modified_time": metadata["modified_time"],
            }

            if warnings:
                result["warnings"] = warnings

            return SkillResult(success=True, output=result)

        except UnicodeDecodeError:
            return SkillResult(
                success=False,
                error=f"Cannot read file as UTF-8 text: '{path_str}'. File may be binary or use a different encoding."
            )
        except PermissionError:
            return SkillResult(success=False, error=f"Permission denied reading: '{path_str}'")
        except Exception as e:
            return SkillResult(success=False, error=f"Error reading document: {str(e)}")

    async def _save_document(self, kwargs: dict) -> SkillResult:
        """Save content to a file, optionally creating a backup of existing files."""
        path_str = kwargs.get("document_path")
        output_path_str = kwargs.get("output_path")
        content = kwargs.get("content")
        create_backup = kwargs.get("create_backup", True)

        if content is None:
            return SkillResult(success=False, error="content is required for save action.")

        # Determine where to save
        save_path_str = output_path_str or path_str
        if not save_path_str:
            return SkillResult(
                success=False,
                error="Either document_path or output_path is required for save action."
            )

        full_path, error = self._resolve_and_validate_path(save_path_str)
        if error:
            return SkillResult(success=False, error=error)

        backup_path = None
        try:
            # Create backup if file exists and backup is requested
            if full_path.exists() and create_backup:
                backup_path = self._create_backup(full_path)

            # Ensure parent directory exists
            full_path.parent.mkdir(parents=True, exist_ok=True)

            # Write content
            full_path.write_text(content, encoding="utf-8")

            result = {
                "saved_path": save_path_str,
                "size_bytes": len(content.encode("utf-8")),
                "line_count": content.count("\n") + 1 if content else 0,
                "action": "saved",
                "created_new": not full_path.exists() or backup_path is None and not full_path.exists(),
            }

            if backup_path:
                result["backup_path"] = str(backup_path.relative_to(Path(self.ALLOWED_BASE_DIR).resolve()))
                result["message"] = f"Saved to '{save_path_str}' with backup created."

            return SkillResult(success=True, output=result)

        except PermissionError:
            return SkillResult(success=False, error=f"Permission denied writing to: '{save_path_str}'")
        except Exception as e:
            return SkillResult(success=False, error=f"Error saving document: {str(e)}")

    def _create_backup(self, file_path: Path) -> Path:
        """Create a backup of an existing file with a .bak extension."""
        base_dir = Path(self.ALLOWED_BASE_DIR).resolve()
        backup_path = file_path.with_suffix(file_path.suffix + ".bak")

        # Increment backup name if it already exists
        counter = 1
        while backup_path.exists():
            backup_path = file_path.with_suffix(f"{file_path.suffix}.bak.{counter}")
            counter += 1

        # Copy the file
        backup_path.write_bytes(file_path.read_bytes())
        return backup_path
```