from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hermes_second_brain.context_sources.adapters.photos import PhotosAdapter
from hermes_second_brain.context_sources.models import ScanRequest


class PhotosTests(unittest.TestCase):
    def test_only_allowlisted_metadata_is_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "Photos.sqlite"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE ZASSET(ZUUID TEXT,ZDATECREATED REAL,ZKIND INTEGER,ZFAVORITE INTEGER,ZDURATION REAL,ZLATITUDE REAL,ZLONGITUDE REAL,ZFACESECRET TEXT)")
            conn.execute("INSERT INTO ZASSET VALUES('private-uuid',800000000,0,1,0,52.5,13.4,'person-name')")
            conn.commit(); conn.close()
            batch = PhotosAdapter(db).scan(ScanRequest(), None)
        value = json.dumps(batch.raw_items[0].payload)
        self.assertNotIn("private-uuid", value)
        self.assertNotIn("person-name", value)
        self.assertNotIn("coarse_region", value)
        self.assertEqual(batch.raw_items[0].payload["media_type"], "photo")
