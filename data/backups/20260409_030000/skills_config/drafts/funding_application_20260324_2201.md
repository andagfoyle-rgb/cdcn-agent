# Funding Application — {{funder_name}}: {{programme_name}} [TO BE CONFIRMED]

**Applicant Organisation:** Community Development Company Nesting (CDCN)
**Scottish Charity Number:** SC048164
**Date of Application:** [TO BE CONFIRMED]
**Amount Requested:** £7,800
**Project Title:** CDCN AI Infrastructure Upgrade — Secure Local Language Model Hosting

---

## 1. Organisation Overview

Community Development Company Nesting (CDCN) is a Scottish charitable organisation (SC048164) and company limited by guarantee (589570), established in 2018. Based at the Aald Skül in South Nesting, Shetland, CDCN serves the local community through the management of facilities including a Scrapstore, gym, growing spaces, and a community hub.

The organisation is volunteer-led at board level and has demonstrated innovation through its development of an AI assistant to support governance and administration. CDCN manages community assets and delivers projects that strengthen local resilience and wellbeing. The organisation contributes match funding to this project through existing infrastructure (server room, solar power, network) and volunteer time for setup and configuration.

---

## 2. Project Description

CDCN will acquire a high-performance computer system capable of running a large language model (LLM) locally. The project will enable the organisation's AI assistant to process governance documents, funding applications, and administrative tasks entirely on-site, ensuring data security and eliminating ongoing cloud API subscription costs.

**Current situation:** CDCN has developed an AI assistant to support its volunteer-run operations. The current system relies on an external API-based LLM, which means sensitive organisational documents are processed by an external service, creating data security concerns and ongoing subscription costs of approximately £50–100 per month. A previous attempt to host a model locally using existing hardware was too slow and lacked capacity for required tasks.

**Proposed solution:** The project will procure an Apple Mac Studio with M3 Ultra processor and 256GB unified memory. This single device can run capable 120B+ parameter models (such as Falcon 120B, Mistral Large, or Llama 3.1 405B with quantisation) with fast inference while keeping all data entirely within CDCN's own infrastructure.

**Technical specifications:**
- Apple Mac Studio M3 Ultra
- 256GB unified memory (sufficient for 120B models at 8-bit quantisation, or larger models at 4-bit)
- 1TB solid-state storage (configurable; sufficient for multiple models)

**Why this hardware:** The M3 Ultra with 256GB unified memory can run 120B parameter models at full 8-bit precision with comfortable overhead, and larger models at 4-bit quantisation. It can handle very large context windows for processing long documents such as funding applications and trustee reports. The single device offers simple setup with no multi-GPU complexity, low power consumption (~100W versus 1200W+ for multi-GPU workstation alternatives), quiet and compact operation, and excellent software support via MLX framework, Ollama, and LM Studio. The estimated lifespan is five to seven years.

**Timeline:**
- Month 1: Procurement and delivery
- Month 2: Setup, model installation, testing
- Month 3: Migration from API to local model
- Month 4 onwards: Full operation

---

## 3. Intended Outcomes

- All CDCN governance documents, funding applications, and administrative files will be processed entirely on-site — no data will leave the organisation's premises.
- Annual savings of £600–£1,200 in API subscription costs will be achieved, freeing funds for community activities.
- Faster response times for document drafting and archive searches, improving efficiency for volunteer directors.
- Enhanced capability to process longer documents (funding applications, trustee reports) through larger context windows.
- Capability to run larger, more capable AI models than previously possible, including 120B+ parameter models at full precision.
- CDCN will serve as a demonstrator project for other community development trusts considering locally-hosted AI solutions.

---

## 4. Budget Narrative

| Item | Description | Cost |
|------|-------------|------|
| Apple Mac Studio M3 Ultra | High-performance computer with 256GB unified memory, 1TB storage; capable of running 120B+ parameter language models locally | £7,100 |
| UPS battery backup | Uninterruptible power supply to protect hardware and ensure safe shutdown during power outages | £350 |
| Monitor and peripherals | Display, keyboard, and mouse for system operation | £250 |
| Contingency | Buffer for minor price variations or unforeseen costs | £100 |
| **Total** | | **£7,800** |

The Apple Mac Studio M3 Ultra represents the primary investment, providing the computational capacity required to run large language models with sufficient memory for both model weights and extended context windows. The UPS battery backup is essential in a rural Shetland location where power interruptions can occur, protecting the investment and preventing data corruption. Monitor and peripherals complete the workstation setup. A modest contingency provides flexibility for any minor price variations at time of purchase.

CDCN contributes match funding through existing infrastructure (server room, solar power, network connectivity) and volunteer time for system setup and configuration, valued in-kind.

---

## 5. Evaluation Approach

CDCN will measure project success through documented evidence of complete data localisation, verified by confirming that no organisational documents are processed through external APIs following migration. Financial savings will be tracked by comparing pre-project API subscription costs against post-implementation expenditure.

Operational performance will be assessed through response time benchmarks for document drafting and archive search tasks, comparing local model performance against the previous API-based system. The board will receive a demonstration of the system's capabilities and a written report confirming successful migration at the end of Month 3, with ongoing monitoring incorporated into regular board reporting.

---

## 6. Funder-Specific Notes

[TO BE CONFIRMED — funder details to be added once the funding body and programme are identified]

---

*Draft prepared by CDCN Agent — requires review and approval by authorised staff before submission.*
*Submission deadline: [TO BE CONFIRMED]*