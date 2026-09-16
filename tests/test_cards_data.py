#!/usr/bin/env python3
"""파트 일정·변경사항 카드의 규칙 검증 (salesplus-wiki/docs/SCHEDULE_SCHEMA.md).

    python3 -m unittest discover -s tests

업무일 창 계산은 위키의 build_schedule.py · 조직 README 빌더와 **구현이 같아야 한다.**
규칙을 고치면 세 곳을 같이 고치고 이 테스트를 먼저 바꾼다.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cards_data import (  # noqa: E402
    business_limit,
    holiday_set,
    load_local,
    recent_entries,
    upcoming_items,
    validate_changelog,
    validate_schedule,
)

WED = date(2026, 9, 16)  # 수
FRI = date(2026, 9, 18)  # 금
SAT = date(2026, 9, 19)  # 토


def sched(**over) -> dict:
    base = {"schema_version": 1, "generated": "2026-09-16", "holidays": [], "items": []}
    base.update(over)
    return base


def chlog(**over) -> dict:
    base = {"schema_version": 1, "generated": "2026-09-16", "keep_days": 7, "entries": []}
    base.update(over)
    return base


def item(d: str, title: str = "일정", end: str | None = None, time: str = "", project: str = "") -> dict:
    return {"date": d, "end": end, "time": time, "title": title, "project": project, "source": "파트일정"}


class BusinessLimit(unittest.TestCase):
    """오늘은 세지 않고 업무일 2개를 더 센 날."""

    def test_수요일은_금요일(self):
        self.assertEqual(business_limit(WED, set()), date(2026, 9, 18))

    def test_금요일은_다음주_화요일(self):
        self.assertEqual(business_limit(FRI, set()), date(2026, 9, 22))

    def test_토요일도_다음주_화요일(self):
        self.assertEqual(business_limit(SAT, set()), date(2026, 9, 22))

    def test_공휴일이_끼면_하루_더(self):
        self.assertEqual(business_limit(WED, {date(2026, 9, 18)}), date(2026, 9, 21))

    def test_연휴가_이어지면_그만큼_밀린다(self):
        holidays = {date(2026, 9, 17), date(2026, 9, 18)}
        self.assertEqual(business_limit(WED, holidays), date(2026, 9, 22))

    def test_holiday_set_은_읽을수_없는_날짜를_버린다(self):
        self.assertEqual(holiday_set(["2026-10-03", "미정", None]), {date(2026, 10, 3)})


class UpcomingItems(unittest.TestCase):
    def test_창_경계는_포함하고_하루_뒤는_제외(self):
        items = [item("2026-09-18", "경계 안"), item("2026-09-19", "경계 밖")]
        got = [i["title"] for i in upcoming_items(items, WED, set())]
        self.assertEqual(got, ["경계 안"])

    def test_진행_중인_기간_일정은_보이고_지난_일정은_안_보인다(self):
        items = [
            item("2026-09-14", "진행 중", end="2026-09-17"),
            item("2026-09-10", "이미 끝남", end="2026-09-15"),
            item("2026-09-15", "어제 하루짜리"),
        ]
        got = upcoming_items(items, WED, set())
        self.assertEqual([i["title"] for i in got], ["진행 중"])
        self.assertTrue(got[0]["ongoing"])

    def test_오늘_시작은_진행_중이_아니다(self):
        got = upcoming_items([item("2026-09-16", "오늘")], WED, set())
        self.assertEqual([i["ongoing"] for i in got], [False])

    def test_공휴일이_끼면_창이_늘어_더_보인다(self):
        items = [item("2026-09-21", "월요일 일정")]
        self.assertEqual(upcoming_items(items, WED, set()), [])
        got = upcoming_items(items, WED, {date(2026, 9, 18)})
        self.assertEqual([i["title"] for i in got], ["월요일 일정"])

    def test_정렬은_날짜_시간_제목_순(self):
        items = [
            item("2026-09-18", "나중", time="18:00"),
            item("2026-09-18", "먼저", time="09:00"),
            item("2026-09-16", "오늘"),
            item("2026-09-18", "가", time="18:00"),
        ]
        got = [i["title"] for i in upcoming_items(items, WED, set())]
        self.assertEqual(got, ["오늘", "먼저", "가", "나중"])

    def test_읽을수_없는_날짜와_객체가_아닌_행은_건너뛴다(self):
        items = [item("미정", "날짜 없음"), "문자열", item("2026-09-16", "정상")]
        self.assertEqual([i["title"] for i in upcoming_items(items, WED, set())], ["정상"])

    def test_원본을_건드리지_않는다(self):
        items = [item("2026-09-16", "오늘")]
        upcoming_items(items, WED, set())
        self.assertNotIn("ongoing", items[0])


class RecentEntries(unittest.TestCase):
    def entry(self, d: str, card: str = "profile", name: str = "가", kind: str = "updated") -> dict:
        return {"date": d, "card": card, "name": name, "kind": kind, "summary": "요약"}

    def test_7일_창_경계(self):
        # `keep_days` 보다 오래된 줄만 버린다 — 꼭 7일 된 줄은 남는다 (.github-private 쪽과 같은 규칙)
        entries = [self.entry("2026-09-09", name="7일 전"), self.entry("2026-09-08", name="8일 전")]
        got = [e["name"] for e in recent_entries(entries, WED, 7)]
        self.assertEqual(got, ["7일 전"])

    def test_최신이_위_같은_날은_카드_이름_순(self):
        entries = [
            self.entry("2026-09-15", card="project", name="나중 날짜 아님"),
            self.entry("2026-09-16", card="project", name="나"),
            self.entry("2026-09-16", card="profile", name="하"),
            self.entry("2026-09-16", card="profile", name="가"),
        ]
        got = [(e["date"], e["card"], e["name"]) for e in recent_entries(entries, WED, 7)]
        self.assertEqual(
            got,
            [
                ("2026-09-16", "profile", "가"),
                ("2026-09-16", "profile", "하"),
                ("2026-09-16", "project", "나"),
                ("2026-09-15", "project", "나중 날짜 아님"),
            ],
        )

    def test_keep_days_가_이상하면_기본_7일(self):
        entries = [self.entry("2026-09-10", name="6일 전"), self.entry("2026-09-01", name="오래됨")]
        self.assertEqual([e["name"] for e in recent_entries(entries, WED, 0)], ["6일 전"])

    def test_원본을_건드리지_않는다(self):
        entries = [self.entry("2026-09-16")]
        recent_entries(entries, WED, 7)[0]["summary"] = "바뀜"
        self.assertEqual(entries[0]["summary"], "요약")

    def test_읽을수_없는_날짜는_버린다(self):
        self.assertEqual(recent_entries([self.entry("어제")], WED, 7), [])


class ValidateSchedule(unittest.TestCase):
    def test_정상(self):
        data = sched(holidays=["2026-10-03"], items=[item("2026-09-18", "배포")])
        self.assertEqual(validate_schedule(data, "schedule"), [])

    def test_schema_version(self):
        self.assertTrue(any("schema_version" in e for e in validate_schedule(sched(schema_version=2), "schedule")))

    def test_날짜_형식(self):
        errs = validate_schedule(sched(items=[item("2026/09/18")]), "schedule")
        self.assertTrue(any("items[0].date" in e for e in errs))

    def test_종료일_형식(self):
        errs = validate_schedule(sched(items=[item("2026-09-18", end="곧")]), "schedule")
        self.assertTrue(any("items[0].end" in e for e in errs))

    def test_제목이_비면_거부(self):
        errs = validate_schedule(sched(items=[item("2026-09-18", "")]), "schedule")
        self.assertTrue(any("items[0].title" in e for e in errs))

    def test_URL_은_거부(self):
        errs = validate_schedule(sched(items=[item("2026-09-18", "배포 https://example.com/a")]), "schedule")
        self.assertTrue(any("URL" in e for e in errs))

    def test_전화번호와_이메일도_거부(self):
        for bad in ("010-1234-5678 로 연락", "a@b.com 확인"):
            with self.subTest(bad=bad):
                self.assertTrue(validate_schedule(sched(items=[item("2026-09-18", bad)]), "schedule"))

    def test_공휴일_형식(self):
        errs = validate_schedule(sched(holidays=["10-03"]), "schedule")
        self.assertTrue(any("holidays[0]" in e for e in errs))

    def test_name_은_요구하지_않는다(self):
        self.assertEqual(validate_schedule(sched(), "schedule"), [])

    def test_객체가_아니면_거부(self):
        self.assertTrue(validate_schedule([], "schedule"))


class ValidateChangelog(unittest.TestCase):
    def entry(self, **over) -> dict:
        base = {"date": "2026-09-16", "card": "profile", "name": "가", "kind": "added", "summary": "카드 신설"}
        base.update(over)
        return base

    def test_정상(self):
        self.assertEqual(validate_changelog(chlog(entries=[self.entry()]), "changelog"), [])

    def test_잘못된_kind(self):
        errs = validate_changelog(chlog(entries=[self.entry(kind="created")]), "changelog")
        self.assertTrue(any("entries[0].kind" in e for e in errs))

    def test_잘못된_card(self):
        errs = validate_changelog(chlog(entries=[self.entry(card="meeting")]), "changelog")
        self.assertTrue(any("entries[0].card" in e for e in errs))

    def test_날짜_형식(self):
        errs = validate_changelog(chlog(entries=[self.entry(date="2026-9-16")]), "changelog")
        self.assertTrue(any("entries[0].date" in e for e in errs))

    def test_요약의_URL_은_거부(self):
        errs = validate_changelog(chlog(entries=[self.entry(summary="www.example.com 참고")]), "changelog")
        self.assertTrue(any("URL" in e for e in errs))

    def test_금지_키는_거부(self):
        errs = validate_changelog(chlog(entries=[self.entry(raw_quote="원문")]), "changelog")
        self.assertTrue(any("금지 키" in e for e in errs))

    def test_entries_가_배열이_아니면_거부(self):
        self.assertTrue(validate_changelog(chlog(entries={}), "changelog"))


class LoadLocalSingleFiles(unittest.TestCase):
    """data/schedule.json · data/changelog.json 은 폴더가 아니라 한 파일에 한 카드다."""

    def test_있으면_읽고_없으면_건너뛴다(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "profiles").mkdir(parents=True)
            (root / "data" / "projects").mkdir(parents=True)
            (root / "data" / "schedule.json").write_text(json.dumps(sched()), encoding="utf-8")
            kinds = {c.kind: c for c in load_local(root)}
            self.assertIn("schedule", kinds)
            self.assertTrue(kinds["schedule"].ok)
            self.assertEqual(kinds["schedule"].file, "schedule.json")
            self.assertNotIn("changelog", kinds)  # 없으면 경고만 남기고 건너뛴다

    def test_깨진_파일은_그_카드만_건너뛴다(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "profiles").mkdir(parents=True)
            (root / "data" / "projects").mkdir(parents=True)
            (root / "data" / "changelog.json").write_text("{", encoding="utf-8")
            card = next(c for c in load_local(root) if c.kind == "changelog")
            self.assertFalse(card.ok)
            self.assertTrue(card.errors)


if __name__ == "__main__":
    unittest.main()
