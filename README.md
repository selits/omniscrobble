# Plex to Trakt Webhook Scrobbler

A lightweight, modern Python service that receives Plex Media Server webhooks and automatically tracks your TV shows and movies, updating playback status in real-time and marking episodes as watched in your Trakt account.

---

## 🌟 Features

- **Automatic Show & Movie Tracking**: Synchronizes playback in real-time (`media.play`, `media.pause`, `media.stop`) and automatically marks episodes as viewed in Trakt history upon completion (`media.scrobble`).
- **Modern GUID Resolution**: Supports Plex's modern metadata agents (`imdb://`, `tmdb://`, `tvdb://`) and provides smart fallback matching for title, year, season, and episode.
- **Robust Multipart Parsing**: Handles Plex's multipart/form-data payloads (both JSON file parts and raw form fields) without validation errors.
- **Smart Pause Handling**: Automatically finalizes scrobbles if playback is paused past the completion threshold (>=80%), preventing Trakt API 422 warnings.
- **Device Code OAuth Flow**: Headless, one-command authorization (`python auth.py`) using Trakt's official device activation code (`https://trakt.tv/activate`).
- **Automatic Token Refresh**: Transparently refreshes single-use Trakt access and refresh tokens before expiration.
- **User Whitelist**: Easily limit scrobbling to your specific Plex username so other family members/friends sharing your server don't overwrite your Trakt history.
- **Built-in Dashboard**: Access `http://<server-ip>:<PORT>/` to view live activity, recent scrobble logs, and connection health.
- **Seedbox & Docker Ready**: Tested and optimized for containerized environments (Ultra.cc, Whatbox, Docker).

---

## 🚀 Step-by-Step Setup Guide

### 1. Create a Trakt API Application

1. Log in to [Trakt.tv](https://trakt.tv) and go to **[API Applications](https://trakt.tv/oauth/applications)**.
2. Click **"New Application"**.
3. Fill in the fields:
   - **Name**: `Plex Trakt Webhook` (or any name you like)
   - **Description**: `Plex scrobbler` (optional)
   - **Redirect uri**: `urn:ietf:wg:oauth:2.0:oob`
   - *(Note: Permissions checkboxes are no longer required on Trakt; full scrobble and history access is included under the default OAuth scope)*.
4. Click **"Save App"**.
5. Copy your **Client ID** and **Client Secret**.

---

### 2. Configure Environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
nano .env
```

Set your configuration:

```ini
TRAKT_CLIENT_ID=your_client_id_from_trakt
TRAKT_CLIENT_SECRET=your_client_secret_from_trakt

# (Recommended) Restrict scrobbling to your Plex username only (leave blank to allow all users)
PLEX_ALLOWED_USERS=your_plex_username

# Host & Port: Use 0.0.0.0 so Plex containers can reach this service
SERVER_HOST=0.0.0.0
SERVER_PORT=8080

# Scrobble behavior
SCROBBLE_MODE=scrobble
SCROBBLE_THRESHOLD=80.0
```

---

### 3. Authenticate with Trakt (One-Time Setup)

Run the device authorization script:

```bash
.venv/bin/python auth.py
```

1. The script will display an 8-character code and direct you to: **`https://trakt.tv/activate`**
2. Open that link in your browser, enter the code, and click **Authorize**.
3. The script will detect your approval, fetch OAuth tokens, and save them securely to `trakt_tokens.json`.

---

## 🌐 Deploying on a Seedbox (e.g., Ultra.cc, Whatbox, VPS)

Because Plex on modern seedboxes (like **Ultra.cc**) runs inside an isolated container, follow these specific guidelines:

### 1. Find an Open Assigned Port
Ultra.cc reserves specific ports for your slot. Run:
```bash
app-ports show
```
Pick any port for an app you **do not use** (e.g. `8080` pyLoad, `14135` The Lounge, etc.). Confirm it's free:
```bash
ss -tuln | grep 8080
```
*(If it returns empty, the port is free to use).*

### 2. Network Configuration: Why `0.0.0.0` is Required
In `.env`, always set:
```ini
SERVER_HOST=0.0.0.0
SERVER_PORT=8080
```
If set to `127.0.0.1`, the service will only accept connections from the host and will block incoming requests from the Plex container.

### 3. Running 24/7 in Background with Screen
Use `screen` to keep the scrobbler running even when you disconnect from SSH:

```bash
# Start a new screen session
screen -S plex-trakt

# Inside the screen session, run:
.venv/bin/python main.py
```

- **Detach from screen** (keeps it running): Press **`Ctrl+A`** followed by **`D`**.
- **Re-attach to check live logs**:
  ```bash
  screen -x plex-trakt
  ```
- **List running screens**:
  ```bash
  screen -ls
  ```
- **Stop or clean up duplicate screens**:
  ```bash
  pkill -f "screen.*plex-trakt"
  ```

---

## 🔗 Adding the Webhook in Plex

1. Open **Plex Web** (`https://app.plex.tv/desktop`).
2. Go to **Settings (wrench icon) &rarr; Webhooks** (under your Account settings).
3. Click **Add Webhook**.
4. Enter your webhook URL:
   - **For Ultra.cc / Remote Seedbox**:
     ```text
     http://<servername>.usbx.me:<PORT>/webhook
     ```
     *(Example: `http://your-server.usbx.me:8080/webhook`)*
     > ⚠️ **Important:** Do **not** use `127.0.0.1` on Ultra.cc! Because Plex runs inside a container, `127.0.0.1` points inside the container itself instead of your server.
   - **For Local PC / Docker on same machine**:
     ```text
     http://<local-lan-ip>:8080/webhook
     ```
5. Click **Save Changes**.

---

## 💡 Troubleshooting & FAQ

### 1. `422 Unprocessable Content`
Plex sends webhooks with `filename="payload.json"` multipart file parts. The scrobbler is already built to handle both file streams and standard form fields. If you see this error, ensure you have pulled the latest code from GitHub.

### 2. `Trakt 409 Conflict/Already scrobbled` in logs
This is normal and expected! When you finish an episode, Plex sends `media.scrobble` (marking it as watched). Immediately afterward, Plex closes the player and fires `media.stop`. Trakt simply informs the scrobbler that the media was already scrobbled. The service logs this as an informational event and returns `200 OK`.

### 3. `"message":"Progress is XX%. Use stop to scrobble."`
Trakt considers any playback past 80% to be completed. If you pause a video after 80%, calling `/scrobble/pause` causes Trakt to return this message. The scrobbler automatically checks `SCROBBLE_THRESHOLD` and routes late pauses to `/scrobble/stop`.

### 4. How to verify the service is running
Run a quick health check from your terminal:
```bash
curl http://localhost:8080/health
```
Expected response:
```json
{"status":"healthy","authenticated":true,"allowed_users":["selits"],"scrobble_mode":"scrobble"}
```

---

## 🧪 Testing

To run the automated test suite locally:

```bash
.venv/bin/pytest -v
```
