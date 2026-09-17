#!/usr/bin/env python3
"""업무 요약·파트 일정·변경사항 카드의 규칙 검증 (salesplus-wiki/docs/DAILY_SCHEMA.md · SCHEDULE_SCHEMA.md · CHANGELOG_SCHEMA.md).

    python3 -m unittest discover -s scripts -p 'test_*.py'

업무일 창 계산(`business_window`·`events_in_window`)은 위키의 `build_schedule.py` · 조직 README 빌더와
**구현이 같아야 한다.** 규칙을 고치면 세 곳을 같이 고치고 이 테스트를 먼저 바꾼다 — 어긋나면
공개 사이트와 텔레그램 요약이 다른 날짜를 말한다.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cards_data import (  # noqa: E402
    business_window,
    events_in_window,
    holiday_set,
    load_local,
    recent_days,
    recent_entries,
    schedule_anchor,
    validate_changelog,
    validate_daily,
    validate_schedule,
)

WED = date(2026, 9, 16)  # 수
THU = date(2026, 9, 17)  # 목
FRI = date(2026, 9, 18)  # 금
SAT = date(2026, 9, 19)  # 토


def sched(**over) -> dict:
    base = {"schema_version": 1, "generated": "2026-09-16", "holidays": [], "events": [], "recurring": []}
    base.update(over)
    return base


def daily(**over) -> dict:
    base = {"schema_version": 1, "generated": "2026-09-18", "keep_days": 2,
            "days": [{"date": "2026-09-17", "rooms": [{"room": "파트방", "items": ["결정: A"]}]}]}
    base.update(over)
    return base


def chlog(**over) -> dict:
    base = {"schema_version": 1, "generated": "2026-09-16", "keep_days": 7, "entries": []}
    base.update(over)
    return base


def ev(d: str, label: str = "일정", end: str = "", time: str = "", kind: str = "회의", **over) -> dict:
    base = {
        "date": d, "end": end, "time": time, "kind": kind, "label": label,
        "members": [], "note": "", "project": "", "source": "파트일정",
    }
    base.update(over)
    return base


def rec(weekdays: list, label: str = "주간 미팅", **over) -> dict:
    base = {"weekdays": weekdays, "time": "", "kind": "회의", "label": label,
            "members": [], "from": "", "until": "", "note": ""}
    base.update(over)
    return base


class BusinessWindow(unittest.TestCase):
    """기준일이 업무일이면 그날이 첫째 날, 아니면 다음 업무일. 창은 첫째 날부터 업무일 2일."""

    def test_목요일은_목_금(self):
        self.assertEqual(business_window(THU, []), (THU, FRI))

    def test_금요일은_금_월(self):
        self.assertEqual(business_window(FRI, []), (FRI, date(2026, 9, 21)))

    def test_토요일은_월_화(self):
        self.assertEqual(business_window(SAT, []), (date(2026, 9, 21), date(2026, 9, 22)))

    def test_목이고_금이_휴일이면_목_월(self):
        self.assertEqual(business_window(THU, ["2026-09-18"]), (THU, date(2026, 9, 21)))

    def test_기준일이_휴일이면_다음_업무일이_첫째_날(self):
        self.assertEqual(business_window(THU, ["2026-09-17"]), (FRI, date(2026, 9, 21)))

    def test_연휴가_이어지면_그만큼_밀린다(self):
        self.assertEqual(business_window(WED, ["2026-09-17", "2026-09-18"]), (WED, date(2026, 9, 21)))

    def test_휴일은_date_객체로도_받는다(self):
        self.assertEqual(business_window(THU, {date(2026, 9, 18)}), (THU, date(2026, 9, 21)))

    def test_날수를_1로_주면_첫째_날_하루(self):
        self.assertEqual(business_window(FRI, [], days=1), (FRI, FRI))

    def test_holiday_set_은_읽을수_없는_날짜를_버린다(self):
        self.assertEqual(holiday_set(["2026-10-03", "미정", None]), {date(2026, 10, 3)})


class EventsInWindow(unittest.TestCase):
    def labels(self, data, today: date = THU) -> list:
        return [e["label"] for e in events_in_window(data, today)]

    def test_창_경계는_포함하고_하루_뒤는_제외(self):
        data = sched(events=[ev("2026-09-18", "경계 안"), ev("2026-09-19", "경계 밖")])
        self.assertEqual(self.labels(data), ["경계 안"])

    def test_주말에_걸친_단발_일정도_창_안이면_보인다(self):
        # 금 기준 창은 금~월이라 그 사이 토요일 일정도 들어온다
        data = sched(events=[ev("2026-09-19", "토요일 행사", kind="행사")])
        self.assertEqual(self.labels(data, FRI), ["토요일 행사"])

    def test_기간_일정이_창을_가로지르면_진행_중(self):
        data = sched(events=[
            ev("2026-09-14", "진행 중", end="2026-09-18"),
            ev("2026-09-10", "이미 끝남", end="2026-09-15"),
        ])
        got = events_in_window(data, THU)
        self.assertEqual([e["label"] for e in got], ["진행 중"])
        self.assertTrue(got[0]["ongoing"])
        self.assertEqual(got[0]["end"], "2026-09-18")

    def test_오늘_시작은_진행_중이_아니다(self):
        got = events_in_window(sched(events=[ev("2026-09-17", "오늘")]), THU)
        self.assertEqual([e["ongoing"] for e in got], [False])

    def test_하루짜리는_end_를_비운다(self):
        got = events_in_window(sched(events=[ev("2026-09-17", "하루", end="2026-09-17")]), THU)
        self.assertEqual(got[0]["end"], "")

    def test_반복_일정은_창_안_업무일에_전개된다(self):
        # 금 기준 창은 금~월. 월(0) 반복은 월요일에만 펴진다
        data = sched(recurring=[rec([0], "파트 주간 미팅")])
        got = events_in_window(data, FRI)
        self.assertEqual([(e["date"], e["label"], e["recurring"]) for e in got],
                         [("2026-09-21", "파트 주간 미팅", True)])

    def test_반복_일정은_휴일에는_펴지_않는다(self):
        data = sched(holidays=["2026-09-21"], recurring=[rec([0], "파트 주간 미팅")])
        self.assertEqual(self.labels(data, FRI), [])

    def test_반복_일정의_from_until_밖은_빠진다(self):
        self.assertEqual(self.labels(sched(recurring=[rec([0], "한시적", **{"from": "2026-09-22"})]), FRI), [])
        self.assertEqual(self.labels(sched(recurring=[rec([0], "끝난 것", until="2026-09-20")]), FRI), [])
        data = sched(recurring=[rec([0], "기간 안", **{"from": "2026-09-01", "until": "2026-12-31"})])
        self.assertEqual(self.labels(data, FRI), ["기간 안"])

    def test_반복_일정은_주말에_펴지_않는다(self):
        # 금~월 창에 토(5)·일(6) 반복이 있어도 업무일이 아니라 나오지 않는다
        self.assertEqual(self.labels(sched(recurring=[rec([5, 6], "주말 당직")]), FRI), [])

    def test_정렬은_날짜_빈시각_시각_라벨_순(self):
        data = sched(events=[
            ev("2026-09-18", "나중", time="18:00"),
            ev("2026-09-18", "먼저", time="09:00"),
            ev("2026-09-18", "종일"),
            ev("2026-09-17", "오늘"),
            ev("2026-09-18", "가", time="18:00"),
        ])
        self.assertEqual(self.labels(data), ["오늘", "종일", "먼저", "가", "나중"])

    def test_같은_날_같은_라벨_시각_멤버는_한_번만(self):
        # 단발과 반복이 겹칠 때 단발이 남는다 (2026-09-16 결정, .github-private 과 같은 규칙)
        data = sched(events=[ev("2026-09-18", "주간 배포", time="18:00", kind="배포")],
                     recurring=[rec([4], "주간 배포", time="18:00", kind="배포")])
        got = events_in_window(data, THU)
        self.assertEqual([e["label"] for e in got], ["주간 배포"])
        self.assertFalse(got[0]["recurring"])

    def test_라벨이_같아도_시각이나_멤버가_다르면_둘_다_남는다(self):
        data = sched(events=[
            ev("2026-09-18", "배포", time="18:00", kind="배포"),
            ev("2026-09-18", "배포", time="09:00", kind="배포"),
            ev("2026-09-18", "배포", time="09:00", kind="배포", members=["고윤지"]),
        ])
        got = events_in_window(data, THU)
        self.assertEqual([(e["time"], e["members"]) for e in got],
                         [("09:00", []), ("09:00", ["고윤지"]), ("18:00", [])])

    def test_읽을수_없는_날짜와_객체가_아닌_행은_건너뛴다(self):
        data = sched(events=[ev("미정", "날짜 없음"), "문자열", ev("2026-09-31", "없는 날"), ev("2026-09-17", "정상")])
        self.assertEqual(self.labels(data), ["정상"])

    def test_원본을_건드리지_않는다(self):
        data = sched(events=[ev("2026-09-17", "오늘")])
        events_in_window(data, THU)[0]["label"] = "바뀜"
        self.assertEqual(data["events"][0]["label"], "오늘")
        self.assertNotIn("ongoing", data["events"][0])

    def test_schedule_이_없으면_빈_목록(self):
        self.assertEqual(events_in_window(None, THU), [])


class RecentEntries(unittest.TestCase):
    def entry(self, d: str, card: str = "profile", name: str = "가", kind: str = "updated") -> dict:
        return {"date": d, "card": card, "name": name, "kind": kind, "summary": "요약"}

    def test_오늘_포함_7일_창_경계(self):
        # 바닥은 today-(keep_days-1) 이다 — 오늘(9/16)을 첫째 날로 세어 일곱째 날(9/10)까지 남고
        # 여덟째 날(9/9)은 빠진다 (.github-private 쪽과 같은 규칙, 2026-09-16 결정)
        entries = [self.entry("2026-09-10", name="일곱째 날"), self.entry("2026-09-09", name="여덟째 날")]
        self.assertEqual([e["name"] for e in recent_entries(entries, WED, 7)], ["일곱째 날"])

    def test_keep_days_가_1이면_오늘만(self):
        entries = [self.entry("2026-09-16", name="오늘"), self.entry("2026-09-15", name="어제")]
        self.assertEqual([e["name"] for e in recent_entries(entries, WED, 1)], ["오늘"])

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

    def test_site_변경도_그대로_남는다(self):
        entries = [{"date": "2026-09-16", "card": "site", "name": "", "kind": "updated", "summary": "섹션 순서 변경"}]
        self.assertEqual([e["card"] for e in recent_entries(entries, WED, 7)], ["site"])

    def test_keep_days_가_이상하면_기본_7일(self):
        entries = [self.entry("2026-09-10", name="6일 전"), self.entry("2026-09-01", name="오래됨")]
        self.assertEqual([e["name"] for e in recent_entries(entries, WED, 0)], ["6일 전"])

    def test_원본을_건드리지_않는다(self):
        entries = [self.entry("2026-09-16")]
        recent_entries(entries, WED, 7)[0]["summary"] = "바뀜"
        self.assertEqual(entries[0]["summary"], "요약")

    def test_읽을수_없는_날짜는_버린다(self):
        self.assertEqual(recent_entries([self.entry("어제")], WED, 7), [])


class ScheduleAnchor(unittest.TestCase):
    """파트 일정 카드는 오늘을 뺀 내일·모레 — 창 함수는 그대로, 기준일만 하루 뒤 (2026-09-17 결정)."""

    def test_목요일_기준이면_금_월(self):
        self.assertEqual(business_window(schedule_anchor(THU)), (FRI, date(2026, 9, 21)))

    def test_금요일_기준이면_월_화(self):
        self.assertEqual(business_window(schedule_anchor(FRI)), (date(2026, 9, 21), date(2026, 9, 22)))

    def test_내일이_휴일이면_그_다음_업무일부터(self):
        self.assertEqual(business_window(schedule_anchor(THU), ["2026-09-18"]), (date(2026, 9, 21), date(2026, 9, 22)))

    def test_오늘_일정은_빠지고_오늘_시작한_기간_일정은_진행_중(self):
        rows = events_in_window(sched(events=[ev("2026-09-17", "오늘"), ev("2026-09-17", "이틀", end="2026-09-18")]),
                                schedule_anchor(THU))
        self.assertEqual([(r["label"], r["ongoing"]) for r in rows], [("이틀", True)])

    def test_done_은_단발에만_실린다(self):
        rows = events_in_window(sched(events=[ev("2026-09-18", "배포", done=True)],
                                      recurring=[rec([4], "스크럼", done=True)]), FRI)
        self.assertEqual({r["label"]: r["done"] for r in rows}, {"배포": True, "스크럼": False})
        self.assertFalse(events_in_window(sched(events=[ev("2026-09-18", "배포")]), FRI)[0]["done"])


class ValidateDaily(unittest.TestCase):
    """업무 요약 카드 — 날짜별 묶음, 방별 문자열 줄 (docs/DAILY_SCHEMA.md)."""

    def test_정상(self):
        self.assertEqual(validate_daily(daily(), "daily"), [])

    def test_days_가_없으면_빈_배열로_본다(self):
        d = daily(); del d["days"]
        self.assertEqual(validate_daily(d, "daily"), [])

    def test_schema_version(self):
        self.assertTrue(any("schema_version" in e for e in validate_daily(daily(schema_version=2), "daily")))

    def test_days_가_배열이_아니면_거부(self):
        self.assertTrue(any("days:" in e for e in validate_daily(daily(days={}), "daily")))

    def test_날짜_형식(self):
        errs = validate_daily(daily(days=[{"date": "9/17", "rooms": []}]), "daily")
        self.assertTrue(any("days[0].date" in e for e in errs))

    def test_방_이름이_비면_거부(self):
        errs = validate_daily(daily(days=[{"date": "2026-09-17", "rooms": [{"room": "", "items": ["a"]}]}]), "daily")
        self.assertTrue(any(".room" in e for e in errs))

    def test_빈_줄과_문자열_아닌_줄은_거부(self):
        errs = validate_daily(daily(days=[{"date": "2026-09-17", "rooms": [{"room": "파트방", "items": ["", 3]}]}]), "daily")
        self.assertEqual(len([e for e in errs if ".items[" in e]), 2)

    def test_items_가_없어도_된다(self):
        self.assertEqual(validate_daily(daily(days=[{"date": "2026-09-17", "rooms": [{"room": "오퍼링"}]}]), "daily"), [])

    def test_keep_days_는_1_이상의_정수(self):
        self.assertTrue(any("keep_days" in e for e in validate_daily(daily(keep_days=0), "daily")))

    def test_URL_은_거부(self):
        d = daily(days=[{"date": "2026-09-17", "rooms": [{"room": "파트방", "items": ["보기 https://x.y"]}]}])
        self.assertTrue(any("URL" in e for e in validate_daily(d, "daily")))

    def test_금지_키는_거부(self):
        d = daily(days=[{"date": "2026-09-17", "rooms": [{"room": "파트방", "items": ["a"], "quotes": ["원문"]}]}])
        self.assertTrue(any("금지 키" in e for e in validate_daily(d, "daily")))

    def test_객체가_아니면_거부(self):
        self.assertTrue(validate_daily([], "daily"))


class RecentDays(unittest.TestCase):
    DAYS = [{"date": "2026-09-15", "rooms": []}, {"date": "2026-09-17", "rooms": []},
            {"date": "2026-09-16", "rooms": []}, {"date": "x", "rooms": []}, "no"]

    def test_최신_2일치_최신이_위(self):
        self.assertEqual([d["date"] for d in recent_days(self.DAYS, FRI)], ["2026-09-17", "2026-09-16"])

    def test_keep_days_를_따른다(self):
        self.assertEqual(len(recent_days(self.DAYS, FRI, 3)), 3)
        self.assertEqual(len(recent_days(self.DAYS, FRI, 0)), 2)  # 이상하면 기본 2일

    def test_먼_미래는_버린다(self):
        days = [{"date": "2026-09-19", "rooms": []}, {"date": "2026-09-30", "rooms": []}]
        self.assertEqual([d["date"] for d in recent_days(days, FRI)], ["2026-09-19"])  # 하루 앞은 시계 차이로 본다

    def test_원본을_건드리지_않는다(self):
        before = json.dumps(self.DAYS, ensure_ascii=False)
        recent_days(self.DAYS, FRI)
        self.assertEqual(json.dumps(self.DAYS, ensure_ascii=False), before)


class ValidateSchedule(unittest.TestCase):
    """docs/SCHEDULE_SCHEMA.md '검증 — 거부되는 조건' 여섯 항목."""

    def test_정상(self):
        data = sched(holidays=["2026-10-03"], events=[ev("2026-09-18", "배포", kind="배포")],
                     recurring=[rec([0, 3])])
        self.assertEqual(validate_schedule(data, "schedule"), [])

    def test_schema_version(self):
        self.assertTrue(any("schema_version" in e for e in validate_schedule(sched(schema_version=2), "schedule")))

    def test_events_가_배열이_아니면_거부(self):
        self.assertTrue(any("events" in e for e in validate_schedule(sched(events={}), "schedule")))

    def test_recurring_이_배열이_아니면_거부(self):
        self.assertTrue(any("recurring" in e for e in validate_schedule(sched(recurring="매주"), "schedule")))

    def test_events_와_recurring_이_없으면_빈_배열로_본다(self):
        self.assertEqual(validate_schedule({"schema_version": 1, "generated": "2026-09-16"}, "schedule"), [])

    def test_날짜_형식(self):
        errs = validate_schedule(sched(events=[ev("2026/09/18")]), "schedule")
        self.assertTrue(any("events[0].date" in e for e in errs))

    def test_달력에_없는_날짜도_거부(self):
        errs = validate_schedule(sched(events=[ev("2026-09-31")]), "schedule")
        self.assertTrue(any("events[0].date" in e for e in errs))

    def test_종료일이_시작일보다_앞서면_거부(self):
        errs = validate_schedule(sched(events=[ev("2026-09-18", end="2026-09-17")]), "schedule")
        self.assertTrue(any("events[0].end" in e for e in errs))

    def test_종료일은_비워도_된다(self):
        self.assertEqual(validate_schedule(sched(events=[ev("2026-09-18", end="")]), "schedule"), [])

    def test_kind_는_일곱_값(self):
        for good in ("회의", "근태", "보고", "행사", "마감", "배포", "기타"):
            with self.subTest(kind=good):
                self.assertEqual(validate_schedule(sched(events=[ev("2026-09-18", kind=good)]), "schedule"), [])
        errs = validate_schedule(sched(events=[ev("2026-09-18", kind="워크숍")]), "schedule")
        self.assertTrue(any("events[0].kind" in e for e in errs))

    def test_label_이_비면_거부(self):
        errs = validate_schedule(sched(events=[ev("2026-09-18", "")]), "schedule")
        self.assertTrue(any("events[0].label" in e for e in errs))

    def test_반복도_label_과_kind_를_본다(self):
        errs = validate_schedule(sched(recurring=[rec([0], "", kind="워크숍")]), "schedule")
        self.assertTrue(any("recurring[0].label" in e for e in errs))
        self.assertTrue(any("recurring[0].kind" in e for e in errs))

    def test_weekdays_는_0에서_6(self):
        for bad in ([7], [], ["월"], [-1]):
            with self.subTest(weekdays=bad):
                errs = validate_schedule(sched(recurring=[rec(bad)]), "schedule")
                self.assertTrue(any("recurring[0].weekdays" in e for e in errs))

    def test_반복의_from_until_형식도_본다(self):
        errs = validate_schedule(sched(recurring=[rec([0], **{"from": "9/22"})]), "schedule")
        self.assertTrue(any("recurring[0].from" in e for e in errs))

    def test_URL_은_거부(self):
        errs = validate_schedule(sched(events=[ev("2026-09-18", "배포 https://example.com/a")]), "schedule")
        self.assertTrue(any("URL" in e for e in errs))

    def test_전화번호와_이메일도_거부(self):
        for bad in ("010-1234-5678 로 연락", "a@b.com 확인"):
            with self.subTest(bad=bad):
                self.assertTrue(validate_schedule(sched(events=[ev("2026-09-18", bad)]), "schedule"))

    def test_금지_키는_거부(self):
        errs = validate_schedule(sched(events=[ev("2026-09-18", raw_quote="원문")]), "schedule")
        self.assertTrue(any("금지 키" in e for e in errs))

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

    def test_card_는_네_값(self):
        for good in ("profile", "project", "schedule", "site"):
            with self.subTest(card=good):
                self.assertEqual(validate_changelog(chlog(entries=[self.entry(card=good)]), "changelog"), [])
        errs = validate_changelog(chlog(entries=[self.entry(card="meeting")]), "changelog")
        self.assertTrue(any("entries[0].card" in e for e in errs))

    def test_site_는_이름이_없어도_된다(self):
        self.assertEqual(validate_changelog(chlog(entries=[self.entry(card="site", name="")]), "changelog"), [])

    def test_kind_는_세_값(self):
        for good in ("added", "updated", "removed"):
            with self.subTest(kind=good):
                self.assertEqual(validate_changelog(chlog(entries=[self.entry(kind=good)]), "changelog"), [])
        errs = validate_changelog(chlog(entries=[self.entry(kind="created")]), "changelog")
        self.assertTrue(any("entries[0].kind" in e for e in errs))

    def test_요약이_비면_거부(self):
        errs = validate_changelog(chlog(entries=[self.entry(summary="  ")]), "changelog")
        self.assertTrue(any("entries[0].summary" in e for e in errs))

    def test_날짜_형식(self):
        errs = validate_changelog(chlog(entries=[self.entry(date="2026-9-16")]), "changelog")
        self.assertTrue(any("entries[0].date" in e for e in errs))

    def test_keep_days_는_1_이상의_정수(self):
        self.assertTrue(any("keep_days" in e for e in validate_changelog(chlog(keep_days=0), "changelog")))

    def test_요약의_URL_은_거부(self):
        errs = validate_changelog(chlog(entries=[self.entry(summary="www.example.com 참고")]), "changelog")
        self.assertTrue(any("URL" in e for e in errs))

    def test_신호_요약의_숫자_조합을_전화번호로_오탐하지_않는다(self):
        # 프로필 줄은 한 사람당 하나라 신호 상세가 그대로 들어온다. 숫자·물결·화살표가 이어져도
        # 전화번호(01x-…) 정규식에 걸리면 안 된다 — 걸리면 그 파일이 통째로 카드에서 빠진다
        for s in (
            "발화 35→41건 · 평균 44→40자 · 활동 09~17→08~19시 · 배지 확인요구 8.0배→단정 3.1배",
            "활동 01~19시 · 발화 2016→8123건",
            "총 글자 수 10160→12345자",
        ):
            with self.subTest(summary=s):
                self.assertEqual(validate_changelog(chlog(entries=[self.entry(summary=s)]), "changelog"), [])

    def test_금지_키는_거부(self):
        errs = validate_changelog(chlog(entries=[self.entry(raw_quote="원문")]), "changelog")
        self.assertTrue(any("금지 키" in e for e in errs))

    def test_entries_가_배열이_아니면_거부(self):
        self.assertTrue(validate_changelog(chlog(entries={}), "changelog"))


class LoadLocalSingleFiles(unittest.TestCase):
    """data/daily.json · data/schedule.json · data/changelog.json 은 폴더가 아니라 한 파일에 한 카드다."""

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
            self.assertNotIn("daily", kinds)

    def test_업무_요약_카드도_한_파일이다(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "profiles").mkdir(parents=True)
            (root / "data" / "projects").mkdir(parents=True)
            (root / "data" / "daily.json").write_text(json.dumps(daily(), ensure_ascii=False), encoding="utf-8")
            kinds = {c.kind: c for c in load_local(root)}
            self.assertTrue(kinds["daily"].ok)
            self.assertEqual(kinds["daily"].file, "daily.json")

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
