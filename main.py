import logging

from gmail_mcp.auth import get_gmail_service


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    print("Getting Gmail service (will open browser on first run)...")
    service = get_gmail_service()

    profile = service.users().getProfile(userId="me").execute()  # type: ignore[no-untyped-call]
    print(f"Email:    {profile['emailAddress']}")
    print(f"Messages: {profile['messagesTotal']}")
    print(f"Threads:  {profile['threadsTotal']}")


if __name__ == "__main__":
    main()
