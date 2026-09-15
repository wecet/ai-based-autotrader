"""Persist daily order attempts before sending broker requests."""
import hashlib
import json
from infrastructure.storage import write_json


class OrderJournal:
    limit = 10

    def __init__(self, path):
        self.state_path = path

    def read(self, day):
        if not self.state_path.exists():
            return {"day": day, "attempts": []}
        state = json.loads(self.state_path.read_text())
        if (not isinstance(state, dict) or not isinstance(state.get("day"), str)
                or not isinstance(state.get("attempts"), list)
                or not all(isinstance(item, str) for item in state["attempts"])):
            raise ValueError("Invalid order state; refusing to reset the daily limit")
        if state["day"] > day:
            raise ValueError("Order state is ahead of broker clock")
        return state if state["day"] == day else {"day": day, "attempts": []}


    def reserve(self, state, fingerprint):
        if len(state["attempts"]) >= self.limit or fingerprint in state["attempts"]:
            raise ValueError("Daily limit reached or target already attempted")
        state["attempts"].append(fingerprint)
        write_json(self.state_path, state)
        digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:20]
        return f"autotrader-{state['day']}-{digest}"
