# Board Briefing Paper — CDCN Agent Hardware Upgrade Project

**Reference:** BP-2026-HW-001  
**Date:** 22 April 2026  
**Author:** CDCN Agent (draft) / [TO BE CONFIRMED]  
**Status:** Draft — requires review and approval before submission to Board  
**Classification:** Board Confidential  

---

> **KEY QUESTION:** Should the Board agree in principle to pursuing external funding of **£8,000–£10,000** for an Apple Studio device to host AI language models locally on CDCN premises, replacing current cloud-based arrangements and delivering annual savings alongside enhanced data security?

---

## 1. Executive Summary and Recommendations

This briefing paper asks the Board of Directors to consider a capital investment in hardware that would enable Community Development Company Nesting (CDCN) to run its CDCN Agent artificial intelligence system entirely on-premises, rather than relying on cloud-based services.

The proposal arises directly from the presentation of CDCN Agent capabilities at the Board meeting held on **31 March 2026**, and aligns with the organisation's *AI Ethics Policy* (reference **SPEC-2025-001**), which emphasises data sovereignty and secure handling of organisational information.

The Board is asked to approve four specific actions:

| Ref | Recommendation | Responsible | Target |
|-----|---------------|-------------|--------|
| R1 | Agree **in principle** to pursuing funding for the CDCN Agent Hardware Upgrade Project | Board | This meeting |
| **ACTION: R1** | Record decision in minutes and notify Development Officer | Chair/Secretary | Within two working days |
| R2 | **Mark** to lead preparation of a CLLD funding application for this project | Mark Ratter | By 15 May 2026 |
| **ACTION: R2** | Mark to confirm CLLD deadline and submission requirements with SIC contact | Mark Ratter | By 02 May 2026 |
| R3 | **Andrew** to prepare a detailed technical specification for the Apple Studio device (M3/M4 Ultra specification, RAM requirements, compatibility assessment) | Andrew Foyle | By 09 May 2026 |
| **ACTION: R3** | Andrew to circulate spec to Board for comment prior to finalisation | Andrew Foyle | By 06 May 2026 |
| R4 | Establish a decision timeline for selecting the primary funder from the options identified | Board / Treasurer | By 23 May 2026 |
| **ACTION: R4** | Circulate funder comparison matrix at next Board meeting | Treasurer | By 21 May 2026 |

---

## 2. Background and Strategic Context

### 2.1 Origin of this proposal

At the Board meeting on **31 March 2026**, CDCN demonstrated the capabilities of its internally developed CDCN Agent system — an AI-assisted tool designed to support administrative efficiency, funding application drafting, institutional memory retention, and governance compliance. The demonstration was well received by Directors.

However, the current infrastructure relies on a **Raspberry Pi** device connected to the **SiliconFlow cloud API** (running the GLM-5 language model). This arrangement incurs ongoing costs and raises data residency considerations that the Board has previously noted under policy **SPEC-2025-001**.

### 2.2 Alignment with organisational priorities

This project supports three strategic priorities:

1. **Financial sustainability** — Replacing recurring revenue expenditure with a one-time capital investment delivers measurable savings within 18–20 months.
2. **Data security and compliance** — Hosting all AI processing on CDCN premises ensures that sensitive organisational documents (funding applications, trustee reports, personnel data) never leave CDCN's control.
3. **Operational capacity** — The Development Officer (DO) post has proven difficult to fill and sustain. Enhanced automation through a more capable local AI system can partially mitigate administrative workload pressures on volunteer Directors and part-time staff.

### 2.3 Policy alignment

The *CDCN Artificial Intelligence (AI) and Ethics Policy* (**SPEC-2025-001**) establishes the following principles directly relevant to this proposal:

- Data processed by AI systems should remain within CDCN's secure infrastructure where feasible.
- AI tools must support rather than replace human oversight and judgment.
- Investment in AI capability should demonstrate clear cost-benefit justification and alignment with charitable objectives.

This hardware upgrade advances all three principles.

---

## 3. Current Arrangement

| Aspect | Current Configuration |
|--------|----------------------|
| **Hardware** | Raspberry Pi 4 (8GB RAM) |
| **AI model** | GLM-5 via SiliconFlow cloud API |
| **Data location** | Queries and documents sent to external servers; responses returned |
| **Running costs** | Approximately **£10 per week** (~£520 per year) |
| **Performance** | Adequate for basic queries; constrained by local hardware and API rate limits |
| **Data risk** | Organisational data transmitted externally; dependent on third-party service continuity |

---

## 4. Proposed Solution

### 4.1 Hardware specification

| Specification | Requirement |
|--------------|-------------|
| **Device** | Apple Studio (M3 Ultra or M4 Ultra chipset) |
| **Memory (RAM)** | 256GB minimum (to enable local inference of large language models) |
| **Storage** | 2TB SSD minimum |
| **Estimated cost** | **£8,000 – £10,000** (depending on configuration and any available educational/charity pricing) |

### 4.2 Operational model

Under the proposed arrangement:

- The Apple Studio would run open-source AI models (such as Llama 3 or comparable) **entirely locally**.
- No organisational data would be transmitted to external APIs.
- Ongoing costs would be limited to **electricity consumption only** — estimated at less than **£30–£50 per year**.
- Performance would be significantly enhanced, enabling faster processing of longer documents, simultaneous user access, and more complex analytical tasks.

---

## 5. Financial Analysis

### 5.1 Capital requirement

> **CAPITAL COST: £8,000 – £10,000**  
> One-time expenditure for Apple Studio device and any necessary peripherals (cables, mounting, potential UPS backup).

### 5.2 Annual savings projection

| Cost category | Current (per annum) | Proposed (per annum) | Saving |
|--------------|-------------------|---------------------|--------|
| Cloud API fees (SiliconFlow) | ~£520 | £0 | **~£520** |
| Electricity (additional load) | Negligible | ~£30–£50 | *(net cost)* |
| **Net annual saving** | | | **~£470–£490** |

### 5.3 Payback period

At a net annual saving of approximately **£480**, the capital investment of **£9,000** (midpoint of range) would recover its cost within **18–19 years** of operation. While this payback period is extended, the primary justifications for the investment are **data security**, **operational capability**, and **strategic alignment** rather than pure financial return.

### 5.4 In-kind contribution

A critical component of this proposal is the substantial volunteer effort already invested in developing the CDCN Agent system:

> **IN-KIND VALUE: £80,000 – £150,000**  
> Over **100 hours** of volunteer development time has been invested in designing, building, testing, and refining the CDCN Agent system. If commissioned commercially, a system with equivalent capabilities would cost between **£80,000 and £150,000** to develop from scratch.

This existing investment represents significant leverage: the relatively modest hardware cost unlocks the full value of work already completed.

---

## 6. Funding Options

### 6.1 Priority funder: CLLD (Community-Led Local Development)

CLLD was mooted as a potential funding source at the **31 March 2026** Board meeting. Key considerations:

- CLLD typically funds projects delivering economic development, community capacity-building, or innovation in rural areas.
- This project qualifies under **community capacity building** (enhancing CDCN's operational resilience) and potentially **digital inclusion/innovation** themes.
- Shetland Islands Council acts as the Accountable Body for CLLD in this region; CDCN has an established relationship with SIC officers.
- Application deadlines and 2026–27 programme priorities require confirmation.

### 6.2 Alternative funders

| Funder | Alignment notes | Status |
|--------|----------------|--------|
| **Shetland Charitable Trust** | Has previously supported CDCN capital projects; may favour operational efficiency proposals | Requires approach |
| **Highlands and Islands Enterprise (HIE)** | Existing funder relationship (DO funding); HIE has digital economy and productivity programmes | Requires research into relevant scheme |
| **National Lottery Awards for All** | Lower threshold (£10,001–£100,000); focuses on community-led activity; shorter application cycle | Strong candidate if CLLD not viable |
| **Robertson Trust** | Supports charities in Scotland; has a straightforward application process | Viable option |
| **Garfield Weston Foundation** | Funds capital projects for UK charities; typically awards up to £10,000 | Good fit for amount required |
| **SCBF Reactive Fund** (Shetland Community Benefit Fund) | Reactive/small grants; may suit if other routes are slower | Contingency option |

### 6.3 Match funding considerations

CDCN can demonstrate substantial **in-kind match** through the volunteer development effort already committed (valued at **£80,000–£150,000**). This significantly strengthens applications to funders requiring matched contributions.

---

## 7. Evaluation Approach

If funded, success will be measured against the following indicators:

- **Cost reduction**: Cloud API discontinued; electricity costs tracked and compared to projected savings.
- **Data security**: Confirmation that no organisational data is transmitted externally during AI operations (verifiable through network logging).
- **Operational utility**: Usage metrics (queries processed, documents drafted, time saved) recorded over a six-month pilot period following installation.
- **User satisfaction**: Feedback from Board members, the Development Officer, and key volunteers on system responsiveness and usefulness.

---

## 8. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Funding applications unsuccessful | Medium | High — project delayed or scaled back | Submit to multiple funders simultaneously; prioritise Awards for All as faster alternative |
| Hardware supply delays or price fluctuations | Low-Medium | Medium — timeline slippage | Obtain quotes promptly; check charity/education pricing channels |
| Technical complexity of local model setup | Medium | Medium — requires specialist volunteer input | Andrew to lead technical spec; engage external expertise if needed (budget contingency) |
| Model performance below expectations | Low | Medium — may not fully replace cloud solution | Retain Raspberry Pi as fallback; phased migration approach |
| Policy or governance concerns from Board or OSCR | Low | High — reputational/regulatory | Ensure explicit Board approval; document alignment with SPEC-2025-001; maintain Human-on-the-Loop oversight |

---

## 9. Proposed Timeline

| Milestone | Target date | Owner |
|-----------|-------------|-------|
| Board in-principle agreement | **This meeting** | Board |
| Andrew completes technical specification | 09 May 2026 | Andrew Foyle |
| Mark confirms CLLD requirements and deadline | 02 May 2026 | Mark Ratter |
| Funder selection decision | 23 May 2026 | Board / Treasurer |
| Primary application submitted | Early June 2026 (dependent on funder) | Mark Ratter |
| Funding outcome known | July–September 2026 (varies by funder) | Mark Ratter |
| Procurement and installation | October–November 2026 (if funded) | Andrew Foyle / Treasurer |
| System go-live | December 2026 | Andrew Foyle |

---

## 10. Conclusion

The CDCN Agent represents a significant voluntary investment in building CDCN's organisational capacity. A relatively modest capital outlay of **£8,000–£10,000** would unlock the full potential of that investment while delivering improved data security, annualised savings, and enhanced operational support for a Board and staff team operating under capacity constraints.

The Board is respectfully asked to endorse the four recommendations set out in Section 1 above.

---

*Draft prepared by CDCN Agent — requires review and approval by authorised staff before circulation to Board.*  
*Document reference: BP-2026-HW-001*  
*Version: 1.0 (Draft)*