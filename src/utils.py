import logging
import os

from alpaca.trading.client import TradingClient
from alpaca.trading.models import Asset, TradeAccount
from dotenv import load_dotenv


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


class Account:
    def __init__(self) -> None:
        load_dotenv()

        self.api_key = os.getenv("APCA_API_KEY_ID")
        self.secret_key = os.getenv("APCA_API_SECRET_KEY")

        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Alpaca credentials were not found. "
                "Check APCA_API_KEY_ID and APCA_API_SECRET_KEY in your .env file."
            )

        self.trading_client: TradingClient | None = None
        self.account_details: TradeAccount | None = None

    def login(self) -> "Account":
        try:
            self.trading_client = TradingClient(api_key=self.api_key, secret_key=self.secret_key, paper=True)

            # This also verifies that the credentials work.
            self.account_details = self.trading_client.get_account()
            logging.info("Successfully connected to Alpaca paper trading account.")

            return self

        except Exception as error:
            logging.exception("Failed to log in to Alpaca: %s", error)
            raise


class Ticker:
    def __init__(self, ticker: str) -> None:
        self.ticker = ticker.upper().strip()
        self.account = Account().login()

    def get_info(self) -> Asset:
        if self.account.trading_client is None:
            raise RuntimeError("The Alpaca trading client is not connected.")

        try:
            asset = self.account.trading_client.get_asset(self.ticker)
        except Exception as error:
            logging.exception("Could not retrieve ticker %s: %s", self.ticker, error)
            raise

        if asset.tradable:
            logging.info("%s is tradable.", self.ticker)
        else:
            logging.warning("%s exists but is not currently tradable.", self.ticker)

        logging.info("Asset details: %s", asset)
        return asset


"""
EXAMPLE:
if __name__ == "__main__":
    ticker = Ticker("AAPL")
    asset = ticker.get_info()
"""
