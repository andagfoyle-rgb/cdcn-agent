# CDCN Agent — Heartbeat Instructions

This file contains standing instructions for the agent's scheduled heartbeat tasks. It is read by the heartbeat worker on each scheduled run. It is NOT a log — log entries are written to `data/memory/heartbeat_log.md`.

---

## Task 1 — Funding Deadline Check

**Schedule:** Every heartbeat run during WAKE mode.

**Instructions:**

1. Read `skills_config/funding_deadlines.yaml`.
2. For each entry where status is not `submitted`, `awarded`, or `declined`:
   - Calculate the number of days until the deadline.
   - If the deadline is 14 days away or fewer, generate a plain-English alert.
3. If any alerts were generated, send them to the configured Discord and Telegram channels.
4. Log the check result (number of deadlines checked, number of alerts sent) to `data/memory/heartbeat_log.md`.

**Format for alerts:**

> **Funding deadline approaching:** [Funder name] — [Programme name] — deadline [DD Month YYYY] ([N] days).

---

## Task 2 — Document Check

**Schedule:** Every heartbeat run during WAKE mode.

**Instructions:**

1. Run `DocumentIndexerSkill` against the configured document folders.
2. Index any new or modified files found since the last run.
3. Log the result (files checked, files indexed, errors) to `data/memory/heartbeat_log.md`.
4. If more than five new files were indexed, note this in the heartbeat log with a summary of document types.

---

## Task 3 — Weekly Digest

**Schedule:** Mondays at 07:15 during WAKE mode.

**Instructions:**

1. Read the session logs from the past seven days (`data/memory/session_log/`).
2. Read the journal entries from the past seven days (`data/memory/journal/`).
3. Generate a short digest (five to eight bullet points) summarising:
   - Documents indexed
   - Drafts produced
   - Funding deadlines acted on or approaching
   - Any board decisions recorded
   - Outstanding follow-up items from memory
4. Write the digest to the journal (`data/memory/journal/YYYY-MM-DD.md`) with the heading `## Weekly Digest`.
5. Send the digest to the configured Discord channel.

---

## Task 4 — Monthly Governance Check

**Schedule:** First day of each month at 08:00 during WAKE mode.

**Instructions:**

1. Search the document archive for governance policies (`doc_type: policy`).
2. For each policy found, check the review date recorded in the document metadata.
3. List any policies whose review date falls within the next 90 days.
4. Generate a plain-English reminder for each policy due for review.
5. Write the list to the journal with the heading `## Governance Review Reminder — [Month YYYY]`.
6. Send the reminders to the configured Discord and Telegram channels.

---

*These instructions are read by the heartbeat worker. Do not delete or reorder tasks.*
*To suspend a task temporarily, add a line beginning `SUSPENDED:` before its schedule line.*
