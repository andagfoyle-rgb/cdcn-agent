# CDCN Agent Enhancement Specification

| | |
|---|---|
| **Document Number** | SPEC-2025-001 |
| **Version** | 1.0 |
| **Status** | Draft |
| **Effective Date** | [TO BE CONFIRMED] |
| **Review Date** | [TO BE CONFIRMED] |
| **Prepared By** | CDCN Agent |
| **Approved By** | Board of Directors of CDCN |
| **Date Approved** | [TO BE CONFIRMED] |
| **Document Owner** | [TO BE CONFIRMED] |

---

## 1. Introduction

### 1.1 Purpose of the Specification

This document defines the technical requirements for enhancements to CDCN Agent, the artificial intelligence assistant supporting Community Development Company Nesting. It provides a comprehensive blueprint for implementing new features identified through analysis of board meeting minutes and organisational needs. The specification is intended to guide development work and ensure that enhancements align with CDCN's operational requirements and charitable objectives.

### 1.2 Background on CDCN Agent Current Capabilities

CDCN Agent currently provides the following core functions:

- **Document generation**: Creating minutes, policies, funding applications, and reports
- **Memory system**: Storing and retrieving organisational knowledge via memory files
- **Dream mode**: Analysing relationships between documents to identify connections
- **Web application**: A FastAPI-based interface for interacting with the agent
- **Style compliance**: Adhering to the CDCN style guide for consistent output

The agent operates under the AI and Ethics Policy, which mandates Human-on-the-Loop (HOTL) oversight for all automated processes.

### 1.3 Rationale for Enhancements

Analysis of meeting minutes and organisational documents has identified several recurring challenges:

- **Deadline management**: Funding applications, reporting requirements, and statutory obligations have explicit deadlines that are currently tracked manually
- **Action point follow-up**: Actions arising from board meetings are recorded in minutes but lack systematic tracking to completion
- **Funding pipeline visibility**: Multiple funding sources and applications require centralised oversight
- **Meeting preparation**: Board members require consolidated information packs prior to meetings
- **Contact management**: Segmented contact lists for members, volunteers, gym members, and general mailing list need maintenance with GDPR compliance
- **Document relationships**: Dream mode analysis exists but is not easily accessible to users

These enhancements will support CDCN's mission by improving administrative efficiency and ensuring compliance with regulatory requirements.

---

## 2. Deadline and Obligation Tracker

### 2.1 Feature Overview and Objectives

The Deadline and Obligation Tracker will provide systematic monitoring of time-sensitive commitments including funding deadlines, statutory reporting dates, policy review dates, and contractual obligations. The objective is to prevent missed deadlines and provide advance warning to relevant stakeholders.

### 2.2 Data Model

```
Deadline Entity
├── deadline_id: unique identifier (string)
├── title: brief description (string)
├── category: enum [funding, statutory, policy_review, contractual, event, other]
├── type: enum [hard_deadline, soft_deadline, reminder]
├── due_date: ISO 8601 date (YYYY-MM-DD)
├── reminder_intervals: array of days before due date [30, 14, 7, 1]
├── status: enum [pending, in_progress, completed, overdue, deferred]
├── assigned_to: role or individual name
├── source_document: reference to originating document
├── notes: additional context (string, optional)
├── created_date: ISO 8601 date
├── completed_date: ISO 8601 date (optional)
└── escalation_contact: role for overdue escalation
```

### 2.3 Storage Requirements

Deadlines will be stored in the existing memory system using YAML format:

```
memory/
├── deadlines/
│   ├── active.yaml        # Current active deadlines
│   ├── completed.yaml     # Archive of completed items
│   └── templates.yaml     # Recurring deadline templates
```

Example YAML structure:

```yaml
- deadline_id: "DL-2026-001"
  title: "OSCR Annual Return Submission"
  category: "statutory"
  type: "hard_deadline"
  due_date: "2026-04-30"
  reminder_intervals: [30, 14, 7, 1]
  status: "pending"
  assigned_to: "Secretary"
  source_document: "OSCR guidance letter"
  notes: "Requires board approval of annual accounts first"
  created_date: "2026-01-15"
  escalation_contact: "Chair"
```

### 2.4 Agent Skills Required

| Skill Name | Description | Parameters |
|------------|-------------|------------|
| add_deadline | Creates a new deadline entry | title, category, due_date, assigned_to, notes (optional) |
| list_upcoming | Returns deadlines within specified timeframe | days_ahead (default 30), category (optional) |
| mark_complete | Updates deadline status to completed | deadline_id, completed_date |
| escalate_overdue | Identifies and flags overdue items | none (checks all active deadlines) |
| edit_deadline | Modifies existing deadline | deadline_id, field, new_value |

### 2.5 Web Interface Requirements

**Calendar View**
- Month and week grid displays
- Colour-coded by category
- Click-through to detail view
- Filters by category and status

**List View**
- Sortable table with columns: Title, Category, Due Date, Status, Assigned To
- Filter controls for status and category
- Quick action buttons (complete, edit, defer)

**Notification Display**
- Alert banner for overdue items
- Summary of items due within seven days
- Integration with email notifications (optional future enhancement)

### 2.6 Integration with Existing Memory Skill

The deadline tracker will integrate with the existing memory skill through:

- Shared YAML file structure in memory directory
- Cross-referencing with source documents using document IDs
- Automatic extraction from meeting minutes (see Section 3)

---

## 3. Action Point Persistence

### 3.1 Feature Overview

Action Point Persistence ensures that actions arising from board meetings are systematically tracked from identification through to resolution. Currently, actions are recorded in minutes but may not receive follow-up. This feature creates a persistent action register that surfaces outstanding items.

### 3.2 Data Model

```
Action Entity
├── action_id: unique identifier (format: ACT-YYYY-MM-NNN)
├── meeting_reference: reference to originating minutes document
├── meeting_date: ISO 8601 date of the meeting
├── description: full text of the action
├── assigned_to: individual name or role
├── due_date: ISO 8601 date (optional if not specified)
├── status: enum [open, in_progress, completed, deferred, closed]
├── priority: enum [high, medium, low, unspecified]
├── resolution_notes: description of outcome (optional)
├── resolution_date: ISO 8601 date (optional)
├── created_date: ISO 8601 date
└── last_updated: ISO 8601 datetime
```

### 3.3 Extraction from Meeting Minutes

Automatic parsing will extract actions from minutes documents using pattern recognition:

**Pattern to match (from style guide):**
```
**ACTION:** [task description] — [name responsible], by [target date]
```

**Extraction process:**
1. Scan minutes document for **ACTION:** marker
2. Parse task description, responsible person, and target date
3. Create new action entity with extracted data
4. Link to source meeting reference
5. Assign default status of "open"

Example parsed action:

| Field | Value |
|-------|-------|
| action_id | ACT-2026-02-001 |
| meeting_reference | MIN-2026-02-12 |
| meeting_date | 2026-02-12 |
| description | Contact SSEN regarding portable generator funding timeline |
| assigned_to | [TO BE CONFIRMED] |
| due_date | [TO BE CONFIRMED] |
| status | open |
| priority | unspecified |

### 3.4 Status Tracking Workflow

```
                    ┌─────────────┐
                    │    OPEN     │
                    └──────┬──────┘
                           │
                    Started work
                           │
                           ▼
                    ┌─────────────┐
          ┌────────│ IN_PROGRESS │────────┐
          │        └─────────────┘        │
       Deferred                      Completed
          │                               │
          ▼                               ▼
    ┌───────────┐                  ┌─────────────┐
    │  DEFERRED │                  │  COMPLETED  │
    └─────┬─────┘                  └──────┬──────┘
          │                               │
    Re-activated                    Confirmed closed
          │                               │
          └───────────┬───────────────────┘
                      ▼
               ┌─────────────┐
               │   CLOSED    │
               └─────────────┘
```

### 3.5 Web Interface

**Action List View**
- Filterable table: Status, Assigned To, Meeting Date, Due Date
- Status indicator badges (colour-coded)
- Sort by due date (overdue items highlighted)

**Action Detail View**
- Full description and context
- Source meeting link
- Status update controls
- Resolution notes field

**Mark Complete Interface**
- Checkbox or button for status update
- Required field: Resolution notes
- Optional field: Resolution date (defaults to current date)

### 3.6 Standing Agenda Integration

Outstanding actions will automatically appear in meeting preparation packs (Section 5). A standing agenda item template will include:

1. Review of actions completed since last meeting
2. Status update on actions in progress
3. Escalation of overdue or blocked actions
4. Assignment of new actions arising

---

## 4. Funding Pipeline Dashboard

### 4.1 Feature Overview

The Funding Pipeline Dashboard provides visibility into the status of all funding applications and active grants. It centralises information on funding sources, amounts, deadlines, and reporting requirements to support financial planning and compliance.

### 4.2 Data Model

```
Funding Opportunity Entity
├── opportunity_id: unique identifier (FUND-YYYY-NNN)
├── funder_name: name of funding body
├── fund_name: specific fund or programme name
├── amount_requested: decimal in £ sterling
├── amount_awarded: decimal in £ sterling (optional)
├── status: enum (see pipeline stages below)
├── application_deadline: ISO 8601 date (optional)
├── submission_date: ISO 8601 date (optional)
├── decision_date: ISO 8601 date (optional)
├── project_title: brief project description
├── reporting_requirements: array of report objects
│   ├── report_type: enum [quarterly, annual, final, other]
│   ├── due_date: ISO 8601 date
│   └── status: enum [pending, submitted]
├── contact_person: CDCN lead for this application
├── notes: additional context
├── created_date: ISO 8601 date
└── last_updated: ISO 8601 datetime
```

### 4.3 Pipeline Stages

| Stage | Description | Entry Criteria | Exit Criteria |
|-------|-------------|----------------|---------------|
| Identified | Funding opportunity identified, not yet in development | Potential funder/source found | Decision to pursue or decline |
| In Progress | Application being developed | Work started on application | Application submitted or abandoned |
| Submitted | Application submitted to funder | All required materials sent | Decision received |
| Approved | Funding awarded, not yet active | Formal approval received | Grant agreement signed |
| Active | Grant active, project underway | Funds received or drawdown started | Project complete, final report due |
| Declined | Application unsuccessful | Rejection received | N/A (terminal state) |
| Closed | Project complete, all reporting done | Final report accepted | N/A (terminal state) |

**Pipeline flow diagram:**

```
┌────────────┐   ┌─────────────┐   ┌───────────┐   ┌──────────┐
│ IDENTIFIED │──▶│ IN_PROGRESS │──▶│ SUBMITTED │──▶│ APPROVED │
└────────────┘   └─────────────┘   └─────┬─────┘   └────┬─────┘
                        │                │              │
                        │                │              ▼
                        │                │        ┌──────────┐
                        │                │        │  ACTIVE  │
                        │                │        └────┬─────┘
                        │                │              │
                        │                ▼              ▼
                        │          ┌───────────┐ ┌──────────┐
                        │          │  DECLINED │ │  CLOSED  │
                        │          └───────────┘ └──────────┘
                        │
                        ▼
                  ┌───────────┐
                  │  ABANDONED│
                  └───────────┘
```

### 4.4 Web Interface

**Kanban Board View**
- Columns for each pipeline stage
- Cards showing: funder, amount, project title
- Drag-and-drop status update (optional)
- Filter by funder, amount range, date range

**Table View**
- Comprehensive list with all fields
- Sortable and filterable
- Export to CSV option

**Detail View**
- Full funding opportunity details
- Timeline of status changes
- Linked reporting requirements
- Related documents (applications, correspondence)

### 4.5 Automated Reminders for Reporting Deadlines

Reporting deadlines will integrate with the Deadline Tracker (Section 2). When a funding opportunity moves to "Active" status, the system will:

1. Parse reporting requirements from the funding record
2. Create corresponding deadline entries
3. Associate deadlines with the funding opportunity
4. Trigger standard reminder intervals

---

## 5. Meeting Preparation Assistant

### 5.1 Feature Overview

The Meeting Preparation Assistant generates consolidated information packs for board meetings, ensuring directors have access to relevant information before each meeting. This reduces preparation time and supports informed decision-making.

### 5.2 Automatic Generation of Preparation Pack

Preparation packs will be generated on demand or on a scheduled basis prior to meetings. The agent skill `generate_meeting_prep` will aggregate information from multiple sources.

### 5.3 Components

**Outstanding Actions Report**
- List of all open actions
- Actions due before the meeting date (highlighted)
- Actions overdue (flagged for escalation)

**Upcoming Deadlines Report**
- Deadlines falling within the next 30 days
- Categorised by type (funding, statutory, policy review)
- Any overdue items flagged

**Policy Reviews Due**
- List of policies approaching review date
- Links to current policy documents
- Recommendation for agenda inclusion

**Draft Agenda**
- Standing items (apologies, previous minutes, matters arising)
- Outstanding actions review
- Upcoming deadlines note
- Policy reviews requiring attention
- Custom items from previous meeting's "next meeting" section

### 5.4 Delivery Mechanism

| Option | Description | Implementation |
|--------|-------------|----------------|
| Web download | Access via CDCN Agent web interface | Primary method |
| Email | Automated email to board distribution list | Requires email configuration |
| Print format | PDF generated for printing | Included in web interface |

The initial implementation will focus on web download, with email delivery as a future enhancement.

### 5.5 Template Structure

```
MEETING PREPARATION PACK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Community Development Company Nesting
Meeting Date: [DATE]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. DRAFT AGENDA
   [Generated agenda items]

2. OUTSTANDING ACTIONS
   [Table of open actions with status]

3. UPCOMING DEADLINES
   [Deadlines within 30 days]

4. POLICY REVIEWS DUE
   [Policies requiring review]

5. MATTERS FOR DECISION
   [Items requiring board approval]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generated: [DATETIME]
Pack prepared by CDCN Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 6. Member and Volunteer Directory

### 6.1 Feature Overview

The Member and Volunteer Directory provides segmented contact management for different stakeholder groups. It supports GDPR compliance through consent tracking and enables targeted communications while maintaining a comprehensive communication log.

### 6.2 Data Model

```
Contact Entity
├── contact_id: unique identifier (CON-NNNNN)
├── full_name: string
├── email: string (optional)
├── phone_primary: string (optional)
├── phone_secondary: string (optional)
├── address: string (optional)
├── postcode: string (optional)
├── segments: array of enum [member, volunteer, gym_member, mailing_list]
├── consent_status: object
│   ├── contact_consent: boolean
│   ├── consent_date: ISO 8601 date
│   ├── consent_method: enum [form, verbal, email, website]
│   └── consent_text: description of what was agreed
├── join_date: ISO 8601 date
├── status: enum [active, inactive, archived]
├── notes: string (optional)
├── communication_log: array of communication records
│   ├── date: ISO 8601 date
│   ├── type: enum [email, phone, letter, meeting, other]
│   ├── subject: string
│   └── notes: string
├── created_date: ISO 8601 date
└── last_updated: ISO 8601 datetime
```

### 6.3 GDPR Considerations

**Consent Tracking**
- All contacts must have recorded consent before being added to communication lists
- Consent must specify method and date obtained
- Consent withdrawal must be supported with immediate effect

**Data Retention**
- Active contacts: retained indefinitely with consent
- Inactive contacts: review after two years of no activity
- Archived contacts: retained for seven years (statutory requirement) then deleted
- Consent records: retained for duration of data retention plus seven years

**Data Subject Rights**
- Right of access: contacts can request their data
- Right to rectification: contacts can correct their data
- Right to erasure: contacts can request deletion (subject to legal retention requirements)
- Right to restrict processing