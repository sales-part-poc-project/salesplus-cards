#!/usr/bin/env python3
"""salesplus-wiki 의 data/ 카드를 읽고 스키마를 검증한다 — 렌더링은 build_site.py 가 맡는다.

데이터는 salesplus-wiki(private) 의 `data/profiles/*.json` · `data/projects/*.json` (폴더당 한 카드)와
`data/schedule.json` · `data/changelog.json` · `data/daily.json` (한 파일에 한 카드)이다.
위키 본문(projects/·members/·raw/)은 읽지 않는다 — 카드는 사람이 공개 범위를 골라 다시 쓴 요약이다.
스키마·공개 범위는 그 저장소의 docs/PROFILE_SCHEMA.md · docs/PROJECT_SCHEMA.md · docs/SCHEDULE_SCHEMA.md ·
docs/CHANGELOG_SCHEMA.md · docs/DAILY_SCHEMA.md · docs/PRIVACY.md. 검증 규칙과 업무일 창 계산은 `.github-private/scripts/update_cards.py`
와 같아야 한다.

환경변수·옵션은 이 모듈을 쓰는 build_site.py 가 읽는다 (GH_TOKEN · ORG · WIKI_REPO · WIKI_REF · --local).

설계
- 표준 라이브러리만 사용.
- 카드 한 건이 스키마에 어긋나면 **그 파일만 건너뛰고** 사유를 남긴다. 한 사람의 실수로
  전체 카드가 사라지지 않게.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta, timezone
from email.message import Message

API = "https://api.github.com"
KST = timezone(timedelta(hours=9), "KST")


class ApiError(Exception):
    """GitHub API 호출 실패."""


def warn(msg: str) -> None:
    print(f"::warning::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"경고: {msg}", file=sys.stderr)


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """urllib 은 호스트가 바뀌어도 Authorization 을 넘긴다 — api.github.com 밖으로는 따라가지 않는다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(API).netloc:
            raise ApiError(f"외부 호스트로의 리다이렉트 거부: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameHostRedirect)


class GitHub:
    """Contents API 읽기 전용 최소 클라이언트 (.github-private/update_readme.py 에서 옮김)."""

    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "salesplus-cards-site-builder",
        }

    def get(self, path: str, params: dict[str, object] | None = None) -> tuple[object, Message]:
        # 파일명이 한글이면 ASCII 가 아니라 putrequest 에서 실패한다 — 경로 세그먼트를 퍼센트 인코딩한다.
        url = f"{API}{urllib.parse.quote(path, safe='/')}" + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers=self._headers)
        for attempt in range(1, 4):
            try:
                with _OPENER.open(req, timeout=30) as resp:
                    return json.load(resp), resp.headers
            except urllib.error.HTTPError as e:
                rate_limited = e.code == 429 or (e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0")
                if (e.code >= 500 or rate_limited) and attempt < 3:
                    time.sleep(2**attempt)
                    continue
                body = e.read().decode("utf-8", errors="replace")[:300]
                raise ApiError(f"HTTP {e.code} {path}: {body}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 3:
                    time.sleep(2**attempt)
                    continue
                raise ApiError(f"연결 실패 {path}: {e}") from e
        raise AssertionError("unreachable")

SCHEMA_VERSION = 1
DEFAULT_PART = "세일즈플러스파트"
BANNER = "MBTI·나이대는 추측이다. 말투 뱃지와 받은 반응은 관측값이다. 업무 성향과 재미 코너를 섞어 읽지 않는다."

# ── 검증 규칙 (salesplus-wiki/docs/PROFILE_SCHEMA.md · PROJECT_SCHEMA.md · PRIVACY.md) ───────────
# 중첩 어디에 있어도 거부하는 키. 원문 인용과 성별 추정을 막는다.
FORBIDDEN_KEY_EXACT = ("raw_quote", "quotes", "gender", "성별")
FORBIDDEN_KEY_PREFIX = ("samples_",)

# 카드에 실으면 안 되는 사람 이름 (임원·타부서). 파트원이 아니고 본인 동의도 없다.
# 비워 두면 이 검사는 건너뛴다 — 빈 정규식은 모든 문자열에 걸리므로 반드시 분기한다.
FORBIDDEN_NAMES: tuple[str, ...] = ()

# JSON 전체를 문자열로 훑어 찾는 금지 패턴. 링크는 종류를 가리지 않고 막는다 — 카드에 링크를 싣지 않는다.
_NAME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (("임원·타부서 실명", re.compile("|".join(map(re.escape, FORBIDDEN_NAMES)))),) if FORBIDDEN_NAMES else ()
)
FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("URL", re.compile(r"https?://|www\.", re.I)),
    ("전화번호", re.compile(r"01[016-9][-\s]?\d{3,4}[-\s]?\d{4}")),
    ("이메일", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("주민등록번호 형태", re.compile(r"\d{6}[-\s]?[1-4]\d{6}")),
) + _NAME_PATTERNS

UNSAFE_NAME = re.compile(r"[/\\:*?\"<>|\x00-\x1f]")
FUN_KEYS = (("mbti", "MBTI"), ("age_band", "나이대"), ("speech_badge", "말투 뱃지"))
RETIRED_FUN_KEYS = ("blood_type",)  # 근거가 0인 항목은 싣지 않는다

AXES = (
    ("delegation", "위임", "직접 처리", "위임·분담"),
    ("verification", "검증", "결과를 그대로 수용", "검증·팩트체크"),
    ("planning", "계획", "즉흥", "계획 우선"),
    ("thoroughness", "집요함", "요점만", "끝까지 파고듦"),
    ("exploration", "탐색", "단정형", "탐색·제안형"),
)

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 파트 일정 · 변경사항 (salesplus-wiki/docs/SCHEDULE_SCHEMA.md · docs/CHANGELOG_SCHEMA.md).
# 한 파일에 한 카드라, 어긋나면 그 섹션만 빠지고 나머지 카드는 그대로 나온다.
SCHEDULE_WINDOW_DAYS = 2  # 업무일 2일 — 기준일이 업무일이면 기준일이 첫째 날이다
# 파트 일정 카드는 **오늘부터** 업무일 2일(오늘·내일)을 보여준다 (2026-09-22 결정 — 2026-09-17 의 "내일·모레"를
# 되돌렸다. 오늘 처리한 일이 `done` 취소선으로 보여야 해서). 창 계산 함수(`business_window`)는 그대로고,
# 위키의 `build_schedule.schedule_summary` · 조직 README 와 같은 앵커다.
# 업무 요약 카드 (docs/DAILY_SCHEMA.md) — 날짜별 묶음, 최근 2일치(어제·오늘). 방 이름은 별칭이다 (파트방 · 오퍼링).
DAILY_KEEP_DAYS = 2
DAILY_MAX_ITEMS = 12
_WINDOW_SCAN_LIMIT = 400  # 휴일 목록이 잘못 채워져도 무한히 돌지 않게
EVENT_KINDS = ("회의", "근태", "보고", "행사", "마감", "배포", "기타")
CHANGELOG_KEEP_DAYS = 7
CHANGELOG_CARDS = ("profile", "project", "schedule", "site")
CHANGELOG_KINDS = ("added", "updated", "removed")
CHANGELOG_KIND_KO = {"added": "신설", "updated": "갱신", "removed": "삭제"}
CHANGELOG_CARD_KO = {"profile": "멤버", "project": "프로젝트", "schedule": "파트 일정", "site": "사이트"}

PROJECT_STATUSES = ("준비", "진행중", "보류", "완료")
PROJECT_STATUS_ORDER = {"진행중": 0, "준비": 1, "보류": 2, "완료": 3}
MILESTONE_STATES = ("done", "doing", "todo")


# ───────────────────────────────────────────────────────────────────────── 유틸


def text(v: object) -> str:
    return "" if v is None else str(v).strip()


def num(v: object, default: float = 0) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def str_list(v: object, limit: int | None = None) -> list[str]:
    items = [text(x) for x in v] if isinstance(v, list) else []
    items = [x for x in items if x]
    return items[:limit] if limit else items


def sub(d: dict[str, Any], key: str) -> dict[str, Any]:
    """d[key] 가 객체면 그것, 아니면 빈 dict. (get 을 두 번 부르면 타입이 좁혀지지 않는다)"""
    v = d.get(key)
    return v if isinstance(v, dict) else {}


# ───────────────────────────────────────────────────────────────────────── 검증


def scan_forbidden_keys(node: object, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}"
            ks = str(k)
            if ks in FORBIDDEN_KEY_EXACT or ks.startswith(FORBIDDEN_KEY_PREFIX):
                hits.append(p)
            hits.extend(scan_forbidden_keys(v, p))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits.extend(scan_forbidden_keys(v, f"{path}[{i}]"))
    return hits


def _common_errors(data: dict[str, Any], stem: str, check_name: bool = True) -> list[str]:
    """schema_version · name · 금지 키 · 금지 패턴. 일정·변경사항 카드에는 name 이 없다 (check_name=False)."""
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version: {SCHEMA_VERSION} 이어야 합니다 (현재 {data.get('schema_version')!r})")
    name = data.get("name")
    if check_name:
        if not isinstance(name, str) or not name.strip():
            errors.append("name: 비어 있습니다")
        elif name.strip() != stem:
            errors.append(f"name: 파일명과 다릅니다 (name={name.strip()!r} · 파일={stem!r})")
        elif UNSAFE_NAME.search(name) or name.strip() in (".", ".."):
            errors.append(f"name: 파일 경로로 쓸 수 없는 문자가 있습니다 ({name.strip()!r})")
    for p in scan_forbidden_keys(data):
        errors.append(f"금지 키: {p} — 원문 인용·성별 추정은 넣지 않습니다 (docs/PRIVACY.md)")
    blob = json.dumps(data, ensure_ascii=False)
    for label, rx in FORBIDDEN_PATTERNS:
        m = rx.search(blob)
        if m:
            errors.append(f"금지 패턴({label}): {m.group(0)[:24]} — docs/PRIVACY.md")
    return errors


def validate_profile(data: object, stem: str) -> list[str]:
    """거부 사유 목록. 빈 리스트면 통과 (docs/PROFILE_SCHEMA.md)."""
    if not isinstance(data, dict):
        return [f"최상위가 객체가 아닙니다 (현재 {type(data).__name__})"]
    errors = _common_errors(data, stem)
    fun = data.get("fun")
    if fun is not None:
        if not isinstance(fun, dict):
            errors.append("fun: 객체이거나 null 이어야 합니다 (재미 코너를 빼려면 null)")
        else:
            for key, ko in FUN_KEYS:
                blk = fun.get(key)
                if not isinstance(blk, dict):
                    errors.append(f'fun.{key}: {{"value":…, "strength":…, "basis":…}} 객체가 필요합니다')
                elif not text(blk.get("strength")):
                    errors.append(f"fun.{key}.strength: 근거 강도 표기가 없습니다 ({ko})")
            for key in RETIRED_FUN_KEYS:
                if key in fun:
                    errors.append(f"fun.{key}: 근거가 없어 폐지된 항목입니다. JSON 에서 지우세요")
    return errors


def validate_project(data: object, stem: str) -> list[str]:
    """거부 사유 목록. 빈 리스트면 통과 (docs/PROJECT_SCHEMA.md)."""
    if not isinstance(data, dict):
        return [f"최상위가 객체가 아닙니다 (현재 {type(data).__name__})"]
    errors = _common_errors(data, stem)
    if not text(data.get("title")):
        errors.append("title: 비어 있습니다 (카드에 표시할 과제명)")
    status = text(data.get("status"))
    if status not in PROJECT_STATUSES:
        errors.append(f"status: {' | '.join(PROJECT_STATUSES)} 중 하나여야 합니다 (현재 {status!r})")
    ms = data.get("milestones", [])
    if ms is not None and not isinstance(ms, list):
        errors.append("milestones: 배열이어야 합니다")
    else:
        for i, m in enumerate(ms or []):
            if not isinstance(m, dict) or not text(m.get("label")):
                errors.append(f'milestones[{i}]: {{"label":…, "date":…, "state":…}} 객체가 필요합니다')
            elif text(m.get("state")) not in MILESTONE_STATES:
                errors.append(f"milestones[{i}].state: {' | '.join(MILESTONE_STATES)} 중 하나여야 합니다")
    for key in ("workstreams", "members", "recent"):
        v = data.get(key, [])
        if v is not None and not isinstance(v, list):
            errors.append(f"{key}: 배열이어야 합니다")
    for i, m in enumerate(data.get("members") or []):
        if not isinstance(m, dict) or not text(m.get("name")):
            errors.append(f'members[{i}]: {{"name":…, "role":…}} 객체가 필요합니다')
    return errors


def _date_errors(value: object, where: str, required: bool = True) -> list[str]:
    """형식(`YYYY-MM-DD`)과 달력 존재 여부. 날짜 비교를 문자열로 하므로 형식이 어긋나면 받지 않는다."""
    s = text(value)
    if not s:
        return [f"{where}: 비어 있습니다 (YYYY-MM-DD)"] if required else []
    if not ISO_DATE.match(s):
        return [f"{where}: YYYY-MM-DD 형식이어야 합니다 (현재 {s!r})"]
    try:
        date.fromisoformat(s)
    except ValueError:
        return [f"{where}: 달력에 없는 날짜입니다 ({s!r})"]
    return []


def _valid_date(s: str) -> bool:
    """형식이 맞고 달력에 있는 날짜인지 (2026-09-31 은 형식은 맞지만 없다)."""
    return not _date_errors(s, "")


def _event_errors(e: object, where: str, recurring: bool) -> list[str]:
    """events[] 와 recurring[] 이 함께 타는 검사 — kind 는 일곱 값, label 은 비어 있지 않음."""
    if not isinstance(e, dict):
        return [f"{where}: 객체가 아닙니다"]
    errors: list[str] = []
    kind = text(e.get("kind"))
    if kind not in EVENT_KINDS:
        errors.append(f"{where}.kind: {' | '.join(EVENT_KINDS)} 중 하나여야 합니다 (현재 {kind!r})")
    if not text(e.get("label")):
        errors.append(f"{where}.label: 비어 있습니다 (카드에 찍을 한 줄)")
    if not recurring:
        errors.extend(_date_errors(e.get("date"), f"{where}.date"))
        errors.extend(_date_errors(e.get("end"), f"{where}.end", required=False))
        start, end = text(e.get("date")), text(e.get("end"))
        if end and ISO_DATE.match(start) and ISO_DATE.match(end) and end < start:
            errors.append(f"{where}.end: date 이상이어야 합니다 ({start} → {end})")
    else:
        wd = e.get("weekdays")
        if not isinstance(wd, list) or not wd:
            errors.append(f"{where}.weekdays: 0=월 … 6=일 정수 배열이 필요합니다")
        else:
            bad = [d for d in wd if not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6]
            if bad:
                errors.append(f"{where}.weekdays: 0~6 정수만 들어갑니다 (현재 {bad!r})")
        # from·until 도 문자열로 비교하므로 형식이 어긋나면 전개 범위가 조용히 틀어진다
        for key in ("from", "until"):
            errors.extend(_date_errors(e.get(key), f"{where}.{key}", required=False))
    return errors


def validate_schedule(data: object, stem: str) -> list[str]:
    """거부 사유 목록. 빈 리스트면 통과 (docs/SCHEDULE_SCHEMA.md '검증' 여섯 항목).

    `name` 검사는 하지 않는다 — 사람 카드와 달리 파일명이 곧 카드 이름이 아니다.
    """
    if not isinstance(data, dict):
        return [f"최상위가 객체가 아닙니다 (현재 {type(data).__name__})"]
    errors = _common_errors(data, stem, check_name=False)
    hol = data.get("holidays")
    if hol is not None and not isinstance(hol, list):
        errors.append("holidays: YYYY-MM-DD 배열이어야 합니다")
    else:
        for i, h in enumerate(hol or []):
            errors.extend(_date_errors(h, f"holidays[{i}]"))
    for key, recurring in (("events", False), ("recurring", True)):
        v = data.get(key)
        if v is None:
            continue  # 없으면 빈 배열로 본다
        if not isinstance(v, list):
            errors.append(f"{key}: 배열이어야 합니다")
            continue
        for i, e in enumerate(v):
            errors.extend(_event_errors(e, f"{key}[{i}]", recurring))
    return errors


def validate_changelog(data: object, stem: str) -> list[str]:
    """거부 사유 목록. 빈 리스트면 통과 (docs/CHANGELOG_SCHEMA.md '검증')."""
    if not isinstance(data, dict):
        return [f"최상위가 객체가 아닙니다 (현재 {type(data).__name__})"]
    errors = _common_errors(data, stem, check_name=False)
    keep = data.get("keep_days")
    if keep is not None and (not isinstance(keep, int) or isinstance(keep, bool) or keep < 1):
        errors.append(f"keep_days: 1 이상의 정수여야 합니다 (현재 {keep!r})")
    entries = data.get("entries")
    if entries is not None and not isinstance(entries, list):
        errors.append("entries: 배열이어야 합니다")
        return errors
    for i, e in enumerate(entries or []):
        where = f"entries[{i}]"
        if not isinstance(e, dict):
            errors.append(f"{where}: 객체가 필요합니다")
            continue
        errors.extend(_date_errors(e.get("date"), f"{where}.date"))
        if text(e.get("card")) not in CHANGELOG_CARDS:
            errors.append(f"{where}.card: {' | '.join(CHANGELOG_CARDS)} 중 하나여야 합니다 (현재 {text(e.get('card'))!r})")
        if text(e.get("kind")) not in CHANGELOG_KINDS:
            errors.append(f"{where}.kind: {' | '.join(CHANGELOG_KINDS)} 중 하나여야 합니다 (현재 {text(e.get('kind'))!r})")
        if not text(e.get("summary")):
            errors.append(f"{where}.summary: 비어 있습니다 (무엇이 바뀌었는지 한 줄)")
    return errors


def validate_daily(data: object, stem: str) -> list[str]:
    """거부 사유 목록. 빈 리스트면 통과 (docs/DAILY_SCHEMA.md '검증').

    `days[].rooms[].items` 는 사람이 쓴 요약 줄이라 금지 패턴(URL·전화·이메일) 검사가 핵심이다 — 위키 쪽이
    정화하지만 공개 사이트는 스스로 한 번 더 본다.
    """
    if not isinstance(data, dict):
        return [f"최상위가 객체가 아닙니다 (현재 {type(data).__name__})"]
    errors = _common_errors(data, stem, check_name=False)
    keep = data.get("keep_days")
    if keep is not None and (not isinstance(keep, int) or isinstance(keep, bool) or keep < 1):
        errors.append(f"keep_days: 1 이상의 정수여야 합니다 (현재 {keep!r})")
    days = data.get("days")
    if days is None:
        return errors  # 없으면 빈 배열로 본다
    if not isinstance(days, list):
        errors.append("days: 배열이어야 합니다")
        return errors
    for i, d in enumerate(days):
        where = f"days[{i}]"
        if not isinstance(d, dict):
            errors.append(f"{where}: 객체가 필요합니다")
            continue
        errors.extend(_date_errors(d.get("date"), f"{where}.date"))
        rooms = d.get("rooms")
        if rooms is None:
            continue
        if not isinstance(rooms, list):
            errors.append(f"{where}.rooms: 배열이어야 합니다")
            continue
        for j, r in enumerate(rooms):
            rw = f"{where}.rooms[{j}]"
            if not isinstance(r, dict):
                errors.append(f"{rw}: 객체가 필요합니다")
                continue
            if not text(r.get("room")):
                errors.append(f"{rw}.room: 비어 있습니다 (방 별칭)")
            items = r.get("items")
            if items is None:
                continue
            if not isinstance(items, list):
                errors.append(f"{rw}.items: 문자열 배열이어야 합니다")
                continue
            for k, it in enumerate(items):
                if not isinstance(it, str) or not it.strip():
                    errors.append(f"{rw}.items[{k}]: 비어 있지 않은 문자열이어야 합니다")
    return errors


def recent_days(days: object, today: date, keep_days: int = DAILY_KEEP_DAYS) -> list[dict[str, Any]]:
    """업무 요약의 날짜 묶음 — 최신이 위, `keep_days` 개만. 미래 날짜는 버린다 (시계 차이는 하루를 넘지 않는다).

    위키가 이미 2일치로 잘라 두지만 카드 데이터가 묵을 수 있어 그리는 쪽에서 다시 거른다.
    """
    keep = keep_days if isinstance(keep_days, int) and keep_days > 0 else DAILY_KEEP_DAYS
    out: list[tuple[date, dict[str, Any]]] = []
    for d in days if isinstance(days, list) else []:
        if not isinstance(d, dict):
            continue
        when = parse_date(d.get("date"))
        if when is None or when > today + timedelta(days=1):
            continue
        out.append((when, dict(d)))
    out.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in out[:keep]]


# ────────────────────────────────────────────────── 업무일 규칙 (SCHEDULE_SCHEMA.md)
# 카드 빌드는 매일 돌고 위키는 매일 바뀌지 않으므로, 표시 창은 JSON 에 넣지 않고 그리는 쪽이
# 빌드 시각(KST) 기준으로 계산한다. 위키의 build_schedule.py · .github-private 의 update_cards.py
# 와 **같은 규칙**이다 (`business_window` · `events_in_window`). 한쪽을 고치면 세 곳을 같이 고친다 —
# 어긋나면 공개 사이트와 텔레그램 요약이 다른 날짜를 말한다. scripts/test_cards_data.py 가 경계값을 지킨다.


def parse_date(v: object) -> date | None:
    """`YYYY-MM-DD` 만 받는다. 읽을 수 없으면 None — 그 행은 표시하지 않는다."""
    s = text(v)
    if not ISO_DATE.match(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def holiday_set(v: object) -> set[date]:
    return {d for d in (parse_date(x) for x in (v if isinstance(v, list) else [])) if d}


def is_business_day(d: date, holidays: object) -> bool:
    """월~금이고 휴일이 아닌 날. 휴일 집합은 `date` 든 `YYYY-MM-DD` 문자열이든 받는다."""
    hs = holidays if isinstance(holidays, (set, frozenset)) else set(holidays or ())
    return d.weekday() < 5 and d not in hs and d.isoformat() not in hs


def business_window(today: date, holidays: object = (), days: int = SCHEDULE_WINDOW_DAYS) -> tuple[date, date]:
    """(창 시작, 창 끝). 기준일이 업무일이면 그날이 첫째 날, 아니면 다음 업무일이 첫째 날.

    첫째 날부터 업무일을 `days` 개 세어 마지막 업무일이 창 끝이다.
    목 → (목, 금) · 금 → (금, 월) · 토 → (월, 화) · 목이고 금이 휴일 → (목, 월).
    """
    hs: set[object] = {holidays} if isinstance(holidays, str) else {
        h.isoformat() if isinstance(h, date) else text(h) for h in (holidays or ())
    }
    start = today
    for _ in range(_WINDOW_SCAN_LIMIT):
        if is_business_day(start, hs):
            break
        start += timedelta(days=1)
    cur, counted = start, 1
    while counted < days:
        cur += timedelta(days=1)
        if (cur - start).days > _WINDOW_SCAN_LIMIT:
            break
        if is_business_day(cur, hs):
            counted += 1
    return start, cur


def schedule_anchor(today: date) -> date:
    """파트 일정 카드의 기준일 — 오늘. `business_window(schedule_anchor(today))` 가 오늘·내일 창이다.

    목요일이면 목·금, 금요일이면 금·월, 오늘이 휴일이면 다음 업무일부터. 한때(2026-09-17~21) 하루 뒤로 밀어
    "내일·모레"였다 — 앵커를 한 곳에서 바꾸려고 함수로 남겨 둔다.
    """
    return today


def _norm_event(e: dict[str, Any], day: str, end: str, recurring: bool, ongoing: bool) -> dict[str, Any]:
    """카드가 읽는 한 줄. 렌더러가 키를 뒤지지 않게 events·recurring 을 같은 모양으로 편다."""
    return {
        "date": day,
        "end": end,
        "time": text(e.get("time")),
        "kind": text(e.get("kind")),
        "label": text(e.get("label")),
        "members": str_list(e.get("members")),
        "note": text(e.get("note")),
        "project": text(e.get("project")),
        "recurring": recurring,
        "ongoing": ongoing,
        # 위키가 `~~취소선~~`·✅ 행에 붙인다 — 카드는 취소선으로 그린다 (docs/SCHEDULE_SCHEMA.md). 반복 일정에는 없다
        "done": bool(e.get("done")) and not recurring,
    }


def events_in_window(schedule: object, today: date, days: int = SCHEDULE_WINDOW_DAYS) -> list[dict[str, Any]]:
    """창 안의 이벤트를 날짜순(같은 날은 time → label)으로. 빈 시각(종일)이 먼저다.

    - 단발 이벤트: `date <= 창 끝` 이고 `(end 또는 date) >= 창 시작` 이면 들어온다.
      창 안의 주말·휴일에 걸린 이벤트도 보인다. `date < 창 시작` 이면 `ongoing`.
    - 반복 일정: 창 안의 **업무일**에만 전개한다 (주말·휴일에는 펴지 않는다).
      `weekdays` 에 요일이 있고 `from <= 날짜 <= until` 이어야 한다 (빈 문자열은 무제한).
    - 같은 날 같은 시각·라벨·멤버는 한 번만 — 단발이 반복보다 먼저라 단발 쪽이 남는다.
    """
    d = schedule if isinstance(schedule, dict) else {}
    holidays = set(str_list(d.get("holidays")))
    start, end = business_window(today, holidays, days)
    s_iso, e_iso = start.isoformat(), end.isoformat()

    out: list[dict[str, Any]] = []
    for e in d.get("events") or []:
        if not isinstance(e, dict):
            continue
        day = text(e.get("date"))
        if not _valid_date(day):
            continue
        tail = text(e.get("end"))
        if not _valid_date(tail) or tail == day:
            tail = ""  # build_schedule.py 와 같게 — 하루짜리는 end 를 비운다
        if day <= e_iso and (tail or day) >= s_iso:
            out.append(_norm_event(e, day, tail, recurring=False, ongoing=day < s_iso))

    cur = start
    while cur <= end:
        if is_business_day(cur, holidays):
            iso = cur.isoformat()
            for r in d.get("recurring") or []:
                if not isinstance(r, dict):
                    continue
                wd = r.get("weekdays")
                if not isinstance(wd, list) or cur.weekday() not in wd:
                    continue
                frm, until = text(r.get("from")), text(r.get("until"))
                if (frm and iso < frm) or (until and iso > until):
                    continue
                out.append(_norm_event(r, iso, "", recurring=True, ongoing=False))
        cur += timedelta(days=1)

    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    uniq: list[dict[str, Any]] = []
    for e in out:
        key = (e["date"], e["time"], e["label"], tuple(e["members"]))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    uniq.sort(key=lambda x: (x["date"], x["time"], x["label"]))
    return uniq


def recent_entries(entries: object, today: date, keep_days: int = CHANGELOG_KEEP_DAYS) -> list[dict[str, Any]]:
    """보관 창(**오늘 포함** `keep_days` 일) 안의 변경만. 최신이 위, 같은 날은 card → name 순.

    바닥은 `today - (keep_days - 1)` 이다 — 7이면 오늘을 첫째 날로 세어 일곱째 날까지 남고
    그 하루 전은 빠진다. `.github-private` 쪽과 같은 규칙이다 (2026-09-16 결정).
    만들 때도 걸러지지만 카드 데이터가 하루 이상 묵을 수 있어 그리는 쪽에서 다시 거른다.
    """
    days = keep_days if isinstance(keep_days, int) and keep_days > 0 else CHANGELOG_KEEP_DAYS
    cutoff = today - timedelta(days=days - 1)
    out: list[dict[str, Any]] = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            continue
        d = parse_date(e.get("date"))
        if d is None or d < cutoff:  # 미래 날짜는 남긴다 — 시계 차이로 숨기지 않는다
            continue
        out.append(dict(e))
    out.sort(key=lambda e: (text(e.get("card")), text(e.get("name"))))
    out.sort(key=lambda e: text(e.get("date")), reverse=True)  # 날짜만 내림차순 (같은 날은 위 순서 유지)
    return out


def project_progress(milestones: object) -> tuple[int, int, str]:
    """(완료 수, 전체 수, 다음 마일스톤). 진행 중인 것이 있으면 그것이 '다음'이다."""
    raw = milestones if isinstance(milestones, list) else []
    items: list[dict[str, Any]] = [m for m in raw if isinstance(m, dict) and text(m.get("label"))]
    done = sum(1 for m in items if text(m.get("state")) == "done")
    for want in ("doing", "todo"):
        for m in items:
            if text(m.get("state")) == want:
                return done, len(items), text(m.get("label"))
    return done, len(items), ""


def badge_short(badge: object) -> str:
    """목록 셀용 짧은 말투 뱃지 — `단정 3.5배` · `물결 안 씀`."""
    if not isinstance(badge, dict) or not text(badge.get("marker")):
        return ""
    marker = text(badge.get("marker"))
    if text(badge.get("kind")) == "안씀":
        return f"{marker} 안 씀"
    ratio = num(badge.get("ratio"))
    return f"{marker} {ratio:g}배" if ratio else marker


# ───────────────────────────────────────────────────────────────────────── 적재


SINGLE_FILES = (("daily", "daily.json"), ("schedule", "schedule.json"), ("changelog", "changelog.json"))  # 한 파일에 한 카드


@dataclass
class Card:
    kind: str  # "profile" | "project" | "schedule" | "changelog" | "daily"
    file: str  # 표시용 경로 (profiles/이현진.json · schedule.json · daily.json)
    data: dict[str, Any] | None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.data is not None and not self.errors


VALIDATORS = {
    "profile": validate_profile,
    "project": validate_project,
    "schedule": validate_schedule,
    "changelog": validate_changelog,
    "daily": validate_daily,
}


def _parse(kind: str, shown: str, raw: str) -> Card:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return Card(kind, shown, None, [f"JSON 파싱 실패 — {e.lineno}행 {e.colno}열: {e.msg}"])
    stem = Path(shown).stem
    errors = VALIDATORS[kind](data, stem)
    return Card(kind, shown, data if isinstance(data, dict) else None, errors)


def load_local(root: Path) -> list[Card]:
    cards: list[Card] = []
    for kind, sub in (("profile", "profiles"), ("project", "projects")):
        d = root / "data" / sub
        if not d.is_dir():
            warn(f"디렉터리 없음: {d}")
            continue
        for jp in sorted(d.glob("*.json")):
            cards.append(_parse(kind, f"{sub}/{jp.name}", jp.read_text(encoding="utf-8")))
    for kind, fname in SINGLE_FILES:
        fp = root / "data" / fname
        if not fp.is_file():
            warn(f"파일 없음: {fp} — 건너뜀")
            continue
        cards.append(_parse(kind, fname, fp.read_text(encoding="utf-8")))
    return cards


def _ensure_repo_visible(gh: GitHub, org: str, repo: str) -> None:
    """저장소 메타데이터가 404 면 토큰이 저장소를 못 보는 것이다 (없거나 만료됐거나 접근 권한이 빠짐)."""
    try:
        gh.get(f"/repos/{org}/{repo}")
    except ApiError as e:
        if "HTTP 404" in str(e):
            raise ApiError(
                f"{org}/{repo} 를 읽을 수 없다 (HTTP 404). 토큰(ORG_READ_TOKEN)이 없거나 만료됐거나 "
                f"이 저장소 Contents: Read 권한이 빠져 있다. 데이터 없음이 아니라 접근 실패다."
            ) from e
        raise


def load_remote(gh: GitHub, org: str, repo: str, ref: str) -> list[Card]:
    """Contents API 로 data/profiles · data/projects 를 받는다.

    디렉터리가 없으면(404) 비어 있는 것으로 본다. 다만 GitHub 은 토큰이 못 보는 private 저장소에도
    404 를 주므로, 저장소 자체가 안 보이면 "데이터 없음"이 아니라 오류로 올린다 — 그래야 빈 사이트가
    정상 배포로 덮어쓰지 않고 워크플로가 실패해 토큰 문제를 알린다.
    """
    cards: list[Card] = []
    for kind, sub in (("profile", "profiles"), ("project", "projects")):
        try:
            listing, _ = gh.get(f"/repos/{org}/{repo}/contents/data/{sub}", {"ref": ref})
        except ApiError as e:
            if "HTTP 404" in str(e):
                _ensure_repo_visible(gh, org, repo)
                warn(f"{repo}/data/{sub} 없음 — 건너뜀")
                continue
            raise
        if not isinstance(listing, list):
            raise ApiError(f"data/{sub} 응답이 목록이 아님: {str(listing)[:200]}")
        for entry in sorted(listing, key=lambda e: str(e.get("name"))):
            name = str(entry.get("name", ""))
            if entry.get("type") != "file" or not name.endswith(".json"):
                continue
            body, _ = gh.get(f"/repos/{org}/{repo}/contents/{entry['path']}", {"ref": ref})
            if not isinstance(body, dict) or body.get("encoding") != "base64":
                cards.append(Card(kind, f"{sub}/{name}", None, ["파일 본문을 받지 못했습니다 (1MB 초과?)"]))
                continue
            raw = base64.b64decode(str(body.get("content", ""))).decode("utf-8", errors="replace")
            cards.append(_parse(kind, f"{sub}/{name}", raw))
    for kind, fname in SINGLE_FILES:
        try:
            body, _ = gh.get(f"/repos/{org}/{repo}/contents/data/{fname}", {"ref": ref})
        except ApiError as e:
            if "HTTP 404" in str(e):
                warn(f"{repo}/data/{fname} 없음 — 건너뜀")  # 저장소 자체가 안 보이면 위 폴더 루프가 이미 올린다
                continue
            raise
        if not isinstance(body, dict) or body.get("encoding") != "base64":
            cards.append(Card(kind, fname, None, ["파일 본문을 받지 못했습니다 (1MB 초과?)"]))
            continue
        raw = base64.b64decode(str(body.get("content", ""))).decode("utf-8", errors="replace")
        cards.append(_parse(kind, fname, raw))
    return cards
