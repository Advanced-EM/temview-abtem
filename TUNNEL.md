# Remote abTEM: running the bridge on a shared GPU machine

Use this to give a class, or the online exhibit, an abTEM backend on a GPU machine instead of each user running it
locally. The service never listens on a public interface: it stays on 127.0.0.1, and a tunnel carries traffic to it.

## 1. Tokens

Make one token per class or site:

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

Then write `tokens.txt` (keep it private, `chmod 600`):

```
# name       token                               max_preset  max_queued
course-2027  <token>                             quick       2
toma         <token>                             full        4
```

| Field | Meaning |
| --- | --- |
| `max_preset` | The largest preset the token may use (`quick`, `standard` or `full`). Run overrides above it are refused too. |
| `max_queued` | How many of its jobs may wait or run at once. |

Tokens can't see each other's jobs. Finished results are forgotten after an hour. Remove a line and restart to revoke a token.

## 2. Serve

```bash
temview-abtem serve --tokens tokens.txt          # still http://127.0.0.1:8797, now token-protected
```

Every request then needs `Authorization: Bearer <token>`. Browsers are accepted only from the exhibit's site and
localhost. Add origins with `TEMVIEW_ORIGINS=https://a.example,https://b.example`.

## 3. Tunnel (pick one)

| Option | Command | Who can reach it |
| --- | --- | --- |
| **Cloudflare Tunnel** | `cloudflared tunnel --url http://127.0.0.1:8797` (quick test), or a named tunnel on your own domain | Anyone with the URL **and** a token |
| **Tailscale** | `tailscale serve --bg 8797` | Only machines in your tailnet |
| **SSH** | from a client: `ssh -N -L 8797:127.0.0.1:8797 user@gpu-host` | Only people with an SSH account |

On a university network, use the tunnel the IT office allows. SSH is usually already allowed.

## 4. Clients

- **The exhibit:** Simulate with abTEM… → *Remote service*, enter the tunnel URL and the token.
- **curl:**

  ```bash
  curl -H "Authorization: Bearer $TOKEN" https://<tunnel>/health
  curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d @request.json https://<tunnel>/jobs
  ```

- **Python:** the same calls with `urllib` or `requests`. Results are TEM-view `.npz` with abTEM's version, settings and citation in the metadata.
