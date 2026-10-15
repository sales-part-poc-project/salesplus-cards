#!/usr/bin/env python3
"""e스포츠 전적 페이지(build_esports.py)의 검증·집계 규칙.

    python3 -m unittest discover -s scripts -p 'test_*.py'

파트너·천적을 고르는 규칙(2판 이상 우선, 다 같으면 '최악' 없음)과
점심 출석의 분모(기록 있는 날만)를 바꾸면 이 테스트부터 고친다.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_esports import RECORDS, compute, pick, render, validate, write_page  # noqa: E402


def base(**over):
    data = {
        "schema_version": 1,
        "title": "별들의 전쟁",
        "members": [{"id": n, "race": r} for n, r in (("가", "테란"), ("나", "저그"), ("다", "프로토스"), ("라", ""))],
        "matches": [],
        "lunch": [],
    }
    data.update(over)
    return data


def game(win, lose):
    return {"win": win, "lose": lose}


class 검증(unittest.TestCase):
    def test_정상(self):
        self.assertEqual(validate(base()), [])

    def test_저장소_기록이_통과한다(self):
        data = json.loads(RECORDS.read_text(encoding="utf-8"))
        self.assertEqual(validate(data), [])

    def test_모르는_이름(self):
        errs = validate(base(matches=[{"date": "2026-10-02", "games": [game(["가"], ["하"])]}]))
        self.assertTrue(any("하" in e for e in errs))

    def test_양쪽_팀에_같은_사람(self):
        errs = validate(base(matches=[{"date": "", "games": [game(["가", "나"], ["나"])]}]))
        self.assertTrue(any("같은 사람" in e for e in errs))

    def test_이긴_팀은_비면_안_된다(self):
        self.assertTrue(validate(base(matches=[{"date": "", "games": [game([], ["가"])]}])))

    def test_점심_하루에_두_상태(self):
        errs = validate(base(lunch=[{"date": "2026-10-06", "gym": ["가"], "skip": ["가"]}]))
        self.assertTrue(any("동시에" in e for e in errs))

    def test_점심에_사유_키를_넣으면_거부(self):
        errs = validate(base(lunch=[{"date": "2026-10-06", "skip": ["가"], "reason": {"가": "약속"}}]))
        self.assertTrue(any("reason" in e for e in errs))

    def test_같은_날짜_두_번(self):
        day = {"date": "2026-10-06", "gym": ["가"]}
        self.assertTrue(any("두 번" in e for e in validate(base(lunch=[day, dict(day)]))))

    def test_링크는_금지(self):
        data = base()
        data["members"][0]["tagline"] = "https://example.com"
        self.assertTrue(any("URL" in e for e in validate(data)))

    def test_모르는_유닛(self):
        data = base()
        data["members"][0]["unit"] = "mutalisk"
        self.assertTrue(any("unit" in e for e in validate(data)))

    def test_렌더링을_죽이던_모양새는_검증에서_걸린다(self):
        # 리뷰에서 재현한 것들 — 전부 validate 가 막아야 build_site 가 안 죽는다
        cases = [
            base(next={"date": "10/23"}),
            base(next="10/23"),
            base(matches=[{"date": "", "games": [game([["가"]], [])]}]),
            base(lunch=[{"date": "2026-10-06", "gym": "가"}]),
            base(lunch=[{"date": "2026-10-06", "gym": [1]}]),
            base(lunch=[{"date": "2026-10-10", "gym": ["가"]}]),  # 토요일
        ]
        bad_member = base()
        bad_member["members"][0]["race"] = ["테란"]
        cases.append(bad_member)
        for data in cases:
            with self.subTest(data=json.dumps(data, ensure_ascii=False)[:80]):
                self.assertTrue(validate(data))

    def test_모르는_종족(self):
        data = base()
        data["members"][0]["race"] = "인간"
        self.assertTrue(any("race" in e for e in validate(data)))


class 집계(unittest.TestCase):
    def test_전적과_상대전적(self):
        s = compute(base(matches=[{"date": "", "games": [game(["가", "나"], ["다"]), game(["다"], ["가"])]}]))
        self.assertEqual(s["record"]["가"], [1, 1])
        self.assertEqual(s["record"]["다"], [1, 1])
        self.assertEqual(s["pair"][("가", "나")], [1, 0])
        self.assertEqual(s["h2h"][("가", "다")], [1, 1])
        self.assertEqual(s["games"], 2)

    def test_진_팀이_비어도_승만_센다(self):
        s = compute(base(matches=[{"date": "", "games": [game(["가", "나"], [])]}]))
        self.assertEqual(s["record"]["가"], [1, 0])
        self.assertEqual(s["h2h"], {})

    def test_한_판_전승보다_두_판_이상을_먼저_본다(self):
        self.assertEqual(pick([("컴", 1, 0), ("나", 3, 2)], True)[0], "나")
        self.assertEqual(pick([("컴", 1, 0)], True)[0], "컴")  # 2판 이상이 없으면 전체에서

    def test_다_반반이면_최악의_파트너는_없다(self):
        games = [game(["가", "나"], ["다", "라"]), game(["다", "라"], ["가", "나"]),
                 game(["가", "다"], ["나", "라"]), game(["나", "라"], ["가", "다"])]
        p = compute(base(matches=[{"date": "", "games": games}]))["people"]["가"]
        self.assertIsNotNone(p["best"])
        self.assertIsNone(p["worst"])

    def test_같이_이긴_적이_없으면_최고의_파트너도_없다(self):
        p = compute(base(matches=[{"date": "", "games": [game(["다"], ["가", "나"])]}]))["people"]["가"]
        self.assertIsNone(p["best"])

    def test_천적은_진_적이_더_많을_때만(self):
        games = [game(["다"], ["가"]), game(["다"], ["가"]), game(["가"], ["나"]), game(["나"], ["가"])]
        p = compute(base(matches=[{"date": "", "games": games}]))["people"]["가"]
        self.assertEqual(p["nemesis"][0], "다")
        self.assertIsNone(p["prey"])

    def test_점심_출석률은_기록된_날만_분모(self):
        lunch = [{"date": "2026-10-06", "gym": ["가"], "skip": ["나"]},
                 {"date": "2026-10-07", "run": ["가"]},
                 {"date": "2026-10-08", "star": ["가", "나"]}]
        c = compute(base(lunch=lunch))["lunch"]
        self.assertEqual((c["가"]["active"], c["가"]["days"]), (3, 3))
        self.assertEqual((c["나"]["active"], c["나"]["days"]), (1, 2))
        self.assertIsNone(c["라"]["ratio"])


class 렌더링(unittest.TestCase):
    def test_이름은_이스케이프된다(self):
        data = base()
        data["members"][0]["nickname"] = "<script>x</script>"
        out = render(data, compute(data), "2026-10-16 09:00 KST")
        self.assertNotIn("<script>x</script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_JSON_블록에_꺾쇠가_남지_않는다(self):
        data = base()
        data["members"][0]["id"] = "<!--<script"
        out = render(data, compute(data), "2026-10-16 09:00 KST")
        blob = out.split('id="es-data">', 1)[1].split("</script>", 1)[0]
        self.assertNotIn("<", blob)

    def test_검증에_걸리면_파일을_쓰지_않는다(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "records.json"
            src.write_text(json.dumps(base(schema_version=9)), encoding="utf-8")
            errs = write_page(Path(tmp) / "out", src)
            self.assertTrue(errs)
            self.assertFalse((Path(tmp) / "out" / "esports.html").exists())

    def test_저장소_기록으로_페이지가_만들어진다(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(write_page(Path(tmp)), [])
            html = (Path(tmp) / "esports.html").read_text(encoding="utf-8")
            self.assertIn("별들의 전쟁", html)
            self.assertNotIn("http://", html.replace("http://www.w3.org", ""))
            # 페이지가 가리키는 그림은 전부 복사되고, 안 쓰는 그림은 옮기지 않는다
            refs = set(re.findall(r"esports-assets/([a-z-]+)\.png", html))
            copied = {p.stem for p in (Path(tmp) / "esports-assets").glob("*.png")}
            self.assertTrue(refs)
            self.assertEqual(refs, copied)


if __name__ == "__main__":
    unittest.main()
