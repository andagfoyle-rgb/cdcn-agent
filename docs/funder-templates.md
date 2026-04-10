# Funder Templates Guide

## Available Templates

| Template file | Use for |
|--------------|---------|
| `funding_application.md` | Generic grant application |
| `board_minute.md` | Board meeting minutes |
| `governance_policy.md` | Governance policy documents |

## Using a Template via Telegram
```
/write funding_application Funder: ABC Foundation; Program: Community Grants; Amount: $25,000
```

## Using a Template via API
```bash
curl -X POST http://localhost:8400/api/skills/writer/run \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "template": "funding_application",
    "context": "Funder: ABC Foundation. Program: Community Grants 2025. Amount: $25,000.",
    "save_draft": true,
    "draft_name": "abc_foundation_2025"
  }'
```

Drafts are saved to `skills_config/drafts/`.

## Adding Custom Templates
1. Create `skills_config/writer_templates/<name>.md`
2. Use `{{PLACEHOLDER}}` syntax for sections the LLM should fill
3. Reference the template by its filename (without `.md`) in API calls

## Funding Deadlines Tracker
Edit `skills_config/funding_deadlines.yaml` to track upcoming deadlines.
The agent can query this file and alert you via Telegram/Discord when deadlines approach.
