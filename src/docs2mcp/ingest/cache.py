"""On-disk cache of fetched pages, so re-runs never refetch."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class CachedPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    final_url: str
    fetched_at: datetime
    content_type: str
    html: str


class PageCache:
    """One JSON file per URL under `root`."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode()).hexdigest()[:32]
        return self.root / f"{key}.json"

    def get(self, url: str) -> CachedPage | None:
        path = self._path(url)
        if not path.exists():
            return None
        page = CachedPage.model_validate_json(path.read_text(encoding="utf-8"))
        return page if page.url == url else None  # guard against a hash prefix collision

    def put(self, page: CachedPage) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        data = json.loads(page.model_dump_json())
        self._path(page.url).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
