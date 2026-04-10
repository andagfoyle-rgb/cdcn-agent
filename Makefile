.PHONY: install dev test lint format index hash-password \
        backup dream-test pending wake dream

PYTHON     := .venv/bin/python
UVICORN    := .venv/bin/uvicorn
CLI        := $(PYTHON) -m app.cli
SERVICE_URL := http://127.0.0.1:8400

# Override in environment: make backup BACKUP_BUCKET=my-bucket
BACKUP_BUCKET ?= cdcn-agent-backup

# ── Development ────────────────────────────────────────────────────────────────

install:
	python3.11 -m venv .venv
	.venv/bin/pip install --upgrade pip setuptools wheel
	.venv/bin/pip install -r requirements.txt

dev:
	$(UVICORN) app.main:app --reload --host 127.0.0.1 --port 8400

test:
	.venv/bin/pytest tests/ -v --tb=short

lint:
	.venv/bin/ruff check app/ tests/

format:
	.venv/bin/ruff format app/ tests/

# ── Indexing ───────────────────────────────────────────────────────────────────

index:
	$(CLI) test-skill indexer --args '{"folder": "data/documents"}'

# ── Auth ───────────────────────────────────────────────────────────────────────

hash-password:
	@read -rsp "Password: " pw; echo; \
	$(PYTHON) -c "from app.auth.auth import hash_password; print(hash_password('$$pw'))"

# ── Agent mode ─────────────────────────────────────────────────────────────────

wake:
	$(CLI) wake

dream:
	$(CLI) dream

dream-test:
	@echo "Running dream cycle with mock LLM (no R710 required)..."
	$(CLI) dream --dry-run

# ── Pending changes ────────────────────────────────────────────────────────────

pending:
	$(CLI) pending

# approve and reject take an ID argument — call the CLI directly:
#   make approve ID=abc12345
approve:
	$(CLI) approve $(ID)

reject:
	$(CLI) reject $(ID) --reason "$(REASON)"

# ── Backup ────────────────────────────────────────────────────────────────────
# Prerequisites: rclone configured with a Backblaze B2 remote named 'b2'
# Set BACKUP_BUCKET in environment or here.

backup:
	@echo "Backing up CDCN Agent data to b2:$(BACKUP_BUCKET)..."
	@echo "  Data → b2:$(BACKUP_BUCKET)/data"
	rclone sync /var/lib/cdcn-agent b2:$(BACKUP_BUCKET)/data \
	    --progress \
	    --exclude "chroma/**.lock"
	@echo "  Config → b2:$(BACKUP_BUCKET)/config (secrets excluded)"
	rclone sync /etc/cdcn-agent b2:$(BACKUP_BUCKET)/config \
	    --progress \
	    --exclude ".env"
	@echo "  Skills config → b2:$(BACKUP_BUCKET)/skills_config"
	rclone sync /opt/cdcn-agent/skills_config b2:$(BACKUP_BUCKET)/skills_config \
	    --progress
	@echo "Backup complete."
