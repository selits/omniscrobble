# Plex to Trakt Webhook Scrobbler

A lightweight, modern Python service that receives Plex Media Server webhooks and automatically tracks your TV shows and movies, updating playback status and marking episodes as watched in your Trakt account.

---

## 🌟 Features

- **Automatic Show & Movie Tracking**: Synchronizes playback in real-time (`media.play`, `media.pause`, `media.stop`) and automatically marks episodes as viewed in Trakt history upon completion (`media.scrobble`).
- **Modern GUID Resolution**: Supports Plex's modern metadata agents (`imdb://`, `tmdb://`, `tvdb://`) and provides smart fallback matching for title/season/episode.
- **Device Code OAuth Flow**: Headless, one-command authorization (`python auth.py`) using Trakt's official device activation code (`https://trakt.tv/activate`).
- **Automatic Token Refresh**: Transparently refreshes single-use Trakt access and refresh tokens before expiration.
- **User Whitelist**: Easily limit scrobbling to your specific Plex username so other family members/friends sharing your server don't overwrite your Trakt history.
- **Built-in Dashboard**: Access `http://<server-ip>:8080/` to view live activity, recent scrobble logs, and connection status.
- **Docker & Local Support**: Ready for deployment directly via systemd/terminal or Docker Compose.

---

## 🚀 Quick Start Guide

### 1. Create a Trakt API Application

1. Log in to [Trakt.tv](https://trakt.tv) and go to **[API Applications](https://trakt.tv/oauth/applications)**.
2. Click **"New Application"**.
3. Fill in the fields:
   - **Name**: `Plex Trakt Webhook` (or any name you like)
   - **Description**: (optional)
   - **Redirect uri**: `urn:ietf:wg:oauth:2.0:oob`
   *(Note: Permissions checkboxes are no longer required on Trakt; standard scrobble access is included automatically)*
4. Click **"Save App"**.
5. Copy your **Client ID** and **Client Secret**.

---

### 2. Configure Environment

In this directory, copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` and fill in your Trakt credentials:

```ini
TRAKT_CLIENT_ID=your_client_id_from_trakt
TRAKT_CLIENT_SECRET=your_client_secret_from_trakt

# (Optional) Restrict scrobbling to your Plex username only:
PLEX_ALLOWED_USERS=your_plex_username

# Port configuration (default 8080)
SERVER_PORT=8080
```

---

### 3. Authenticate with Trakt (One-Time Setup)

Run the device authorization script:

```bash
# Using the project's virtualenv
.venv/bin/python auth.py
```

1. The script will display a link (`https://trakt.tv/activate`) and an 8-character code (e.g., `3DF8A19B`).
2. Open the URL in your browser and enter the code.
3. Click **Authorize**.
4. The script will detect your approval, fetch OAuth tokens, and save them securely to `trakt_tokens.json`.

---

### 4. Run the Webhook Server

#### Option A: Running locally with Python
```bash
.venv/bin/python main.py
```

#### Option B: Running with Docker Compose
```bash
docker compose up -d
```

Visit the dashboard in your web browser:
```
http://localhost:8080/
```

---

### 5. Add Webhook to Plex Media Server

1. Open **Plex Web** (`https://app.plex.tv` or `http://<your-plex-ip>:32400/web`).
2. Click the **Settings (Wrench)** icon in the top right.
3. In the left sidebar under your account, select **Webhooks**.
4. Click **Add Webhook**.
5. Enter the URL of your scrobbler service:
   ```
   http://<YOUR_SERVER_IP>:8080/webhook
   ```
6. Click **Save Changes**.

---


---

## 🌐 Deploying on a Seedbox (e.g., Ultra.cc, Whatbox, VPS)

If your Plex Media Server runs on a remote seedbox like **Ultra.cc**:
You can run this scrobbler directly on the seedbox so it stays active 24/7 without needing your home computer. Because Plex and the scrobbler run on the same server, Plex can communicate with it locally without needing any public reverse proxy!

1. **SSH into your seedbox**:
   ```bash
   ssh username@servername.usbx.me
   ```
2. **Find an available port** assigned to your slot:
   ```bash
   app-ports show
   ```
3. **Clone and setup**:
   ```bash
   git clone https://github.com/selits/plex-trakt-webhook.git
   cd plex-trakt-webhook
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   cp .env.example .env
   ```
4. **Configure `.env`**:
   Set `SERVER_PORT=<your_assigned_port>`, `SERVER_HOST=127.0.0.1`, and your Trakt credentials.
5. **Authenticate with Trakt**:
   ```bash
   .venv/bin/python auth.py
   ```
6. **Keep it running 24/7**:
   Use `screen` or `tmux`:
   ```bash
   screen -S plex-trakt
   .venv/bin/python main.py
   ```
   *(Press `Ctrl+A` then `D` to detach and leave it running in the background).*
7. **Add Webhook in Plex Web**:
   In **Plex Web &rarr; Settings &rarr; Webhooks**, add:
   ```text
   http://127.0.0.1:<YOUR_ASSIGNED_PORT>/webhook
   ```

## 🧪 Testing

To run the automated test suite:

```bash
.venv/bin/pytest -v
```

All tests simulate Plex webhook payloads (episodes, movies, user filtering, and Trakt scrobble mapping).
