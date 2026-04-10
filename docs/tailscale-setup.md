# Tailscale Setup

## Overview
The CDCN Agent uses Tailscale to securely connect the Raspberry Pi 5 (always-on host)
to the Dell R710 inference server without exposing either to the public internet.

## Installation

### Pi 5
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
```

### R710
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --advertise-tags=tag:inference
```

## ACL Policy (tailscale admin console)
Allow only the Pi to reach the R710 Ollama port:

```json
{
  "acls": [
    {
      "action": "accept",
      "src":    ["tag:pi"],
      "dst":    ["tag:inference:11434"]
    }
  ]
}
```

## Environment Variables
Set `OLLAMA_BASE_URL` in `.env` to the R710's Tailscale IP:

```env
OLLAMA_BASE_URL=http://100.x.x.x:11434
```

## Testing
```bash
curl http://<r710-tailscale-ip>:11434/api/tags
```
