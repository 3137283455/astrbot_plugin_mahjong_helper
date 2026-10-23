"""Rebuild the user-supplied books with complete cross-page explanations.

Build-time only: numpy, Pillow, pdfplumber, pypdfium2. No OCR is used to
interpret tiles or invent explanations. A separate label verification file
records the printed Q/A numbers checked during the source audit.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
import pypdfium2 as pdfium

DETECT_SCALE = 1.3
OUTPUT_SCALE = 2.5
BODY_TOP = 40.0
BODY_BOTTOM = 798.0


def start_of(anchor):
    if anchor["kind"] == "answer":
        if anchor["image"] is None:
            raise ValueError(f"Missing original diagram: {anchor['id']}")
        return max(BODY_TOP, anchor["image"][1] - 2)
    top = anchor["badge"][1] - 5
    if anchor["image"] and anchor["badge"][1] - anchor["image"][1] < 40:
        top = min(top, anchor["image"][1] - 2)
    return max(BODY_TOP, top)


def contains(box, other, tolerance=0.15):
    return (box[0] <= other[0] + tolerance and box[1] <= other[1] + tolerance
            and box[2] >= other[2] - tolerance and box[3] >= other[3] - tolerance)


def intersects(box, other):
    return min(box[2], other[2]) - max(box[0], other[0]) > .15 and min(box[3], other[3]) - max(box[1], other[1]) > .15


def inside_chars(page, box):
    return [c for c in page["chars"] if contains(box, c["box"])]


def answer_regions(book, page, anchor, next_entry):
    pages = {p["page"]: p for p in book["pages"]}
    end_page = next_entry[0]["page"] if next_entry else book["pages"][-1]["page"]
    regions = []
    for number in range(page["page"], end_page + 1):
        current = pages[number]
        top = start_of(anchor) if number == page["page"] else BODY_TOP
        bottom = min(BODY_BOTTOM, current["size"][1] - 43)
        if next_entry and number == end_page:
            bottom = min(bottom, start_of(next_entry[1]))
        # A new chapter/theory section is not part of the previous answer.
        heading = current["heading"]
        stop = heading is not None and top <= heading < bottom
        if stop:
            bottom = heading - 2
        if bottom > top:
            box = [24, top, current["size"][0] - 24, bottom]
            chars = inside_chars(current, box)
            partial_chars = [c for c in current["chars"] if intersects(box, c["box"]) and not contains(box, c["box"])]
            if partial_chars:
                raise ValueError(f"Text cut at {anchor['id']} page {number}: {partial_chars[:2]}")
            images = [b for b in current["images"] if b[3] > top + .2 and b[1] < bottom - .2]
            if chars or images:
                partial = [b for b in images if not contains(box, b)]
                if partial:
                    raise ValueError(f"Diagram cut at {anchor['id']} page {number}: {partial}")
                regions.append({"page": number, "box": box, "text": "".join(c["text"] for c in chars),
                                "text_chars": len(chars), "image_count": len(images)})
        if stop:
            break
    if not regions or not contains(regions[0]["box"], anchor["image"]):
        raise ValueError(f"Incomplete answer diagram: {anchor['id']}")
    return regions


def question_region(page, anchor):
    top = start_of(anchor)
    bottom = anchor["image"][3] + 2
    later = [start_of(a) for a in page["anchors"] if start_of(a) > top + 1]
    if later:
        bottom = min(bottom, min(later))
    return {"page": page["page"], "box": [24, top, page["size"][0] - 24, bottom]}


def trim_white(image, padding=20):
    pixels = np.asarray(image.convert("RGB"))
    content = np.any(pixels < 245, axis=2)
    ys, xs = np.where(content)
    if not len(xs):
        raise ValueError("Unexpected blank source fragment")
    return image.crop((max(0, int(xs.min()) - padding), max(0, int(ys.min()) - padding),
                       min(image.width, int(xs.max()) + padding + 1),
                       min(image.height, int(ys.max()) + padding + 1)))


def render_regions(document, regions, output, cache):
    parts = []
    for region in regions:
        number = region["page"]
        if number not in cache:
            cache.clear()  # Bound memory; most consecutive answers share a page.
            cache[number] = document[number - 1].render(scale=OUTPUT_SCALE).to_pil().convert("RGB")
        image = cache[number]
        l, t, r, b = region["box"]
        crop = image.crop((math.floor(l * OUTPUT_SCALE), math.floor(t * OUTPUT_SCALE),
                           math.ceil(r * OUTPUT_SCALE), math.ceil(b * OUTPUT_SCALE)))
        parts.append(trim_white(crop))
    width = max(p.width for p in parts)
    result = Image.new("RGB", (width, sum(p.height for p in parts) + 24 * (len(parts) - 1)), "white")
    y = 0
    for part in parts:
        result.paste(part, ((width - part.width) // 2, y))
        y += part.height + 24
    result.save(output, "PNG", optimize=True)
    return list(result.size)


def old_region(page, anchor):
    labels = page["anchors"]
    index = labels.index(anchor)
    offset = 42 if anchor["kind"] == "question" else 45
    start = max(35, anchor["center"] * DETECT_SCALE - offset) / DETECT_SCALE
    end = ((max(35, labels[index + 1]["center"] * DETECT_SCALE
                - (42 if labels[index + 1]["kind"] == "question" else 45))) / DETECT_SCALE
           if index + 1 < len(labels) else (math.ceil(page["size"][1] * DETECT_SCALE) - 58) / DETECT_SCALE)
    return [42 / DETECT_SCALE, start, page["size"][0] - 42 / DETECT_SCALE, end]


def content_outside(image, box, allowed):
    l, t, r, b = [int(round(value * OUTPUT_SCALE)) for value in box]
    crop = np.asarray(image.crop((l, t, r, b)))
    dark = np.any(crop < 220, axis=2)
    xs = (np.arange(l, r) + .5) / OUTPUT_SCALE
    ys = (np.arange(t, b) + .5) / OUTPUT_SCALE
    outside = ((ys[:, None] < allowed[1]) | (ys[:, None] > allowed[3])
               | (xs[None, :] < allowed[0]) | (xs[None, :] > allowed[2]))
    return int(np.count_nonzero(dark & outside))


def build(source_dir, output_dir, scan_path, verified_path, legacy_dir):
    books = json.loads(scan_path.read_text(encoding="utf-8"))
    verified = json.loads(verified_path.read_text(encoding="utf-8"))
    records, audits, question_audits, source_hashes = [], [], [], {}
    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    for book in books:
        source = source_dir / book["filename"]
        source_hashes[book["code"]] = hashlib.sha256(source.read_bytes()).hexdigest()
        document = pdfium.PdfDocument(source)
        entries = [(p, a) for p in book["pages"] for a in p["anchors"]]
        questions, answers, cache = {}, {}, {}
        for index, (page, anchor) in enumerate(entries):
            ident = anchor["id"]
            if verified.get(ident, {}).get("number") != anchor["number"]:
                raise ValueError(f"Printed label not verified: {ident}")
            regions = (answer_regions(book, page, anchor, entries[index + 1] if index + 1 < len(entries) else None)
                       if anchor["kind"] == "answer" else [question_region(page, anchor)])
            path = image_dir / f"{ident}.png"
            size = render_regions(document, regions, path, cache)
            entry = {"image": f"images/{path.name}", "page": page["page"]}
            if anchor["kind"] == "question":
                old_box = old_region(page, anchor)
                full = cache[page["page"]]
                missing_pixels = content_outside(full, regions[0]["box"], old_box)
                extra_pixels = content_outside(full, old_box, regions[0]["box"])
                reused = missing_pixels == 0 and extra_pixels == 0
                question_audits.append({"id": ident.removesuffix("-question"), "source_region": regions[0],
                                        "old_missing_pixels": missing_pixels, "old_extra_pixels": extra_pixels,
                                        "original_reused": reused, "rebuilt_preview": entry["image"]})
                if reused:
                    old = legacy_dir / "images" / f"{ident}.jpg"
                    shutil.copy2(old, image_dir / old.name)
                    entry["image"] = f"images/{old.name}"
            (answers if anchor["kind"] == "answer" else questions)[anchor["number"]] = entry
            if anchor["kind"] == "answer":
                old_box = old_region(page, anchor)
                expected = [(r["page"], c) for r in regions for c in inside_chars(
                    next(p for p in book["pages"] if p["page"] == r["page"]), r["box"])]
                missing = sum(number != page["page"] or not contains(old_box, c["box"]) for number, c in expected)
                old_chars = [c for c in page["chars"] if intersects(old_box, c["box"])]
                new_first_box = regions[0]["box"]
                extra = sum(not contains(new_first_box, c["box"]) for c in old_chars)
                clipped_top = max(0, old_box[1] - anchor["image"][1])
                audits.append({"id": ident.removesuffix("-answer"), "source_pages": [r["page"] for r in regions],
                               "regions": regions, "text_chars": len(expected), "old_missing_chars": missing,
                               "old_extra_chars": extra, "old_diagram_top_cut_points": round(clipped_top, 2),
                               "answer_image": entry["image"], "image_size": size,
                               "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        assert len(questions) == len(answers) == book["count"]
        for number in range(1, book["count"] + 1):
            q, a = questions[number], answers[number]
            records.append({"id": f"{book['code']}-{number:03d}", "global_id": len(records) + 1,
                            "book": book["title"], "book_question_id": number,
                            "question_image": q["image"], "answer_image": a["image"],
                            "source_question_page": q["page"], "source_answer_page": a["page"]})
        document.close()
        print("BUILT", book["code"], book["count"], flush=True)
    summary = {"question_count": len(records), "printed_labels_verified": len(verified),
               "ocr_verified": sum(v["method"] == "windows_ocr" for v in verified.values()),
               "visually_verified_labels": sum(v["method"] == "visual" for v in verified.values()),
               "cross_page_answers": sum(len(a["source_pages"]) > 1 for a in audits),
               "answers_with_missing_text_in_old_dataset": sum(a["old_missing_chars"] > 0 for a in audits),
               "answers_with_neighbor_text_in_old_dataset": sum(a["old_extra_chars"] > 0 for a in audits),
               "answers_with_old_diagram_top_cut": sum(a["old_diagram_top_cut_points"] > .5 for a in audits),
               "answers_with_entire_explanation_missing": sum(a["old_missing_chars"] == a["text_chars"] for a in audits),
               "affected_answers_union": sum(bool(a["old_missing_chars"] or a["old_extra_chars"]
                                                   or a["old_diagram_top_cut_points"] > .5) for a in audits),
               "question_images_reused": sum(a["original_reused"] for a in question_audits),
               "question_images_rebuilt": sum(not a["original_reused"] for a in question_audits),
               "source_has_no_explanation_text": [a["id"] for a in audits if not a["text_chars"]],
               "source_sha256": source_hashes, "render_scale": OUTPUT_SCALE}
    for name, data in [("questions.json", records), ("audit_manifest.json", audits),
                       ("question_audit.json", question_audits), ("audit_summary.json", summary)]:
        (output_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(output_dir, records, audits, summary, legacy_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def write_report(output_dir, records, audits, summary, legacy_dir):
    rows = []
    for record, audit in zip(records, audits):
        before = (Path(os.path.relpath(legacy_dir / "images" / (record["id"] + "-answer.jpg"), output_dir)).as_posix()
                  if legacy_dir else record["question_image"])
        left_title = "修复前" if legacy_dir else "题面"
        rows.append(f"<article id='{record['id']}'><h2>#{record['global_id']} {record['id']}</h2>"
                    f"<p>原书页码 {audit['source_pages']} · 原文字符 {audit['text_chars']} · "
                    f"旧图漏字 {audit['old_missing_chars']} · 混入邻题文字 {audit['old_extra_chars']} · "
                    f"旧图题头裁掉 {audit['old_diagram_top_cut_points']}pt</p>"
                    f"<div class='pair'><section><h3>{left_title}</h3><img loading='lazy' src='{html.escape(before)}'></section>"
                    f"<section><h3>修复后（原书全解析）</h3><img loading='lazy' src='{audit['answer_image']}'></section></div>"
                    f"<details><summary>核对原书文字与裁切区域</summary><pre>{html.escape(json.dumps(audit['regions'], ensure_ascii=False, indent=2))}</pre></details></article>")
    page = """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>何切601题全量对照</title>
<style>body{font:16px system-ui;margin:30px;color:#182330;background:#eef2f6}article{background:white;padding:22px;margin:24px 0;border-radius:15px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}img{max-width:100%;height:auto}pre{white-space:pre-wrap;font:13px system-ui}h2{margin-top:0}p{line-height:1.7}</style>
<h1>何切601题全量对照</h1><p>以原书图像对象边界裁切，保留跨页解析，遇到新章节或理论专栏停止。题号独立识别并人工复核不确定项；所有答案按原书文字边界与插图边界检查。</p>"""
    page += "<pre>" + html.escape(json.dumps(summary, ensure_ascii=False, indent=2)) + "</pre>" + "".join(rows) + "</html>"
    (output_dir / "validation_report.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scan", type=Path, required=True)
    parser.add_argument("--verified-labels", type=Path, required=True)
    parser.add_argument("--legacy-dir", type=Path, required=True)
    args = parser.parse_args()
    build(args.source_dir, args.output_dir, args.scan, args.verified_labels, args.legacy_dir)
