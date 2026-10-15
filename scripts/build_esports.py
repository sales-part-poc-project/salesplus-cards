#!/usr/bin/env python3
"""e스포츠 방 재미 페이지 — 점심 스타크래프트 전적·듀오 시너지·천적·점심 운동 출석을 한 장으로 그린다.

    python3 scripts/build_esports.py --out _site            # _site/esports.html
    python3 scripts/build_site.py ...                       # 사이트 빌드 때 같이 만들어진다

데이터는 이 저장소의 `esports/records.json` 하나다. 다른 카드와 달리 위키 data/ 를 거치지 않는다 —
업무와 무관한 재미 기록이라 위키에 쌓지 않고, 방 사람들이 이 파일만 고쳐서 유지보수하게 하려는 것이다.
경기를 하면 matches 에 한 줄, 점심마다 lunch 에 한 줄 넣으면 승률·파트너·천적·출석률은 전부 여기서 다시 계산한다.

⚠️ 이 페이지도 public 사이트에 올라간다. 그래서
- 이름은 방에서 부르는 이름(성 없이)만 쓴다
- 점심에 운동을 안 한 날은 사유(약속·회식 등)를 싣지 않고 `skip` 하나로만 적는다.
  휴가·병가로 자리에 없던 날은 아예 적지 않는다 — 출석률 분모에서 빠져야 공정하다
- 대화 원문은 옮기지 않는다 — 별명·한 줄 소개는 사람이 다시 쓴 문장이다
- 링크·전화번호·이메일은 cards_data 의 금지 패턴으로 막는다

표준 라이브러리만 쓴다. CSS·JS 인라인, 외부 리소스 없음 (file:// 로 열어도 같다).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cards_data import KST, FORBIDDEN_PATTERNS, scan_forbidden_keys  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RECORDS = ROOT / "esports" / "records.json"
ASSET_DIR = ROOT / "esports" / "assets"  # 유닛·건물 PNG (투명 배경). 파일명이 곧 키 — members[].unit 에 쓴다
ASSET_URL = "esports-assets"             # 사이트에서의 경로. 페이지가 실제로 쓴 파일만 복사한다
UNIT_KO = {
    "scv": "SCV", "marine": "마린", "firebat": "파이어뱃", "medic": "메딕", "ghost": "고스트", "vulture": "벌처",
    "siege-tank": "시즈 탱크", "goliath": "골리앗", "wraith": "레이스", "dropship": "드랍쉽", "science-vessel": "사이언스 베슬",
    "battlecruiser": "배틀크루저", "probe": "프로브", "zealot": "질럿", "dragoon": "드라군", "high-templar": "하이 템플러",
    "archon": "아콘", "dark-archon": "다크 아콘", "observer": "옵저버", "arbiter": "아비터", "drone": "드론", "larva": "라바",
    "zergling": "저글링", "hydralisk": "히드라리스크", "lurker": "러커", "overlord": "오버로드", "queen": "퀸", "devourer": "디바우러",
    "command-center": "커맨드 센터", "supply-depot": "서플라이 디포", "barracks": "배럭", "factory": "팩토리",
    "pylon": "파일런", "gateway": "게이트웨이", "photon-cannon": "포톤 캐논", "templar-archives": "템플러 아카이브",
    "hatchery": "해처리", "spawning-pool": "스포닝 풀", "spire": "스파이어", "sunken-colony": "성큰 콜로니",
}
# 꾸밈용 배치. 바꾸고 싶으면 키만 갈아 끼운다 (esports/assets/ 에 있는 파일명)
HERO_FLEET = ("battlecruiser", "overlord", "arbiter")
HERO_LINEUP = ("marine", "firebat", "ghost", "zealot", "dragoon", "zergling", "hydralisk")
SECTION_UNITS = {"HALL OF FAME": "command-center", "ROSTER": "hatchery", "LADDER": "photon-cannon", "SYNERGY": "pylon",
                 "BALANCE PATCH": "factory", "BATTLE LOG": "barracks", "LUNCH OPS": "spawning-pool"}
TILE_UNITS = {"gold": "archon", "pink": "dropship", "cyan": "marine", "violet": "probe"}
LUNCH_UNITS = {"gym": "firebat", "run": "zergling", "star": "scv"}  # 근육질 · 발업 · 일꾼(PC방 출근)
SCHEMA_VERSION = 1
LUNCH_STATUSES = ("gym", "run", "star", "skip")
MEMBER_STR_KEYS = ("race", "race_confidence", "unit", "nickname", "role", "race_evidence", "tagline")
LUNCH_KO = {"gym": "헬스", "run": "러닝", "star": "스타", "skip": "불참"}
RACES = {"테란": "terran", "저그": "zerg", "프로토스": "protoss", "랜덤": "random", "": "none"}
RACE_EN = {"terran": "TERRAN", "zerg": "ZERG", "protoss": "PROTOSS", "random": "RANDOM", "none": "PEACE"}
CONFIDENCE_PIPS = {"높음": 3, "중간": 2, "낮음": 1}
WEEKDAYS = "월화수목금토일"
MIN_GAMES_FOR_TITLE = 4  # 승률 1위는 이 판 수 이상만 — 1판 1승 100% 가 왕관을 쓰지 않게


def esc(v: object) -> str:
    return html.escape("" if v is None else str(v), quote=True)


# ───────────────────────────────────────────────────────────────────────── 검증


def validate(data: object) -> list[str]:
    """거부 사유 목록. 빈 리스트면 통과."""
    if not isinstance(data, dict):
        return [f"최상위가 객체가 아닙니다 (현재 {type(data).__name__})"]
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version: {SCHEMA_VERSION} 이어야 합니다 (현재 {data.get('schema_version')!r})")
    members = data.get("members")
    if not isinstance(members, list) or not members:
        return errors + ["members: 한 명 이상 있어야 합니다"]
    ids: list[str] = []
    for i, m in enumerate(members):
        mid = m.get("id") if isinstance(m, dict) else None
        if not isinstance(mid, str) or not mid.strip():
            errors.append(f"members[{i}].id: 비어 있습니다")
            continue
        if mid in ids:
            errors.append(f"members[{i}].id: {mid!r} 가 두 번 나옵니다")
        ids.append(mid)
        bad = [k for k in MEMBER_STR_KEYS if k in m and not isinstance(m[k], str)]
        for k in bad:
            errors.append(f"members[{i}].{k}: 문자열이어야 합니다")
        if "race" not in bad and m.get("race", "") not in RACES:
            errors.append(f"members[{i}].race: {m.get('race')!r} — 테란·저그·프로토스·랜덤·빈 값 중 하나")
        if "unit" not in bad and m.get("unit") and m["unit"] not in UNIT_KO:
            errors.append(f"members[{i}].unit: {m['unit']!r} — esports/assets/ 의 파일명(확장자 없이) 중 하나")
    known = set(ids)

    for i, match in enumerate(data.get("matches") or []):
        where = f"matches[{i}]"
        if not isinstance(match, dict):
            errors.append(f"{where}: 객체가 아닙니다")
            continue
        if match.get("date") and not _is_date(match["date"]):
            errors.append(f"{where}.date: YYYY-MM-DD 또는 빈 값 ({match['date']!r})")
        for k in ("title", "mode", "note"):
            if k in match and not isinstance(match[k], str):
                errors.append(f"{where}.{k}: 문자열이어야 합니다")
        games = match.get("games")
        if not isinstance(games, list) or not games:
            errors.append(f"{where}.games: 한 판 이상 있어야 합니다")
            continue
        for j, g in enumerate(games):
            win, lose = (g.get("win"), g.get("lose")) if isinstance(g, dict) else (None, None)
            if not isinstance(win, list) or not win or not isinstance(lose, list):
                errors.append(f"{where}.games[{j}]: win(한 명 이상)·lose(빈 목록 가능) 목록이 필요합니다")
                continue
            if not all(isinstance(x, str) for x in win + lose):
                errors.append(f"{where}.games[{j}]: win·lose 에는 이름(문자열)만 넣습니다")
                continue
            for name in win + lose:
                if name not in known:
                    errors.append(f"{where}.games[{j}]: members 에 없는 이름 {name!r}")
            if set(win) & set(lose):
                errors.append(f"{where}.games[{j}]: 이긴 팀과 진 팀에 같은 사람 {sorted(set(win) & set(lose))}")

    seen_days: set[str] = set()
    for i, day in enumerate(data.get("lunch") or []):
        where = f"lunch[{i}]"
        d = day.get("date") if isinstance(day, dict) else None
        if not _is_date(d):
            errors.append(f"{where}.date: YYYY-MM-DD 이어야 합니다 ({d!r})")
            continue
        if d in seen_days:
            errors.append(f"{where}.date: {d} 가 두 번 나옵니다")
        seen_days.add(d)
        if date.fromisoformat(d).weekday() >= 5:
            errors.append(f"{where}.date: {d} 는 주말 — 점심 기록은 평일만 (요일 막대가 월~금만 그린다)")
        placed: dict[str, str] = {}
        for status in LUNCH_STATUSES:
            names = day.get(status) or []
            if not isinstance(names, list) or not all(isinstance(x, str) for x in names):
                errors.append(f"{where}.{status}: 이름(문자열) 목록이어야 합니다")
                continue
            for name in names:
                if name not in known:
                    errors.append(f"{where}.{status}: members 에 없는 이름 {name!r}")
                elif name in placed:
                    errors.append(f"{where}: {name} 이 {placed[name]}·{status} 에 동시에 있습니다")
                else:
                    placed[name] = status
        for extra in set(day) - {"date", *LUNCH_STATUSES}:
            errors.append(f"{where}.{extra}: 모르는 키 — 사유는 싣지 않고 skip 하나로 적습니다")

    nxt = data.get("next")
    if nxt is not None:
        if not isinstance(nxt, dict):
            errors.append("next: 객체({date, title, status}) 또는 null 이어야 합니다")
        else:
            if nxt.get("date") and not _is_date(nxt["date"]):
                errors.append(f"next.date: YYYY-MM-DD 또는 빈 값 ({nxt['date']!r})")
            for k in ("title", "status"):
                if k in nxt and not isinstance(nxt[k], str):
                    errors.append(f"next.{k}: 문자열이어야 합니다")
    for k in ("title", "room", "source", "updated"):
        if k in data and not isinstance(data[k], str):
            errors.append(f"{k}: 문자열이어야 합니다")

    for p in scan_forbidden_keys(data):
        errors.append(f"금지 키: {p}")
    blob = json.dumps(data, ensure_ascii=False)
    for label, rx in FORBIDDEN_PATTERNS:
        m = rx.search(blob)
        if m:
            errors.append(f"금지 패턴({label}): {m.group(0)[:24]}")
    return errors


def _is_date(v: object) -> bool:
    if not isinstance(v, str):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return len(v) == 10


# ───────────────────────────────────────────────────────────────────────── 집계


def rate(w: int, l: int) -> float | None:
    return w / (w + l) if w + l else None


def rating(w: int, l: int) -> float:
    """판 수가 적은 사람을 50% 쪽으로 당긴 승률 (라플라스 보정). 순위·팀 짜기용."""
    return (w + 1) / (w + l + 2)


def pick(cands: list[tuple[str, int, int]], best: bool) -> tuple[str, int, int] | None:
    """(상대, 승, 패) 중 승률 최고(best) 또는 최저. 같이(또는 맞서) 2판 이상 한 사람이 있으면 그 안에서만 고른다 —
    1판 1승짜리가 '최고의 파트너' 를 가져가지 않게. 동률이면 판 수가 많은 쪽, 그다음 이름 순."""
    pool = [t for t in cands if t[1] + t[2] >= 2] or cands
    if not pool:
        return None
    def key(t: tuple[str, int, int]) -> tuple[float, int, str]:
        r = rate(t[1], t[2]) or 0.0
        return (-r if best else r, -(t[1] + t[2]), t[0])
    return min(pool, key=key)


def compute(data: dict[str, Any]) -> dict[str, Any]:
    members = data["members"]
    ids = [m["id"] for m in members]
    record = {i: [0, 0] for i in ids}
    pair: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    h2h: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    games_total = 0
    for match in data.get("matches") or []:
        for g in match["games"]:
            games_total += 1
            win, lose = g["win"], g["lose"]
            for p in win:
                record[p][0] += 1
            for p in lose:
                record[p][1] += 1
            for team, slot in ((win, 0), (lose, 1)):
                for a, b in combinations(sorted(team), 2):
                    pair[(a, b)][slot] += 1
            for a in win:
                for b in lose:
                    h2h[(a, b)][0] += 1
                    h2h[(b, a)][1] += 1

    people: dict[str, dict[str, Any]] = {}
    for i in ids:
        partners = [(b if a == i else a, w, l) for (a, b), (w, l) in pair.items() if i in (a, b)]
        rivals = [(b, w, l) for (a, b), (w, l) in h2h.items() if a == i]
        best, worst = pick(partners, True), pick(partners, False)
        if best and best[1] == 0:
            best = None  # 같이 이겨 본 적이 없으면 '최고' 라고 부르지 않는다
        if worst and best and rate(worst[1], worst[2]) >= rate(best[1], best[2]):
            worst = None  # 다 똑같이 잘(또는 못) 맞으면 '최악' 을 억지로 고르지 않는다
        nemesis, prey = pick(rivals, False), pick(rivals, True)
        if nemesis and rate(nemesis[1], nemesis[2]) >= 0.5:
            nemesis = None  # 진 적이 더 많은 상대가 없으면 천적도 없다
        if prey and rate(prey[1], prey[2]) <= 0.5:
            prey = None
        w, l = record[i]
        people[i] = {"w": w, "l": l, "rate": rate(w, l), "rating": rating(w, l),
                     "best": best, "worst": worst, "nemesis": nemesis, "prey": prey}

    lunch_days = sorted(data.get("lunch") or [], key=lambda d: d["date"])
    lunch = {i: {s: 0 for s in LUNCH_STATUSES} for i in ids}
    for day in lunch_days:
        for s in LUNCH_STATUSES:
            for p in day.get(s) or []:
                lunch[p][s] += 1
    for i in ids:
        c = lunch[i]
        c["active"] = c["gym"] + c["run"] + c["star"]
        c["days"] = c["active"] + c["skip"]
        c["ratio"] = c["active"] / c["days"] if c["days"] else None

    weekday: dict[int, list[int]] = defaultdict(lambda: [0, 0])  # 요일 → [활동 인원 합, 기록일 수]
    for day in lunch_days:
        wd = date.fromisoformat(day["date"]).weekday()
        weekday[wd][0] += sum(len(day.get(s) or []) for s in ("gym", "run", "star"))
        weekday[wd][1] += 1

    return {"ids": ids, "record": record, "pair": dict(pair), "h2h": dict(h2h), "people": people,
            "games": games_total, "lunch": lunch, "lunch_days": lunch_days, "weekday": dict(weekday)}


def best_duo(stats: dict[str, Any], skip: set[str]) -> tuple[str, str, int, int] | None:
    """같은 팀으로 2판 이상 뛴 짝 중 승률 최고. 동률이면 판 수가 많은 쪽."""
    pairs = [(a, b, w, l) for (a, b), (w, l) in stats["pair"].items()
             if w + l >= 2 and a not in skip and b not in skip]
    return min(pairs, key=lambda t: (-rate(t[2], t[3]), -(t[2] + t[3]), t[0], t[1]), default=None)


def top_rivalries(stats: dict[str, Any], skip: set[str], n: int = 4) -> list[tuple[str, str, int, int]]:
    """맞붙은 판이 많은 순. (a, b) 와 (b, a) 는 한 번만 — 앞쪽이 이긴 쪽."""
    out = []
    for (a, b), (w, l) in stats["h2h"].items():
        if a in skip or b in skip or w < l or (w == l and a > b):
            continue
        out.append((a, b, w, l))
    return sorted(out, key=lambda t: (-(t[2] + t[3]), -(t[2] - t[3]), t[0]))[:n]


# ───────────────────────────────────────────────────────────────────────── 렌더링


def pct(v: float | None) -> str:
    return "—" if v is None else f"{round(v * 100)}%"


def short_date(s: str) -> str:
    if not s:
        return "방 개설 전"
    d = date.fromisoformat(s)
    return f"{d.month}/{d.day} ({WEEKDAYS[d.weekday()]})"


def emblem(race: str, size: int = 40) -> str:
    cls = RACES.get(race, "none")
    shapes = {
        "terran": '<path d="M24 4 42 11v13c0 10-8 17-18 20C14 41 6 34 6 24V11Z"/><path d="M14 19l10 8 10-8M14 27l10 8 10-8" fill="none"/>',
        "zerg": '<path d="M24 3c4 9 13 13 20 25-8-3-14-1-20 17C18 27 12 25 4 28 11 16 20 12 24 3Z"/><circle cx="24" cy="25" r="3.2"/>',
        "protoss": '<path d="M24 3 40 24 24 45 8 24Z"/><path d="M24 3v42M8 24h32" fill="none"/>',
        "random": '<circle cx="24" cy="24" r="19"/><path d="M18 19a6 6 0 1 1 8 5.6c-1.5.6-2 1.6-2 3.4M24 34v.5" fill="none"/>',
        "none": '<circle cx="24" cy="24" r="19"/><path d="M24 5v38M24 24 10.5 37.5M24 24l13.5 13.5" fill="none"/>',
    }
    return (f'<svg class="emb emb-{cls}" width="{size}" height="{size}" viewBox="0 0 48 48" aria-hidden="true" '
            f'stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round">{shapes[cls]}</svg>')


def unit_img(key: str, cls: str, label: bool = False) -> str:
    """에셋 PNG. 파일이 없으면 빈 문자열 — 그림 하나 빠져도 페이지는 그대로 선다."""
    if key not in UNIT_KO or not (ASSET_DIR / f"{key}.png").is_file():
        return ""
    alt = esc(UNIT_KO[key]) if label else ""
    return f'<img class="{cls}" src="{ASSET_URL}/{key}.png" alt="{alt}" title="{esc(UNIT_KO[key])}" loading="lazy" decoding="async">'


def name_chip(mid: str, member_by_id: dict[str, dict[str, Any]]) -> str:
    m = member_by_id.get(mid, {})
    cls = RACES.get(m.get("race", ""), "none")
    return f'<span class="nc nc-{cls}">{unit_img(m.get("unit", ""), "nc-img")}{esc(mid)}</span>'


def wl(w: int, l: int) -> str:
    return f'<b class="w">{w}승</b> <b class="l">{l}패</b>'


def render(data: dict[str, Any], stats: dict[str, Any], built: str) -> str:
    members = data["members"]
    by_id = {m["id"]: m for m in members}
    people, lunch = stats["people"], stats["lunch"]
    side = {m["id"] for m in members if m.get("ai") or m.get("guest")}
    ai = {m["id"] for m in members if m.get("ai")}
    humans = [m for m in members if not m.get("ai") and not m.get("guest")]
    played = [m for m in humans if people[m["id"]]["w"] + people[m["id"]]["l"]]

    # ── 히어로
    sessions = len(data.get("matches") or [])
    lunch_days = stats["lunch_days"]
    active_sum = sum(lunch[m["id"]]["active"] for m in humans)
    avg_daily = active_sum / len(lunch_days) if lunch_days else 0
    nxt = data.get("next") or {}
    next_html = ""
    if nxt.get("date"):
        next_html = (
            f'<div class="next" data-date="{esc(nxt["date"])}"><span class="blink"></span>'
            f'<span class="next-k">NEXT BATTLE</span><b class="next-d" id="es-dday">{esc(short_date(nxt["date"]))}</b>'
            f'<span class="next-t">{esc(nxt.get("title", ""))}</span>'
            f'<span class="next-s">{esc(nxt.get("status", ""))}</span></div>'
        )
    hud = "".join(
        f'<div class="hud-cell"><span class="hud-v">{v}</span><span class="hud-k">{esc(k)}</span></div>'
        for v, k in (
            (stats["games"], "기록된 판"),
            (sessions, "전쟁 회차"),
            (len(played), "참전 용사"),
            (f"{avg_daily:.1f}", "하루 평균 점심 활동"),
        )
    )
    # 최근 N일 창이 아니라 처음부터의 누적이다 — 활동 월드컵(최근 7일)과 헷갈리지 않게 기간을 박아 둔다
    dated = sorted([m["date"] for m in data.get("matches") or [] if m.get("date")] + [d["date"] for d in lunch_days])
    undated = any(not m.get("date") for m in data.get("matches") or [])
    span_all = "전체 누적 기록"
    if dated:
        span_all += f" · {short_date(dated[0])} ~ {short_date(dated[-1])}"
    if undated:
        span_all += " (방 개설 전 경기 포함)"
    fleet = "".join(unit_img(k, f"fly fly-{n}") for n, k in enumerate(HERO_FLEET, 1))
    lineup = "".join(f'<span class="ln ln-{n}">{unit_img(k, "ln-img")}</span>' for n, k in enumerate(HERO_LINEUP, 1))
    hero = (
        '<header class="hero"><div class="stars s1"></div><div class="stars s2"></div><div class="stars s3"></div>'
        f'<div class="grid-floor"></div><div class="planet"></div><div class="fleet" aria-hidden="true">{fleet}</div>'
        f'<div class="lineup" aria-hidden="true">{lineup}</div>'
        '<div class="in"><a class="back" href="index.html">← 파트 카드</a>'
        f'<div class="eyebrow">LUNCHTIME STARCRAFT LEAGUE · SEASON 1 · {esc(data.get("room", ""))} 방</div>'
        f'<h1 class="title" data-text="{esc(data.get("title", ""))}">{esc(data.get("title", ""))}</h1>'
        '<p class="sub">점심 한 시간. 누구는 바벨을 들고, 누구는 따릉이를 달려 PC방으로 워프한다. '
        '헬스장과 피시방을 오가는 세일즈플러스 용사들의 전적실.</p>'
        f'<p class="range">📜 {esc(span_all)}</p>'
        f'<div class="hud">{hud}</div>{next_html}</div></header>'
    )

    # ── MVP 보드
    ranked = sorted((m for m in played if people[m["id"]]["w"] + people[m["id"]]["l"] >= MIN_GAMES_FOR_TITLE),
                    key=lambda m: (-people[m["id"]]["rating"], -people[m["id"]]["w"], m["id"]))
    tiles = []
    if ranked:
        k = ranked[0]["id"]
        p = people[k]
        tiles.append(("👑", "승률 1위", name_chip(k, by_id), f'{wl(p["w"], p["l"])} · {pct(p["rate"])}', "gold"))
    duo = best_duo(stats, side)
    if duo:
        a, b, w, l = duo
        tiles.append(("💞", "최강 듀오", f"{name_chip(a, by_id)}<i>×</i>{name_chip(b, by_id)}", f"같은 팀 {wl(w, l)} · {pct(rate(w, l))}", "pink"))
    lunch_king = min(humans, key=lambda m: (-lunch[m["id"]]["active"], -(lunch[m["id"]]["ratio"] or 0), m["id"]), default=None)
    if lunch_king and lunch[lunch_king["id"]]["active"]:
        c = lunch[lunch_king["id"]]
        tiles.append(("🏋️", "점심 출석왕", name_chip(lunch_king["id"], by_id), f'{c["active"]}회 활동 · 출석률 {pct(c["ratio"])}', "cyan"))
    for k in sorted(ai):
        p = people[k]
        if p["w"] + p["l"]:
            tiles.append(("🤖", "용병 승률", name_chip(k, by_id), f'{wl(p["w"], p["l"])} · {pct(p["rate"])}', "violet"))
    mvp = "".join(
        f'<div class="tile t-{tone}">{unit_img(TILE_UNITS.get(tone, ""), "tile-unit")}<span class="tile-ic">{ic}</span><span class="tile-k">{esc(k)}</span>'
        f'<span class="tile-v">{v}</span><span class="tile-s">{s}</span></div>'
        for ic, k, v, s, tone in tiles
    )
    s_mvp = section("HALL OF FAME", "명예의 전당", f'<div class="tiles">{mvp}</div>',
                    f"승률 1위는 {MIN_GAMES_FOR_TITLE}판 이상 · 듀오는 같은 팀 2판 이상만 후보")

    # ── 전사 명부
    order = sorted(members, key=lambda m: (
        2 if m.get("ai") else 1 if m.get("guest") else 0,
        0 if people[m["id"]]["w"] + people[m["id"]]["l"] else 1,
        -people[m["id"]]["rating"], m["id"]))
    roster = "".join(unit_card(m, people[m["id"]], lunch[m["id"]], by_id, i + 1) for i, m in enumerate(order))
    s_roster = section("ROSTER", "전사 명부", f'<div class="units">{roster}</div>',
                       "예상 종족은 대화 속 빌드·유닛으로 추정 — 막대 칸 수가 확신도")

    # ── 랭킹표
    rows = []
    for rank, m in enumerate(sorted(played, key=lambda m: (-people[m["id"]]["rating"], -people[m["id"]]["w"], m["id"])), 1):
        p = people[m["id"]]
        bar = round((p["rate"] or 0) * 100)
        rows.append(
            f'<tr><td class="rk">{rank:02d}</td><td>{emblem(m.get("race", ""), 22)} {name_chip(m["id"], by_id)}'
            f'<span class="nick">{esc(m.get("nickname", ""))}</span></td>'
            f'<td class="num">{p["w"]}</td><td class="num">{p["l"]}</td>'
            f'<td><div class="rbar"><span style="width:{bar}%"></span><em>{pct(p["rate"])}</em></div></td></tr>'
        )
    s_rank = section("LADDER", "래더 랭킹",
                     '<div class="panel scroll"><table class="ladder"><thead><tr><th>#</th><th>용사</th><th>승</th><th>패</th><th>승률</th></tr></thead>'
                     f'<tbody>{"".join(rows)}</tbody></table></div>',
                     "순위는 판 수가 적으면 50% 쪽으로 당긴 보정 승률 기준 · 게스트·용병 제외")

    # ── 듀오 시너지 매트릭스
    mx_ids = [m["id"] for m in order if people[m["id"]]["w"] + people[m["id"]]["l"]]
    head = "".join(f'<th><span class="vt">{esc(i)}</span></th>' for i in mx_ids)
    body = []
    for a in mx_ids:
        cells = []
        for b in mx_ids:
            if a == b:
                cells.append(f'<td class="self">{emblem(by_id[a].get("race", ""), 18)}</td>')
                continue
            w, l = stats["pair"].get(tuple(sorted((a, b))), (0, 0))
            r = rate(w, l)
            if r is None:
                cells.append('<td class="none" title="같은 팀 기록 없음">·</td>')
            else:
                tone = "hot" if r > 0.5 else "cold" if r < 0.5 else "even"
                # 진하기는 승률 차이 × 판 수 — 1판짜리 100% 가 5판짜리보다 튀지 않게
                cells.append(f'<td class="{tone}" style="--a:{0.18 + abs(r - 0.5) * 1.4 * min(1, (w + l) / 4):.2f}" '
                             f'title="{esc(a)} + {esc(b)} · 같은 팀 {w}승 {l}패">{w}-{l}</td>')
        body.append(f'<tr><th class="rh">{esc(a)}</th>{"".join(cells)}</tr>')
    rivals = "".join(
        f'<li><span class="rv-a">{name_chip(a, by_id)}</span><span class="rv-x">{w} : {l}</span>'
        f'<span class="rv-b">{name_chip(b, by_id)}</span><span class="rv-n">{w + l}번 맞붙음</span></li>'
        for a, b, w, l in top_rivalries(stats, side)
    )
    s_syn = section("SYNERGY", "듀오 시너지 · 라이벌",
                    '<div class="syn"><div class="panel scroll"><table class="matrix"><thead><tr><th></th>'
                    f'{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
                    '<p class="legend"><span class="lg hot"></span>같이 하면 이김 <span class="lg cold"></span>같이 하면 짐 <span class="lg even"></span>반반 · 칸은 같은 팀 승-패</p></div>'
                    f'<div class="panel"><h3 class="ph">⚔️ 라이벌 매치업</h3><ul class="rivals">{rivals}</ul>'
                    '<p class="legend">맞붙은 판이 많은 순 · 왼쪽이 앞선 쪽</p></div></div>',
                    "같은 팀일 때 성적이 파트너 궁합이다")

    # ── 팀 짜기
    pool = [m for m in members if m.get("race") or people[m["id"]]["w"] + people[m["id"]]["l"]]
    picks = "".join(
        f'<label class="pick pk-{RACES.get(m.get("race", ""), "none")}"><input type="checkbox" value="{esc(m["id"])}"'
        f'{" checked" if not m.get("guest") and not m.get("ai") and people[m["id"]]["w"] + people[m["id"]]["l"] else ""}>'
        f'<span>{unit_img(m.get("unit", ""), "pick-img")}{esc(m["id"])}</span></label>'
        for m in pool
    )
    s_draft = section("BALANCE PATCH", "커미셔너의 밸런스 패치",
                      f'<div class="panel draft"><div class="picks">{picks}</div>'
                      '<button type="button" class="go" id="es-draft">⚡ 팀 짜기</button>'
                      '<div class="draft-out" id="es-draft-out"><p class="legend">오늘 갈 사람을 고르고 버튼을 누르면 보정 승률 합이 가장 비슷하게 나눈다. 누를 때마다 비슷한 후보 중에서 새로 뽑는다.</p></div></div>',
                      "현진 커미셔너가 매번 손으로 하던 일을 기계에게")

    # ── 전투 기록
    logs = []
    for match in sorted(data.get("matches") or [], key=lambda x: x.get("date") or "", reverse=True):
        games = []
        for n, g in enumerate(match["games"], 1):
            win = "".join(name_chip(p, by_id) for p in g["win"])
            lose = "".join(name_chip(p, by_id) for p in g["lose"]) or '<span class="unk">상대 기록 없음</span>'
            games.append(f'<li><span class="gn">G{n}</span><span class="gw">{win}</span><span class="vs">VS</span><span class="gl">{lose}</span></li>')
        logs.append(
            f'<article class="log"><div class="log-d">{esc(short_date(match.get("date", "")))}</div>'
            f'<div class="log-b"><h3>{esc(match.get("title", ""))} <span class="mode">{esc(match.get("mode", ""))}</span></h3>'
            f'<ul class="games">{"".join(games)}</ul><p class="log-n">{esc(match.get("note", ""))}</p></div></article>'
        )
    s_log = section("BATTLE LOG", "전투 기록", f'<div class="timeline">{"".join(logs)}</div>',
                    "빛나는 쪽이 이긴 팀 · 판 순서·판 수는 대화로 복원한 것이라 틀릴 수 있다")

    # ── 점심 출석부
    lrows = []
    for m in sorted(humans, key=lambda m: (-(lunch[m["id"]]["ratio"] or -1), -lunch[m["id"]]["active"], m["id"])):
        c = lunch[m["id"]]
        if not c["days"]:
            continue
        segs = "".join(
            f'<span class="sg sg-{s}" style="width:{c[s] / c["days"] * 100:.2f}%" title="{LUNCH_KO[s]} {c[s]}회"></span>'
            for s in LUNCH_STATUSES if c[s]
        )
        lrows.append(
            f'<div class="lrow"><span class="lname">{name_chip(m["id"], by_id)}</span><div class="lbar">{segs}</div>'
            f'<span class="lpct">{pct(c["ratio"])}</span><span class="lcnt">헬스 {c["gym"]} · 러닝 {c["run"]} · 스타 {c["star"]}</span></div>'
        )
    cal_head = "".join(
        f'<th title="{esc(d["date"])}"><span>{date.fromisoformat(d["date"]).day}</span>'
        f'<em>{WEEKDAYS[date.fromisoformat(d["date"]).weekday()]}</em></th>' for d in lunch_days
    )
    cal_rows = []
    for m in humans:
        status_of = {}
        for d in lunch_days:
            for s in LUNCH_STATUSES:
                if m["id"] in (d.get(s) or []):
                    status_of[d["date"]] = s
        cells = "".join(
            f'<td class="cc cc-{status_of.get(d["date"], "na")}" title="{esc(d["date"])} {LUNCH_KO.get(status_of.get(d["date"], ""), "기록 없음")}"></td>'
            for d in lunch_days
        )
        cal_rows.append(f'<tr><th class="rh">{esc(m["id"])}</th>{cells}</tr>')
    wd_bars = []
    wd = stats["weekday"]
    peak = max((v[0] / v[1] for v in wd.values() if v[1]), default=0) or 1
    for i in range(5):
        tot, n = wd.get(i, (0, 0))
        avg = tot / n if n else 0
        wd_bars.append(f'<div class="wd"><span class="wd-bar" style="height:{avg / peak * 100:.1f}%"><em>{avg:.1f}</em></span><b>{WEEKDAYS[i]}</b></div>')
    span = (f"{short_date(lunch_days[0]['date'])} ~ {short_date(lunch_days[-1]['date'])} · 기록 {len(lunch_days)}일"
            if lunch_days else "아직 기록 없음")
    s_lunch = section("LUNCH OPS", "점심 운동 출석부",
                      '<div class="lunch"><div class="panel">'
                      f'<h3 class="ph">개인 출석률 <span class="ph-s">{esc(span)}</span></h3>'
                      f'{"".join(lrows)}'
                      '<p class="legend">'
                      f'<span class="lg sg-gym"></span>{unit_img(LUNCH_UNITS["gym"], "lg-img")}헬스 '
                      f'<span class="lg sg-run"></span>{unit_img(LUNCH_UNITS["run"], "lg-img")}러닝(발업) '
                      f'<span class="lg sg-star"></span>{unit_img(LUNCH_UNITS["star"], "lg-img")}스타(e스포츠도 스포츠) '
                      '<span class="lg sg-skip"></span>불참</p></div>'
                      f'<div class="panel"><h3 class="ph">요일별 평균 출동 인원</h3><div class="wds">{"".join(wd_bars)}</div></div></div>'
                      '<div class="panel scroll cal-wrap"><table class="cal"><thead><tr><th></th>'
                      f'{cal_head}</tr></thead><tbody>{"".join(cal_rows)}</tbody></table></div>',
                      "방에서 그날 밝힌 것만 센다 · 말이 없던 날과 휴가·병가로 자리에 없던 날은 빈칸이고 분모에서 뺀다 · 불참 사유는 싣지 않는다")

    payload = {
        "ratings": {m["id"]: round(people[m["id"]]["rating"], 4) for m in members},
        "races": {m["id"]: RACES.get(m.get("race", ""), "none") for m in members},
        "units": {m["id"]: f'{ASSET_URL}/{m["unit"]}.png' for m in members
                  if m.get("unit") in UNIT_KO and (ASSET_DIR / f'{m["unit"]}.png').is_file()},
        "next": nxt.get("date", ""),
    }
    blob = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")  # </script>·<!-- 로 블록이 끊기지 않게
    script = f'<script type="application/json" id="es-data">{blob}</script><script>{JS}</script>'
    foot = (
        f'<footer><p>{esc(data.get("source", ""))} · 기록 기준 {esc(data.get("updated", ""))} · 빌드 {esc(built)}</p>'
        '<p>경기를 했으면 이 저장소 <code>esports/records.json</code> 의 matches 에 판마다 win·lose 한 줄, 점심이면 lunch 에 하루 한 줄. '
        '나머지 숫자는 빌드가 다시 계산한다. 이름·별명이 마음에 안 들면 같은 파일에서 고치면 된다.</p>'
        '<p>재미로 보는 페이지다. 인사·평가와 무관하고, 대화 원문과 불참 사유는 싣지 않는다.</p>'
        '<p>StarCraft 및 유닛·건물 이미지는 Blizzard Entertainment 의 상표·저작물이다. 비영리 팬 페이지로만 쓰며 Blizzard 와 무관하다.</p></footer>'
    )
    body = (hero + '<main class="wrap">' + s_mvp + s_roster + s_rank + s_syn + s_draft + s_log + s_lunch + foot + "</main>" + script)
    return (
        '<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="robots" content="noindex,nofollow">\n<meta name="color-scheme" content="dark">\n'
        f'<title>{esc(data.get("title", "e스포츠"))} · 점심 스타 전적실</title>\n<style>{CSS}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n'
    )


def section(tag: str, title: str, body: str, sub: str = "") -> str:
    subhtml = f'<span class="sec-s">{esc(sub)}</span>' if sub else ""
    icon = unit_img(SECTION_UNITS.get(tag, ""), "sec-ic")
    return (f'<section class="sec"><div class="sec-h">{icon}<span class="sec-tag">{esc(tag)}</span><h2>{esc(title)}</h2>{subhtml}</div>'
            f"{body}</section>")


def unit_card(m: dict[str, Any], p: dict[str, Any], c: dict[str, Any], by_id: dict[str, dict[str, Any]], no: int) -> str:
    race = m.get("race", "")
    cls = RACES.get(race, "none")
    games = p["w"] + p["l"]
    pips = CONFIDENCE_PIPS.get(m.get("race_confidence", ""), 0)
    pip_html = "".join(f'<i class="{"on" if k < pips else ""}"></i>' for k in range(3))
    deg = round((p["rate"] or 0) * 360)
    ring = (
        f'<div class="ring" style="--deg:{deg}deg"><div><b>{pct(p["rate"])}</b><span>{p["w"]}W {p["l"]}L</span></div></div>'
        if games else '<div class="ring off"><div><b>—</b><span>미출전</span></div></div>'
    )

    def rel(icon: str, label: str, t: tuple[str, int, int] | None, empty: str, kind: str) -> str:
        if not t:
            return f'<li class="rel rel-{kind} none"><span class="ri">{icon}</span><span class="rk">{esc(label)}</span><span class="rv">{esc(empty)}</span></li>'
        who, w, l = t
        return (f'<li class="rel rel-{kind}"><span class="ri">{icon}</span><span class="rk">{esc(label)}</span>'
                f'<span class="rv">{name_chip(who, by_id)}<small>{w}승 {l}패</small></span></li>')

    rels = ""
    if games:
        rels = (
            '<ul class="rels">'
            + rel("💞", "최고의 파트너", p["best"], "아직 같이 이겨 본 짝이 없다", "best")
            + rel("💀", "최악의 파트너", p["worst"], "아직 없음 — 다 잘 맞았다", "worst")
            + rel("⚔️", "천적", p["nemesis"], "아직 없음", "nem")
            + rel("🍖", "단골 손님", p["prey"], "아직 없음", "prey")
            + "</ul>"
        )
    lunch_html = ""
    if c["days"]:
        segs = "".join(f'<span class="sg sg-{s}" style="width:{c[s] / c["days"] * 100:.2f}%"></span>' for s in LUNCH_STATUSES if c[s])
        lunch_html = (f'<div class="u-lunch"><span>점심 출석 {pct(c["ratio"])}</span><div class="lbar sm">{segs}</div>'
                      f'<small>헬스 {c["gym"]} · 러닝 {c["run"]} · 스타 {c["star"]} / {c["days"]}일</small></div>')
    tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in (("게스트",) if m.get("guest") else ()) + (("AI 용병",) if m.get("ai") else ()))
    race_label = race or "종족 미정"
    key = m.get("unit", "")
    portrait = unit_img(key, "u-img", label=True)
    port = (f'<div class="u-port">{portrait}<span class="u-emb">{emblem(race, 18)}</span></div>' if portrait
            else emblem(race, 54))
    sig = f'<span class="u-sig">대표 유닛 · {esc(UNIT_KO[key])}</span>' if portrait else ""
    return (
        f'<article class="unit u-{cls}"><div class="u-glow"></div>'
        f'<div class="u-top"><span class="u-no">#{no:02d}</span>{tags}<span class="u-race">{esc(RACE_EN[cls])}</span></div>'
        f'<div class="u-head">{port}<div><h3>{esc(m["id"])}</h3><p class="u-nick">{esc(m.get("nickname", ""))}</p>{sig}'
        f'<p class="u-role">{esc(m.get("role", ""))}</p></div>{ring}</div>'
        f'<div class="u-pred"><span>예상 종족</span><b>{esc(race_label)}</b><span class="pips" title="확신도 {esc(m.get("race_confidence", "") or "—")}">{pip_html}</span></div>'
        f'<p class="u-ev">{esc(m.get("race_evidence", ""))}</p>'
        f'{rels}{lunch_html}<p class="u-tag">“{esc(m.get("tagline", ""))}”</p></article>'
    )


# ───────────────────────────────────────────────────────────────────────── 스타일·스크립트

CSS = """
:root{
  --bg:#04050c; --bg-2:#0a0e1f; --panel:rgba(13,19,40,.74); --panel-2:rgba(20,28,58,.6);
  --line:rgba(130,165,255,.16); --line-2:rgba(130,165,255,.3);
  --ink:#ECF0FF; --ink-2:#B9C3E6; --muted:#8792B8;
  --terran:#3DA9FF; --terran-2:#9AD4FF; --zerg:#C45BFF; --zerg-2:#FF4F7E; --protoss:#FFD23F; --protoss-2:#47F0FF;
  --random:#9FA8C9; --peace:#7CF3B8; --brand:#FF6A3D;
  --win:#3DFFA8; --lose:#FF5577; --gym:#FF7A45; --run:#38E1FF; --star:#B26BFF; --skip:rgba(150,160,200,.22);
  --r:18px;
}
*{box-sizing:border-box;margin:0;padding:0}
html{background:var(--bg)}
body{background:var(--bg);color:var(--ink);font:15px/1.6 "Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;overflow-x:hidden}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:50;
  background:repeating-linear-gradient(0deg,rgba(255,255,255,.018) 0 1px,transparent 1px 3px);mix-blend-mode:overlay}
a{color:inherit}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:rgba(255,255,255,.07);padding:1px 6px;border-radius:6px}

/* ── 히어로: 별 3겹 시차 + 원근 격자 바닥 + 행성 */
.hero{position:relative;overflow:hidden;isolation:isolate;padding:58px 0 46px;min-height:520px;
  background:radial-gradient(120% 90% at 80% -10%,#24124a 0%,transparent 55%),radial-gradient(90% 80% at 0% 100%,#081e3d 0%,transparent 60%),var(--bg)}
.stars{position:absolute;inset:-50% 0 0;z-index:-3;background-repeat:repeat;animation:drift linear infinite}
.s1{background-image:radial-gradient(1px 1px at 20px 30px,#fff,transparent),radial-gradient(1px 1px at 120px 80px,#cfe3ff,transparent),
  radial-gradient(1px 1px at 200px 160px,#fff,transparent),radial-gradient(1px 1px at 60px 210px,#ffe9b0,transparent);background-size:240px 240px;animation-duration:90s;opacity:.7}
.s2{background-image:radial-gradient(1.6px 1.6px at 70px 50px,#fff,transparent),radial-gradient(1.4px 1.4px at 300px 260px,#bfe0ff,transparent),
  radial-gradient(1.6px 1.6px at 180px 330px,#ffd9f2,transparent);background-size:380px 380px;animation-duration:60s;opacity:.8}
.s3{background-image:radial-gradient(2.4px 2.4px at 140px 120px,#fff,transparent),radial-gradient(2px 2px at 420px 380px,#9fe9ff,transparent);
  background-size:520px 520px;animation-duration:38s;animation-name:drift,twinkle;animation-duration:38s,3.2s;animation-iteration-count:infinite,infinite;animation-timing-function:linear,ease-in-out}
@keyframes drift{to{transform:translateY(50%)}}
@keyframes twinkle{50%{opacity:.35}}
.grid-floor{position:absolute;left:-50%;right:-50%;bottom:-2px;height:46%;z-index:-2;
  background:linear-gradient(transparent 0 96%,rgba(71,240,255,.55) 96%),linear-gradient(90deg,transparent 0 97%,rgba(196,91,255,.5) 97%);
  background-size:60px 60px;transform:perspective(320px) rotateX(62deg);transform-origin:bottom;animation:grid 3.2s linear infinite;
  -webkit-mask-image:linear-gradient(to top,#000 10%,transparent 92%);mask-image:linear-gradient(to top,#000 10%,transparent 92%)}
@keyframes grid{to{background-position:0 60px,0 0}}
.planet{position:absolute;z-index:-1;right:max(-80px,calc(50% - 640px));top:40px;width:clamp(150px,26vw,280px);aspect-ratio:1;border-radius:50%;
  background:radial-gradient(circle at 32% 30%,#ffe7a3 0%,#ff8a4c 26%,#a12a6b 58%,#1b0b33 80%);
  box-shadow:0 0 80px 10px rgba(255,106,61,.32),inset -26px -18px 60px rgba(0,0,0,.6);animation:float 9s ease-in-out infinite}
.planet::after{content:"";position:absolute;left:-28%;right:-28%;top:44%;height:16%;border-radius:50%;
  border:3px solid rgba(255,210,63,.55);border-top-color:transparent;transform:rotate(-14deg)}
@keyframes float{50%{transform:translateY(-12px)}}
.hero .in{max-width:1120px;margin:0 auto;padding:0 20px;position:relative}
.back{display:inline-block;font-size:12.5px;color:var(--ink-2);text-decoration:none;margin-bottom:22px;padding:4px 12px;border:1px solid var(--line-2);border-radius:999px;background:rgba(4,5,12,.5)}
.back:hover{color:#fff;border-color:var(--protoss-2)}
.eyebrow{font:700 11.5px/1 ui-monospace,Menlo,monospace;letter-spacing:.22em;color:var(--protoss-2);text-shadow:0 0 12px rgba(71,240,255,.7)}
.title{position:relative;display:inline-block;margin-top:16px;font-size:clamp(52px,13vw,128px);font-weight:900;line-height:.95;letter-spacing:-.05em;
  background:linear-gradient(180deg,#fff 0%,#ffe48a 38%,#ff7a45 70%,#c43c8f 100%);-webkit-background-clip:text;background-clip:text;color:transparent;
  filter:drop-shadow(0 0 22px rgba(255,138,76,.45))}
.title::before,.title::after{content:attr(data-text);position:absolute;inset:0;background:inherit;-webkit-background-clip:text;background-clip:text;color:transparent}
.title::before{animation:glitch 4.2s infinite steps(1);clip-path:inset(0 0 62% 0);transform:translateX(-3px);filter:hue-rotate(160deg)}
.title::after{animation:glitch 3.4s infinite steps(1) reverse;clip-path:inset(58% 0 0 0);transform:translateX(3px);filter:hue-rotate(-90deg)}
@keyframes glitch{0%,91%,100%{opacity:0}92%{opacity:1;transform:translate(-5px,1px)}95%{opacity:1;transform:translate(4px,-2px)}97%{opacity:1;transform:translate(-2px,0)}}
.sub{max-width:58ch;margin-top:18px;color:var(--ink-2);font-size:15.5px}
.range{display:inline-block;margin-top:12px;padding:5px 12px;border-radius:999px;font-size:12.5px;font-weight:700;color:var(--protoss);
  background:rgba(255,210,63,.08);border:1px solid rgba(255,210,63,.35)}
.hud{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:30px;max-width:760px}
.hud-cell{position:relative;padding:14px 16px 12px;background:rgba(10,14,31,.66);border:1px solid var(--line-2);border-radius:14px;backdrop-filter:blur(8px);
  clip-path:polygon(0 0,calc(100% - 14px) 0,100% 14px,100% 100%,14px 100%,0 calc(100% - 14px))}
.hud-v{display:block;font:800 30px/1 ui-monospace,Menlo,monospace;color:#fff;text-shadow:0 0 16px rgba(71,240,255,.6)}
.hud-k{display:block;margin-top:6px;font-size:12px;color:var(--muted)}
.next{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;margin-top:16px;max-width:760px;padding:12px 16px;border-radius:14px;
  background:linear-gradient(90deg,rgba(255,79,126,.18),rgba(196,91,255,.12));border:1px solid rgba(255,79,126,.45)}
.blink{width:9px;height:9px;border-radius:50%;background:var(--zerg-2);box-shadow:0 0 12px var(--zerg-2);animation:twinkle 1s infinite}
.next-k{font:700 11px ui-monospace,Menlo,monospace;letter-spacing:.2em;color:var(--zerg-2)}
.next-d{font:800 18px ui-monospace,Menlo,monospace;color:#fff}
.next-t{font-weight:700}.next-s{font-size:12px;color:var(--muted)}

/* ── 섹션 */
.wrap{max-width:1120px;margin:0 auto;padding:10px 20px 80px}
.sec{margin-top:56px}
.sec-h{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px;margin-bottom:18px}
.sec-tag{font:700 11px ui-monospace,Menlo,monospace;letter-spacing:.24em;color:var(--protoss);padding:4px 9px;border:1px solid rgba(255,210,63,.4);border-radius:6px;background:rgba(255,210,63,.07)}
.sec h2{font-size:clamp(22px,4vw,30px);font-weight:850;letter-spacing:-.03em}
.sec-s{font-size:12.5px;color:var(--muted);flex-basis:100%}
.panel{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:18px;backdrop-filter:blur(10px)}
.panel.scroll{overflow-x:auto}
.ph{font-size:15px;font-weight:800;margin-bottom:12px}.ph-s{font-size:12px;font-weight:500;color:var(--muted);margin-left:6px}
.legend{margin-top:12px;font-size:12px;color:var(--muted);line-height:1.9}
.lg{display:inline-block;width:11px;height:11px;border-radius:3px;vertical-align:-1px;margin:0 4px 0 10px}
.lg:first-child{margin-left:0}

/* 종족별 이름표 */
.nc{display:inline-block;padding:1px 9px;margin:2px 3px 2px 0;border-radius:999px;font-weight:750;font-size:13px;border:1px solid;white-space:nowrap}
.nc-terran{color:var(--terran-2);border-color:rgba(61,169,255,.5);background:rgba(61,169,255,.1)}
.nc-zerg{color:#E6B3FF;border-color:rgba(196,91,255,.5);background:rgba(196,91,255,.12)}
.nc-protoss{color:#FFE7A0;border-color:rgba(255,210,63,.5);background:rgba(255,210,63,.1)}
.nc-random{color:#DCE2F7;border-color:rgba(159,168,201,.5);background:rgba(159,168,201,.1)}
.nc-none{color:#B5F7D6;border-color:rgba(124,243,184,.45);background:rgba(124,243,184,.08)}
.emb{flex:none;fill:none;stroke:currentColor}
.emb-terran{color:var(--terran);filter:drop-shadow(0 0 6px rgba(61,169,255,.8))}
.emb-zerg{color:var(--zerg);filter:drop-shadow(0 0 6px rgba(196,91,255,.85))}
.emb-protoss{color:var(--protoss);filter:drop-shadow(0 0 6px rgba(255,210,63,.8))}
.emb-random{color:var(--random)}
.emb-none{color:var(--peace);filter:drop-shadow(0 0 6px rgba(124,243,184,.6))}
.emb-zerg circle{fill:currentColor}

/* ── 명예의 전당 */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
.tile{position:relative;overflow:hidden;display:flex;flex-direction:column;gap:6px;padding:20px 18px 18px;border-radius:var(--r);background:var(--panel);border:1px solid var(--line-2)}
.tile::before{content:"";position:absolute;inset:-1px;border-radius:inherit;padding:1px;background:conic-gradient(from var(--spin,0deg),transparent 0 60%,var(--tc) 75%,transparent 90%);
  -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;animation:spin 5s linear infinite}
@property --spin{syntax:"<angle>";inherits:false;initial-value:0deg}
@keyframes spin{to{--spin:360deg}}
.t-gold{--tc:var(--protoss)}.t-pink{--tc:var(--zerg-2)}.t-cyan{--tc:var(--protoss-2)}.t-violet{--tc:var(--zerg)}
.tile-ic{font-size:30px;line-height:1;filter:drop-shadow(0 0 10px var(--tc))}
.tile-k{font:700 11px ui-monospace,Menlo,monospace;letter-spacing:.18em;color:var(--tc)}
.tile-v{font-size:20px}.tile-v .nc{font-size:16px}.tile-v i{font-style:normal;color:var(--muted);margin:0 4px}
.tile-s{font-size:13px;color:var(--ink-2)}
.w{color:var(--win)}.l{color:var(--lose)}

/* ── 전사 명부 */
.units{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.unit{--rc:var(--random);--rc2:var(--random);position:relative;overflow:hidden;padding:16px 18px 18px;border-radius:var(--r);
  background:linear-gradient(160deg,color-mix(in srgb,var(--rc) 12%,transparent),transparent 42%),var(--panel);
  border:1px solid color-mix(in srgb,var(--rc) 38%,transparent);transition:transform .25s,box-shadow .25s,border-color .25s}
.unit:hover{transform:translateY(-4px);border-color:var(--rc);box-shadow:0 16px 44px -18px var(--rc),0 0 0 1px color-mix(in srgb,var(--rc) 40%,transparent) inset}
.u-terran{--rc:var(--terran);--rc2:var(--terran-2)}.u-zerg{--rc:var(--zerg);--rc2:var(--zerg-2)}.u-protoss{--rc:var(--protoss);--rc2:var(--protoss-2)}
.u-none{--rc:var(--peace);--rc2:var(--peace)}
.u-glow{position:absolute;right:-60px;top:-60px;width:170px;height:170px;border-radius:50%;background:radial-gradient(circle,color-mix(in srgb,var(--rc) 35%,transparent),transparent 70%);pointer-events:none}
.u-top{display:flex;align-items:center;gap:6px;font:700 10.5px ui-monospace,Menlo,monospace;letter-spacing:.16em;color:var(--muted)}
.u-race{margin-left:auto;color:var(--rc)}
.tag{padding:1px 7px;border-radius:5px;background:rgba(255,255,255,.08);color:var(--ink-2);letter-spacing:.04em}
.u-head{display:flex;align-items:center;gap:12px;margin-top:10px}
.u-head h3{font-size:24px;font-weight:900;letter-spacing:-.03em;line-height:1.1}
.u-nick{font-weight:800;color:var(--rc2);font-size:14px}
.u-role{font-size:12px;color:var(--muted)}
.ring{--deg:0deg;margin-left:auto;flex:none;width:76px;height:76px;border-radius:50%;padding:6px;
  background:conic-gradient(var(--win) var(--deg),rgba(255,85,119,.55) var(--deg));box-shadow:0 0 22px -6px var(--win)}
.ring.off{background:conic-gradient(rgba(150,160,200,.25) 360deg,transparent 0);box-shadow:none}
.ring>div{width:100%;height:100%;border-radius:50%;background:var(--bg-2);display:flex;flex-direction:column;align-items:center;justify-content:center}
.ring b{font:800 17px/1 ui-monospace,Menlo,monospace}.ring span{font-size:10.5px;color:var(--muted);margin-top:3px}
.u-pred{display:flex;align-items:center;gap:8px;margin-top:14px;padding:8px 11px;border-radius:11px;background:rgba(0,0,0,.28);font-size:12.5px;color:var(--muted)}
.u-pred b{color:var(--rc);font-size:14px}
.pips{display:inline-flex;gap:3px;margin-left:auto}
.pips i{width:16px;height:6px;border-radius:2px;background:rgba(255,255,255,.12)}
.pips i.on{background:var(--rc);box-shadow:0 0 6px var(--rc)}
.u-ev{font-size:12px;color:var(--muted);margin-top:6px}
.rels{list-style:none;margin-top:12px;display:grid;grid-template-columns:20px max-content minmax(0,1fr);gap:5px 8px;align-items:center;font-size:12.5px}
.rel{display:contents}
.rel .rk{color:var(--muted);white-space:nowrap}.rel .rv small{color:var(--muted);margin-left:2px}
.rel.none .rv{color:var(--muted);font-size:12px}
.u-lunch{margin-top:12px;font-size:12px;color:var(--ink-2)}
.u-lunch small{color:var(--muted)}
.u-tag{margin-top:12px;padding-top:11px;border-top:1px dashed var(--line-2);font-size:13px;color:var(--ink-2);font-style:italic}

/* 막대 */
.lbar{display:flex;height:12px;border-radius:999px;overflow:hidden;background:rgba(255,255,255,.05)}
.lbar.sm{height:7px;margin:5px 0 3px}
.sg{display:block;height:100%}
.sg-gym{background:linear-gradient(90deg,#FF5E3A,var(--gym))}.sg-run{background:linear-gradient(90deg,#1FB8FF,var(--run))}
.sg-star{background:linear-gradient(90deg,#8B4DFF,var(--star))}.sg-skip{background:var(--skip)}
.lg.sg-gym,.lg.sg-run,.lg.sg-star,.lg.sg-skip{height:11px}

/* ── 래더 */
.ladder{width:100%;border-collapse:collapse;min-width:460px}
.ladder th{font:700 11px ui-monospace,Menlo,monospace;letter-spacing:.14em;color:var(--muted);text-align:left;padding:6px 10px;border-bottom:1px solid var(--line-2)}
.ladder td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:middle}
.ladder tbody tr:first-child td{background:rgba(255,210,63,.09)}
.ladder td .emb{vertical-align:middle}
.rk{font:800 15px ui-monospace,Menlo,monospace;color:var(--protoss)}
.num{font:700 14px ui-monospace,Menlo,monospace}
.nick{margin-left:6px;font-size:12px;color:var(--muted)}
.rbar{position:relative;height:18px;min-width:120px;border-radius:5px;background:rgba(255,85,119,.22);overflow:hidden}
.rbar span{position:absolute;inset:0 auto 0 0;background:linear-gradient(90deg,#1fd18a,var(--win));box-shadow:0 0 14px var(--win)}
.rbar em{position:relative;font:700 11.5px/18px ui-monospace,Menlo,monospace;font-style:normal;padding-left:7px;color:#03140c}

/* ── 시너지 */
.syn{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:14px}
.matrix{border-collapse:separate;border-spacing:3px;margin:0 auto}
.matrix th{font-size:12px;font-weight:700;color:var(--ink-2)}
.matrix thead th{height:64px;vertical-align:bottom}
.vt{display:inline-block;writing-mode:vertical-rl}
.matrix .rh{text-align:right;padding-right:6px;white-space:nowrap}
.matrix td{width:42px;height:38px;text-align:center;border-radius:8px;font:700 12px ui-monospace,Menlo,monospace;transition:transform .15s}
.matrix td:hover{transform:scale(1.15);outline:1px solid #fff}
.matrix td.hot{background:rgba(61,255,168,var(--a));color:#eafff5}
.matrix td.cold{background:rgba(255,85,119,var(--a));color:#ffe9ee}
.matrix td.even{background:rgba(255,210,63,.22);color:#fff3c9}
.matrix td.none{color:rgba(255,255,255,.18);background:rgba(255,255,255,.025)}
.matrix td.self{background:rgba(255,255,255,.04)}
.lg.hot{background:rgba(61,255,168,.7)}.lg.cold{background:rgba(255,85,119,.7)}.lg.even{background:rgba(255,210,63,.5)}
.rivals{list-style:none;display:grid;gap:10px}
.rivals li{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:8px;padding:10px 12px;border-radius:12px;background:rgba(0,0,0,.25);border:1px solid var(--line)}
.rv-a{text-align:right}.rv-x{font:800 18px ui-monospace,Menlo,monospace;color:#fff;text-shadow:0 0 10px var(--zerg-2)}
.rv-n{grid-column:1/-1;text-align:center;font-size:11.5px;color:var(--muted)}

/* ── 팀 짜기 */
.draft{display:grid;gap:14px}
.picks{display:flex;flex-wrap:wrap;gap:8px}
.pick{cursor:pointer}
.pick input{position:absolute;opacity:0;pointer-events:none}
.pick span{display:inline-block;padding:7px 14px;border-radius:10px;font-weight:750;border:1px solid var(--line-2);color:var(--muted);transition:.15s}
.pick input:checked+span{color:#fff;border-color:var(--pc);background:color-mix(in srgb,var(--pc) 22%,transparent);box-shadow:0 0 16px -4px var(--pc)}
.pick input:focus-visible+span{outline:2px solid #fff;outline-offset:2px}
.pk-terran{--pc:var(--terran)}.pk-zerg{--pc:var(--zerg)}.pk-protoss{--pc:var(--protoss)}.pk-random{--pc:var(--random)}.pk-none{--pc:var(--peace)}
.go{justify-self:start;font-family:inherit;font-weight:800;font-size:15px;padding:12px 22px;border:0;border-radius:12px;cursor:pointer;color:#170a00;
  background:linear-gradient(90deg,var(--protoss),#FF8A4C);box-shadow:0 0 26px -6px #FF8A4C;transition:transform .15s}
.go:hover{transform:translateY(-2px)}.go:active{transform:scale(.97)}
.draft-out .teams{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;align-items:stretch}
.team{padding:14px;border-radius:14px;background:rgba(0,0,0,.3);border:1px solid var(--line-2);animation:warp .45s ease-out}
.team h4{font:800 12px ui-monospace,Menlo,monospace;letter-spacing:.18em;margin-bottom:8px}
.team.a{border-color:rgba(61,169,255,.55)}.team.a h4{color:var(--terran)}
.team.b{border-color:rgba(255,79,126,.55)}.team.b h4{color:var(--zerg-2)}
.team p{margin-top:8px;font-size:12px;color:var(--muted)}
.vsbig{align-self:center;font:900 26px ui-monospace,Menlo,monospace;color:#fff;text-shadow:0 0 18px var(--protoss)}
@keyframes warp{from{opacity:0;transform:scale(.92);filter:blur(6px)}}

/* ── 전투 기록 */
.timeline{position:relative;display:grid;gap:14px;padding-left:22px}
.timeline::before{content:"";position:absolute;left:6px;top:6px;bottom:6px;width:2px;background:linear-gradient(var(--protoss-2),var(--zerg),transparent)}
.log{position:relative;display:grid;grid-template-columns:110px minmax(0,1fr);gap:14px;padding:16px 18px;border-radius:var(--r);background:var(--panel);border:1px solid var(--line)}
.log::before{content:"";position:absolute;left:-21px;top:22px;width:12px;height:12px;border-radius:50%;background:var(--bg);border:2px solid var(--protoss-2);box-shadow:0 0 10px var(--protoss-2)}
.log-d{font:800 14px ui-monospace,Menlo,monospace;color:var(--protoss-2)}
.log h3{font-size:17px;font-weight:850}
.mode{font:600 11.5px ui-monospace,Menlo,monospace;color:var(--muted);margin-left:6px;padding:2px 7px;border:1px solid var(--line-2);border-radius:5px}
.games{list-style:none;display:grid;gap:7px;margin-top:10px}
.games li{display:grid;grid-template-columns:30px minmax(0,1fr) auto minmax(0,1fr);gap:8px;align-items:center;font-size:13px}
.gn{font:700 11px ui-monospace,Menlo,monospace;color:var(--muted)}
.gw{padding:5px 7px;border-radius:9px;background:rgba(61,255,168,.09);box-shadow:0 0 0 1px rgba(61,255,168,.35) inset,0 0 18px -8px var(--win)}
.gl{padding:5px 7px;border-radius:9px;background:rgba(255,255,255,.03);opacity:.55;filter:grayscale(.5)}
.vs{font:900 11px ui-monospace,Menlo,monospace;color:var(--zerg-2)}
.unk{font-size:12px;color:var(--muted)}
.log-n{margin-top:10px;font-size:12.5px;color:var(--ink-2)}

/* ── 점심 */
.lunch{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:14px;align-items:start}
.lrow{display:grid;grid-template-columns:62px minmax(0,1fr) 44px;grid-template-rows:auto auto;column-gap:10px;align-items:center;margin-bottom:10px}
.lname .nc{margin:0}.lpct{font:800 13px ui-monospace,Menlo,monospace;text-align:right}
.lcnt{grid-column:2/4;font-size:11.5px;color:var(--muted)}
.wds{display:flex;align-items:flex-end;gap:12px;height:190px;padding-top:20px}
.wd{flex:1;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:8px}
.wd b{font-size:13px;color:var(--ink-2)}
.wd-bar{position:relative;width:100%;max-width:46px;min-height:4px;border-radius:9px 9px 3px 3px;background:linear-gradient(180deg,var(--protoss-2),var(--star));box-shadow:0 0 18px -4px var(--protoss-2)}
.wd-bar em{position:absolute;top:-19px;left:50%;transform:translateX(-50%);font:700 11.5px ui-monospace,Menlo,monospace;font-style:normal}
.cal-wrap{margin-top:14px}
.cal{border-collapse:separate;border-spacing:3px}
.cal thead th{font:600 10.5px/1.15 ui-monospace,Menlo,monospace;color:var(--muted);min-width:22px}
.cal thead th span{display:block;color:var(--ink-2)}.cal thead th em{font-style:normal}
.cal .rh{text-align:right;padding-right:6px;font-size:12px;color:var(--ink-2);white-space:nowrap}
.cc{width:22px;height:22px;border-radius:5px}
.cc-gym{background:var(--gym);box-shadow:0 0 8px -2px var(--gym)}.cc-run{background:var(--run);box-shadow:0 0 8px -2px var(--run)}
.cc-star{background:var(--star);box-shadow:0 0 10px -1px var(--star)}.cc-skip{background:var(--skip)}.cc-na{background:rgba(255,255,255,.03)}

/* ── 스타크래프트 에셋: 히어로 함대 · 지평선 위 3종족 대치 · 초상화 · 건물 아이콘 */
.hero{padding-bottom:150px}
.fleet{position:absolute;inset:0;z-index:-1;pointer-events:none}
.fly{position:absolute;height:auto}
.fly-1{width:clamp(130px,17vw,230px);top:52%;left:0;filter:drop-shadow(0 0 18px rgba(71,240,255,.45));animation:cruise 46s linear infinite}
.fly-2{width:clamp(64px,7vw,104px);top:9%;right:34%;opacity:.85;filter:drop-shadow(0 0 14px rgba(196,91,255,.55));animation:bob 7s ease-in-out infinite}
.fly-3{width:clamp(80px,9vw,128px);top:44%;right:7%;filter:drop-shadow(0 0 16px rgba(255,210,63,.5));animation:bob 9s ease-in-out -3s infinite}
@keyframes cruise{0%{transform:translateX(-30vw) translateY(0)}50%{transform:translateX(50vw) translateY(-26px)}100%{transform:translateX(130vw) translateY(0)}}
@keyframes bob{50%{transform:translateY(-14px) rotate(-2deg)}}
.lineup{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);z-index:-1;display:flex;align-items:flex-end;gap:clamp(6px,2.4vw,30px);pointer-events:none}
.ln{position:relative;display:flex;align-items:flex-end;animation:idle 2.8s ease-in-out infinite}
.ln::after{content:"";position:absolute;left:12%;right:12%;bottom:-5px;height:9px;border-radius:50%;background:radial-gradient(rgba(0,0,0,.75),transparent 70%);z-index:-1}
.ln-img{height:clamp(40px,6.6vw,88px);width:auto}
.ln-1,.ln-2,.ln-3{--glow:rgba(61,169,255,.5)}.ln-4,.ln-5{--glow:rgba(255,210,63,.5)}.ln-6,.ln-7{--glow:rgba(196,91,255,.55)}
.ln-img{filter:drop-shadow(0 0 12px var(--glow)) drop-shadow(0 6px 6px rgba(0,0,0,.6))}
.ln-3{margin-right:clamp(10px,4vw,56px)}.ln-5{margin-right:clamp(10px,4vw,56px)}
.ln-2{animation-delay:-.6s}.ln-3{animation-delay:-1.2s}.ln-4{animation-delay:-.3s}.ln-5{animation-delay:-1.6s}.ln-6{animation-delay:-.9s}.ln-7{animation-delay:-2s}
@keyframes idle{50%{transform:translateY(-4px)}}
.sec-h{align-items:center}
.sec-ic{height:44px;width:auto;margin-right:-2px;filter:drop-shadow(0 0 10px rgba(71,240,255,.3))}
.tile>*:not(.tile-unit){position:relative;z-index:1}
.tile-unit{position:absolute;right:-4px;bottom:-6px;max-height:104px;max-width:46%;opacity:.5;filter:drop-shadow(0 0 16px var(--tc));transition:opacity .3s,transform .3s}
.tile:hover .tile-unit{opacity:.95;transform:scale(1.07) rotate(-3deg)}
.u-port{position:relative;flex:none;width:76px;height:76px;border-radius:16px;display:grid;place-items:center;
  background:radial-gradient(circle at 50% 62%,color-mix(in srgb,var(--rc) 32%,transparent),rgba(0,0,0,.4) 72%);
  border:1px solid color-mix(in srgb,var(--rc) 50%,transparent);box-shadow:inset 0 0 20px rgba(0,0,0,.65)}
.u-port::before{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  background:repeating-linear-gradient(0deg,rgba(255,255,255,.05) 0 1px,transparent 1px 3px)}
.u-img{max-width:66px;max-height:66px;width:auto;height:auto;filter:drop-shadow(0 0 9px color-mix(in srgb,var(--rc) 70%,transparent));transition:transform .3s}
.unit:hover .u-img{transform:scale(1.14) translateY(-3px)}
.u-emb{position:absolute;right:-7px;bottom:-7px;line-height:0;padding:3px;border-radius:50%;background:var(--bg-2);border:1px solid color-mix(in srgb,var(--rc) 50%,transparent)}
.u-sig{display:block;font-size:11px;color:var(--muted);margin-top:1px}
.nc-img{height:18px;width:auto;vertical-align:-4px;margin-right:4px}
.pick-img{height:24px;width:auto;vertical-align:-7px;margin-right:6px}
.lg-img{height:20px;width:auto;vertical-align:-6px;margin-right:2px}

footer{margin-top:64px;padding-top:20px;border-top:1px solid var(--line);font-size:12px;color:var(--muted);display:grid;gap:6px}

@media (max-width:860px){.syn,.lunch{grid-template-columns:1fr}}
@media (max-width:640px){
  .hero{padding:34px 0 96px;min-height:0}.hero .in,.wrap{padding-left:16px;padding-right:16px}
  .fly-2,.fly-3{display:none}.fly-1{top:30%;opacity:.7}
  .ln-img{height:34px}.sec-ic{height:34px}
  .hud{grid-template-columns:repeat(2,minmax(0,1fr))}
  .units{grid-template-columns:1fr}
  .log{grid-template-columns:1fr;gap:6px}
  .games li{grid-template-columns:24px minmax(0,1fr);row-gap:4px}
  .games .vs{display:none}.games .gl{grid-column:2}
  .draft-out .teams{grid-template-columns:1fr}.vsbig{text-align:center}
}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
"""

JS = r"""
(function(){
  var D = JSON.parse(document.getElementById('es-data').textContent);
  // 다음 경기 D-day
  var dd = document.getElementById('es-dday');
  if (dd && D.next) {
    var t = new Date(D.next + 'T00:00:00+09:00'), now = new Date();
    var kst = new Date(now.getTime() + (now.getTimezoneOffset() + 540) * 60000);
    var today = new Date(kst.getFullYear(), kst.getMonth(), kst.getDate());
    var target = new Date(t.getTime() + (t.getTimezoneOffset() + 540) * 60000);
    var diff = Math.round((new Date(target.getFullYear(), target.getMonth(), target.getDate()) - today) / 86400000);
    if (diff > 0) dd.textContent = 'D-' + diff + ' · ' + dd.textContent;
    else if (diff === 0) dd.textContent = 'D-DAY · ' + dd.textContent;
    else dd.textContent = '지난 일정 · ' + dd.textContent + ' — 기록 갱신 대기';
  }
  // 밸런스 패치 — 보정 승률 합의 차이가 가장 작은 분할 몇 개 중에서 하나를 뽑는다
  var btn = document.getElementById('es-draft'), out = document.getElementById('es-draft-out');
  function chip(n){
    var s = document.createElement('span'); s.className = 'nc nc-' + (D.races[n] || 'none');
    if (D.units[n]) { var i = document.createElement('img'); i.src = D.units[n]; i.alt = ''; i.className = 'nc-img'; s.appendChild(i); }
    s.appendChild(document.createTextNode(n)); return s;
  }
  function team(cls, label, names, power){
    var box = document.createElement('div'); box.className = 'team ' + cls;
    var h = document.createElement('h4'); h.textContent = label; box.appendChild(h);
    names.forEach(function(n){ box.appendChild(chip(n)); });
    var p = document.createElement('p'); p.textContent = '전투력 ' + power.toFixed(2); box.appendChild(p);
    return box;
  }
  if (btn) btn.addEventListener('click', function(){
    var names = Array.prototype.map.call(document.querySelectorAll('.picks input:checked'), function(i){ return i.value; });
    out.textContent = '';
    if (names.length < 2) { var p = document.createElement('p'); p.className = 'legend'; p.textContent = '두 명 이상 골라야 전쟁이 열린다.'; out.appendChild(p); return; }
    var n = names.length, best = [], total = 0;
    names.forEach(function(x){ total += D.ratings[x] || 0.5; });
    for (var mask = 1; mask < (1 << n) - 1; mask++) {
      if (mask & 1) continue;               // A·B 를 뒤집은 같은 분할은 한 번만
      var cnt = 0, a = 0;
      for (var i = 0; i < n; i++) if (mask & (1 << i)) { cnt++; a += D.ratings[names[i]] || 0.5; }
      if (Math.abs(cnt * 2 - n) > 1) continue;  // 인원 차이는 최대 1명
      best.push({mask: mask, gap: Math.abs(total - 2 * a)});
    }
    best.sort(function(x, y){ return x.gap - y.gap; });
    var top = best.filter(function(x){ return x.gap <= best[0].gap + 0.08; }).slice(0, 6);
    var pick = top[Math.floor(Math.random() * top.length)];
    var A = [], B = [], pa = 0, pb = 0, flip = Math.random() < 0.5;  // 첫 사람이 늘 같은 팀에 들어가지 않게
    names.forEach(function(x, i){ var r = D.ratings[x] || 0.5; if (!!(pick.mask & (1 << i)) !== flip) { A.push(x); pa += r; } else { B.push(x); pb += r; } });
    var wrap = document.createElement('div'); wrap.className = 'teams';
    wrap.appendChild(team('a', 'TEAM ALPHA', A, pa));
    var vs = document.createElement('div'); vs.className = 'vsbig'; vs.textContent = 'VS'; wrap.appendChild(vs);
    wrap.appendChild(team('b', 'TEAM OMEGA', B, pb));
    out.appendChild(wrap);
    var note = document.createElement('p'); note.className = 'legend';
    note.textContent = '예상 승률 ALPHA ' + Math.round(pa / (pa + pb) * 100) + '% : ' + Math.round(pb / (pa + pb) * 100) + '% OMEGA — 재미용 추정, 동준 변수는 반영 못 함';
    out.appendChild(note);
  });
})();
"""


# ───────────────────────────────────────────────────────────────────────── 진입점


def load(path: Path = RECORDS) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"{path} 이 없습니다"]
    except json.JSONDecodeError as e:
        return None, [f"JSON 파싱 실패: {e}"]
    errors = validate(data)
    return (data if not errors else None), errors


def write_page(out_dir: Path, path: Path = RECORDS, built: str | None = None) -> list[str]:
    """out_dir/esports.html 을 만든다. 검증에 걸리면 쓰지 않고 사유를 돌려준다 (사이트 빌드는 계속된다)."""
    data, errors = load(path)
    if data is None:
        return errors
    built = built or datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    out_dir.mkdir(parents=True, exist_ok=True)
    page = render(data, compute(data), built)
    (out_dir / "esports.html").write_text(page, encoding="utf-8")
    # 페이지가 실제로 가리키는 그림만 옮긴다 (img src 와 팀 짜기 JSON 둘 다 이 경로 문자열을 쓴다)
    used = set(re.findall(rf"{ASSET_URL}/([a-z-]+)\.png", page))
    if used:
        (out_dir / ASSET_URL).mkdir(exist_ok=True)
        for key in sorted(used):
            shutil.copyfile(ASSET_DIR / f"{key}.png", out_dir / ASSET_URL / f"{key}.png")
    return []


def main() -> None:
    ap = argparse.ArgumentParser(description="e스포츠 방 전적 페이지(esports.html)를 만든다")
    ap.add_argument("--out", default="_site", metavar="DIR")
    ap.add_argument("--records", default=str(RECORDS), metavar="FILE")
    args = ap.parse_args()
    errors = write_page(Path(args.out), Path(args.records))
    if errors:
        raise SystemExit("esports.html 을 만들지 않았다:\n  " + "\n  ".join(errors))
    print(f"{args.out}/esports.html 생성", file=sys.stderr)


if __name__ == "__main__":
    main()
