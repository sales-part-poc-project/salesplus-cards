#!/usr/bin/env python3
"""salesplus-wiki 의 data/ 카드를 읽고 스키마를 검증한다 — 렌더링은 build_site.py 가 맡는다.

데이터는 salesplus-wiki(private) 의 `data/profiles/*.json` · `data/projects/*.json` (폴더당 한 카드)와
`data/schedule.json` · `data/changelog.json` (한 파일에 한 카드)이다.
위키 본문(projects/·members/·raw/)은 읽지 않는다 — 카드는 사람이 공개 범위를 골라 다시 쓴 요약이다.
스키마·공개 범위는 그 저장소의 docs/PROFILE_SCHEMA.md · docs/PROJECT_SCHEMA.md · docs/SCHEDULE_SCHEMA.md ·
docs/PRIVACY.md. 검증 규칙과 업무일 창 계산은 `.github-private/scripts/update_cards.py` 와 같아야 한다.

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

# 파트 일정 · 변경사항 (salesplus-wiki/docs/SCHEDULE_SCHEMA.md). 한 파일에 한 카드다.
BUSINESS_DAYS_AHEAD = 2  # "업무일 2일 이내" — 오늘은 세지 않는다
CHANGELOG_KEEP_DAYS = 7
CHANGELOG_CARDS = ("profile", "project", "schedule")
CHANGELOG_KINDS = ("added", "updated", "removed")
CHANGELOG_KIND_KO = {"added": "신설", "updated": "갱신", "removed": "삭제"}
CHANGELOG_CARD_KO = {"profile": "멤버", "project": "프로젝트", "schedule": "파트 일정"}

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


def validate_schedule(data: object, stem: str) -> list[str]:
    """거부 사유 목록. 빈 리스트면 통과 (docs/SCHEDULE_SCHEMA.md)."""
    if not isinstance(data, dict):
        return [f"최상위가 객체가 아닙니다 (현재 {type(data).__name__})"]
    errors = _common_errors(data, stem, check_name=False)
    hol = data.get("holidays")
    if hol is None or isinstance(hol, list):
        for i, h in enumerate(hol or []):
            if not ISO_DATE.match(text(h)):
                errors.append(f"holidays[{i}]: YYYY-MM-DD 형식이어야 합니다 (현재 {text(h)!r})")
    else:
        errors.append("holidays: 배열이어야 합니다")
    items = data.get("items")
    if items is not None and not isinstance(items, list):
        errors.append("items: 배열이어야 합니다")
        return errors
    for i, it in enumerate(items or []):
        if not isinstance(it, dict):
            errors.append(f"items[{i}]: 객체가 필요합니다")
            continue
        if not ISO_DATE.match(text(it.get("date"))):
            errors.append(f"items[{i}].date: YYYY-MM-DD 형식이어야 합니다 (현재 {text(it.get('date'))!r})")
        end = it.get("end")
        if end is not None and not ISO_DATE.match(text(end)):
            errors.append(f"items[{i}].end: YYYY-MM-DD 이거나 null 이어야 합니다 (현재 {text(end)!r})")
        if not text(it.get("title")):
            errors.append(f"items[{i}].title: 비어 있습니다")
    return errors


def validate_changelog(data: object, stem: str) -> list[str]:
    """거부 사유 목록. 빈 리스트면 통과 (docs/SCHEDULE_SCHEMA.md)."""
    if not isinstance(data, dict):
        return [f"최상위가 객체가 아닙니다 (현재 {type(data).__name__})"]
    errors = _common_errors(data, stem, check_name=False)
    entries = data.get("entries")
    if entries is not None and not isinstance(entries, list):
        errors.append("entries: 배열이어야 합니다")
        return errors
    for i, e in enumerate(entries or []):
        if not isinstance(e, dict):
            errors.append(f"entries[{i}]: 객체가 필요합니다")
            continue
        if not ISO_DATE.match(text(e.get("date"))):
            errors.append(f"entries[{i}].date: YYYY-MM-DD 형식이어야 합니다 (현재 {text(e.get('date'))!r})")
        if text(e.get("card")) not in CHANGELOG_CARDS:
            errors.append(f"entries[{i}].card: {' | '.join(CHANGELOG_CARDS)} 중 하나여야 합니다 (현재 {text(e.get('card'))!r})")
        if text(e.get("kind")) not in CHANGELOG_KINDS:
            errors.append(f"entries[{i}].kind: {' | '.join(CHANGELOG_KINDS)} 중 하나여야 합니다 (현재 {text(e.get('kind'))!r})")
    return errors


# ────────────────────────────────────────────────── 업무일 규칙 (SCHEDULE_SCHEMA.md)
# 카드 빌드는 매일 돌고 위키는 매일 바뀌지 않으므로, 표시 창은 JSON 에 넣지 않고 그리는 쪽이
# 빌드 시각 기준으로 계산한다. 위키의 build_schedule.py 와 구현이 같아야 한다.


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


def is_business_day(d: date, holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def business_limit(today: date, holidays: set[date], days: int = BUSINESS_DAYS_AHEAD) -> date:
    """오늘은 세지 않고 업무일을 `days` 개 더 센 날 (수→금, 금→화, 토→화).

    휴일이 끼면 그만큼 뒤로 민다. 60일 안에서 못 채우면 거기서 멈춘다 — 휴일 목록이 이상해도 돌지 않게.
    """
    d, left = today, max(0, days)
    for _ in range(60):
        if left <= 0:
            break
        d += timedelta(days=1)
        if is_business_day(d, holidays):
            left -= 1
    return d


def upcoming_items(items: object, today: date, holidays: set[date]) -> list[dict[str, Any]]:
    """창(오늘~업무일 2일) 안에 걸치는 일정. 각 항목에 `ongoing`(오늘 이전 시작) 을 달아 돌려준다."""
    limit = business_limit(today, holidays)
    out: list[dict[str, Any]] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        start = parse_date(it.get("date"))
        if start is None:
            continue
        end = parse_date(it.get("end")) or start
        if start > limit or end < today:
            continue
        row = dict(it)
        row["ongoing"] = start < today
        out.append(row)
    out.sort(key=lambda r: (text(r.get("date")), text(r.get("time")), text(r.get("title"))))
    return out


def recent_entries(entries: object, today: date, keep_days: int = CHANGELOG_KEEP_DAYS) -> list[dict[str, Any]]:
    """보관 기간(`today - keep_days` 이후) 안의 변경만. 최신이 위, 같은 날은 card → name 순.

    만들 때도 걸러지지만 카드 데이터가 하루 이상 묵을 수 있어 그리는 쪽에서 다시 거른다.
    """
    days = keep_days if isinstance(keep_days, int) and keep_days > 0 else CHANGELOG_KEEP_DAYS
    cutoff = today - timedelta(days=days)
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


SINGLE_FILES = (("schedule", "schedule.json"), ("changelog", "changelog.json"))  # 한 파일에 한 카드


@dataclass
class Card:
    kind: str  # "profile" | "project" | "schedule" | "changelog"
    file: str  # 표시용 경로 (profiles/이현진.json · schedule.json)
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
