import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from test_ai_entry import plugin


class DatasetTests(unittest.TestCase):
    def test_every_published_answer_is_the_verified_complete_image(self):
        root = Path(__file__).resolve().parents[1] / "data"
        records = json.loads((root / "questions.json").read_text(encoding="utf-8"))
        audited = {a["id"]: a for a in json.loads((root / "audit_manifest.json").read_text(encoding="utf-8"))}
        self.assertEqual(len(records), 601)
        self.assertEqual(set(audited), {r["id"] for r in records})
        for record in records:
            audit = audited[record["id"]]
            path = root / record["answer_image"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), audit["image_sha256"], record["id"])
            self.assertGreater(audit["text_chars"], 0, record["id"])
            with Image.open(path) as im:
                self.assertEqual(list(im.size), audit["image_size"], record["id"])
                im.verify()
            with Image.open(root / record["question_image"]) as im:
                im.verify()
        self.assertEqual(audited["H300-150"]["source_pages"], [106, 107])
        self.assertGreaterEqual(audited["H300-150"]["text_chars"], 160)

    def test_replacing_source_invalidates_hd_image_even_if_timestamp_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            p = plugin(d)
            p.config["enhance_images"] = True
            source = Path(d) / "same-name.png"
            Image.new("RGB", (20, 20), "white").save(source)
            timestamp = source.stat().st_mtime
            first = p._enhanced_image_path(source)
            Image.new("RGB", (20, 20), "black").save(source)
            os.utime(source, (timestamp, timestamp))
            second = p._enhanced_image_path(source)
            self.assertNotEqual(first, second)
            with Image.open(second) as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
