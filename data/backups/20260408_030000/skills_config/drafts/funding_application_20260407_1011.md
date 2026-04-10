# Board Briefing Paper — CDCN Agent Hardware Upgrade Project

**Date:** [TO BE CONFIRMED]
**Author:** Development Officer / Board
**Reference:** BP-2026-HW-001
**Status:** Draft — for Board consideration
**Classification:** Internal

---

## 1. Purpose of This Paper

This briefing paper outlines the business case for upgrading the hardware that powers **CDCN's AI agent system** ("CDCN Agent"). The Board is asked to agree *in principle* to pursuing funding for a high-specification Apple Studio device that would enable CDCN to run advanced language models locally, on our own premises.

This paper does **not** commit CDCN to any specific purchase or funder. It sets out the project rationale, costs, potential funding sources, and risks so that the Board can make an informed decision before any applications are submitted.

> **Key question for the Board:** Should CDCN pursue external funding to upgrade its AI infrastructure from the current cloud-based arrangement to a fully local, on-premises system?

---

## 2. Project Overview

### What We Propose

CDCN wishes to purchase a high-specification **Apple Studio device** (estimated cost **£8,000–£10,000**) capable of running a mid-sized Large Language Model (LLM) with 70 billion to 120 billion parameters entirely in-house.

### Current Arrangement

At present, the CDCN Agent runs on:

- **Hardware:** A Raspberry Pi hosted at a director's home
- **Language processing:** Via cloud API using the **SiliconFlow platform** (currently the GLM-5 model)
- **Running costs:** Approximately **£10 per week**
- **Development investment:** Around **£150** in direct costs plus **over 100 hours** of volunteer time

### What Would Change

Moving to a local Apple Studio device would:

| Aspect | Current (Cloud-Based) | Proposed (Local) |
|--------|----------------------|------------------|
| **Ongoing costs** | ~£10/week (~£520/year) | Near-zero (electricity only) |
| **Data location** | Processed externally via API | Entirely on CDCN premises |
| **Model capability** | Limited by cloud API selection | Open-source models up to 120B parameters |
| **Performance** | Constrained by Raspberry Pi hardware | Substantially faster responses |
| **Subscription fees** | Ongoing API charges | None for inference |

The proposed specification would be an **Apple M3 Ultra or M4 Ultra** processor with **256 GB or more of unified memory**, which is required to run 70B–120B parameter models effectively. An alternative approach using a high-specification PC with dedicated GPUs was considered, but **Apple Silicon** was identified as more power-efficient and quieter for continuous 24/7 operation in a community building setting.

---

## 3. Why This Matters

### Reducing Administrative Burden

CDCN faces ongoing challenges recruiting and retaining staff. The Development Officer post has been difficult to fill, and volunteer directors carry a significant administrative load. The upgraded system would automate or accelerate many routine tasks, freeing staff and directors to focus on work that requires human judgment.

### Keeping Data On-Premises

All CDCN governance documents, financial information, and community data would remain **entirely within CDCN's own infrastructure**. No data would leave our premises for processing. This strengthens our position on data protection and governance — particularly important given the sensitive nature of some documents we handle (financial records, personnel matters, community consultations).

### Staying at the Forefront of Third-Sector Innovation

CDCN has invested significantly in developing practical AI tools for community development organisations. This upgrade would allow us to demonstrate what is possible when a small rural charity takes control of its own digital infrastructure — a message that resonates with funders interested in innovation and organisational resilience.

### Enabling More Powerful Capabilities

With local hosting of larger models, CDCN Agent would gain capabilities including:

- **Better document understanding** across longer and more complex documents
- **Faster response times** for queries and drafting tasks
- **Multi-document analysis** (e.g., comparing multiple funding applications or policy drafts side-by-side)
- **Offline operation** during broadband outages (a real consideration in rural Shetland)

---

## 4. Current System Capabilities

As presented to the Board on **31 March 2026**, the CDCN Agent currently delivers the following functions:

- **Searchable document archive** — all CDCN documents indexed and retrievable
- **Document drafting** — minutes, policies, funding applications, reports
- **Calendar and deadline tracking** — automatic monitoring of key dates
- **Action point tracker** — extraction and tracking of actions from minutes
- **Funding pipeline monitor** — RSS feeds and web scraping for opportunities
- **Morning report / overnight review** — automated summary of overnight activity

These capabilities have been built incrementally through volunteer effort and represent a significant operational asset for CDCN. The hardware upgrade would protect and extend this investment.

---

## 5. Costs Summary

| Item | Estimated Cost | Notes |
|------|---------------|-------|
| Apple Studio device (M3/M4 Ultra, 256GB+ RAM) | £8,000 – £10,000 | Depending on final spec; prices fluctuate |
| Any necessary peripherals (cables, mounting) | ~£50 – £100 | Minor additional items |
| Installation and configuration | Volunteer time (Andrew) | No external cost expected |
| **Total capital cost** | **~£8,050 – £10,100** | One-time expenditure |
| **Annual ongoing cost (current)** | **~£520** | Cloud API fees at £10/week |
| **Annual ongoing cost (post-upgrade)** | **~£30–£50** | Electricity only (estimated) |

> **Net saving:** Approximately **£470–£490 per year** in direct costs, plus significant value from improved capability and data security.

---

## 6. Potential Funding Sources

The following funders have been identified as potential matches for this project, listed in priority order:

### 1. Community Led Local Development (CLLD) — **Highest Priority**

- **Status:** Explicitly mooted at the **31 March 2026** Board meeting
- **Local Action Group (LAG):** Has expressed enthusiasm for AI and digital innovation projects in Shetland
- **Existing relationship:** CDCN recently completed a CLLD-funded Scrapstore improvements grant successfully
- **Typical range:** Capital projects up to approximately **£10,000–£25,000** for community facilities and innovation
- **Fit:** Strong — this is a capital investment in community organisational infrastructure

### 2. Shetland Charitable Trust

- **Relationship:** Long-standing funder with strong existing relationship
- **New funds:** Opened new grant programmes in **November 2025**
- **Focus:** Innovative community projects that benefit Shetland residents
- **Fit:** Good — could be framed as "innovation in community administration" supporting charitable efficiency

### 3. Highlands and Islands Enterprise (HIE)

- **Relationship:** Already funds the CDCN Development Officer post; **Fiona Stirling** at HIE has been supportive of CDCN's innovation work
- **Potential framing:** Organisational capacity building; digital innovation for a rural community organisation
- **Note:** May have smaller innovation funds available outside main programmes

### 4. National Lottery Community Fund — "Awards for All" Scotland

- **Focus:** Ideas that bring communities together and improve people's lives
- **Range:** Typically **£500–£15,000**
- **Fit:** Less obvious, but possible if framed around community resilience and organisational capacity

### 5. Robertson Trust

- **History:** Has funded CDCN previously (business plan development phase)
- **Focus:** Charities in Scotland addressing poverty and trauma, though general grants are also available
- **Fit:** Harder fit, but worth exploring

### 6. Garfield Weston Foundation

- **Process:** Straightforward application process
- **Range:** Smaller grants typically **£1,000–£10,000**
- **Fit:** General charitable purposes — feasible

### 7. Scottish Community Business Fund (SCBF) — Reactive Fund

- **Status:** Mentioned at the **September 2025** Board meeting
- **Fit:** Might support innovation in community business administration — to be explored

---

## 7. Risks and Mitigants

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Technology becomes obsolete** | Medium | Medium | Choose future-proofed specification (M4 Ultra where possible); Apple Silicon ecosystem is well-supported with long support windows; open-source model ecosystem is growing, not shrinking |
| **Staff/director skills gap** | Medium | Low–Medium | Andrew acts as technical lead with deep knowledge of the system; system designed to be user-friendly via natural language interface; training materials can be developed |
| **Funder perceives project as "too niche"** | Medium | High | Frame applications strongly around **administrative savings** and **capacity building**, not "AI for AI's sake"; emphasise data security benefits and cost reductions |
| **Data protection concerns raised** | Low | Positive risk | Local hosting actually **improves** data protection — no data leaves CDCN premises; this is a strength to highlight, not a weakness |
| **Hardware supply/delay issues** | Low | Medium | Apple Studio devices are generally available; ordering early avoids deadline pressure; alternative PC-based approach exists as backup |

---

## 8. Recommendation

The Board is asked to consider and approve the following recommendations:

### Recommendation 1 — In-Principle Agreement

**RESOLVED:** That the Board agrees **in principle** to pursuing the CDCN Agent Hardware Upgrade Project, subject to identifying suitable funding and confirming the technical specification.

*Proposed by:* [TO BE CONFIRMED]
*Seconded by:* [TO BE CONFIRMED]

### Recommendation 2 — Lead on Funding Application

**ACTION:** **Mark (Development Officer)** to lead on preparing a **CLLD funding application** for this project as part of the wider funding investigation agreed at the **31 March 2026** meeting.

**Target date:** Application draft ready for Board review by [TO BE CONFIRMED]

### Recommendation 3 — Technical Specification

**ACTION:** **Andrew** to prepare a detailed technical specification document for inclusion in funding applications, covering:

- Confirmed hardware specification (exact model, memory, storage)
- Power consumption and running cost estimates
- Comparison table of current vs. proposed capabilities
- Installation requirements and timeline

**Target date:** Specification document ready by [TO BE CONFIRMED]

### Recommendation 4 — Decision Timeline

**ACTION:** The Board should target a final decision on which funder(s) to approach by **[DATE TO BE CONFIRMED]** in order to meet the next **CLLD or Shetland Charitable Trust application deadline**.

---

## 9. Background Context

This project builds on work presented to the Board at the meeting held on **31 March 2026**, at which the CDCN Agent's current capabilities and development roadmap were demonstrated. The Board expressed interest in exploring how the system could be made more robust, more capable, and less dependent on ongoing subscription costs.

The Development Officer post — currently funded by Highlands and Islands Enterprise (HIE) — remains difficult to fill on a sustainable basis. Any investment that reduces the administrative burden on whoever holds that role (or on volunteer directors in the interim) directly supports CDCN's organisational resilience.

CDCN's governing documents, including the **AI and Ethics Policy (SPEC-2025-001)**, establish the framework within which this upgrade would operate. Local hosting aligns with the policy's principles of data sovereignty, human oversight, and ethical technology use.

---

*Draft prepared for CDCN Board of Directors — requires review and approval before any external circulation or funding applications.*

**Next steps pending Board approval:**
- Circulate technical specification to relevant funders for informal feedback
- Prepare full CLLD application
- Identify backup funder options
- Schedule follow-up Board paper with confirmed costs and timelines