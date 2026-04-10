"""
VisionSkill — analyse images using the GLM-5V vision model.

GLM-5V-Turbo (via SiliconFlow) supports OpenAI-compatible multimodal
messages with base64-encoded images.  This skill:
  1. Accepts a file path to an image (PNG, JPG, TIFF, BMP, PDF page).
  2. Encodes it as base64 and sends it to the vision model.
  3. Returns the model's analysis as text.

Use cases:
  - Describe/summarise a scanned document image
  - Extract text from photographs of handwritten notes
  - Analyse charts, tables, or diagrams in board packs
  - Enhanced OCR fallback for the document parser
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from app.skills.base import BaseSkill, SkillResult

log = logging.getLogger(__name__)

# Supported image types and their MIME types
_MIME_MAP: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
}

_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB limit


def _encode_image(path: Path) -> tuple[str, str]:
    """Read an image file and return (base64_data, mime_type).

    Raises ValueError for unsupported or oversized files.
    """
    suffix = path.suffix.lower()
    mime = _MIME_MAP.get(suffix)
    if not mime:
        raise ValueError(
            f"Unsupported image format: {suffix}. "
            f"Supported: {', '.join(sorted(_MIME_MAP.keys()))}"
        )
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    size = path.stat().st_size
    if size > _MAX_IMAGE_BYTES:
        raise ValueError(
            f"Image too large: {size / 1024 / 1024:.1f} MB "
            f"(limit: {_MAX_IMAGE_BYTES / 1024 / 1024:.0f} MB)"
        )
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii"), mime


class VisionSkill(BaseSkill):
    """
    Analyse an image using the GLM-5V vision model.

    Tool definition (for the LLM):
      - image_path (str, required): Path to the image file to analyse.
      - question (str, optional): Specific question about the image.
        Defaults to a general description/OCR request.
      - mode (str, optional): "describe" | "ocr" | "analyse".
        Controls the system prompt emphasis.
    """

    name = "vision"
    description = (
        "Analyse an image using the vision model. Can describe content, "
        "extract text (OCR), or answer questions about images."
    )

    _MODE_PROMPTS = {
        "describe": (
            "Describe the contents of this image in detail. "
            "Include any text, logos, signatures, or notable features. "
            "If it's a document, summarise its structure and key information."
        ),
        "ocr": (
            "Extract ALL text from this image, preserving the original "
            "layout and structure as closely as possible. Include headings, "
            "bullet points, table data, and any handwritten text. "
            "Return the extracted text only, no commentary."
        ),
        "analyse": (
            "Analyse this image carefully. If it contains a chart or table, "
            "describe the data and key trends. If it contains a document, "
            "summarise the key points. If it contains photographs, describe "
            "what is shown and any relevant context."
        ),
    }

    async def run(
        self,
        image_path: str = "",
        question: str = "",
        mode: str = "describe",
        **kwargs: Any,
    ) -> SkillResult:
        if not image_path:
            return SkillResult(success=False, error="image_path is required.")

        path = Path(image_path)

        # Security: restrict to document/data directories
        from app.config import settings
        allowed_roots = [
            Path(settings.watched_folder).resolve(),
            Path(settings.memory_path).resolve(),
            Path(settings.backup_path).resolve(),
            Path("/tmp"),
        ]
        resolved = path.resolve()
        if not any(
            str(resolved).startswith(str(root)) for root in allowed_roots
        ):
            return SkillResult(
                success=False,
                error=(
                    f"Access denied: images must be in the documents, memory, "
                    f"or backup directories. Got: {path}"
                ),
            )

        try:
            b64_data, mime = _encode_image(path)
        except (ValueError, FileNotFoundError) as exc:
            return SkillResult(success=False, error=str(exc))

        # Build the prompt
        mode = mode if mode in self._MODE_PROMPTS else "describe"
        system_text = self._MODE_PROMPTS[mode]
        if question:
            user_text = question
        else:
            user_text = "Please analyse this image."

        # Build OpenAI-compatible multimodal message
        messages = [
            {"role": "system", "content": system_text},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{b64_data}",
                        },
                    },
                    {"type": "text", "text": user_text},
                ],
            },
        ]

        try:
            from app.llm_client import CDCNLLMClient

            # Use the SiliconFlow vision model directly
            vision_client = CDCNLLMClient(provider="siliconflow")
            result = await vision_client.chat(messages, skill_used="vision")

            if not result or not result.strip():
                return SkillResult(
                    success=False,
                    error="Vision model returned an empty response.",
                )

            log.info(
                "Vision analysis complete: path=%s mode=%s result_len=%d",
                path.name,
                mode,
                len(result),
            )

            return SkillResult(
                success=True,
                output=result,
                metadata={
                    "image_path": str(path),
                    "mode": mode,
                    "image_size_kb": path.stat().st_size // 1024,
                },
            )

        except Exception as exc:
            log.error("Vision analysis failed: %s", exc, exc_info=True)
            return SkillResult(
                success=False,
                error=f"Vision analysis failed: {exc}",
            )


async def vision_ocr(image_path: str | Path) -> str | None:
    """
    Convenience function for the document parser to use vision-based OCR.

    Returns extracted text, or None on failure.  Designed as a fallback
    after pdfplumber and pdftotext fail.
    """
    skill = VisionSkill()
    result = await skill.run(
        image_path=str(image_path),
        mode="ocr",
    )
    if result.success:
        return result.output
    log.warning("Vision OCR fallback failed: %s", result.error)
    return None
