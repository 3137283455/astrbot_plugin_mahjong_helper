import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_dataset import answer_regions, start_of


def page(number, chars=(), images=(), heading=None):
    return {"page": number, "size": [595, 842], "chars": list(chars), "images": list(images), "heading": heading}


def char(text, top, bottom):
    return {"text": text, "box": [68, top, 78, bottom], "size": 10.2}


def answer(number, top=100, bottom=200):
    return {"id": f"test-{number}-answer", "number": number, "kind": "answer",
            "image": [80, top, 515, bottom], "badge": [430, top + 40, 510, top + 65]}


class BuildTests(unittest.TestCase):
    def test_unlabelled_continuation_is_kept_and_theory_chapter_is_not_attached(self):
        a = answer(150)
        first = page(106, images=[a["image"]])
        continuation = page(107, chars=[char("完整解析", 80, 92)])
        theory = page(108, chars=[char("专栏", 76, 94)], heading=76)
        next_page = page(110)
        next_a = answer(151)
        regions = answer_regions({"pages": [first, continuation, theory, page(109), next_page]}, first, a, (next_page, next_a))
        self.assertEqual([r["page"] for r in regions], [106, 107])
        self.assertEqual(regions[1]["text"], "完整解析")

    def test_top_of_next_page_before_an_answer_belongs_to_previous_answer(self):
        a, following = answer(146), answer(147, top=100)
        first = page(105, images=[a["image"]])
        second = page(106, chars=[char("上一题续文", 65, 76)], images=[following["image"]])
        regions = answer_regions({"pages": [first, second]}, first, a, (second, following))
        self.assertEqual([r["page"] for r in regions], [105, 106])
        self.assertEqual(regions[1]["text"], "上一题续文")
        self.assertLess(regions[1]["box"][3], following["image"][1])

    def test_answer_uses_diagram_top_instead_of_badge_offset(self):
        a = answer(150, top=650, bottom=765)
        a["badge"] = [430, 688, 510, 712]
        self.assertEqual(start_of(a), 648)

    def test_first_question_starts_before_entire_group_image(self):
        q = {"kind": "question", "badge": [96, 69.2, 171, 92.3], "image": [85, 62.7, 509, 512]}
        self.assertLess(start_of(q), q["image"][1])

    def test_partial_text_is_rejected_instead_of_silently_exported(self):
        a, following = answer(1), answer(2, top=400)
        first = page(1, chars=[char("不能切半行", 394, 404)], images=[a["image"], following["image"]])
        with self.assertRaisesRegex(ValueError, "Text cut"):
            answer_regions({"pages": [first]}, first, a, (first, following))


if __name__ == "__main__":
    unittest.main()
