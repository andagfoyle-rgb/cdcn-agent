#!/usr/bin/env node
/**
 * CDCN DOCX Generator
 *
 * Reads a JSON document description from stdin and writes a professionally
 * formatted Word document with full CDCN branding.
 *
 * JSON schema accepted on stdin:
 * {
 *   "title": "Document Title",
 *   "filename": "output.docx",          // optional — auto-generated if omitted
 *   "outputDir": "/var/lib/...",         // optional — defaults to /var/lib/cdcn-agent/documents
 *   "metadata": [ { "key": "...", "value": "..." }, ... ],
 *   "sections": [
 *     {
 *       "heading": "Section Title",
 *       "level": 1,                      // 1-3
 *       "body": [                        // array of content blocks
 *         { "type": "paragraph", "text": "...", "bold": false, "italic": false },
 *         { "type": "bullets", "items": ["...", "..."] },
 *         { "type": "numbered", "items": ["...", "..."] },
 *         { "type": "table", "headers": ["..."], "rows": [["...", "..."]] },
 *         { "type": "blockquote", "text": "..." },
 *         { "type": "resolution", "text": "..." },
 *         { "type": "metadata_line", "key": "...", "value": "..." },
 *         { "type": "divider" },
 *         { "type": "page_break" }
 *       ]
 *     }
 *   ]
 * }
 *
 * Outputs the absolute path of the generated DOCX to stdout.
 */

const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  HeadingLevel, AlignmentType, WidthType, BorderStyle, ShadingType,
  PageBreak, Footer, Header, PageNumber, NumberFormat, TabStopType,
  TabStopPosition, LevelFormat, convertInchesToTwip, ExternalHyperlink,
  TableLayoutType,
} = require("docx");
const fs = require("fs");
const path = require("path");

// ── Brand constants ──────────────────────────────────────────────────────────
const BRAND = {
  font: "Rubik",
  fontFallback: "Calibri",
  codeFont: "Consolas",
  charcoal: "2C3E50",
  sailingBlue: "5EAFE5",
  bodyText: "333333",
  subtleGrey: "949494",
  metaGrey: "5A5A5A",
  white: "FFFFFF",
  tableAlt: "F2F5F8",
  borderGrey: "D0D0D0",
  amberBorder: "E8A317",
  creamBg: "FFF8E7",
};

const DEFAULT_OUTPUT_DIR = "/var/lib/cdcn-agent/documents";

// ── Inline markdown parser ───────────────────────────────────────────────────
// Parses ***bold-italic***, **bold**, *italic*, `code` into TextRun arrays.

function parseInlineMarkdown(text, baseOpts = {}) {
  if (!text) return [new TextRun({ text: "", font: BRAND.font, ...baseOpts })];

  const pattern = /(\*\*\*.*?\*\*\*)|(\*\*.*?\*\*)|(\*[^*]+?\*)|(`[^`]+?`)/g;
  const runs = [];
  let lastIndex = 0;

  for (const m of text.matchAll(pattern)) {
    // Plain text before match
    if (m.index > lastIndex) {
      runs.push(new TextRun({
        text: text.slice(lastIndex, m.index),
        font: BRAND.font,
        size: 22, // 11pt in half-points
        color: BRAND.bodyText,
        ...baseOpts,
      }));
    }

    const matched = m[0];
    if (matched.startsWith("***") && matched.endsWith("***")) {
      runs.push(new TextRun({
        text: matched.slice(3, -3),
        font: BRAND.font,
        size: 22,
        color: BRAND.bodyText,
        bold: true,
        italics: true,
        ...baseOpts,
      }));
    } else if (matched.startsWith("**") && matched.endsWith("**")) {
      runs.push(new TextRun({
        text: matched.slice(2, -2),
        font: BRAND.font,
        size: 22,
        color: BRAND.bodyText,
        bold: true,
        ...baseOpts,
      }));
    } else if (matched.startsWith("*") && matched.endsWith("*")) {
      runs.push(new TextRun({
        text: matched.slice(1, -1),
        font: BRAND.font,
        size: 22,
        color: BRAND.bodyText,
        italics: true,
        ...baseOpts,
      }));
    } else if (matched.startsWith("`") && matched.endsWith("`")) {
      runs.push(new TextRun({
        text: matched.slice(1, -1),
        font: BRAND.codeFont,
        size: 21, // 10.5pt
        color: BRAND.sailingBlue,
        ...baseOpts,
      }));
    }

    lastIndex = m.index + m[0].length;
  }

  // Remaining plain text
  if (lastIndex < text.length) {
    runs.push(new TextRun({
      text: text.slice(lastIndex),
      font: BRAND.font,
      size: 22,
      color: BRAND.bodyText,
      ...baseOpts,
    }));
  }

  return runs.length > 0 ? runs : [new TextRun({
    text: text,
    font: BRAND.font,
    size: 22,
    color: BRAND.bodyText,
    ...baseOpts,
  })];
}


// ── Title page ───────────────────────────────────────────────────────────────

function buildTitlePage(title, metadata) {
  const children = [];

  // Spacer
  children.push(new Paragraph({ spacing: { before: 3000 } }));

  // Organisation name
  children.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new TextRun({
      text: "Community Development Company of Nesting",
      font: BRAND.font,
      size: 28, // 14pt
      color: BRAND.sailingBlue,
    })],
  }));

  // Underline accent
  children.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 600 },
    border: {
      bottom: { style: BorderStyle.SINGLE, size: 6, color: BRAND.sailingBlue },
    },
    children: [new TextRun({ text: "" })],
  }));

  // Document title
  children.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 400 },
    children: [new TextRun({
      text: title || "CDCN Document",
      font: BRAND.font,
      size: 52, // 26pt
      color: BRAND.charcoal,
      bold: true,
    })],
  }));

  // Metadata pairs
  if (metadata && metadata.length > 0) {
    children.push(new Paragraph({ spacing: { before: 600 } }));
    for (const { key, value } of metadata) {
      children.push(new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 80 },
        children: [
          new TextRun({
            text: `${key}: `,
            font: BRAND.font,
            size: 22,
            color: BRAND.metaGrey,
            bold: true,
          }),
          new TextRun({
            text: value,
            font: BRAND.font,
            size: 22,
            color: BRAND.bodyText,
          }),
        ],
      }));
    }
  }

  // Charity number
  children.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1200 },
    children: [new TextRun({
      text: "Scottish Charity SC048164",
      font: BRAND.font,
      size: 18, // 9pt
      color: BRAND.subtleGrey,
      italics: true,
    })],
  }));

  // Page break after title page
  children.push(new Paragraph({
    children: [new PageBreak()],
  }));

  return children;
}


// ── Content block builders ───────────────────────────────────────────────────

function buildParagraph(block) {
  const opts = {};
  if (block.bold) opts.bold = true;
  if (block.italic) opts.italics = true;
  return new Paragraph({
    spacing: { after: 120 },
    children: parseInlineMarkdown(block.text, opts),
  });
}

function buildBullets(block) {
  return (block.items || []).map((item) => new Paragraph({
    bullet: { level: 0 },
    spacing: { after: 60 },
    children: parseInlineMarkdown(item),
  }));
}

function buildNumbered(block) {
  return (block.items || []).map((item, idx) => new Paragraph({
    numbering: { reference: "cdcn-numbering", level: 0 },
    spacing: { after: 60 },
    children: parseInlineMarkdown(item),
  }));
}

function buildTable(block) {
  const headers = block.headers || [];
  const rows = block.rows || [];
  const numCols = headers.length || (rows[0] || []).length || 1;

  // Header row
  const headerRow = new TableRow({
    tableHeader: true,
    children: headers.map((h) => new TableCell({
      shading: { type: ShadingType.CLEAR, fill: BRAND.charcoal },
      margins: { top: 60, bottom: 60, left: 100, right: 100 },
      children: [new Paragraph({
        spacing: { before: 60, after: 60 },
        children: [new TextRun({
          text: h.replace(/\*\*/g, ""),
          font: BRAND.font,
          size: 20,
          color: BRAND.white,
          bold: true,
        })],
      })],
    })),
  });

  // Data rows with alternating shading
  const dataRows = rows.map((row, ri) => new TableRow({
    children: row.map((cell, ci) => new TableCell({
      shading: ri % 2 === 1
        ? { type: ShadingType.CLEAR, fill: BRAND.tableAlt }
        : undefined,
      margins: { top: 60, bottom: 60, left: 100, right: 100 },
      children: [new Paragraph({
        spacing: { before: 60, after: 60 },
        children: parseInlineMarkdown(cell.replace(/\*\*/g, ""), { size: 20 }),
      })],
    })),
  }));

  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    rows: [headerRow, ...dataRows],
    borders: {
      top: { style: BorderStyle.SINGLE, size: 6, color: BRAND.charcoal },
      bottom: { style: BorderStyle.SINGLE, size: 6, color: BRAND.charcoal },
      left: { style: BorderStyle.SINGLE, size: 2, color: BRAND.borderGrey },
      right: { style: BorderStyle.SINGLE, size: 2, color: BRAND.borderGrey },
      insideHorizontal: { style: BorderStyle.SINGLE, size: 4, color: BRAND.borderGrey },
      insideVertical: { style: BorderStyle.SINGLE, size: 2, color: BRAND.borderGrey },
    },
  });
}

function buildBlockquote(block) {
  return new Paragraph({
    indent: { left: convertInchesToTwip(0.4) },
    spacing: { before: 120, after: 120 },
    border: {
      left: { style: BorderStyle.SINGLE, size: 12, space: 8, color: BRAND.sailingBlue },
    },
    children: parseInlineMarkdown(block.text, {
      size: 21,
      color: BRAND.metaGrey,
      italics: true,
    }),
  });
}

function buildResolution(block) {
  // Resolution/action box: amber left border, cream background
  return new Paragraph({
    indent: { left: convertInchesToTwip(0.4) },
    spacing: { before: 160, after: 160 },
    shading: { type: ShadingType.CLEAR, fill: BRAND.creamBg },
    border: {
      left: { style: BorderStyle.SINGLE, size: 18, space: 8, color: BRAND.amberBorder },
    },
    children: parseInlineMarkdown(block.text, {
      size: 22,
      color: BRAND.charcoal,
      bold: true,
    }),
  });
}

function buildMetadataLine(block) {
  return new Paragraph({
    spacing: { after: 80 },
    children: [
      new TextRun({
        text: `${block.key}: `,
        font: BRAND.font,
        size: 20,
        color: BRAND.metaGrey,
        bold: true,
      }),
      new TextRun({
        text: block.value,
        font: BRAND.font,
        size: 20,
        color: BRAND.bodyText,
      }),
    ],
  });
}

function buildDivider() {
  return new Paragraph({
    spacing: { before: 80, after: 80 },
    border: {
      bottom: { style: BorderStyle.SINGLE, size: 2, space: 4, color: BRAND.borderGrey },
    },
    children: [new TextRun({ text: "" })],
  });
}


// ── Heading builder ──────────────────────────────────────────────────────────

function buildHeading(text, level) {
  const headingMap = {
    1: HeadingLevel.HEADING_1,
    2: HeadingLevel.HEADING_2,
    3: HeadingLevel.HEADING_3,
  };

  const sizeMap = { 1: 36, 2: 28, 3: 24 }; // half-points: 18pt, 14pt, 12pt
  const spaceBeforeMap = { 1: 480, 2: 360, 3: 280 };

  const opts = {
    heading: headingMap[level] || HeadingLevel.HEADING_3,
    spacing: {
      before: spaceBeforeMap[level] || 280,
      after: level === 1 ? 200 : level === 2 ? 160 : 120,
    },
    keepNext: true,
    children: [new TextRun({
      text: text,
      font: BRAND.font,
      size: sizeMap[level] || 24,
      color: BRAND.charcoal,
      bold: true,
    })],
  };

  // H2 gets Sailing Blue underline
  if (level === 2) {
    opts.border = {
      bottom: { style: BorderStyle.SINGLE, size: 6, space: 3, color: BRAND.sailingBlue },
    };
  }

  return new Paragraph(opts);
}


// ── Section processor ────────────────────────────────────────────────────────

function buildSectionContent(section) {
  const children = [];

  // Section heading
  if (section.heading) {
    children.push(buildHeading(section.heading, section.level || 2));
  }

  // Body blocks
  for (const block of (section.body || [])) {
    switch (block.type) {
      case "paragraph":
        children.push(buildParagraph(block));
        break;
      case "bullets":
        children.push(...buildBullets(block));
        break;
      case "numbered":
        children.push(...buildNumbered(block));
        break;
      case "table":
        children.push(buildTable(block));
        children.push(new Paragraph({ spacing: { after: 120 } }));
        break;
      case "blockquote":
        children.push(buildBlockquote(block));
        break;
      case "resolution":
        children.push(buildResolution(block));
        break;
      case "metadata_line":
        children.push(buildMetadataLine(block));
        break;
      case "divider":
        children.push(buildDivider());
        break;
      case "page_break":
        children.push(new Paragraph({ children: [new PageBreak()] }));
        break;
      default:
        // Treat unknown as plain paragraph
        if (block.text) children.push(buildParagraph(block));
    }
  }

  return children;
}


// ── Document assembly ────────────────────────────────────────────────────────

function buildDocument(input) {
  const title = input.title || "CDCN Document";
  const sections = input.sections || [];
  const metadata = input.metadata || [];

  // Collect all document children
  const allChildren = [];

  // Title page
  allChildren.push(...buildTitlePage(title, metadata));

  // Process sections — page break before each H1 (except first)
  let h1Count = 0;
  for (const section of sections) {
    const level = section.level || 2;
    if (level === 1) {
      h1Count++;
      if (h1Count > 1) {
        allChildren.push(new Paragraph({ children: [new PageBreak()] }));
      }
    }
    allChildren.push(...buildSectionContent(section));
  }

  // Draft notice footer
  allChildren.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 480 },
    children: [new TextRun({
      text: "Draft prepared by CDCN Agent — requires review and approval before use.",
      font: BRAND.font,
      size: 17,
      color: BRAND.subtleGrey,
      italics: true,
    })],
  }));

  // Build the document
  const doc = new Document({
    styles: {
      default: {
        document: {
          run: {
            font: BRAND.font,
            size: 22,
            color: BRAND.bodyText,
          },
          paragraph: {
            spacing: { after: 120, line: 276 }, // 1.15 line spacing
          },
        },
        heading1: {
          run: {
            font: BRAND.font,
            size: 36,
            color: BRAND.charcoal,
            bold: true,
          },
          paragraph: {
            spacing: { before: 480, after: 200 },
          },
        },
        heading2: {
          run: {
            font: BRAND.font,
            size: 28,
            color: BRAND.charcoal,
            bold: true,
          },
          paragraph: {
            spacing: { before: 360, after: 160 },
          },
        },
        heading3: {
          run: {
            font: BRAND.font,
            size: 24,
            color: BRAND.charcoal,
            bold: true,
          },
          paragraph: {
            spacing: { before: 280, after: 120 },
          },
        },
      },
    },
    numbering: {
      config: [{
        reference: "cdcn-numbering",
        levels: [{
          level: 0,
          format: LevelFormat.DECIMAL,
          text: "%1.",
          alignment: AlignmentType.START,
          style: {
            paragraph: { indent: { left: convertInchesToTwip(0.5), hanging: convertInchesToTwip(0.25) } },
            run: { font: BRAND.font, size: 22 },
          },
        }],
      }],
    },
    sections: [{
      properties: {
        page: {
          margin: {
            top: convertInchesToTwip(1.0),
            bottom: convertInchesToTwip(1.0),
            left: convertInchesToTwip(1.0),
            right: convertInchesToTwip(1.0),
          },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            alignment: AlignmentType.RIGHT,
            spacing: { after: 0 },
            border: {
              bottom: { style: BorderStyle.SINGLE, size: 4, space: 4, color: BRAND.sailingBlue },
            },
            children: [new TextRun({
              text: "Community Development Company of Nesting",
              font: BRAND.font,
              size: 16,
              color: BRAND.subtleGrey,
            })],
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [
              new TextRun({
                text: "CDCN  ·  SC048164  ·  Page ",
                font: BRAND.font,
                size: 15,
                color: BRAND.subtleGrey,
              }),
              new TextRun({
                children: [PageNumber.CURRENT],
                font: BRAND.font,
                size: 15,
                color: BRAND.subtleGrey,
              }),
            ],
          })],
        }),
      },
      children: allChildren,
    }],
  });

  return doc;
}


// ── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  let raw = "";

  // Read JSON from stdin
  await new Promise((resolve, reject) => {
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => { raw += chunk; });
    process.stdin.on("end", resolve);
    process.stdin.on("error", reject);
  });

  let input;
  try {
    input = JSON.parse(raw);
  } catch (err) {
    process.stderr.write(`JSON parse error: ${err.message}\n`);
    process.exit(1);
  }

  // Build document
  const doc = buildDocument(input);

  // Determine output path
  const outputDir = input.outputDir || DEFAULT_OUTPUT_DIR;
  fs.mkdirSync(outputDir, { recursive: true });

  let filename = input.filename || "";
  if (!filename) {
    const now = new Date();
    const stamp = now.toISOString().replace(/[-:T]/g, "").slice(0, 13).replace(/(\d{8})(\d{4})/, "$1_$2");
    filename = `CDCN_Draft_${stamp}.docx`;
  }
  if (!filename.endsWith(".docx")) filename += ".docx";
  filename = filename.replace(/[<>:"/\\|?*]/g, "_");

  const outPath = path.join(outputDir, filename);

  // Generate buffer and write
  const buffer = await Packer.toBuffer(doc);
  fs.writeFileSync(outPath, buffer);

  // Output the path for the caller
  process.stdout.write(outPath);
}

main().catch((err) => {
  process.stderr.write(`docx_generator error: ${err.message}\n${err.stack}\n`);
  process.exit(1);
});
