# Dell R710 — Inference Server Setup

The R710 runs the full-size language models during wake hours (07:00–22:00 by default).
It is powered off overnight to save electricity, and woken by a WoL magic packet from the Pi.

---

## Hardware

| Component | Recommended |
|-----------|-------------|
| RAM       | 48 GB (minimum 32 GB for llama3.1:14b) |
| Storage   | 500 GB SSD or HDD for OS + model weights |
| NIC       | Broadcom or Intel (WoL-capable) |
| RAID      | Optional — single disk is fine for this use case |

Power consumption: approximately 250–400 W under load, ~50 W idle.
Budget roughly £30–£50/month at UK electricity prices if the machine runs 15 h/day.

---

## OS — Ubuntu Server 22.04 LTS

```bash
# After installation, update and install prerequisites
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y curl git htop
```

Use Ubuntu 22.04 LTS (not 24.04) — Ollama's packages are more widely tested on it.

---

## Static LAN IP

Set a DHCP reservation in your router using the R710's MAC address so it always gets
the same IP. This is the value you put in `OLLAMA_BASE_URL` in `.env`.

To find the MAC address:
```bash
ip link show | grep ether
# Output example: link/ether aa:bb:cc:dd:ee:ff brd ff:ff:ff:ff:ff:ff
```

---

## Ollama Installation

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable ollama
sudo systemctl start ollama
```

### Configure Ollama to listen on the LAN interface

Edit the Ollama systemd service to add the `OLLAMA_HOST` environment variable:

```bash
sudo systemctl edit ollama
```

Add these lines in the override editor:
```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

Then reload and restart:
```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Verify it is listening:
```bash
curl http://localhost:11434/api/tags
```

> **Security:** Never expose port 11434 to the public internet.
> Restrict it to the Tailscale interface using a firewall rule, or use Nginx basic auth
> (see Optional: Reverse Proxy below).

---

## Pull Models

### Recommended (48 GB RAM)

```bash
ollama pull llama3.1:14b          # default — good balance of speed and quality
ollama pull nomic-embed-text      # required for document embeddings
```

### Optional (highest quality, needs ~24 GB RAM headroom)

```bash
ollama pull llama3.1:32b          # significantly slower, noticeably better
```

Set `OLLAMA_MODEL=llama3.1:32b` in `/etc/cdcn-agent/.env` and restart the service.

---

## Wake-on-LAN Setup

### 1. Enable WoL in BIOS / iDRAC

- Enter the BIOS setup at boot (F2 or Delete)
- Navigate to: **System Setup → Network Settings → Integrated NIC 1**
- Set "Wake-on-LAN" to **Enabled**
- Save and exit

For iDRAC 6/7, WoL can also be configured under:
**iDRAC Settings → Network → Common Settings → Enable WoL**

### 2. Find the MAC address

```bash
ip link show | grep ether
# Look for the primary NIC (usually eth0 or eno1)
```

### 3. Configure in .env

```env
WAKEONLAN_MAC=aa:bb:cc:dd:ee:ff   # R710 primary NIC MAC
WAKEONLAN_BROADCAST=192.168.1.255 # your LAN broadcast address
WAKEONLAN_BOOT_WAIT_SECS=180      # seconds to wait for Ollama to respond
```

### 4. Test from the Pi

```bash
python3 -c "
import wakeonlan
wakeonlan.send_magic_packet('aa:bb:cc:dd:ee:ff', ip_address='192.168.1.255')
print('WoL packet sent')
"
```

### 5. Suggested auto-power-on schedule

Add a cron job on the R710 itself to shut down at 22:10 (just after the CDCN dream
transition at 22:00), then the Pi wakes it via WoL at 07:00:

```bash
# /etc/cron.d/r710-shutdown
10 22 * * * root /sbin/shutdown -h now "Overnight power saving"
```

The CDCN scheduler sends a WoL packet at `WAKE_START_TIME` automatically.

---

## Optional: Nginx Reverse Proxy with Auth

Add a layer of bearer-token authentication in front of Ollama:

```bash
sudo apt-get install -y nginx
sudo nano /etc/nginx/sites-available/ollama
```

```nginx
server {
    listen 11435;  # exposed proxy port

    location / {
        # Require Authorization: Bearer <token>
        if ($http_authorization != "Bearer your-secret-token") {
            return 401;
        }
        proxy_pass http://127.0.0.1:11434/;
        proxy_read_timeout 300s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/ollama /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Then in `.env`:
```env
OLLAMA_BASE_URL=http://192.168.1.100:11435
OLLAMA_API_KEY=your-secret-token
```

---

## Tailscale (recommended)

Install Tailscale on the R710 so the Pi can reach it securely without a local
LAN requirement:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --advertise-tags=tag:inference
```

Then set `OLLAMA_BASE_URL` to the R710's Tailscale IP (starts with `100.`):
```env
OLLAMA_BASE_URL=http://100.x.x.x:11434
```

See [tailscale-setup.md](tailscale-setup.md) for ACL configuration.
