import os
import logging
from alpaca.trading.client import TradingClient


class Account:
    def __init__(self):
        self.api_key = os.getenv("APCA_API_KEY_ID")
        self.secret_key = os.getenv("APCA_API_SECRET_KEY")

        if not self.api_key and not self.secret_key:
            logging.warning("Credentials not Found")

        self.trading_client = None
        self.account_details = None

    def login(self):
        try:
            self.trading_client = TradingClient(api_key=self.api_key, secret_key=self.secret_key, paper=True)
        except Exception:
            print("Failed to Login")
        self.account = self.trading_client.get_account()
