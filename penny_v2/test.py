#!/usr/bin/env python3
"""
Standalone script to fetch a Twitch App Access Token via Client Credentials flow.

Usage:
  1. Install dependencies:
       pip install requests python-dotenv

  2. Create a `.env` alongside this script with:
       TWITCH_CLIENT_ID=your_client_id
       TWITCH_CLIENT_SECRET=your_client_secret

  3. Run:
       python refresh_twitch_app_token.py

The script will print the new access token, its expiry, and type.
"""
import os
import sys
import requests
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("Error: TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set in the environment or .env.", file=sys.stderr)
    sys.exit(1)


def fetch_app_token(client_id: str, client_secret: str):
    """
    Hits Twitch OAuth2 token endpoint to get an app access token.
    Returns a dict with access_token, expires_in, and token_type.
    """
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    response = requests.post(url, params=params)
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        print(f"Failed to fetch token: {e}\nResponse: {response.text}", file=sys.stderr)
        sys.exit(1)

    return response.json()


def main():
    data = fetch_app_token(CLIENT_ID, CLIENT_SECRET)
    access_token = data.get("access_token")
    expires_in = data.get("expires_in")
    token_type = data.get("token_type")

    print(f"Access Token: {access_token}")
    print(f"Expires In : {expires_in} seconds")
    print(f"Token Type : {token_type}")


if __name__ == "__main__":
    main()
