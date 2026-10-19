import os
import sys

from alpaca.trading.client import TradingClient
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    api_key = os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("APCA_API_SECRET_KEY")

    if not api_key or not secret_key:
        print("Missing APCA_API_KEY_ID or APCA_API_SECRET_KEY in .env")
        sys.exit(1)

    # paper=True is critical: it selects the paper-trading environment.
    trading_client = TradingClient(
        api_key=api_key,
        secret_key=secret_key,
        paper=True,
    )

    account = trading_client.get_account()

    print(f"Account ID: {account.id}")
    print(f"Status: {account.status}")
    print(f"Currency: {account.currency}")
    print(f"Cash: ${account.cash}")
    print(f"Portfolio value: ${account.portfolio_value}")
    print(f"Buying power: ${account.buying_power}")
    print(f"Trading blocked: {account.trading_blocked}")


if __name__ == "__main__":
    main()
