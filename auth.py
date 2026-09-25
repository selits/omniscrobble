import sys
import time
from config import Config
from trakt_client import TraktClient


def main():
    print("=" * 60)
    print("Trakt Account Authorization (Device Code Flow)")
    print("=" * 60)

    if not Config.TRAKT_CLIENT_ID or not Config.TRAKT_CLIENT_SECRET:
        print("\n[ERROR] Missing TRAKT_CLIENT_ID or TRAKT_CLIENT_SECRET in .env!")
        print("Please create an app at https://trakt.tv/oauth/applications")
        print("and add your Client ID and Client Secret to your .env file.")
        sys.exit(1)

    client = TraktClient(Config)

    print("\nRequesting authorization code from Trakt...")
    try:
        data = client.generate_device_code()
    except Exception as e:
        print(f"\n[ERROR] Failed to get device code: {e}")
        sys.exit(1)

    user_code = data.get("user_code")
    verification_url = data.get("verification_url", "https://trakt.tv/activate")
    device_code = data.get("device_code")
    expires_in = data.get("expires_in", 600)
    interval = data.get("interval", 5)

    print("\n" + "*" * 60)
    print(f" 1. Go to:   {verification_url}")
    print(f" 2. Enter:   {user_code}")
    print(" 3. Click 'Authorize' to link your Trakt account.")
    print("*" * 60)
    print(f"\nWaiting for authorization (expires in {expires_in // 60} minutes)...")

    start_time = time.time()
    while time.time() - start_time < expires_in:
        time.sleep(interval)
        try:
            res = client.poll_for_token(device_code)
            if res.get("status") == "pending":
                print(".", end="", flush=True)
                continue
            elif res.get("status") == "slow_down":
                interval += 5
                continue
            elif "access_token" in res:
                print("\n\n[SUCCESS] Successfully authenticated with Trakt!")
                print(f"Tokens saved to: {Config.TRAKT_TOKENS_FILE}")
                print("You can now start the webhook server with: python main.py")
                return
        except TimeoutError:
            print("\n[ERROR] Code expired. Please run this script again.")
            sys.exit(1)
        except PermissionError:
            print("\n[ERROR] Authorization denied by user on Trakt.")
            sys.exit(1)
        except Exception as e:
            print(f"\n[ERROR] Unexpected error: {e}")
            sys.exit(1)

    print("\n[ERROR] Timed out waiting for authorization. Please try again.")
    sys.exit(1)


if __name__ == "__main__":
    main()
