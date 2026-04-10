"""
DocumentWriterSkill — LLM-powered document drafter using CDCN templates.

Templates live in skills_config/writer_templates/ and use {{placeholder}}
syntax.  <!-- LLM instruction: ... --> HTML comments embedded in templates
provide section-level guidance; they are removed from the final output.

The style guide (skills_config/memory/style_guide.md) and knowledge graph
(skills_config/memory/knowledge_graph.md) are loaded and passed to the LLM
so it can match CDCN's voice and reference known entities correctly.

System identity — injected into every writer call:
  "You are a professional administrator writing for CDCN (Community
   Development Company Nesting), a Scottish charitable organisation in
   South Nesting, Shetland.  Write in clear professional plain English.
   Follow the style guide provided.  Do not invent facts."
"""
import logging
from datetime import datetime
from pathlib import Path

from app.llm_client import llm_client as _llm_singleton
from app.skills.base import BaseSkill, SkillResult
from app.skills.docx_converter import markdown_to_docx, get_download_url

# Module-level alias so tests can patch ``app.skills.writer.chat``
chat = _llm_singleton.chat

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path("skills_config/writer_templates")
MEMORY_DIR = Path("skills_config/memory")
DRAFTS_DIR = Path("skills_config/drafts")

STYLE_GUIDE_FILE = MEMORY_DIR / "style_guide.md"
KNOWLEDGE_GRAPH_FILE = MEMORY_DIR / "knowledge_graph.md"

KNOWN_TEMPLATES = frozenset({"funding_application", "board_minute", "governance_policy", "trustees_report"})

_SYSTEM_IDENTITY = (
    "You are a professional administrator writing for CDCN "
    "(Community Development Company Nesting), a Scottish charitable organisation "
    "in South Nesting, Shetland. "
    "Write in clear professional plain English. "
    "Follow the style guide provided. "
    "Do not invent facts — if information is not supplied in the brief or context, "
    "leave the placeholder text and append [TO BE CONFIRMED] rather than fabricating details."
)


def _load_optional(path: Path) -> str:
    try:
        return path.read_text() if path.exists() else ""
    except OSError:
        return ""


class DocumentWriterSkill(BaseSkill):
    """
    Draft CDCN governance documents, board minutes, and funding applications.

    Accepts a template name and a brief describing what the document should
    contain.  Optional additional context (e.g. from a prior search) can be
    passed via the context argument.

    Drafts are saved to skills_config/drafts/ and returned as a string.
    """

    name = "writer"
    description = (
        "Draft a CDCN document from a template. "
        "Templates: funding_application, board_minute, governance_policy, trustees_report. "
        "Provide a brief describing the purpose and key facts for the document."
    )

    async def run(
        self,
        template: str = "",
        brief: str = "",
        context: str = "",
        save_draft: bool = True,
        draft_name: str = "",
        **kwargs,
    ) -> SkillResult:
        if not template:
            return SkillResult(
                success=False,
                error=(
                    f"template is required. "
                    f"Available: {', '.join(sorted(KNOWN_TEMPLATES))}"
                ),
            )

        # Load template
        tmpl_path = TEMPLATE_DIR / f"{template}.md"
        if not tmpl_path.exists():
            return SkillResult(
                success=False,
                error=(
                    f"Template '{template}' not found. "
                    f"Available: {', '.join(sorted(KNOWN_TEMPLATES))}"
                ),
            )
        tmpl_text = tmpl_path.read_text()

        # Build system prompt — identity + style guide
        style_guide = _load_optional(STYLE_GUIDE_FILE)
        system = _SYSTEM_IDENTITY
        if style_guide:
            system += f"\n\n## Style Guide\n\n{style_guide}"

        # Build user message
        knowledge = _load_optional(KNOWLEDGE_GRAPH_FILE)
        user_parts = [
            f"## Document Template\n\n{tmpl_text}",
            f"## Brief\n\n{brief or '(no brief provided — use placeholder values)'}",
        ]
        if context:
            user_parts.append(f"## Additional Context\n\n{context}")
        if knowledge.strip():
            user_parts.append(
                "## Organisation Knowledge Graph\n"
                "(Use for reference only — do not reproduce verbatim)\n\n"
                f"{knowledge}"
            )
        user_parts.append(
            "Please produce a complete draft of this document.\n"
            "• Replace every {{placeholder}} with appropriate content from the brief.\n"
            "• Follow every <!-- LLM instruction: ... --> comment in the template.\n"
            "• Remove all HTML comments (<!-- ... -->) from the final output.\n"
            "• Do not add a preamble — output the document directly.\n\n"
            "## Formatting Guide\n\n"
            "The output will be converted to a professionally styled DOCX document. "
            "Use these markdown features to produce well-formatted output:\n\n"
            "**Structure:**\n"
            "• Use # for document title, ## for major sections, ### for subsections, #### for minor headings.\n"
            "• Place key metadata (Date, Author, Reference, Status) as **Key:** Value pairs "
            "on separate lines immediately after the title — these render as a styled metadata block.\n"
            "• Use --- (horizontal rule) to separate major document sections.\n\n"
            "**Emphasis and inline styles:**\n"
            "• Use **bold** for key terms, names, amounts, and decisions.\n"
            "• Use *italic* for emphasis, document titles, and foreign/technical terms.\n"
            "• Use ***bold italic*** sparingly for critical warnings or highlights.\n"
            "• Use `code` for reference numbers, file names, or technical identifiers.\n\n"
            "**Lists:**\n"
            "• Use - or * for bullet lists. Indent with two spaces for sub-bullets.\n"
            "• Use 1. 2. 3. for numbered/ordered lists where sequence matters.\n\n"
            "**Tables:**\n"
            "• Use markdown tables (| Header | Header |) for structured data such as "
            "budgets, timelines, risk registers, and attendance lists.\n\n"
            "**Callouts:**\n"
            "• Use > blockquote lines for important notes, caveats, or recommendations "
            "that should stand out from the body text.\n\n"
            "**General:**\n"
            "• Write substantive, detailed content — not skeleton outlines.\n"
            "• Prefer short paragraphs (3–5 sentences) over long blocks of text.\n"
            "• Where the template calls for a table or list, always use one."
        )

        user_msg = "\n\n---\n\n".join(user_parts)

        # LLM call
        try:
            draft = await chat(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system,
                skill_used="writer",
            )
        except Exception as exc:
            log.exception("Writer LLM call failed for template '%s'", template)
            return SkillResult(success=False, error=str(exc))

        # Save draft
        if save_draft:
            DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            fname = draft_name or f"{template}_{stamp}"
            if not fname.endswith(".md"):
                fname += ".md"
            out = DRAFTS_DIR / fname
            out.write_text(draft)
            log.info("Draft saved: %s", out)

        # Convert to DOCX
        try:
            docx_filename = draft_name or f"{template}_{stamp}"
            if docx_filename.endswith(".md"):
                docx_filename = docx_filename[:-3]
            docx_path = markdown_to_docx(draft, filename=docx_filename + ".docx")
            download_url = get_download_url(docx_path)
            log.info("DOCX generated: %s", docx_path)
            return SkillResult(
                success=True,
                output=draft,
                metadata={
                    "docx_path": str(docx_path),
                    "download_url": download_url,
                    "filename": docx_path.name,
                },
            )
        except Exception as exc:
            log.warning("DOCX conversion failed: %s — returning markdown only", exc)
            return SkillResult(success=True, output=draft)


# Backward-compat alias
WriterSkill = DocumentWriterSkill
