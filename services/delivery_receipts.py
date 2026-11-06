"""Durable delivery evidence for notifications with ambiguous send results."""

import json
import time
from pathlib import Path


class DeliveryReceipts:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.records: dict[str, dict] = {}
        if path is not None and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("records"), dict):
                raise ValueError("Invalid subscription delivery receipt store")
            self.records = data["records"]

    def put(self, key: str, value: dict) -> None:
        records = {**self.records, key: value}
        # Unresolved attempts must survive cleanup and plugin restarts.
        confirmed = sorted(
            (k for k, v in records.items() if v.get("status") == "confirmed"),
            key=lambda k: records[k].get("updated_at", 0),
        )
        for old in confirmed[: max(0, len(confirmed) - 512)]:
            records.pop(old)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".new")
            temporary.write_text(
                json.dumps({"version": 1, "records": records}, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(self.path)
        self.records = records

    def confirm(self, key: str, record: dict) -> None:
        self.put(key, {**record, "status": "confirmed", "updated_at": time.time()})
