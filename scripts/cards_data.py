#!/usr/bin/env python3
"""salesplus-wiki 의 data/profiles·data/projects 를 읽고 스키마를 검증한다 (build_site.py 가 쓴다).

`profile/README.md` 안의 `<!-- CARDS:START -->` ~ `<!-- CARDS:END -->` 사이만 다시 쓴다.
`update_readme.py` 가 관리하는 STATS 블록과 매핑 표는 건드리지 않는다.

데이터는 salesplus-wiki(private) 의 `data/profiles/*.json` · `data/projects/*.json` 이다.
위키 본문(projects/·members/·raw/)은 읽지 않는다 — 카드는 사람이 공개 범위를 골라 다시 쓴 요약이다.
스키마·공개 범위는 그 저장소의 docs/PROFILE_SCHEMA.md · docs/PROJECT_SCHEMA.md · docs/PRIVACY.md.

환경변수
  GH_TOKEN    salesplus-wiki 를 읽을 PAT — update_readme.py 와 같은 ORG_READ_TOKEN (Contents: Read)
  ORG         조직 로그인          (기본 sales-part-poc-project)
  WIKI_REPO   데이터 저장소        (기본 salesplus-wiki)
  WIKI_REF    브랜치               (기본 main)
  README      대상 파일 경로       (기본 profile/README.md)

옵션
  --local DIR   API 대신 로컬 클론(DIR/data/…)을 읽는다 — 토큰 없이 확인할 때
  --print       README 를 쓰지 않고 생성된 블록만 표준출력으로

설계
- 표준 라이브러리만 사용. HTTP 클라이언트·마크다운 이스케이프는 update_readme.py 것을 그대로 쓴다.
- 카드 한 건이 스키마에 어긋나면 **그 파일만 건너뛰고** 블록 안에 사유를 남긴다. 한 사람의 실수로
  전체 카드가 사라지지 않게.
- 카드 문자열은 파트원 누구나 쓰는 값이므로 마크다운/HTML 로 해석되지 않게 전부 이스케이프한다.
- 갱신 시각만 바뀐 경우는 README 를 다시 쓰지 않는다 (매일 무의미한 봇 커밋 방지).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
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


_MD_SPECIAL = re.compile(r"([\\`*_\[\]~|<>&#])")


def _cell(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", " ".join(text.split()))


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

START = "<!-- CARDS:START -->"
END = "<!-- CARDS:END -->"
SCHEMA_VERSION = 1
DEFAULT_PART = "세일즈플러스파트"
BANNER = "MBTI·나이대는 추측이다. 말투 뱃지와 받은 반응은 관측값이다. 업무 성향과 재미 코너를 섞어 읽지 않는다."
BAR_WIDTH = 10

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

PROJECT_STATUSES = ("준비", "진행중", "보류", "완료")
PROJECT_STATUS_ORDER = {"진행중": 0, "준비": 1, "보류": 2, "완료": 3}
PROJECT_STATUS_ICON = {"준비": "⚪", "진행중": "🟢", "보류": "🟡", "완료": "✅"}
MILESTONE_STATES = ("done", "doing", "todo")
MILESTONE_ICON = {"done": "✅", "doing": "🔄", "todo": "⬜"}


# ───────────────────────────────────────────────────────────────────────── 유틸


def text(v: object) -> str:
    return "" if v is None else str(v).strip()


def num(v: object, default: float = 0) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def cell(v: object) -> str:
    """모든 카드 문자열은 여기를 거친다. 빈 값은 '-'."""
    s = text(v)
    return _cell(s) if s else "-"


def str_list(v: object, limit: int | None = None) -> list[str]:
    items = [text(x) for x in v] if isinstance(v, list) else []
    items = [x for x in items if x]
    return items[:limit] if limit else items


def sub(d: dict[str, Any], key: str) -> dict[str, Any]:
    """d[key] 가 객체면 그것, 아니면 빈 dict. (get 을 두 번 부르면 타입이 좁혀지지 않는다)"""
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def rows(d: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """d[key] 가 배열이면 그 안의 객체만."""
    v = d.get(key)
    return [m for m in v if isinstance(m, dict)] if isinstance(v, list) else []


def bar(done: int, total: int) -> str:
    if total <= 0:
        return "░" * BAR_WIDTH
    filled = round(done / total * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


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


def _common_errors(data: dict[str, Any], stem: str) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version: {SCHEMA_VERSION} 이어야 합니다 (현재 {data.get('schema_version')!r})")
    name = data.get("name")
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


@dataclass
class Card:
    kind: str  # "profile" | "project"
    file: str  # 표시용 경로 (profiles/이현진.json)
    data: dict[str, Any] | None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.data is not None and not self.errors


def _parse(kind: str, shown: str, raw: str) -> Card:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return Card(kind, shown, None, [f"JSON 파싱 실패 — {e.lineno}행 {e.colno}열: {e.msg}"])
    stem = Path(shown).stem
    errors = validate_profile(data, stem) if kind == "profile" else validate_project(data, stem)
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
    return cards


def load_remote(gh: GitHub, org: str, repo: str, ref: str) -> list[Card]:
    """Contents API 로 data/profiles · data/projects 를 받는다. 디렉터리가 없으면(404) 비어 있는 것으로 본다."""
    cards: list[Card] = []
    for kind, sub in (("profile", "profiles"), ("project", "projects")):
        try:
            listing, _ = gh.get(f"/repos/{org}/{repo}/contents/data/{sub}", {"ref": ref})
        except ApiError as e:
            if "HTTP 404" in str(e):
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
    return cards
