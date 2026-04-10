# CDCN Agent — Operations Guide

## Starting, Stopping, and Restarting

The agent runs as a systemd service:
```bash
sudo systemctl start cdcn-agent
sudo systemctl stop cdcn-agent
sudo systemctl restart cdcn-agent
sudo systemctl status cdcn-agent
```

The service is configured to auto-restart on failure (max 5 restarts in 120 seconds).

## Checking Logs

```bash
# Recent logs
sudo journalctl -u cdcn-agent --since "1 hour ago" --no-pager

# Follow live logs
sudo journalctl -u cdcn-agent -f

# Search for errors
sudo journalctl -u cdcn-agent --since today | grep -i error
```

The agent also writes structured audit data to SQLite:
- `/var/lib/cdcn-agent/cdcn_audit.db` — all events, LLM calls, auth events
- View via: `sqlite3 /var/lib/cdcn-agent/cdcn_audit.db "SELECT * FROM auth_events ORDER BY ts DESC LIMIT 20;"`

## Diagnosing Issues

### Agent won't start
1. Check logs: `sudo journalctl -u cdcn-agent --since "5 minutes ago"`
2. Common causes:
   - `.env` permission error — ensure `/etc/cdcn-agent/.env` is readable by cdcn user (640, root:cdcn)
   - Port already in use — check `ss -tlnp | grep 8400`
   - Python dependency missing — run `.venv/bin/pip install -r requirements.txt`
3. Test manually: `cd /opt/cdcn-agent && sudo -u cdcn .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8400`

### Agent starts but chat doesn't work
1. Check health: `curl http://localhost:8400/health`
2. Check LLM connectivity: the agent uses SiliconFlow or local Ollama
3. Check wake/dream state — agent may be in dream mode (rest hours)

### Slow responses
1. Check LLM latency in audit log: `sqlite3 /var/lib/cdcn-agent/cdcn_audit.db "SELECT AVG(latency_ms) FROM llm_calls WHERE ts > datetime('now', '-1 hour');"`
2. Check disk space: `df -h /`
3. Check memory: `free -h`

## Adding New Documents

1. **Via the web UI**: Navigate to /archive, click Upload, select files (PDF, DOCX, TXT, XLSX, etc.)
2. **Via the filesystem**: Copy files to `/var/lib/cdcn-agent/documents/` (or subdirectory), then trigger re-indexing from the chat: "Please index the new documents"
3. **Supported formats**: PDF, DOCX, TXT, MD, XLSX, CSV, ODT, PPTX, JPG, PNG, GIF
4. **Size limit**: 10 MB per file
5. **Auto-indexing**: The agent's dream worker re-indexes during overnight cycles

Documents are parsed, chunked, and embedded into ChromaDB for semantic search.

## Managing Users

### Via the Admin panel (/admin):
- View all users and their roles
- Approve or reject registration requests
- Change user roles (trustee, staff, admin)
- Suspend or reactivate accounts
- Reset passwords
- Create custom roles with specific permissions

### Via command line:
```bash
cd /opt/cdcn-agent
.venv/bin/python3 -c "
from app.auth.auth import create_user
create_user('username', 'password', 'staff')
"
```

### Roles and Permissions:
- **trustee** — search, query, read pending changes, individual/group chat
- **staff** — above + index documents, write skill
- **admin** — above + add users, delete documents, approve changes, view audit log

## Rotating the API Key

1. Get a new key from the SiliconFlow dashboard
2. Edit `/etc/cdcn-agent/.env`: update `SILICONFLOW_API_KEY=new_key_here`
3. Restart: `sudo systemctl restart cdcn-agent`
4. Verify: send a test chat message and confirm response

## Backup and Restore

### Manual backup:
```bash
BACKUP_DIR="/var/lib/cdcn-agent/backups/manual_$(date +%Y%m%d_%H%M)"
mkdir -p "$BACKUP_DIR"
cp /var/lib/cdcn-agent/*.db "$BACKUP_DIR/"
cp /etc/cdcn-agent/.env "$BACKUP_DIR/"
cp -r /var/lib/cdcn-agent/documents "$BACKUP_DIR/"
```

### Restore from backup:
```bash
sudo systemctl stop cdcn-agent
cp /path/to/backup/*.db /var/lib/cdcn-agent/
sudo systemctl start cdcn-agent
```

### Automated backups:
The dream worker runs nightly backups during dream mode. Backups are stored in `/var/lib/cdcn-agent/backups/`.

## Updating the Agent Code

1. Make changes in `/home/cdcn/CDCN_Agent/` (development path)
2. Copy to production: `sudo cp -r /home/cdcn/CDCN_Agent/app/* /opt/cdcn-agent/app/`
3. Restart: `sudo systemctl restart cdcn-agent`
4. Verify: `curl http://localhost:8400/health`

Or for a full sync:
```bash
sudo rsync -av --exclude='.venv' --exclude='data' --exclude='__pycache__' \
  /home/cdcn/CDCN_Agent/ /opt/cdcn-agent/
sudo systemctl restart cdcn-agent
```

## Common Problems and Solutions

| Problem | Solution |
|---------|----------|
| "Account temporarily locked" | Wait 15 minutes (login lockout after 5 failed attempts) |
| "Permission denied: .env" | Fix permissions: `sudo chown root:cdcn /etc/cdcn-agent/.env && sudo chmod 640 /etc/cdcn-agent/.env` |
| Empty chat responses | Check LLM provider connectivity and API key validity |
| "Rest mode" message | Agent is in dream mode; wait for wake hours or trigger manually |
| High disk usage | Run `VACUUM` on databases; check `/var/lib/cdcn-agent/` for large files |
| WebSocket disconnects | Check Caddy/proxy timeouts; increase idle timeout |

## Monitoring

### Health check:
```bash
curl http://localhost:8400/health
# Returns: {"status":"ok","mode":"wake","started_at":"..."}
```

### Disk space:
```bash
df -h /
du -sh /var/lib/cdcn-agent/*
```

### Database sizes:
```bash
ls -lh /var/lib/cdcn-agent/*.db
```

## Technical Support

For technical issues beyond this guide, contact the CDCN development team or raise an issue in the project repository.
