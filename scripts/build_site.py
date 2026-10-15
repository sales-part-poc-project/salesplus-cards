#!/usr/bin/env python3
"""salesplus-wiki 의 data/profiles·data/projects 로 GitHub Pages 용 정적 사이트를 만든다.

    python3 scripts/build_site.py --out _site --local ../salesplus-wiki   # 토큰 없이 옆 클론으로
    GH_TOKEN=<PAT> python3 scripts/build_site.py --out _site              # Contents API 로 (워크플로)

적재(load_local/load_remote)와 스키마 검증은 cards_data.py (.github-private/update_cards.py 와 같은 규칙) 에 있고,
여기는 HTML 렌더링만 맡는다.

⚠️ 이 저장소는 public 이고 사이트는 GitHub Pages 로 **인터넷에 공개**된다.
그래서 위키 본문·대화 원문은 절대 싣지 않고,
카드 JSON 화이트리스트만 빌드한다 (salesplus-wiki/docs/PRIVACY.md). 검색 엔진 색인은 noindex 로 막는다.

표준 라이브러리만 쓴다. CSS 는 인라인이라 file:// 로 열어도 그대로 보인다.
한 건이 스키마에 어긋나면 그 파일만 건너뛰고 index 하단에 사유를 남긴다.
프로필·프로젝트가 0건이어도 사이트는 만들어진다 (빈 상태 문구만 찍힌다).
index 순서는 활동 월드컵 → 업무 요약 → 파트 일정 → 프로젝트 → 멤버 → 네트워킹비(docs/BUDGET_SCHEMA.md) → 최근 변경.
활동 월드컵(재미)만 인라인 JS 를 쓴다 — 대진이 새로고침마다 바뀌어야 해서. 외부 리소스는 여전히 없다.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cards_data import (  # noqa: E402
    AXES,
    BANNER,
    BUDGET_LOW_REMAINING,
    CHANGELOG_CARD_KO,
    CHANGELOG_KEEP_DAYS,
    CHANGELOG_KIND_KO,
    DEFAULT_PART,
    FUN_KEYS,
    MILESTONE_STATES,
    PROJECT_STATUS_ORDER,
    DAILY_KEEP_DAYS,
    DAILY_MAX_ITEMS,
    SCHEDULE_WINDOW_DAYS,
    Card,
    badge_short,
    budget_rows,
    business_window,
    events_in_window,
    holiday_set,
    load_local,
    load_remote,
    num,
    parse_date,
    project_progress,
    recent_days,
    recent_entries,
    schedule_anchor,
    str_list,
    sub,
    text,
)
from cards_data import KST, GitHub, warn  # noqa: E402

PROJECT_STATUS_CLASS = {"준비": "ps-todo", "진행중": "ps-doing", "보류": "ps-hold", "완료": "ps-done"}
# 일정 종류 일곱 값 (docs/SCHEDULE_SCHEMA.md). 색은 파트 로고 팔레트 안에서만 고른다.
EVENT_KIND_CLASS = {
    "회의": "ek-meet",
    "근태": "ek-att",
    "보고": "ek-report",
    "행사": "ek-event",
    "마감": "ek-due",
    "배포": "ek-deploy",
    "기타": "ek-etc",
}
MILESTONE_KO = {"done": "완료", "doing": "진행 중", "todo": "예정"}
WORK_STATE_HINTS = (
    ("완료", "ws-done"),
    ("진행", "ws-doing"),
    ("검토", "ws-doing"),
    ("대기", "ws-todo"),
    ("보류", "ws-hold"),
    ("미착수", "ws-todo"),
)
PROJECT_BANNER = "과제 페이지는 위키 요약이다. 일정·범위는 데이터 기준일 시점의 상태이며 확정이 아니다."
SIGNAL_ROWS = (
    ("utterances", "발화 수", "건"),
    ("total_chars", "총 글자 수", "자"),
    ("chat_chars", "대화만 글자 수", "자"),
    ("avg_chars", "평균 길이", "자"),
    ("avg_chat_chars", "평균 대화 길이", "자"),
    ("artifacts", "산출물 언급", "건"),
    ("active_hours", "활동 시간대", ""),
    ("confidence", "신뢰도", ""),
)
CHAT_GAP_RATIO = 2.0


# ───────────────────────────────────────────────────────────────────────── 유틸


def esc(v: object) -> str:
    """모든 카드 문자열은 여기를 거친다. 이름에 '<' 가 있어도 깨지지 않게."""
    return html.escape("" if v is None else str(v), quote=True)


def fmt_int(v: object) -> str:
    n = num(v)
    return f"{n:,.1f}" if not float(n).is_integer() else f"{int(n):,}"


def g(d: object, path: str, default: Any = None) -> Any:
    cur = d
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return default if cur is None else cur


# ───────────────────────────────────────────────────────────────────────── 스타일

CSS = """
/* 파트 로고 실측 팔레트. 역할: 업무 성향 = 버밀리온, 프로젝트 = 블루, 재미 코너 = 마젠타·바이올렛.
   *-ink 는 글자용(대비 4.5:1 이상), 나머지는 면·선용. 정본 표는 파트 위키 노하우에 있다. */
:root{
  --ground:#F7F3EF; --surface:#FFFFFF; --surface-2:#FBF8F5;
  --ink:#1C1512; --ink-2:#4E433C; --muted:#756A61;
  --line:#EAE1D9; --line-2:#F2ECE6;
  --brand:#E8431B; --brand-2:#F35119; --brand-deep:#C92E17; --brand-ink:#BE2F14;
  --brand-soft:#FDEBE4; --brand-line:#F3C2B1;
  --amber:#FFB511; --amber-2:#F5A20A; --amber-ink:#3A2200;
  --sky:#2A99E6; --sky-ink:#1268A8; --sky-deep:#0E558A; --sky-soft:#E4F1FC; --sky-line:#B9DAF5;
  --hsp:#F9308E; --hsp-2:#861ECF; --hsp-ink:#B0136F; --hsp-soft:#FDE8F2; --hsp-line:#F6BEDB;
  --vio-ink:#6A1AA8; --vio-soft:#F1E8FB;
  --warn-ink:#7A4B00; --warn-soft:#FFF1CF; --warn-line:#F2D48A;
  --ok-ink:#176E47; --ok-soft:#E2F4EA;
  --hero-ink:#3A1F16; --hero-sub:#5E453B; --hero-base:#FBEBE2; --hero-amber:rgba(255,205,120,.55);
  --hero-sky:#C6E3F8; --hero-chip:rgba(255,255,255,.78); --hero-pill:rgba(190,47,20,.08); --hero-pill-line:rgba(190,47,20,.2);
  --avatar-ink:#FFFFFF;
  --shadow:0 1px 2px rgba(28,21,18,.04),0 10px 28px -14px rgba(201,46,23,.16);
  --shadow-hover:0 2px 4px rgba(28,21,18,.05),0 18px 36px -16px rgba(201,46,23,.32);
  --r-lg:22px; --r-md:16px; --r-sm:11px;
}
@media (prefers-color-scheme:dark){
  :root{
    --ground:#141010; --surface:#1D1715; --surface-2:#241D1A;
    --ink:#F4EDE7; --ink-2:#D2C7BE; --muted:#A3968C;
    --line:#332A26; --line-2:#28211E;
    --brand:#FF6A3D; --brand-2:#FF7E4A; --brand-deep:#E8431B; --brand-ink:#FF9370;
    --brand-soft:#3B1E15; --brand-line:#5F2E20;
    --amber:#FFC23B; --amber-2:#F5A20A; --amber-ink:#3A2200;
    --sky:#4FB0F0; --sky-ink:#8CCBF7; --sky-deep:#4FB0F0; --sky-soft:#13293B; --sky-line:#274C6A;
    --hsp:#FF5CA8; --hsp-2:#B26BF0; --hsp-ink:#FF9BC9; --hsp-soft:#3A1830; --hsp-line:#60294F;
    --vio-ink:#CDB0F8; --vio-soft:#2A1C3D;
    --warn-ink:#FFD077; --warn-soft:#3A2A10; --warn-line:#6B4E1B;
    --ok-ink:#7EE0AC; --ok-soft:#133224;
    --hero-ink:#F7E9E1; --hero-sub:#D9C3B8; --hero-base:#2C1A15; --hero-amber:rgba(255,181,17,.16);
    --hero-sky:#2B4E68; --hero-chip:rgba(20,16,16,.5); --hero-pill:rgba(255,255,255,.08); --hero-pill-line:rgba(255,255,255,.18);
    --avatar-ink:#141010;
    --shadow:0 1px 2px rgba(0,0,0,.3);
    --shadow-hover:0 6px 24px -10px rgba(0,0,0,.6);
  }
}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--ground);color:var(--ink);font-size:15px;line-height:1.62;
  letter-spacing:-.012em;-webkit-font-smoothing:antialiased;
  font-family:"Pretendard Variable","Pretendard",-apple-system,BlinkMacSystemFont,system-ui,
    "Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic","맑은 고딕",sans-serif}
a{color:var(--brand-ink);text-underline-offset:3px}
h1,h2,h3,h4{margin:0;letter-spacing:-.03em}
p{margin:0 0 10px}
ul{margin:0;padding-left:19px}
li{margin:0 0 6px}
code{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:.92em;background:var(--line-2);
  padding:1px 5px;border-radius:5px}
.num{font-variant-numeric:tabular-nums}
:focus-visible{outline:2px solid var(--brand);outline-offset:3px;border-radius:6px}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;animation:none!important}}
.wrap{max-width:1040px;margin:0 auto;padding:0 20px 72px}

/* 히어로 — 로고의 구성(주황 몸통·우상단 앰버·모서리 블루)을 파스텔로 옅게 (2026-09-17: 원색 그대로는 너무 강렬하다는 피드백) */
.hero{position:relative;overflow:hidden;isolation:isolate;color:var(--hero-ink);
  background:var(--hero-base);
  background-image:
    radial-gradient(90% 120% at 100% 0%,var(--hero-amber) 0%,transparent 48%),
    linear-gradient(128deg,color-mix(in srgb,var(--hero-base) 88%,#FFB511) 0%,var(--hero-base) 46%,color-mix(in srgb,var(--hero-base) 90%,#E8431B) 100%);
  padding:46px 0 40px}
.hero::before{content:"";position:absolute;z-index:-1;right:-5%;top:-42%;width:min(32vw,320px);aspect-ratio:1;
  border-radius:50%;background:var(--hero-sky);opacity:.9}
.hero .in{max-width:1040px;margin:0 auto;padding:0 20px;position:relative}
.hero-eyebrow{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.02em;
  background:var(--hero-pill);border:1px solid var(--hero-pill-line);border-radius:999px;
  padding:4px 11px;margin-bottom:14px;backdrop-filter:blur(6px)}
.hero-title{font-size:clamp(30px,6.4vw,54px);font-weight:800;line-height:1.05;letter-spacing:-.04em}
.hero-sub{font-size:14.5px;color:var(--hero-sub);margin-top:14px;max-width:62ch;line-height:1.6}
.hero-chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:20px}
.hero-chips .chip{background:var(--hero-chip);color:var(--brand-deep);border:1px solid var(--hero-pill-line);
  border-radius:999px;padding:5px 12px;font-size:12.5px;font-weight:600;max-width:100%;
  box-shadow:0 1px 0 rgba(0,0,0,.04)}
.hero-chips .chip b{color:#8C2410;font-weight:700;margin-right:6px;font-size:11px}
@media (prefers-color-scheme:dark){
  .hero-chips .chip{color:#fff;box-shadow:none}
  .hero-chips .chip b{color:#FFD3B8}
}
.back{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--hero-ink);
  text-decoration:none;margin-bottom:18px;padding:5px 11px 5px 8px;border-radius:999px;
  background:var(--hero-pill);border:1px solid var(--hero-pill-line);transition:background .15s}
.back:hover{background:var(--hero-chip);color:var(--hero-ink)}

/* 상단 고정 안내 */
.banner{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--surface) 88%,transparent);
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);color:var(--ink-2);
  border-bottom:1px solid var(--line);padding:8px 20px;font-size:12.5px;font-weight:600;
  text-align:center;line-height:1.45}
@supports not (background:color-mix(in srgb,#fff 88%,transparent)){.banner{background:var(--surface)}}
.banner span{font-weight:400;display:block;font-size:11.5px;color:var(--muted);margin-top:1px}
.banner.proj{color:var(--sky-ink)}

/* 섹션 · 카드 */
.sec{margin:40px 0 0}
.seclabel{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:16px;
  padding-bottom:10px;border-bottom:1px solid var(--line)}
.sectag{font-size:11px;font-weight:700;letter-spacing:.01em;padding:3px 9px;border-radius:999px}
.sectitle{font-size:22px;font-weight:800;color:var(--ink)}
.secsub{margin-left:auto;font-size:12.5px;color:var(--muted);font-weight:400}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-md);padding:20px;
  box-shadow:var(--shadow)}
.card+.card{margin-top:14px}
.cardtitle{font-size:12px;font-weight:700;color:var(--muted);letter-spacing:0;margin-bottom:12px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}
.note{font-size:12.5px;color:var(--muted);margin-top:14px;padding-top:12px;border-top:1px solid var(--line-2)}
.empty{color:var(--muted);font-size:13px;margin:0}
.sec-work .sectag{background:var(--brand-soft);color:var(--brand-ink)}
.sec-fun{margin-top:48px}
.sec-fun .sectag{background:var(--hsp-soft);color:var(--hsp-ink)}
.sec-members{margin-top:34px}
.sec-members .sectag{background:var(--surface);color:var(--ink-2);border:1px solid var(--line)}
.sec-proj{margin-top:48px}
.sec-proj .sectag{background:var(--sky-soft);color:var(--sky-ink)}
.sec-chg{margin-top:30px}
.sec-chg .sectag{background:var(--vio-soft);color:var(--vio-ink)}
.sec-sched .sectag{background:var(--warn-soft);color:var(--warn-ink)}
.sec-daily .sectag{background:var(--brand-soft);color:var(--brand-ink)}
.sec-sched{margin-top:30px}

/* 업무 요약 — 날짜 → 방 순으로 묶는다. 사람이 쓴 요약 줄이라 칩 없이 문장만 */
.dday+.dday{margin-top:16px;padding-top:16px;border-top:1px solid var(--line-2)}
.ddate{display:flex;align-items:baseline;gap:8px;font-size:13px;font-weight:800;color:var(--ink);
  font-variant-numeric:tabular-nums;margin-bottom:10px}
.yday{font-size:10.5px;font-weight:800;padding:2px 8px;border-radius:999px;
  background:var(--surface-2);border:1px solid var(--line);color:var(--muted)}
.droom+.droom{margin-top:11px}
.droomname{font-size:12px;font-weight:800;color:var(--brand-ink);margin-bottom:6px}
/* 끝난 일정 — 취소선으로 눈에 띄게 (위키에서 ~~취소선~~·✅ 로 표시한 행) */
.slist li.done .evlabel{text-decoration:line-through;text-decoration-thickness:2px;
  text-decoration-color:var(--ok-ink);color:var(--muted);font-weight:600}
.slist li.done .evtime,.slist li.done .evproj{opacity:.6}
.donechip{font-size:10.5px;font-weight:800;padding:1px 8px;border-radius:999px;
  background:var(--ok-soft);color:var(--ok-ink);white-space:nowrap}
.dlist{list-style:none;padding:0;margin:0}
.dlist li{position:relative;padding-left:16px;margin:0 0 8px;font-size:13.5px;line-height:1.55;color:var(--ink);
  word-break:keep-all;overflow-wrap:anywhere}
.dlist li::before{content:"•";position:absolute;left:2px;color:var(--brand)}
.dlist li:last-child{margin-bottom:0}

/* 변경사항 · 파트 일정 — 좁은 화면에서는 행이 카드처럼 접힌다 (가로 스크롤 없음) */
.dchips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.dchip{font-size:11.5px;background:var(--surface-2);border:1px solid var(--line);border-radius:999px;
  padding:3px 9px;color:var(--muted)}
.chcard{background:var(--sky-soft);border-color:var(--sky-line)}
.chday+.chday{margin-top:14px;padding-top:14px;border-top:1px solid var(--sky-line)}
.chdate{display:flex;align-items:baseline;gap:8px;font-size:12.5px;font-weight:800;color:var(--sky-ink);
  margin-bottom:9px;font-variant-numeric:tabular-nums}
.chlist{list-style:none;padding:0;margin:0}
.chlist li{display:flex;flex-wrap:wrap;align-items:baseline;gap:9px;margin:0 0 9px;font-size:13.5px;line-height:1.55}
.chlist li:last-child{margin-bottom:0}
.ck{font-size:11px;font-weight:800;padding:2px 9px;border-radius:999px;white-space:nowrap;
  background:transparent;border:1px solid var(--sky-line);color:var(--sky-ink)}
.ck-profile{border-color:var(--vio-ink);color:var(--vio-ink)}
.ck-project{background:var(--sky);border-color:var(--sky);color:#fff}
.ck-sched{border-style:dashed}
.ck-site{border-color:var(--line-2);color:var(--muted)}
.chkind{font-size:10.5px;font-weight:800;padding:1px 7px;border-radius:999px}
.chkind-added{background:var(--ok-soft);color:var(--ok-ink)}
.chkind-removed{background:var(--warn-soft);color:var(--warn-ink)}
.chtarget{font-weight:800;color:var(--ink)}
.chtarget a{color:var(--sky-ink);font-weight:800;text-decoration:none;border-bottom:1px solid var(--sky-line)}
.chtarget a:hover{border-bottom-color:var(--sky)}
.chsum{color:var(--ink-2);flex:1 1 240px;min-width:0;word-break:keep-all;overflow-wrap:anywhere}
.chseg{display:inline-block;max-width:100%;vertical-align:top}
.chseg:not(:last-child)::after{content:" ·";color:var(--muted)}
.chseg+.chseg{margin-left:5px}
.today{font-size:10.5px;font-weight:800;padding:2px 8px;border-radius:999px;
  background:var(--brand);color:#fff;letter-spacing:.01em}

/* 파트 일정 — 날짜별로 묶는다. 표가 아니라 목록이라 좁은 화면에서도 접힐 게 없다 */
.sday+.sday{margin-top:15px;padding-top:15px;border-top:1px solid var(--line-2)}
.sdate{display:flex;align-items:baseline;gap:8px;font-size:13px;font-weight:800;color:var(--ink);
  font-variant-numeric:tabular-nums;margin-bottom:9px}
.slist{list-style:none;padding:0;margin:0}
.slist li{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;margin:0 0 10px;
  font-size:13.5px;line-height:1.55}
.slist li:last-child{margin-bottom:0}
.slist li.rec{color:var(--ink-2)}
.evtime{font-variant-numeric:tabular-nums;font-weight:800;color:var(--brand-ink);font-size:12.5px;
  flex:0 0 auto;min-width:46px}
.ek{font-size:11px;font-weight:800;padding:2px 9px;border-radius:999px;white-space:nowrap;
  border:1px solid transparent;flex:0 0 auto}
.ek-meet{background:var(--sky-soft);border-color:var(--sky-line);color:var(--sky-ink)}
.ek-att{background:var(--vio-soft);color:var(--vio-ink)}
.ek-report{background:var(--hsp-soft);border-color:var(--hsp-line);color:var(--hsp-ink)}
.ek-event{background:var(--ok-soft);color:var(--ok-ink)}
.ek-due{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn-ink)}
.ek-deploy{background:var(--brand);border-color:var(--brand);color:#fff}
.ek-etc{background:var(--surface-2);border-color:var(--line);color:var(--muted)}
.evlabel{font-weight:700;color:var(--ink);flex:1 1 260px;min-width:0}
.slist li.rec .evlabel{font-weight:600}
.evproj{font-size:11.5px;background:var(--sky-soft);color:var(--sky-ink);border:1px solid var(--sky-line);
  border-radius:999px;padding:1px 9px;white-space:nowrap}
.evmem{font-size:11.5px;background:var(--surface-2);border:1px solid var(--line);border-radius:999px;
  padding:1px 9px;color:var(--ink-2);text-decoration:none;white-space:nowrap}
a.evmem:hover{border-color:var(--brand-line);color:var(--brand-ink)}
.evrepeat{font-size:10.5px;font-weight:700;padding:1px 8px;border-radius:999px;
  border:1px dashed var(--line);color:var(--muted)}
.evspan,.evnote{font-size:12.5px;color:var(--muted);font-variant-numeric:tabular-nums}
.on{font-size:10.5px;font-weight:800;color:var(--brand-ink);white-space:nowrap}
@media (max-width:560px){
  .evlabel{flex:1 1 100%}
  .evtime{min-width:0}
              line-height:1.7}
}

/* 재미 코너 — HSP 로고 계열 */
.funwrap{position:relative;background:var(--surface-2);border:1px solid var(--hsp-line);border-radius:var(--r-lg);
  padding:18px;overflow:hidden}
.funwrap::before{content:"";position:absolute;inset:0 0 auto 0;height:4px;
  background:linear-gradient(90deg,var(--hsp),var(--hsp-2))}
.funwrap .card{background:var(--surface);border-color:var(--line)}
.funhead{font-size:12.5px;color:var(--ink-2);margin:2px 0 16px;line-height:1.55}
.funhead b{color:var(--hsp-ink)}
.st{display:inline-block;font-size:10.5px;font-weight:700;padding:3px 9px;border-radius:999px;white-space:nowrap}
.st-obs{background:var(--brand-soft);color:var(--brand-ink)}
.st-weak{background:var(--line-2);color:var(--muted);border:1px solid var(--line)}
.st-none{background:var(--warn-soft);color:var(--warn-ink)}

/* 활동 월드컵 — 맨 위 재미 섹션 (2026-10-15). 대진표는 아래(참가자 타일)에서 위(트로피)로 모인다.
   좁은 폭에서는 대진표 칸만 안에서 가로로 밀린다 — 페이지는 넘치지 않는다 */
.sec-battle{margin-top:30px}
.sec-battle .sectag{background:var(--hsp-soft);color:var(--hsp-ink)}
.btwrap{position:relative;background:var(--surface);border:1px solid var(--hsp-line);border-radius:var(--r-lg);
  padding:20px 18px 16px;box-shadow:var(--shadow);overflow:hidden}
.btwrap::before{content:"";position:absolute;inset:0 0 auto 0;height:4px;
  background:linear-gradient(90deg,var(--amber),var(--brand),var(--hsp))}
.bt-champ{display:flex;flex-direction:column;align-items:center;text-align:center;gap:4px;min-height:132px}
.bt-trophy{font-size:46px;line-height:1;filter:drop-shadow(0 6px 12px rgba(245,162,10,.35))}
.bt-kicker{font-size:11px;font-weight:800;background:var(--amber);color:var(--amber-ink);
  border-radius:999px;padding:2px 11px}
.bt-who{display:flex;align-items:center;gap:8px;max-width:100%;opacity:0;transform:translateY(8px) scale(.96);
  transition:opacity .45s ease,transform .45s cubic-bezier(.3,1.6,.5,1)}
.bt-champ.bt-on .bt-who{opacity:1;transform:none}
.bt-who .ch{flex:0 0 auto;width:44px;height:52px}
.bt-who>div{min-width:0;word-break:keep-all;overflow-wrap:anywhere}
.bt-cname{font-size:clamp(19px,4vw,23px);font-weight:800;letter-spacing:-.03em}
.bt-cname a{color:var(--ink);text-decoration:none;border-bottom:3px solid var(--amber)}
.bt-cname a:hover{color:var(--brand-ink)}
.bt-cline{font-size:12.5px;color:var(--ink-2)}
.bt-wait{font-size:12.5px;color:var(--muted)}
.bt-champ.bt-on .bt-wait{display:none}
.bt-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;scroll-snap-type:x proximity;
  margin:6px -6px 0;padding:0 6px 6px}
.bt-board{--n:4;width:100%;max-width:calc(var(--n) * 124px + 80px);min-width:calc(var(--n) * 78px + 64px);margin:0 auto}
.bt-final{display:block;width:fit-content;margin:0 auto;font-size:12.5px;font-weight:800;color:var(--ink);
  background:var(--surface-2);border:2px solid var(--amber);border-radius:var(--r-sm);padding:3px 16px}
.bt-region{position:relative}
.bt-region svg{display:block;width:100%;height:auto;overflow:visible}
.bt-ghost{fill:none;stroke:var(--line);stroke-width:4;stroke-linejoin:round}
.bt-seg{fill:none;stroke-width:6;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1;stroke-dashoffset:1;
  opacity:0;transition:stroke-dashoffset .55s ease,opacity .1s}
.bt-seg.win{stroke:var(--brand)}
.bt-seg.lose{stroke:var(--muted);stroke-opacity:.38}
.bt-seg.top{stroke:var(--amber)}
.bt-seg.bt-on{opacity:1;stroke-dashoffset:0}
.bt-lvl{position:absolute;transform:translate(-50%,-50%);font-size:10.5px;font-weight:800;white-space:nowrap;
  color:var(--hsp-ink);background:var(--hsp-soft);border:1px solid var(--hsp-line);border-radius:999px;padding:0 7px}
.bt-mini{position:absolute;width:30px;height:30px;border-radius:50%;background:var(--surface);
  box-shadow:0 0 0 2px var(--brand),var(--shadow);transform:translate(-50%,-50%) scale(.2);opacity:0;
  transition:opacity .3s ease,transform .4s cubic-bezier(.3,1.6,.5,1)}
.bt-mini .ch{display:block;width:30px;height:35px;margin-top:-7px}
.bt-mini.bt-on{opacity:1;transform:translate(-50%,-50%) scale(1)}
.bt-mini.out{opacity:.4;filter:grayscale(1);box-shadow:0 0 0 2px var(--line)}
.bt-tiles{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr))}
.bt-tile{position:relative;margin:0 3px;border-radius:var(--r-sm);background:var(--surface-2);overflow:hidden;
  border:1px solid var(--line);scroll-snap-align:center;outline-offset:2px}
.bt-tile.champ{border-color:var(--amber);box-shadow:0 0 0 2px var(--amber),0 10px 22px -12px rgba(245,162,10,.6)}
.bt-in{padding:9px 4px 8px;text-align:center;transition:opacity .35s,filter .35s}
.bt-in .ch{width:46px;height:54px;display:block;margin:0 auto}
.bt-name{font-size:13.5px;font-weight:800;line-height:1.2;margin-top:3px;word-break:keep-all}
.bt-short{display:none}
.bt-job{font-size:10px;font-weight:700;color:var(--hsp-ink);line-height:1.25;margin-top:2px;word-break:keep-all}
.bt-rank{font-size:10.5px;color:var(--muted);margin-top:2px;font-variant-numeric:tabular-nums}
.bt-icons{font-size:10px;color:var(--ink-2);margin-top:4px;line-height:1.4;font-variant-numeric:tabular-nums}
.bt-icons span{display:inline-block;white-space:nowrap;margin:0 2px}
.bt-tile.out .bt-in{opacity:.55;filter:grayscale(.5)}
.bt-stamp{position:absolute;left:50%;top:62%;font-size:14px;font-weight:900;letter-spacing:.06em;color:var(--brand-deep);
  border:2.5px solid var(--brand-deep);border-radius:7px;padding:0 6px;pointer-events:none;white-space:nowrap;
  background:color-mix(in srgb,var(--surface) 70%,transparent);opacity:0;
  transform:translate(-50%,-50%) rotate(-14deg)}
.bt-tile.out .bt-stamp{opacity:1;animation:bt-thump .32s cubic-bezier(.5,0,.75,0) both}
@keyframes bt-thump{from{opacity:0;transform:translate(-50%,-50%) rotate(-14deg) scale(2.4)}
  to{opacity:1;transform:translate(-50%,-50%) rotate(-14deg) scale(1)}}
@keyframes bt-thump-s{from{opacity:0;transform:translateX(-50%) rotate(-10deg) scale(2.2)}
  to{opacity:1;transform:translateX(-50%) rotate(-10deg) scale(1)}}
/* 타일을 누르거나 올리면 RPG 스탯 면으로 뒤집힌다 (업무 성향 5축을 그대로 옮긴 것) */
.bt-stats{position:absolute;inset:0;display:none;flex-direction:column;justify-content:center;gap:1px;
  padding:6px 5px;border-radius:inherit;background:var(--surface);font-size:10px;line-height:1.35;text-align:left;z-index:1}
.bt-tile:hover .bt-stats,.bt-tile:focus .bt-stats{display:flex}
.bt-stats b{font-size:10.5px;color:var(--hsp-ink);word-break:keep-all;margin-bottom:2px}
.bt-stats span{display:flex;justify-content:space-between;gap:4px;color:var(--ink-2);white-space:nowrap}
.bt-stats i{font-style:normal;color:var(--brand-ink);letter-spacing:-.1em}
.bt-foot{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin-top:12px}
.bt-again{font:inherit;font-size:13px;font-weight:800;color:var(--avatar-ink);background:var(--brand-ink);border:0;
  border-radius:999px;padding:7px 16px;cursor:pointer;box-shadow:0 6px 16px -8px var(--brand)}
.bt-again:hover{background:var(--brand-deep)}
.bt-hint{font-size:11.5px;color:var(--muted)}
details.bt-log{margin-top:12px;border-top:1px solid var(--line-2);padding-top:6px}
details.bt-log>summary{cursor:pointer;font-size:12.5px;font-weight:700;color:var(--ink-2);padding:4px 0}
.bt-rd+.bt-rd{margin-top:8px}
.bt-rdname{font-size:11px;font-weight:800;color:var(--hsp-ink);margin:4px 0}
.bt-mlist{list-style:none;padding:0;margin:0}
.bt-mlist li{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 7px;font-size:13px;margin:0 0 6px;
  font-variant-numeric:tabular-nums}
.bt-mlist .w{font-weight:800;color:var(--ink)}
.bt-mlist .l{color:var(--muted)}
.bt-mlist .vs{color:var(--brand)}
.bt-mlist small{flex-basis:100%;font-size:11px;color:var(--muted)}
/* 캐릭터 몸 색 — 활동 월드컵 전용 중채도 팔레트 10색 (로고 팔레트 밖, 캐릭터 몸에만 쓴다).
   눈·입은 어두운 선이라 라이트·다크 어느 바탕에서도 같은 색으로 읽힌다. 번호는 battle_colors 가 겹치지 않게 배정한다 */
.ch .bt-bd{fill:#FF8A65;stroke:rgba(28,21,18,.2);stroke-width:1}
.bt-c1 .bt-bd{fill:#FFC94D}
.bt-c2 .bt-bd{fill:#6EC1F0}
.bt-c3 .bt-bd{fill:#F58BBF}
.bt-c4 .bt-bd{fill:#A99AF0}
.bt-c5 .bt-bd{fill:#6FD3A0}
.bt-c6 .bt-bd{fill:#FFA94D}
.bt-c7 .bt-bd{fill:#8AA4FF}
.bt-c8 .bt-bd{fill:#E3C39A}
.bt-c9 .bt-bd{fill:#C5E066}
.ch .bt-mo{fill:none;stroke:#1C1512;stroke-width:2.2;stroke-linecap:round}
.ch .bt-ck{fill:#F9308E;opacity:.35}
@media (max-width:560px){
  .bt-board{min-width:calc(var(--n) * 56px + 52px)}
  .bt-in .ch{width:36px;height:42px}
  .bt-job,.bt-icons,.bt-full{display:none}
  .bt-short{display:inline}
  .bt-name{font-size:12.5px}
  .bt-tile{margin:0 2px}
  .bt-mini{width:24px;height:24px}
  .bt-mini .ch{width:24px;height:28px;margin-top:-6px}
  /* 도장은 얼굴 아래(이름·순위 줄)에 작게 — 캐릭터가 가려지지 않게 */
  .bt-stamp{top:auto;bottom:6px;font-size:10.5px;border-width:2px;padding:0 4px;
    transform:translateX(-50%) rotate(-10deg)}
  .bt-tile.out .bt-stamp{animation-name:bt-thump-s}
}

/* 업무 성향 5축 */
.axes{display:flex;flex-direction:column;gap:16px}
.axis-head{display:flex;align-items:baseline;gap:8px;font-size:13px;margin-bottom:6px}
.axis-head b{font-weight:800;font-size:14px}
.axis-score{margin-left:auto;font-variant-numeric:tabular-nums;font-weight:800;color:var(--brand-ink)}
.axis-score i{font-style:normal;font-weight:400;color:var(--muted);font-size:11.5px}
.axis-track{position:relative;height:10px;border-radius:999px;background:var(--line-2);overflow:hidden}
.axis-fill{display:block;height:100%;border-radius:999px;min-width:6px;
  background:linear-gradient(90deg,var(--brand-2),var(--brand-deep))}
.axis-track::after{content:"";position:absolute;inset:0;pointer-events:none;
  background:repeating-linear-gradient(90deg,transparent 0 calc(20% - 2px),var(--surface) calc(20% - 2px) 20%)}
.axis-pole{display:flex;justify-content:space-between;gap:10px;font-size:11.5px;color:var(--muted);margin-top:6px}
.axis-pole span:last-child{text-align:right}

/* 말 걸기 */
.talk{background:var(--surface);border:1px solid var(--hsp-line);border-radius:var(--r-md);padding:20px;margin-bottom:14px}
.talk-h{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:4px}
.talk-h h3{font-size:clamp(17px,3.6vw,21px);font-weight:800;color:var(--vio-ink)}
.talk-lead{font-size:12.5px;color:var(--muted);margin:0 0 15px}
.talk-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}
.talk-box{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--r-sm);padding:13px 15px}
.talk-box.good{border-color:var(--ok-soft);background:var(--ok-soft)}
.talk-box.avoid{border-color:var(--warn-line);background:var(--warn-soft)}
.talk-k{font-size:11.5px;font-weight:700;margin-bottom:8px}
.talk-box.good .talk-k{color:var(--ok-ink)}
.talk-box.avoid .talk-k{color:var(--warn-ink)}
.talk-box ul{padding-left:17px;font-size:13.5px}
.talk-meta{display:grid;grid-template-columns:1fr 1fr;gap:13px;margin-top:13px}
.talk-time{background:var(--vio-soft);border-radius:var(--r-sm);padding:13px 15px}
.talk-time .talk-k{color:var(--vio-ink)}
.talk-time .v{font-size:clamp(18px,4vw,24px);font-weight:800;color:var(--vio-ink);
  font-variant-numeric:tabular-nums;line-height:1.2}
.talk-ex{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--r-sm);padding:13px 15px}
.talk-ex .talk-k{color:var(--muted)}
.talk-ex .q{font-size:13.5px;color:var(--ink);line-height:1.6;margin:0}
.talk-ex .q::before{content:"“"}
.talk-ex .q::after{content:"”"}
.fun3{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}
.funcard{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-md);padding:18px}
.funk{font-size:12px;color:var(--muted);font-weight:700}
.funval{font-size:clamp(24px,5vw,30px);font-weight:800;margin:4px 0 10px;line-height:1.15;color:var(--hsp-ink);
  background:linear-gradient(90deg,var(--hsp),var(--hsp-2));-webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent;width:fit-content;max-width:100%}
.funbasis{font-size:12.5px;color:var(--ink-2);margin-top:10px;line-height:1.55}

/* 멤버 카드 */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,238px),1fr));gap:14px}
.mcard{display:block;position:relative;background:var(--surface);border:1px solid var(--line);border-radius:var(--r-md);
  padding:18px;text-decoration:none;color:inherit;box-shadow:var(--shadow);
  transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease}
.mcard:hover{transform:translateY(-2px);box-shadow:var(--shadow-hover);border-color:var(--brand-line)}
.mcard.low{border-style:dashed}
.mhead{display:flex;align-items:center;gap:12px;min-width:0}
/* 아바타는 글자용 토큰(*-ink)만 배경으로 쓴다 — 글자색 --avatar-ink 와 4.5:1 이상. 앰버만 어두운 글자 */
.avatar{flex:0 0 auto;width:46px;height:46px;border-radius:14px;display:grid;place-items:center;
  color:var(--avatar-ink);font-weight:800;font-size:18px;letter-spacing:0;
  background:linear-gradient(135deg,var(--brand-ink),var(--brand-deep));box-shadow:inset 0 -2px 0 rgba(0,0,0,.12)}
.avatar.av-1{background:linear-gradient(135deg,var(--amber),var(--amber-2));color:var(--amber-ink)}
.avatar.av-2{background:linear-gradient(135deg,var(--sky-ink),var(--sky-deep))}
.avatar.av-3{background:linear-gradient(135deg,var(--hsp-ink),var(--vio-ink))}
.mname{font-size:19px;font-weight:800;line-height:1.2;letter-spacing:-.03em}
.mrole{font-size:12.5px;color:var(--muted);margin-top:2px}
.mnick{font-size:13.5px;color:var(--brand-ink);margin-top:12px;font-weight:700;line-height:1.45}
.mchips{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.mchip{font-size:11.5px;background:var(--surface-2);border:1px solid var(--line);border-radius:999px;
  padding:3px 9px;color:var(--ink-2)}
.mchip b{font-weight:800;color:var(--hsp-ink)}
.mchip.mbti{background:var(--hsp-soft);border-color:var(--hsp-soft);color:var(--hsp-ink);font-weight:800}
.mchip.age{border-color:var(--hsp-line);color:var(--hsp-ink);font-weight:700}
.mchip.age i{font-style:normal;font-weight:600;font-size:10px;opacity:.75;margin-right:5px}
.mchip.badge{border-style:dashed;border-color:var(--hsp-line);color:var(--hsp-ink)}
.mchip.react{font-size:14px;padding:1px 10px;line-height:1.5}
.lowbadge{display:flex;align-items:center;gap:6px;margin-top:12px;background:var(--warn-soft);
  color:var(--warn-ink);border:1px solid var(--warn-line);border-radius:var(--r-sm);padding:7px 10px;
  font-size:11.5px;font-weight:700;line-height:1.35}

/* 프로젝트 */
.pcards{display:grid;grid-template-columns:1fr;gap:18px}
.pcard{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:24px;background:var(--surface);
  border:1px solid var(--line);border-radius:var(--r-lg);padding:26px 28px;text-decoration:none;color:inherit;
  box-shadow:var(--shadow);position:relative;overflow:hidden;
  transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease}
.pcard::before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;
  background:linear-gradient(180deg,var(--sky),var(--sky-ink))}
.pcard:hover{transform:translateY(-2px);box-shadow:var(--shadow-hover);border-color:var(--sky-line)}
.pmain{display:flex;flex-direction:column;min-width:0}
.paside{min-width:0;border-left:1px solid var(--line-2);padding-left:24px}
.phead{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.pphase{font-size:12.5px;color:var(--muted)}
.pname{font-size:clamp(22px,3.4vw,28px);font-weight:800;line-height:1.2;color:var(--ink);letter-spacing:-.035em}
.pcode{font-size:11.5px;color:var(--muted);font-weight:700;margin-left:6px}
.ptag{font-size:14px;color:var(--sky-ink);font-weight:700;margin-top:6px}
.psum{font-size:14px;color:var(--ink-2);margin-top:10px;line-height:1.65}
.pnext{font-size:13.5px;color:var(--ink);margin-top:auto;padding-top:16px;font-weight:700;line-height:1.45}
.pk{font-size:12px;font-weight:700;color:var(--muted);margin:0 0 8px}
.paside .ms{margin-top:0}
.paside .ms li{font-size:13px;padding-bottom:9px}
.paside .mems{margin-top:14px}
.paside .mem{padding:6px 10px;font-size:12.5px}
.ptarget{font-size:12.5px;color:var(--muted);margin-top:12px}
.ptarget b{color:var(--ink-2)}
.pnext i{font-style:normal;font-weight:700;font-size:11px;color:var(--sky-ink);margin-right:6px;
  background:var(--sky-soft);padding:2px 7px;border-radius:999px}
.pcard .mchips{margin-top:10px}
.mchip.proj{background:var(--sky-soft);border-color:var(--sky-soft);color:var(--sky-ink);font-weight:700}
.ps{display:inline-block;font-size:10.5px;font-weight:800;padding:3px 9px;border-radius:999px;white-space:nowrap}
.ps-doing{background:var(--sky-soft);color:var(--sky-ink)}
.ps-todo{background:var(--line-2);color:var(--muted);border:1px solid var(--line)}
.ps-hold{background:var(--warn-soft);color:var(--warn-ink)}
.ps-done{background:var(--ok-soft);color:var(--ok-ink)}
.prog{position:relative;height:8px;border-radius:999px;background:var(--line-2);overflow:hidden;margin-top:14px}
.prog span{display:block;height:100%;border-radius:999px;min-width:4px;
  background:linear-gradient(90deg,var(--sky),var(--sky-ink))}
.prog.zero span{display:none}
.progk{display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 10px;font-size:11.5px;color:var(--muted);
  margin-top:6px;font-variant-numeric:tabular-nums}
.progk span:first-child{white-space:nowrap}
.progk b{color:var(--sky-ink);font-weight:800}
.ms{list-style:none;padding:0;margin:14px 0 0;position:relative}
.ms::before{content:"";position:absolute;left:7px;top:6px;bottom:6px;width:2px;background:var(--line)}
.ms li{position:relative;padding:0 0 12px 26px;margin:0;font-size:13.5px;line-height:1.45}
.ms li:last-child{padding-bottom:0}
.ms li::before{content:"";position:absolute;left:1px;top:4px;width:14px;height:14px;border-radius:50%;
  background:var(--surface);border:2px solid var(--line)}
.ms li.done::before{background:var(--sky);border-color:var(--sky)}
.ms li.done::after{content:"";position:absolute;left:5px;top:7px;width:4px;height:7px;
  border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}
.ms li.doing::before{border-color:var(--brand);border-width:3px;background:var(--surface);
  box-shadow:0 0 0 3px var(--brand-soft)}
.ms li.doing{font-weight:800;color:var(--ink)}
.ms li.todo{color:var(--muted)}
.ms .d{display:inline-block;min-width:88px;font-variant-numeric:tabular-nums;color:var(--muted);
  font-size:12px;font-weight:400;margin-right:6px}
.ms .k{font-size:10.5px;font-weight:700;margin-left:8px;color:var(--muted)}
.ms li.doing .k{color:var(--brand-ink)}
.ws{display:inline-block;font-size:10.5px;font-weight:700;padding:2px 8px;border-radius:999px;
  white-space:nowrap;background:var(--line-2);color:var(--ink-2);border:1px solid var(--line)}
.ws-done{background:var(--ok-soft);color:var(--ok-ink);border-color:var(--ok-soft)}
.ws-doing{background:var(--sky-soft);color:var(--sky-ink);border-color:var(--sky-soft)}
.ws-todo{background:var(--line-2);color:var(--muted)}
.ws-hold{background:var(--warn-soft);color:var(--warn-ink);border-color:var(--warn-line)}
td.area{font-weight:800;white-space:nowrap;color:var(--ink)}
.mems{display:flex;flex-wrap:wrap;gap:8px}
.mem{display:inline-flex;align-items:baseline;gap:7px;background:var(--surface-2);border:1px solid var(--line);
  border-radius:var(--r-sm);padding:8px 12px;text-decoration:none;color:var(--ink);font-size:13.5px;font-weight:800;
  transition:border-color .15s,color .15s}
a.mem:hover{border-color:var(--sky);color:var(--sky-ink)}
.mem i{font-style:normal;font-weight:400;font-size:12px;color:var(--muted)}
.mem.noprofile{opacity:.7}
.recent td.d{white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--muted);width:1%}

/* 네트워킹비 — 멤버 바로 아래. 사람별 사용/할당 막대 (2026-09-22). 글자 칸은 고정폭이 아니라 내용 길이(auto)로 —
   고정폭이면 "사용 / 할당 · 잔액 + 표식" 이 카드 밖으로 넘친다 (위키 views/파트예산 9/22 사례) */
.sec-budget{margin-top:34px}
.sec-budget .sectag{background:var(--warn-soft);color:var(--warn-ink)}
.btiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:16px}
.btile{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--r-sm);padding:12px 14px}
.btile .k{font-size:11.5px;font-weight:700;color:var(--muted)}
.btile .v{font-size:clamp(20px,4vw,24px);font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums;
  color:var(--ink);line-height:1.25;margin-top:2px}
.btile .v i{font-style:normal;font-size:12px;font-weight:600;color:var(--muted);margin-left:2px}
.btile.hi .v{color:var(--brand-ink)}
.bgrid{display:grid;grid-template-columns:auto minmax(120px,1fr) auto;gap:10px 12px;align-items:center}
.bname{font-weight:800;font-size:14px;white-space:nowrap}
.bname a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--line)}
.bname a:hover{color:var(--brand-ink);border-bottom-color:var(--brand-line)}
.btrack{position:relative;height:16px;border-radius:5px;background:var(--line-2);overflow:hidden;min-width:0}
.bfill{display:block;height:100%;border-radius:0 5px 5px 0;min-width:3px;
  background:linear-gradient(90deg,var(--brand-2),var(--brand-deep))}
.bfill.spent{background:var(--muted)}
.bamt{font-size:12.5px;color:var(--muted);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.bamt b{color:var(--ink);font-weight:800}
.bflag{display:inline-block;font-size:10.5px;font-weight:800;padding:1px 7px;border-radius:999px;margin-left:6px;
  vertical-align:1px}
.bflag.low{background:var(--warn-soft);color:var(--warn-ink)}
.bflag.zero{background:var(--line-2);color:var(--muted);border:1px solid var(--line)}
@media (max-width:640px){
  .bgrid{grid-template-columns:auto 1fr}
  .bamt{grid-column:2;text-align:left;margin-top:-4px;white-space:normal}
}

/* 관측 신호 · 표 · 경고 · 푸터 */
details.sig{margin-top:36px;background:var(--surface);border:1px solid var(--line);border-radius:var(--r-md);padding:0 20px}
details.sig>summary{cursor:pointer;padding:15px 0;font-size:13.5px;font-weight:700;color:var(--ink-2);list-style:none}
details.sig>summary::-webkit-details-marker{display:none}
details.sig>summary::before{content:"▸ ";color:var(--muted)}
details.sig[open]>summary::before{content:"▾ "}
details.sig>div{padding-bottom:18px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line-2);vertical-align:top}
th{color:var(--muted);font-weight:700;font-size:12px;white-space:nowrap}
td.n{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.tscroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.gap{display:inline-block;margin-left:8px;font-size:10.5px;font-weight:700;padding:2px 8px;
  border-radius:999px;background:var(--brand-soft);color:var(--brand-ink);white-space:nowrap}
.warnbox{background:var(--warn-soft);border:1px solid var(--warn-line);border-radius:var(--r-md);
  padding:16px 18px;color:var(--warn-ink);font-size:13px}
.warnbox h3{font-size:14px;margin-bottom:8px}
.warnbox p{margin:0}
.warnbox li{color:var(--warn-ink)}
.lowwarn{margin-top:28px}
.skips{margin-top:36px}
footer{margin-top:52px;padding-top:20px;border-top:1px solid var(--line);font-size:12px;color:var(--muted);line-height:1.65}
@media (max-width:720px){
  .grid2,.talk-grid,.talk-meta{grid-template-columns:1fr}
  .fun3{grid-template-columns:1fr}
  .pcard{grid-template-columns:1fr;gap:16px;padding:20px 20px 20px 24px}
  .paside{border-left:0;padding-left:0;border-top:1px solid var(--line-2);padding-top:16px}
  .hero{padding:34px 0 30px}
}
@media (max-width:430px){
  body{font-size:14.5px}
  .wrap{padding:0 16px 56px}
  .hero .in{padding:0 16px}
  .banner{padding:8px 16px;font-size:11.5px}
  .funwrap{padding:14px}
  .card,.talk,.mcard{padding:15px}
  .pcard{padding:16px 16px 16px 20px}
  .secsub{margin-left:0;width:100%}
  .ms .d{display:block;min-width:0;margin:0}
}
"""


# ───────────────────────────────────────────────────────────────────────── 조각


def html_doc(title: str, body: str) -> str:
    return (
        '<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="robots" content="noindex,nofollow">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>{esc(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def banner_html() -> str:
    return (
        f'<div class="banner">{esc(BANNER)}'
        "<span>업무 성향만 관측된 행동에 근거한다. 인사 평가나 줄세우기에 쓰지 않는다.</span></div>"
    )


def project_banner_html() -> str:
    return (
        f'<div class="banner proj">{esc(PROJECT_BANNER)}'
        "<span>상세 근거·검토 항목은 위키 본문에 있고 이 사이트에는 싣지 않는다.</span></div>"
    )


def sec(cls: str, tag: str, title: str, body: str, subtitle: str = "") -> str:
    subhtml = f'<span class="secsub">{esc(subtitle)}</span>' if subtitle else ""
    return (
        f'<section class="sec {cls}"><div class="seclabel"><span class="sectag">{esc(tag)}</span>'
        f'<h2 class="sectitle">{esc(title)}</h2>{subhtml}</div>{body}</section>'
    )


def card(title: str, body: str) -> str:
    head = f'<div class="cardtitle">{esc(title)}</div>' if title else ""
    return f'<div class="card">{head}{body}</div>'


def ul(items: object, empty: str = "적어 두지 않았습니다") -> str:
    xs = str_list(items)
    if not xs:
        return f'<p class="empty">{esc(empty)}</p>'
    return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in xs) + "</ul>"


def strength_badge(s: object) -> str:
    t = text(s)
    flat = re.sub(r"\s+", "", t)
    cls = "st-none" if ("없음" in flat or "무작위" in flat) else ("st-weak" if t.startswith("약") else "st-obs")
    return f'<span class="st {cls}">근거 강도 · {esc(t or "미기재")}</span>'


def axes_html(axes: object) -> str:
    if not isinstance(axes, dict) or not axes:
        return '<p class="empty">축 데이터가 없습니다</p>'
    rows_html = []
    for key, ko, lo, hi in AXES:
        if key not in axes:
            continue
        v = max(1.0, min(5.0, float(num(axes.get(key)))))
        pct = (v - 1) / 4 * 100
        rows_html.append(
            f'<div class="axis"><div class="axis-head"><b>{esc(ko)}</b>'
            f'<span class="axis-score num">{v:g}<i>/5</i></span></div>'
            f'<div class="axis-track"><span class="axis-fill" style="width:{max(pct, 4):.1f}%"></span></div>'
            f'<div class="axis-pole"><span>1 · {esc(lo)}</span><span>{esc(hi)} · 5</span></div></div>'
        )
    return f'<div class="axes">{"".join(rows_html)}</div>' if rows_html else '<p class="empty">축 데이터가 없습니다</p>'


def talk_html(talk: object) -> str:
    if not isinstance(talk, dict) or not talk:
        return ""
    good = ul(talk.get("good"), "아직 정리된 요청 방식이 없습니다")
    avoid = ul(talk.get("avoid"), "특별히 피할 방식은 적혀 있지 않습니다")
    best, example = text(talk.get("best_time")), text(talk.get("example"))
    blocks = []
    if best:
        blocks.append(f'<div class="talk-time"><div class="talk-k">말 걸기 좋은 시간</div><div class="v num">{esc(best)}</div></div>')
    if example:
        blocks.append(f'<div class="talk-ex"><div class="talk-k">이렇게 보내면 된다</div><p class="q">{esc(example)}</p></div>')
    meta = f'<div class="talk-meta">{"".join(blocks)}</div>' if blocks else ""
    return (
        '<div class="talk"><div class="talk-h"><h3>이렇게 말 걸면 통한다</h3>'
        f'{strength_badge("관측된 응답 패턴 기반 · 참고용")}</div>'
        '<p class="talk-lead">집계된 말투·활동 시간대에서 뽑았다. 성격 판정이 아니라 요청 방식 제안이다.</p>'
        f'<div class="talk-grid"><div class="talk-box good"><div class="talk-k">통하는 방식</div>{good}</div>'
        f'<div class="talk-box avoid"><div class="talk-k">피하면 좋은 방식</div>{avoid}</div></div>{meta}</div>'
    )


def signals_html(sig: dict[str, Any]) -> str:
    if not sig:
        return ""
    badges: dict[str, str] = {}
    has_chat = sig.get("avg_chat_chars") is not None
    a_all, a_chat = num(sig.get("avg_chars")), num(sig.get("avg_chat_chars"))
    if a_all > 0 and a_chat > 0 and a_all >= a_chat * CHAT_GAP_RATIO:
        badges["avg_chat_chars"] = f'<span class="gap">문서 빼면 {a_all / a_chat:.1f}배 짧다</span>'
    trs = []
    for key, ko, unit in SIGNAL_ROWS:
        v = sig.get(key)
        if v is None:
            continue
        extra = badges.get(key, "")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            cell = f'<td class="n">{esc(fmt_int(v))}{esc(unit)}{extra}</td>'
        else:
            cell = f"<td>{esc(v)}{extra}</td>"
        trs.append(f"<tr><th>{esc(ko)}</th>{cell}</tr>")
    markers = sig.get("markers")
    if isinstance(markers, dict) and markers:
        pairs = sorted(markers.items(), key=lambda kv: num(kv[1]), reverse=True)
        trs.append(f'<tr><th>말투 마커</th><td>{" · ".join(f"{esc(k)} {esc(fmt_int(v))}" for k, v in pairs)}</td></tr>')
    endings = str_list(sig.get("endings_top"), 8)
    if endings:
        trs.append(f'<tr><th>자주 쓰는 어미</th><td>{esc(" · ".join(endings))}</td></tr>')
    if not trs:
        return ""
    chat_note = "<b>평균 길이</b>에는 공유한 문서·인용문이 포함된다. <b>평균 대화 길이</b>는 그것을 뺀 값이다. " if has_chat else ""
    return (
        '<details class="sig"><summary>관측 신호 (집계 수치)</summary><div>'
        f'<div class="tscroll"><table>{"".join(trs)}</table></div>'
        f'<div class="note">{chat_note}원문 인용은 담지 않는다. 건수·비율 같은 집계값만 남긴다 (docs/PRIVACY.md).</div></div></details>'
    )


def footer_html(part: str, built: str, sources: object = None) -> str:
    items = str_list(sources)
    src = f'<p>출처 — {esc(" · ".join(items))}</p>' if items else ""
    return (
        f"<footer><p>{esc(part)} · 빌드 {esc(built)}</p>{src}"
        "<p>표본은 파트 텔레그램 방에서 집계한 것이 전부다. 발화가 적은 사람의 프로필을 데이터가 많은 사람과 같은 무게로 비교하지 않는다.</p>"
        "<p>공개 범위와 금지 패턴은 salesplus-wiki 의 docs/PRIVACY.md · 스키마는 docs/PROFILE_SCHEMA.md · docs/PROJECT_SCHEMA.md 를 따른다. "
        "재미 코너에서 빠지고 싶으면 본인 JSON 의 <code>fun</code> 을 <code>null</code> 로 두면 된다.</p></footer>"
    )


# ───────────────────────────────────────────────────────────────── 멤버 페이지


def render_person(d: dict[str, Any], built: str) -> str:
    name, part, role = text(d.get("name")), text(d.get("part")) or DEFAULT_PART, text(d.get("role"))
    generated = text(d.get("generated"))
    profile, sig = sub(d, "profile"), sub(d, "signals")
    fun_raw = d.get("fun")
    fun: dict[str, Any] | None = fun_raw if isinstance(fun_raw, dict) else None
    nick = text(fun.get("nickname")) if fun else ""
    conf, utter = text(sig.get("confidence")), sig.get("utterances")

    chips = []
    if role:
        chips.append(f'<span class="chip"><b>직무</b>{esc(role)}</span>')
    if generated:
        chips.append(f'<span class="chip"><b>데이터 기준</b>{esc(generated)}</span>')
    if conf:
        chips.append(
            f'<span class="chip"><b>신뢰도</b>{esc(conf)}'
            + (f" · 발화 {esc(fmt_int(utter))}건" if utter is not None else "")
            + "</span>"
        )
    hero = (
        '<header class="hero"><div class="in"><a class="back" href="../index.html">← 멤버 목록</a>'
        f'<div class="hero-eyebrow">{esc(part)}</div><h1 class="hero-title">{esc(name)}</h1>'
        + (f'<div class="hero-sub">{esc(nick)}</div>' if nick else "")
        + (f'<div class="hero-chips">{"".join(chips)}</div>' if chips else "")
        + "</div></header>"
    )

    lowwarn = ""
    if conf == "낮음":
        n = fmt_int(utter) if utter is not None else "?"
        lowwarn = (
            f'<div class="warnbox lowwarn"><h3>⚠ 표본 {esc(n)}건 — 참고만</h3>'
            "<p>발화가 적어 아래 내용의 근거가 약하다. 데이터가 많은 사람과 같은 무게로 읽지 않는다.</p></div>"
        )

    axes_note = ""
    if sub(profile, "axes"):
        axes_note = (
            '<div class="note">점수는 아래 <b>관측 신호</b>의 말투 마커에서 뽑은 것이 아니다. '
            "무엇을 먼저 정리했는지, 어떤 산출물을 냈는지 같은 <b>행동</b>을 보고 매겼다.</div>"
        )
    work_style = text(profile.get("work_style"))
    if not profile:
        work_body = card("", '<p class="empty" style="margin:0">아직 발화가 적어 업무 성향을 적지 않았다. 대화가 쌓이면 근거를 달아 채운다.</p>')
    else:
        work_body = (
            card("업무 성향 5축", axes_html(profile.get("axes")) + axes_note)
            + '<div class="grid2" style="margin-top:13px">'
            + card("일하는 방식", f'<p style="margin:0">{esc(work_style)}</p>' if work_style else '<p class="empty">아직 적혀 있지 않습니다</p>')
            + card("강점", ul(profile.get("strengths")))
            + "</div>"
        )
    s_work = sec("sec-work", "관측 근거 있음", "업무 성향", work_body, "실제 업무 참고에 쓸 수 있는 수준")

    if fun:
        fcards = []
        for key, ko in FUN_KEYS:
            blk = sub(fun, key)
            fcards.append(
                f'<div class="funcard"><div class="funk">{esc(ko)}</div>'
                f'<div class="funval">{esc(text(blk.get("value")) or "-")}</div>{strength_badge(blk.get("strength"))}'
                f'<div class="funbasis">{esc(text(blk.get("basis")) or "근거 메모 없음")}</div></div>'
            )
        top = sig.get("reactions_top") or []
        if isinstance(top, list) and top and isinstance(top[0], (list, tuple)) and top[0]:
            emoji = esc(str(top[0][0]))
            others = " ".join(esc(str(r[0])) for r in top[1:3] if isinstance(r, (list, tuple)) and r)
            fcards.append(
                f'<div class="funcard"><div class="funk">받은 반응</div><div class="funval" style="font-size:34px">{emoji}</div>'
                f'{strength_badge("관측")}<div class="funbasis">이 사람 글에 가장 많이 달린 이모지다.'
                + (f" 다음은 {others}." if others else "")
                + " 개수는 발화량에 비례해서 적지 않는다.</div></div>"
            )
        fun_body = (
            '<div class="funwrap"><p class="funhead">MBTI·나이대는 <b>추측</b>이다. 위 업무 성향과 같은 근거로 쓰이지 않았다. '
            "말투 뱃지와 받은 반응은 <b>집계한 관측값</b>이다. 항목마다 <b>근거 강도</b>를 함께 본다.</p>"
            + talk_html(fun.get("how_to_talk"))
            + f'<div class="fun3">{"".join(fcards)}</div></div>'
        )
        s_fun = sec("sec-fun", "추측 · 재미용", "재미 코너", fun_body, "근거 강도를 항목마다 표시한다")
    elif "fun" in d:
        s_fun = sec("sec-fun", "비공개", "재미 코너", card("", '<p class="empty" style="margin:0">본인 요청으로 재미 코너를 싣지 않는다. 업무 성향만 표시한다.</p>'))
    else:
        s_fun = sec("sec-fun", "아직 없음", "재미 코너", card("", '<p class="empty" style="margin:0">표본이 적어 아직 만들지 않았다. 대화가 쌓이면 채운다.</p>'))

    body = hero + banner_html() + '<main class="wrap">' + lowwarn + s_work + s_fun + signals_html(sig) + footer_html(part, built, d.get("sources")) + "</main>"
    return html_doc(f"{name} · {part} 프로필", body)


def avatar_html(name: str) -> str:
    """이름 첫 글자 모노그램. 색은 로고 4계열(버밀리온·앰버·블루·HSP) 중 이름으로 고정 배정 — 빌드마다 바뀌지 않는다."""
    if not name:
        return ""
    variant = sum(ord(ch) for ch in name) % 4
    cls = f" av-{variant}" if variant else ""
    return f'<span class="avatar{cls}" aria-hidden="true">{esc(name[0])}</span>'


def member_card(d: dict[str, Any], href: str) -> str:
    name, role = text(d.get("name")), text(d.get("role"))
    fun_raw = d.get("fun")
    fun: dict[str, Any] | None = fun_raw if isinstance(fun_raw, dict) else None
    sig = sub(d, "signals")
    low = text(sig.get("confidence")) == "낮음"
    nick = text(fun.get("nickname")) if fun else ""
    chips = []
    if fun and text(g(fun, "mbti.value")):
        chips.append(f'<span class="mchip mbti">{esc(g(fun, "mbti.value"))}</span>')
    if fun and text(g(fun, "age_band.value")):
        chips.append(
            f'<span class="mchip age" title="나이대 추정 · 근거 약함 · {esc(g(fun, "age_band.basis", ""))}">'
            f'<i>나이대</i>{esc(g(fun, "age_band.value"))}</span>'
        )
    bc = badge_short(sig.get("badge"))
    if bc:
        chips.append(f'<span class="mchip badge" title="{esc(g(fun, "speech_badge.value", "") if fun else "")}">{esc(bc)}</span>')
    top = sig.get("reactions_top") or []
    if isinstance(top, list) and top and isinstance(top[0], (list, tuple)) and top[0]:
        chips.append(f'<span class="mchip react" title="가장 많이 받은 반응">{esc(str(top[0][0]))}</span>')
    if not fun:
        chips.append('<span class="mchip">재미 코너 비공개</span>' if "fun" in d else '<span class="mchip">아직 표본 부족</span>')
    badge = ""
    if low:
        n = fmt_int(sig.get("utterances")) if sig.get("utterances") is not None else "?"
        badge = f'<div class="lowbadge">⚠ 표본 {esc(n)}건 — 참고만</div>'
    return (
        f'<a class="mcard{" low" if low else ""}" href="{esc(href)}">'
        f'<div class="mhead">{avatar_html(name)}<div><div class="mname">{esc(name)}</div>'
        + (f'<div class="mrole">{esc(role)}</div>' if role else "")
        + "</div></div>"
        + (f'<div class="mnick">{esc(nick)}</div>' if nick else "")
        + (f'<div class="mchips">{"".join(chips)}</div>' if chips else "")
        + badge
        + "</a>"
    )


# ─────────────────────────────────────────────────────────────── 프로젝트 페이지


def status_badge(status: str) -> str:
    return f'<span class="ps {PROJECT_STATUS_CLASS.get(status, "ps-todo")}">{esc(status or "상태 미기재")}</span>'


def work_state_chip(state: object) -> str:
    s = text(state)
    if not s:
        return ""
    cls = next((c for word, c in WORK_STATE_HINTS if word in s), "")
    return f'<span class="ws {cls}">{esc(s)}</span>'


def progress_html(milestones: object, compact: bool = False) -> str:
    done, total, nxt = project_progress(milestones)
    if total == 0:
        return ""
    bar = f'<div class="prog{" zero" if done == 0 else ""}"><span style="width:{done / total * 100:.1f}%"></span></div>'
    right = f"다음 · {esc(nxt)}" if (nxt and not compact) else ""
    return bar + f'<div class="progk"><span><b>마일스톤 {done}/{total}</b> 완료</span><span>{right}</span></div>'


def milestones_html(milestones: object) -> str:
    items = [m for m in (milestones if isinstance(milestones, list) else []) if isinstance(m, dict) and text(m.get("label"))]
    if not items:
        return '<p class="empty">마일스톤이 아직 없습니다</p>'
    lis = []
    for m in items:
        st = text(m.get("state"))
        st = st if st in MILESTONE_STATES else "todo"
        lis.append(f'<li class="{st}"><span class="d">{esc(text(m.get("date")))}</span>{esc(m.get("label"))}<span class="k">{esc(MILESTONE_KO[st])}</span></li>')
    return f'<ul class="ms">{"".join(lis)}</ul>'


def workstreams_html(ws: object) -> str:
    items = [r for r in (ws if isinstance(ws, list) else []) if isinstance(r, dict) and text(r.get("area"))]
    if not items:
        return '<p class="empty">영역별 현황이 아직 없습니다</p>'
    trs = "".join(f'<tr><td class="area">{esc(r.get("area"))}</td><td>{work_state_chip(r.get("state"))}</td><td>{esc(text(r.get("note")))}</td></tr>' for r in items)
    return f'<div class="tscroll"><table><thead><tr><th>영역</th><th>상태</th><th>메모</th></tr></thead><tbody>{trs}</tbody></table></div>'


def members_html(members: object, member_hrefs: dict[str, str], prefix: str) -> str:
    items = [m for m in (members if isinstance(members, list) else []) if isinstance(m, dict) and text(m.get("name"))]
    if not items:
        return '<p class="empty">담당이 아직 적혀 있지 않습니다</p>'
    chips = []
    for m in items:
        name, role = text(m.get("name")), text(m.get("role"))
        inner = esc(name) + (f"<i>{esc(role)}</i>" if role else "")
        href = member_hrefs.get(name)
        chips.append(f'<a class="mem" href="{esc(prefix + href)}">{inner}</a>' if href else f'<span class="mem noprofile" title="프로필 카드 없음">{inner}</span>')
    return f'<div class="mems">{"".join(chips)}</div>'


def recent_html(recent: object) -> str:
    items = [r for r in (recent if isinstance(recent, list) else []) if isinstance(r, dict) and text(r.get("note"))]
    if not items:
        return '<p class="empty">최근 변화가 아직 없습니다</p>'
    trs = "".join(f'<tr><td class="d">{esc(text(r.get("date")))}</td><td>{esc(r.get("note"))}</td></tr>' for r in items)
    return f'<div class="tscroll"><table class="recent"><tbody>{trs}</tbody></table></div>'


def render_project(d: dict[str, Any], built: str, member_hrefs: dict[str, str]) -> str:
    name = text(d.get("name"))
    title = text(d.get("title")) or name
    part = text(d.get("part")) or DEFAULT_PART
    status, phase, target = text(d.get("status")), text(d.get("phase")), text(d.get("target"))
    codename, generated = text(d.get("codename")), text(d.get("generated"))
    summary, tagline = text(d.get("summary")), text(d.get("tagline"))
    members = d.get("members") if isinstance(d.get("members"), list) else []

    chips = [f'<span class="chip"><b>상태</b>{esc(status)}</span>']
    for label, v in (("단계", phase), ("목표", target), ("코드명", codename)):
        if v:
            chips.append(f'<span class="chip"><b>{label}</b>{esc(v)}</span>')
    if members:
        chips.append(f'<span class="chip"><b>담당</b>{len(members)}명</span>')
    if generated:
        chips.append(f'<span class="chip"><b>데이터 기준</b>{esc(generated)}</span>')
    hero = (
        '<header class="hero"><div class="in"><a class="back" href="../index.html#projects">← 목록</a>'
        f'<div class="hero-eyebrow">프로젝트 · {esc(part)}</div><h1 class="hero-title">{esc(title)}</h1>'
        + (f'<div class="hero-sub">{esc(tagline or summary)}</div>' if (tagline or summary) else "")
        + f'<div class="hero-chips">{"".join(chips)}</div></div></header>'
    )
    intro = card("한 줄 정의", f'<p style="margin:0">{esc(summary)}</p>') if (tagline and summary) else ""
    s_prog = sec(
        "sec-proj", "위키 요약", "진행 상황",
        card("진행률", progress_html(d.get("milestones")) or '<p class="empty">마일스톤이 없어 진행률을 계산하지 않는다</p>')
        + f'<div style="margin-top:13px">{card("마일스톤", milestones_html(d.get("milestones")))}</div>',
        "완료한 마일스톤 수로만 계산한다",
    )
    s_work = sec("sec-proj", "영역별", "지금 어디까지 왔나", card("", workstreams_html(d.get("workstreams"))), "데이터 기준일 시점")
    s_mem = sec("sec-proj", "담당", "누가 하나", card("", members_html(members, member_hrefs, "../")), "카드가 있는 사람은 프로필로 이어진다")
    s_next = sec("sec-proj", "다음", "다음 단계", card("", ul(d.get("next"), "다음 단계가 아직 적혀 있지 않습니다")))
    s_recent = sec("sec-proj", "이력", "최근 변화", card("", recent_html(d.get("recent"))), "최신이 위")
    body = (
        hero + project_banner_html() + '<main class="wrap">'
        + (f'<div style="margin-top:28px">{intro}</div>' if intro else "")
        + s_prog + s_work + s_mem + s_next + s_recent
        + footer_html(part, built, d.get("sources")) + "</main>"
    )
    return html_doc(f"{title} · {part} 과제", body)


def project_card(d: dict[str, Any], href: str) -> str:
    """목록의 프로젝트 카드. 동시 진행 과제가 보통 2건이라 한 줄에 하나씩 크게 — 요약 전체·마일스톤·담당까지 보인다."""
    title = text(d.get("title")) or text(d.get("name"))
    status, phase, summary, codename = text(d.get("status")), text(d.get("phase")), text(d.get("summary")), text(d.get("codename"))
    tagline, target = text(d.get("tagline")), text(d.get("target"))
    members = d.get("members") if isinstance(d.get("members"), list) else []
    _, _, nxt = project_progress(d.get("milestones"))
    chips = [f'<span class="mchip proj">{esc(b)}</span>' for b in str_list(d.get("badges"), 3)]
    if members:
        chips.append(f'<span class="mchip">담당 {len(members)}명</span>')
    main = (
        f'<div class="pmain"><div class="phead">{status_badge(status)}'
        + (f'<span class="pphase">{esc(phase)}</span>' if phase else "")
        + f'</div><div class="pname">{esc(title)}'
        + (f'<span class="pcode">{esc(codename)}</span>' if codename else "")
        + "</div>"
        + (f'<div class="ptag">{esc(tagline)}</div>' if tagline else "")
        + (f'<div class="psum">{esc(summary)}</div>' if summary else "")
        + (f'<div class="mchips">{"".join(chips)}</div>' if chips else "")
        + progress_html(d.get("milestones"), compact=True)
        + (f'<div class="pnext"><i>다음</i>{esc(nxt)}</div>' if nxt else "")
        + "</div>"
    )
    # 카드 전체가 <a> 라 담당 칩은 링크 없이 이름만 (중첩 링크 금지)
    aside = (
        '<div class="paside"><div class="pk">마일스톤</div>'
        + milestones_html(d.get("milestones"))
        + (f'<div class="ptarget"><b>목표</b> {esc(target)}</div>' if target else "")
        + ('<div class="pk" style="margin-top:14px">담당</div>' + members_html(members, {}, "") if members else "")
        + "</div>"
    )
    return f'<a class="pcard" href="{esc(href)}">{main}{aside}</a>'


# ─────────────────────────────────────────────────── 변경사항 · 파트 일정 (SCHEDULE_SCHEMA.md)

WEEKDAY_KO = "월화수목금토일"


def day_ko(d: date) -> str:
    """`9/18(금)`."""
    return f"{d.month}/{d.day}({WEEKDAY_KO[d.weekday()]})"


def day_ko_str(s: object) -> str:
    """`2026-09-18` → `9/18(금)`. 읽을 수 없으면 적힌 그대로."""
    d = parse_date(s)
    return day_ko(d) if d else text(s)


def group_by_date(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """정렬된 목록을 날짜별로 묶는다 (정렬 순서를 그대로 지킨다)."""
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for it in items:
        if groups and groups[-1][0] == it["date"]:
            groups[-1][1].append(it)
        else:
            groups.append((it["date"], [it]))
    return groups


def today_mark(day: str, today: date) -> str:
    return '<span class="today">오늘</span>' if day == today.isoformat() else ""


def gen_chip(generated: str) -> str:
    return f'<div class="dchips"><span class="dchip">데이터 기준 {esc(generated)}</span></div>' if generated else ""


def summary_html(summary: str) -> str:
    """`·` 로 이은 요약을 조각으로 나눈다.

    프로필 줄은 한 사람당 하나뿐이라 신호 요약이 길어진다 (`발화 35→41건 · 평균 44→40자 · …`).
    좁은 폭에서 글자 아무 데서나 접히지 않고 **조각 단위로** 줄이 바뀌게 조각마다 상자를 준다.
    구분자는 앞 조각에 붙여(CSS `::after`) 다음 줄이 `·` 로 시작하지 않게 한다.
    """
    # 구분자는 양옆에 공백이 있는 ` · ` 뿐이다 — 값 안의 `종류·담당` 같은 바른 점은 쪼개지 않는다
    parts = [p for p in (chunk.strip() for chunk in summary.split(" · ")) if p]
    if len(parts) < 2:
        return esc(summary)
    return "".join(f'<span class="chseg">{esc(p)}</span>' for p in parts)


CHANGE_CARD_CLASS = {"profile": "ck-profile", "project": "ck-project", "schedule": "ck-sched", "site": "ck-site"}


def change_name(ctype: str, name: str, member_hrefs: dict[str, str], project_hrefs: dict[str, str]) -> str:
    """바뀐 카드 이름. 프로필·프로젝트 카드가 있으면 상세로 잇는다. site·일정은 이름이 없거나 종류와 같아 비운다."""
    if ctype == "site" or not name or name == CHANGELOG_CARD_KO.get(ctype):
        return ""
    href = member_hrefs.get(name) if ctype == "profile" else (project_hrefs.get(name) if ctype == "project" else None)
    who = f'<a href="{esc(href)}">{esc(name)}</a>' if href else esc(name)
    return f'<span class="chtarget">{who}</span>'


def changelog_days(entries: list[dict[str, Any]], today: date, member_hrefs: dict[str, str], project_hrefs: dict[str, str]) -> str:
    """날짜별로 묶은 변경 목록 — part-cards 와 같은 모양 (2026-09-16 결정).

    한 줄 = 카드 종류 칩 · 바뀐 카드 이름(상세 링크) · 요약. 신설·삭제만 작은 표식을 덧붙이고 '갱신'은 표식 없이 둔다.
    """
    blocks = []
    for day, items in group_by_date(entries):
        lis = []
        for e in items:
            card_kind = text(e.get("card"))
            chip = f'<span class="ck {CHANGE_CARD_CLASS.get(card_kind, "")}">{esc(CHANGELOG_CARD_KO.get(card_kind, card_kind))}</span>'
            target = change_name(card_kind, text(e.get("name")), member_hrefs, project_hrefs)
            kind = text(e.get("kind"))
            mark = f'<span class="chkind chkind-{esc(kind)}">{esc(CHANGELOG_KIND_KO.get(kind, kind))}</span>' if kind in ("added", "removed") else ""
            lis.append(f'<li>{chip}{target}{mark}<span class="chsum">{summary_html(text(e.get("summary")))}</span></li>')
        head = f'<div class="chdate">{esc(day_ko_str(day))}{today_mark(day, today)}</div>'
        blocks.append(f'<div class="chday">{head}<ul class="chlist">{"".join(lis)}</ul></div>')
    return "".join(blocks)


def changelog_section(
    data: dict[str, Any] | None, today: date, member_hrefs: dict[str, str], project_hrefs: dict[str, str]
) -> str:
    """최근 변경 — 7일. 파일 자체가 없으면 한 줄만 남긴다. 목록 맨 아래 섹션."""
    if data is None:
        body = card("", '<p class="empty" style="margin:0">아직 변경 기록이 없다. salesplus-wiki 에서 카드가 한 번 더 갱신되면 여기에 쌓인다.</p>')
        return sec("sec-chg", "카드 이력", "최근 변경 — 7일", body)
    keep = int(num(data.get("keep_days"), CHANGELOG_KEEP_DAYS)) or CHANGELOG_KEEP_DAYS
    entries = recent_entries(data.get("entries"), today, keep)
    if entries:
        body = f'<div class="card chcard">{changelog_days(entries, today, member_hrefs, project_hrefs)}</div>'
    else:
        body = card("", '<p class="empty" style="margin:0">지난 7일간 카드 변경 없음</p>')
    return sec("sec-chg", "카드 이력", "최근 변경 — 7일", body, "카드가 언제 무엇 때문에 바뀌었나 · 한 사람당 한 줄")


def event_members(names: list[str], member_hrefs: dict[str, str]) -> str:
    """제목에 등장한 파트원. 프로필 카드가 있으면 상세로 잇는다."""
    out = []
    for n in names:
        href = member_hrefs.get(n)
        out.append(f'<a class="evmem" href="{esc(href)}">{esc(n)}</a>' if href else f'<span class="evmem">{esc(n)}</span>')
    return "".join(out)


def clamp_to_window(events: list[dict[str, Any]], start: date) -> list[dict[str, Any]]:
    """창 앞에서 시작한(진행 중) 일정은 창 첫날 묶음에 넣는다 — "오늘·내일" 섹션에 어제 묶음이 생기지 않게.

    기간 표시(`9/17(목)~9/18(금)`)는 원래 시작일을 써야 하므로 `begun` 에 남긴다.
    """
    s_iso = start.isoformat()
    out = [dict(e, begun=e["date"], date=max(e["date"], s_iso)) for e in events]
    out.sort(key=lambda x: (x["date"], x["time"], x["label"]))
    return out


def schedule_days(events: list[dict[str, Any]], today: date, member_hrefs: dict[str, str]) -> str:
    """날짜별로 묶은 일정 목록. 한 줄 = 시각 · 종류 · 라벨 · 과제 · 멤버 · 기간."""
    blocks = []
    for day, items in group_by_date(events):
        lis = []
        for e in items:
            kind = e["kind"]
            row = f'<span class="evtime">{esc(e["time"])}</span>' if e["time"] else ""
            row += f'<span class="ek {EVENT_KIND_CLASS.get(kind, "ek-etc")}">{esc(kind)}</span>'
            row += f'<span class="evlabel">{esc(e["label"])}</span>'
            if e["project"]:
                row += f'<span class="evproj">{esc(e["project"])}</span>'
            row += event_members(e["members"], member_hrefs)
            if e["end"]:  # events_in_window 가 하루짜리는 이미 비워 준다
                row += f'<span class="evspan">{esc(day_ko_str(e.get("begun", day)))}~{esc(day_ko_str(e["end"]))}</span>'
            if e["ongoing"]:
                row += '<span class="on">진행 중</span>'
            if e["recurring"]:
                row += '<span class="evrepeat">매주</span>'
            if e.get("done"):
                row += '<span class="donechip">완료</span>'
            if e["note"]:
                row += f'<span class="evnote">{esc(e["note"])}</span>'
            classes = " ".join(c for c in ("rec" if e["recurring"] else "", "done" if e.get("done") else "") if c)
            lis.append(f'<li class="{classes}">{row}</li>')
        head = f'<div class="sdate">{esc(day_ko_str(day))}{today_mark(day, today)}</div>'
        blocks.append(f'<div class="sday">{head}<ul class="slist">{"".join(lis)}</ul></div>')
    return "".join(blocks)


def holiday_note(data: dict[str, Any], start: date, end: date) -> str:
    """창 안에 낀 휴일. 업무일 계산에서 뺐다는 근거를 남긴다."""
    rest = sorted(d for d in holiday_set(data.get("holidays")) if start <= d <= end)
    if not rest:
        return ""
    return f'<div class="note">휴무: {esc(" · ".join(day_ko(d) for d in rest))} — 업무일 계산에서 뺐다.</div>'


def schedule_section(data: dict[str, Any] | None, today: date, member_hrefs: dict[str, str]) -> str:
    """파트 일정 — 오늘부터 업무일 2일(오늘·내일). 창은 빌드 시각 기준으로 여기서 계산한다.

    기준일은 `schedule_anchor(today)`(오늘)다. 오늘 처리한 일은 위키가 `done` 을 붙여 취소선 + "완료" 칩으로
    그려진다 (2026-09-22 결정).
    "데이터 없음"과 "창 안에 일정 없음"은 다른 문구다 — 파일이 빠진 것과 잡힌 일정이 없는 것은 다르다.
    """
    title = "파트 일정 — 오늘 · 내일"
    if data is None:
        body = card("", '<p class="empty" style="margin:0">아직 일정 데이터가 없다. salesplus-wiki 의 data/schedule.json 이 생기면 여기에 뜬다.</p>')
        return sec("sec-sched", "위키 요약", title, body)
    anchor = schedule_anchor(today)
    events = events_in_window(data, anchor)
    start, end = business_window(anchor, str_list(data.get("holidays")))
    inner = (
        schedule_days(clamp_to_window(events, start), today, member_hrefs)
        if events
        else f'<p class="empty" style="margin:0">다음 업무일 {SCHEDULE_WINDOW_DAYS}일 안에 잡힌 일정 없음</p>'
    )
    body = card("", inner + holiday_note(data, start, end))
    generated = text(data.get("generated"))
    subtitle = f"{day_ko(start)} ~ {day_ko(end)} · 업무일 {SCHEDULE_WINDOW_DAYS}일"
    if generated:
        subtitle += f" · 데이터 기준 {generated}"
    return sec("sec-sched", "위키 요약", title, body, subtitle)


def yesterday_mark(day: str, today: date) -> str:
    return '<span class="yday">어제</span>' if day == (today - timedelta(days=1)).isoformat() else ""


def daily_rooms(rooms: object) -> str:
    """하루치 — 방별 요약 줄. 대화 없는 방은 그렇다고 적는다."""
    blocks = []
    for r in rooms if isinstance(rooms, list) else []:
        if not isinstance(r, dict) or not text(r.get("room")):
            continue
        items = str_list(r.get("items"), DAILY_MAX_ITEMS)
        inner = ('<ul class="dlist">' + "".join(f"<li>{esc(it)}</li>" for it in items) + "</ul>") if items else '<p class="empty">대화 없음</p>'
        blocks.append(f'<div class="droom"><div class="droomname">{esc(text(r.get("room")))}</div>{inner}</div>')
    return "".join(blocks) or '<p class="empty">요약 없음</p>'


def daily_section(data: dict[str, Any] | None, today: date) -> str:
    """업무 요약 — 최근 2일치(어제·오늘), 동기화 때 사람이 쓴 방별 요약 줄 (docs/DAILY_SCHEMA.md). 맨 위 섹션.

    아침에 보면 어제 것이, 저녁에 보면 오늘 것이 맨 위에 온다. 둘 다 없으면(며칠 동기화가 없었으면)
    마지막 요약 날짜를 밝힌다 — 묵은 요약을 오늘 것처럼 읽지 않게.
    """
    title = "업무 요약 — 최근 2일"
    if data is None:
        body = card("", '<p class="empty" style="margin:0">아직 요약 데이터가 없다. salesplus-wiki 의 data/daily.json 이 생기면 여기에 뜬다.</p>')
        return sec("sec-daily", "위키 요약", title, body)
    keep = int(num(data.get("keep_days"), DAILY_KEEP_DAYS)) or DAILY_KEEP_DAYS
    days = recent_days(data.get("days"), today, keep)
    blocks = []
    for d in days:
        day = text(d.get("date"))
        head = f'<div class="ddate">{esc(day_ko_str(day))}{today_mark(day, today)}{yesterday_mark(day, today)}</div>'
        blocks.append(f'<div class="dday">{head}{daily_rooms(d.get("rooms"))}</div>')
    inner = "".join(blocks) if blocks else '<p class="empty" style="margin:0">아직 쌓인 요약이 없다.</p>'
    latest = parse_date(days[0].get("date")) if days else None
    stale = latest is not None and latest < today - timedelta(days=1)
    if stale:
        inner += f'<div class="note">마지막 동기화가 {esc(day_ko(latest))} 다 — 그 뒤 요약은 아직 없다.</div>'
    subtitle = "동기화 때 방별로 정리한 업무 요약 · 최신이 위"
    generated = text(data.get("generated"))
    if generated:
        subtitle += f" · 데이터 기준 {generated}"
    return sec("sec-daily", "위키 요약", title, card("", inner), subtitle)


def budget_bars(rows: list[dict[str, Any]], member_hrefs: dict[str, str]) -> str:
    """사람별 막대 한 줄 = 이름(프로필 링크) · 사용 비율 막대 · 사용 / 할당 · 잔액 (+ 소진·1만 미만 표식)."""
    cells = []
    for r in rows:
        href = member_hrefs.get(r["name"])
        who = f'<a href="{esc(href)}">{esc(r["name"])}</a>' if href else esc(r["name"])
        flag = ""
        if r["remaining"] <= 0:
            flag = '<span class="bflag zero">소진</span>'
        elif r["remaining"] < BUDGET_LOW_REMAINING:
            flag = '<span class="bflag low">1만 미만</span>'
        spent = " spent" if r["remaining"] <= 0 else ""
        pct = r["ratio"] * 100
        cells.append(
            f'<div class="bname">{who}</div>'
            f'<div class="btrack" role="img" aria-label="{esc(r["name"])} 사용 {fmt_int(r["used"])}원 / 할당 {fmt_int(r["allotted"])}원">'
            f'<span class="bfill{spent}" style="width:{pct:.1f}%"></span></div>'
            f'<div class="bamt">{esc(fmt_int(r["used"]))} / {esc(fmt_int(r["allotted"]))} · 잔액 <b>{esc(fmt_int(r["remaining"]))}</b>{flag}</div>'
        )
    return f'<div class="bgrid" role="table" aria-label="사람별 네트워킹비 사용액과 잔액">{"".join(cells)}</div>'


def budget_section(data: dict[str, Any] | None, member_hrefs: dict[str, str]) -> str:
    """네트워킹비 — 사람별 사용/할당 막대와 잔액 (docs/BUDGET_SCHEMA.md). 멤버 섹션 바로 아래.

    합계는 파일의 `total` 이 아니라 사람 줄을 다시 더한다 — 둘이 어긋나면 검증이 이미 파일을 뺐지만,
    `total` 이 없는 파일도 그릴 수 있게. 파일이 없으면 한 줄만 남긴다.
    """
    title = "네트워킹비 — 사람별 잔액"
    if data is None:
        body = card("", '<p class="empty" style="margin:0">아직 네트워킹비 데이터가 없다. salesplus-wiki 의 data/budget.json 이 생기면 여기에 뜬다.</p>')
        return sec("sec-budget", "위키 요약", title, body)
    rows = budget_rows(data.get("members"))
    if not rows:
        return sec("sec-budget", "위키 요약", title, card("", '<p class="empty" style="margin:0">사람별 줄이 아직 없다.</p>'))
    allotted = sum(r["allotted"] for r in rows)
    used = sum(r["used"] for r in rows)
    remaining = sum(r["remaining"] for r in rows)
    spent_n = sum(1 for r in rows if r["remaining"] <= 0)
    pct = f"{used / allotted * 100:.0f}%" if allotted else "-"
    tiles = (
        '<div class="btiles">'
        f'<div class="btile hi"><div class="k">잔액 합계 ({len(rows)}명)</div><div class="v">{esc(fmt_int(remaining))}<i>원</i></div></div>'
        f'<div class="btile"><div class="k">사용 / 할당</div><div class="v">{esc(fmt_int(used))}<i>/ {esc(fmt_int(allotted))} · {esc(pct)}</i></div></div>'
        f'<div class="btile"><div class="k">소진</div><div class="v">{spent_n}<i>명</i></div></div>'
        "</div>"
    )
    rule = text(data.get("rule"))
    note = "막대는 할당 대비 사용 비율." + (f" 네트워킹비는 {esc(rule)}만 쓸 수 있다." if rule else "") + " 정본 장부와 파트공용비는 위키에 있고 여기에는 싣지 않는다."
    body = card("", tiles + budget_bars(rows, member_hrefs) + f'<div class="note">{note}</div>')
    subtitle = " · ".join(x for x in (text(data.get("quarter")), f"데이터 기준 {text(data.get('generated'))}" if text(data.get("generated")) else "") if x)
    return sec("sec-budget", "위키 요약", title, body, subtitle)


def upcoming_count(data: dict[str, Any] | None, today: date) -> int:
    """오늘·내일 창의 일정 수 — 파트 일정 섹션과 같은 앵커."""
    return len(events_in_window(data, schedule_anchor(today))) if data is not None else 0


# ─────────────────────────────────────────────────────────── 활동 월드컵 (재미)

# 캐릭터는 이미 사이트에 공개된 관측값에서만 만든다 — 말투 배지(signals.badge, 멤버 목록에 나간다)와
# 업무 성향 5축(profile.axes, 상세 페이지에 나간다). 별명(fun.nickname)은 재미 코너가 있을 때만 쓴다.
# 말투 배지 마커 → (상징 이모지, 많이 쓸 때 직업, 안 쓸 때 직업, 모자, 같은 뜻의 축). 안 쓰는 쪽도 호의적으로 붙인다.
BATTLE_HEROES = {
    "제안형": ("🧙", "아이디어 마법사", "묵묵한 실행 마법사", "wizard", "exploration"),
    "단정": ("🗡️", "결단 검사", "신중한 검사", "helm", ""),
    "확인요구": ("🛡️", "검증 수호자", "믿음직한 수호자", "crest", "verification"),
    "물음표": ("🔍", "질문 탐정", "직감 탐정", "detective", ""),
    "느낌표": ("⚡", "번개 전령", "차분한 전령", "antenna", ""),
    "웃음ㅋ": ("😆", "웃음 폭격기", "포커페이스 승부사", "party", ""),
    "웃음ㅎ": ("😊", "미소 치유사", "담백한 치유사", "flower", ""),
    "물결": ("🌊", "물결 서퍼", "직진 서퍼", "band", ""),
    "말줄임": ("🌙", "여운 술사", "깔끔한 술사", "nightcap", ""),
    "습니다체": ("🎩", "정중 기사", "친근한 기사", "tophat", ""),
    "겸양": ("🕊️", "평화 수호자", "당당한 수호자", "halo", ""),
    "위임분담": ("🧭", "작전 지휘관", "솔선 지휘관", "captain", "delegation"),
    "존댓말요": ("🌸", "상냥한 음유시인", "담백한 음유시인", "beret", ""),
}
# 5축 → (RPG 스탯 이름, 직업 앞 수식어, 배지가 없을 때 이모지·직업·모자). 순서가 동점일 때의 우선순위다
BATTLE_AXES = (
    ("delegation", "리더십", "든든한", "🧭", "작전 지휘관", "captain"),
    ("verification", "방어", "빈틈없는", "🛡️", "검증 수호자", "crest"),
    ("planning", "전략", "계획적인", "📜", "전략가", "mortar"),
    ("thoroughness", "집중", "집요한", "🔬", "장인", "goggles"),
    ("exploration", "창의", "호기심 많은", "🔭", "탐험가", "explorer"),
)
BATTLE_DEFAULT_HERO = ("⭐", "신입 모험가", "sprout")  # 배지도 5축도 없을 때
# 상징 이모지가 참가자끼리 겹치면 쓰는 같은 테마의 대체 아이콘. 다 쓰였으면 BATTLE_ICON_SPARE 에서
BATTLE_ICON_ALTS = {
    "🧙": ("🪄", "🔮", "💡"), "🗡️": ("⚔️", "🏹"), "🛡️": ("🏰", "🔰"), "🔍": ("🕵️", "🔎"),
    "⚡": ("📣", "🚀"), "😆": ("🎉", "🤣"), "😊": ("🌷", "🍀", "☀️"), "🌊": ("🏄", "🐬"),
    "🌙": ("✨", "🌌"), "🎩": ("👑", "🎖️"), "🕊️": ("🤝", "🌿"), "🧭": ("🚩", "🗺️", "⚓"),
    "🌸": ("🎵", "🎻"), "📜": ("♟️", "🗂️"), "🔬": ("🛠️", "⚙️"), "🔭": ("🧗", "🌍"), "⭐": ("🌱", "🐣"),
}
BATTLE_ICON_SPARE = ("🎯", "🔥", "🌈", "🦄", "🐯", "🦉", "🐳", "🎈", "🍩", "🧩")
# 눈 모양 — 웃음 배지는 ^^ 눈, 최고 축이 검증이면 안경·탐색이면 반짝 눈, 나머지는 배지별
BATTLE_EYES = {"느낌표": "wide", "말줄임": "sleepy", "단정": "sharp", "물음표": "sparkle"}
BATTLE_PALETTE = 10  # 캐릭터 몸 색 수 — CSS .bt-c0 ~ .bt-c9
BATTLE_WEEK_KEYS = (("utterances", "u"), ("artifacts", "a"), ("active_days", "d"))

# 대진·점수는 브라우저에서 매번 새로 계산한다 (새로고침마다 대진이 바뀐다). 순수 로직은 Ladder 에 두고
# DOM 은 document 가 있을 때만 그린다 — node 로 Ladder 만 꺼내 검증할 수 있게.
# 데이터는 textContent·setAttribute 로만 넣는다 (innerHTML 에 데이터를 넣지 않는다).
BATTLE_JS = r"""
const Ladder = {
  rand() {
    if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
      const b = new Uint32Array(1);
      crypto.getRandomValues(b);
      return b[0] / 4294967296;
    }
    return Math.random();
  },
  // 활동점수 = 수다(발화) + 5×산출물 + 10×활동일
  score(p) { return p.u + 5 * p.a + 10 * p.d; },
  shuffle(xs, rnd) {
    const out = xs.slice();
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(rnd() * (i + 1));
      [out[i], out[j]] = [out[j], out[i]];
    }
    return out;
  },
  roundName(size, hasBye) {
    if (size === 2) return '결승전';
    return hasBye ? '1라운드' : size + '강전';
  },
  // 첫 라운드 대진. 2의 거듭제곱에 모자란 만큼 부전승 — 부전승 쌍은 한 자리만 비어 있어 부전승끼리는 붙지 않는다.
  // 어느 쌍이 부전승인지도 무작위
  draw(players, rnd) {
    let size = 2;
    while (size < players.length) size *= 2;
    const pairs = size / 2;
    const byeAt = new Set(Ladder.shuffle([...Array(pairs).keys()], rnd).slice(0, size - players.length));
    const order = Ladder.shuffle(players, rnd);
    const out = [];
    let k = 0;
    for (let i = 0; i < pairs; i++) out.push(byeAt.has(i) ? [order[k++], null] : [order[k++], order[k++]]);
    return out;
  },
  // 승패는 활동점수로만 가린다 (운 없음) — 대진은 새로고침마다 바뀌어도 우승자는 늘 활동점수 1위다.
  // 동점이면 산출물 → 활동일 → 이름 순으로 고정해 가린다
  beats(a, b) {
    const ka = [Ladder.score(a), a.a, a.d], kb = [Ladder.score(b), b.a, b.d];
    for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] > kb[i];
    return a.n < b.n;
  },
  duel(a, b) {
    return { pa: Ladder.score(a), pb: Ladder.score(b), w: Ladder.beats(a, b) ? a : b };
  },
  run(players, rnd) {
    rnd = rnd || Ladder.rand;
    let pairs = Ladder.draw(players, rnd);
    const rounds = [];
    for (;;) {
      const hasBye = pairs.some((pr) => !pr[1]);
      const matches = pairs.map(([a, b]) => (b ? Object.assign({ a, b, bye: false }, Ladder.duel(a, b)) : { a, b: null, w: a, bye: true }));
      rounds.push({ name: Ladder.roundName(pairs.length * 2, hasBye), matches });
      const winners = matches.map((m) => m.w);
      if (winners.length === 1) return { rounds, champion: winners[0] };
      pairs = [];
      for (let i = 0; i < winners.length; i += 2) pairs.push([winners[i], winners[i + 1]]);
    }
  },
  // 주간 활동 순위 — 같은 점수는 같은 순위
  ranks(players) {
    const s = players.map((p) => Ladder.score(p));
    return s.map((v) => 1 + s.filter((x) => x > v).length);
  },
};

if (typeof document !== 'undefined') (function () {
  const box = document.getElementById('battle');
  const src = document.getElementById('battle-data');
  if (!box || !src) return;
  let data;
  try { data = JSON.parse(src.textContent); } catch (e) { return; }
  const isNum = (v) => typeof v === 'number' && isFinite(v) && v >= 0;
  const str = (v) => (typeof v === 'string' ? v : '');
  const players = (Array.isArray(data.players) ? data.players : [])
    .filter((p) => p && typeof p.n === 'string' && p.n && isNum(p.u) && isNum(p.a) && isNum(p.d))
    .map((p, i) => Object.assign({}, p, { i }));
  if (players.length < 2) return;
  Ladder.ranks(players).forEach((r, i) => { players[i].rank = r; });

  const NS = 'http://www.w3.org/2000/svg';
  const COL = 100, STEP = 74, TOP = 36, GUT = 80;  // viewBox 단위 — 한 사람 열 폭 100
  const INK = '#1C1512';
  const reduce = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const champBox = box.querySelector('.bt-champ');
  const who = box.querySelector('.bt-who');
  const scroller = box.querySelector('.bt-scroll');
  const board = box.querySelector('.bt-board');
  const log = box.querySelector('.bt-rounds');
  const again = box.querySelector('.bt-again');
  const swipe = box.querySelector('.bt-swipe');
  let timers = [];

  // 머리 장식 — 마커·축마다 하나씩. 캐릭터 좌표(viewBox 48×56)에서 머리 꼭대기는 y=19
  const HATS = {
    wizard: [['path', { d: 'M11 23L25 0L37 23Z', fill: '#6B4FD8' }], ['ellipse', { cx: 24, cy: 23, rx: 15, ry: 3.2, fill: '#5139B0' }],
      ['circle', { cx: 26, cy: 12, r: 1.9, fill: '#FFD84D' }]],
    helm: [['path', { d: 'M8 28Q8 13 24 13Q40 13 40 28H35V24H13V28Z', fill: '#A7B0BA' }],
      ['path', { d: 'M24 13Q26 3 35 3', fill: 'none', stroke: '#E8431B', 'stroke-width': 3.2, 'stroke-linecap': 'round' }]],
    crest: [['path', { d: 'M9 26Q9 14 24 14Q39 14 39 26Z', fill: '#E0B43A' }],
      ['path', { d: 'M14 15Q24 -1 34 15', fill: 'none', stroke: '#D2241A', 'stroke-width': 4, 'stroke-linecap': 'round' }]],
    detective: [['path', { d: 'M15 22L17 9Q24 6 31 9L33 22Z', fill: '#8B6B47' }], ['rect', { x: 16, y: 17, width: 16, height: 3, fill: '#4A3523' }],
      ['ellipse', { cx: 24, cy: 22, rx: 16, ry: 3, fill: '#6E5235' }]],
    antenna: [['path', { d: 'M24 20V9', stroke: '#2B1D16', 'stroke-width': 2 }],
      ['path', { d: 'M27 0L19 9H24L21 16L31 6H26L29 0Z', fill: '#FFC21A', stroke: '#B07800', 'stroke-width': 0.8 }]],
    party: [['path', { d: 'M17 22L25 2L32 21Z', fill: '#F9308E' }], ['path', { d: 'M20.5 14L29.5 13M22.6 8.5L27.4 8', stroke: '#fff', 'stroke-width': 2 }],
      ['circle', { cx: 25, cy: 2.8, r: 2.8, fill: '#FFC21A' }]],
    flower: [['circle', { cx: 32, cy: 11, r: 3.4, fill: '#FF8FC4' }], ['circle', { cx: 37, cy: 15, r: 3.4, fill: '#FF8FC4' }],
      ['circle', { cx: 35, cy: 20.5, r: 3.4, fill: '#FF8FC4' }], ['circle', { cx: 29, cy: 20.5, r: 3.4, fill: '#FF8FC4' }],
      ['circle', { cx: 27.2, cy: 15, r: 3.4, fill: '#FF8FC4' }], ['circle', { cx: 32, cy: 16.3, r: 2.6, fill: '#FFC21A' }]],
    band: [['path', { d: 'M8.5 28Q24 21 39.5 28V31.5Q24 24.5 8.5 31.5Z', fill: '#2A7FD0' }],
      ['path', { d: 'M39 28.5Q43 25 47 27.5Q43 28.5 42 32Z', fill: '#2A7FD0' }]],
    nightcap: [['path', { d: 'M9 25Q13 6 31 8Q42 10 44 21Q36 14 39 25Z', fill: '#4B5BA8' }], ['circle', { cx: 44, cy: 21.5, r: 3, fill: '#fff' }],
      ['path', { d: 'M9.5 25H38.5', stroke: '#fff', 'stroke-width': 3, 'stroke-linecap': 'round' }]],
    tophat: [['rect', { x: 16, y: 3, width: 16, height: 18, rx: 1.5, fill: '#2B2B33' }], ['rect', { x: 16, y: 15, width: 16, height: 3, fill: '#D2241A' }],
      ['rect', { x: 10, y: 20, width: 28, height: 3.4, rx: 1.7, fill: '#2B2B33' }]],
    halo: [['ellipse', { cx: 24, cy: 9, rx: 10, ry: 3, fill: 'none', stroke: '#FFC21A', 'stroke-width': 2.8 }],
      ['path', { d: 'M8 30Q2 24 5 18Q9 23 11 26Z', fill: '#fff', stroke: '#C9BDB4', 'stroke-width': 0.8 }],
      ['path', { d: 'M40 30Q46 24 43 18Q39 23 37 26Z', fill: '#fff', stroke: '#C9BDB4', 'stroke-width': 0.8 }]],
    captain: [['path', { d: 'M11 23Q11 10 24 10Q37 10 37 23Z', fill: '#1F3A68' }], ['path', { d: 'M9 23H39Q36 27.5 24 27.5Q12 27.5 9 23Z', fill: '#15294A' }],
      ['circle', { cx: 24, cy: 16.5, r: 2.7, fill: '#FFC21A' }]],
    beret: [['ellipse', { cx: 21, cy: 20, rx: 14, ry: 5, fill: '#C2185B' }],
      ['path', { d: 'M21 15V10.5', stroke: '#C2185B', 'stroke-width': 2.4, 'stroke-linecap': 'round' }]],
    mortar: [['path', { d: 'M14 17V24H34V17Z', fill: '#2B2B33' }], ['path', { d: 'M5 14.5L24 7L43 14.5L24 22Z', fill: '#3A3A46' }],
      ['path', { d: 'M38 14V25', stroke: '#FFC21A', 'stroke-width': 1.6 }], ['circle', { cx: 38, cy: 26, r: 2, fill: '#FFC21A' }]],
    goggles: [['path', { d: 'M8 26Q24 19 40 26', fill: 'none', stroke: '#5A4636', 'stroke-width': 3 }],
      ['circle', { cx: 18, cy: 23.5, r: 4.6, fill: '#9AD4F5', stroke: '#5A4636', 'stroke-width': 2 }],
      ['circle', { cx: 30, cy: 23.5, r: 4.6, fill: '#9AD4F5', stroke: '#5A4636', 'stroke-width': 2 }]],
    explorer: [['path', { d: 'M12 23Q12 8 24 8Q36 8 36 23Z', fill: '#D8C08A' }], ['rect', { x: 12, y: 18.5, width: 24, height: 3, fill: '#8B6B47' }],
      ['ellipse', { cx: 24, cy: 23, rx: 17, ry: 2.8, fill: '#BFA163' }]],
    sprout: [['path', { d: 'M24 20V11', stroke: '#3F9E5A', 'stroke-width': 2.2, 'stroke-linecap': 'round' }],
      ['ellipse', { cx: 19.5, cy: 10, rx: 5, ry: 2.8, fill: '#5CC27A', transform: 'rotate(-25 19.5 10)' }],
      ['ellipse', { cx: 28.5, cy: 10, rx: 5, ry: 2.8, fill: '#5CC27A', transform: 'rotate(25 28.5 10)' }]],
  };
  const own = (o, k) => typeof k === 'string' && Object.prototype.hasOwnProperty.call(o, k);

  function el(tag, cls, txt) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt != null) e.textContent = String(txt);
    return e;
  }
  function sv(tag, attrs) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, String(attrs[k]));
    return e;
  }
  // 상세 페이지는 사이트 안 상대경로(m/이름.html)만 잇는다
  function safeHref(h) { return typeof h === 'string' && /^m\/[A-Za-z0-9%._~-]+\.html$/.test(h) ? h : ''; }
  function shortName(n) { return n.length === 3 ? n.slice(1) : n; }  // 좁은 폭 — 성 뺀 두 글자
  function pct(v, of) { return (v / of * 100).toFixed(3) + '%'; }
  function later(fn, ms) { if (reduce) fn(); else timers.push(setTimeout(fn, ms)); }

  function eyes(s, kind) {
    [18.5, 29.5].forEach((x, k) => {
      if (kind === 'smile' || kind === 'sleepy') {  // ^^ 눈 · 지그시 감은 눈
        const d = kind === 'smile' ? 'M' + (x - 3.6) + ' 36.5q3.6 -4.8 7.2 0' : 'M' + (x - 3.6) + ' 34q3.6 3.8 7.2 0';
        s.appendChild(sv('path', { d, fill: 'none', stroke: INK, 'stroke-width': 2.3, 'stroke-linecap': 'round' }));
        return;
      }
      s.appendChild(sv('circle', { cx: x, cy: 35, r: kind === 'wide' ? 5.2 : 4.3, fill: '#fff' }));
      s.appendChild(sv('circle', { cx: x + 0.7, cy: 35.6, r: kind === 'sparkle' ? 3 : kind === 'wide' ? 1.9 : 2.3, fill: INK }));
      if (kind === 'sparkle') s.appendChild(sv('circle', { cx: x + 1.8, cy: 34.2, r: 1.1, fill: '#fff' }));
      if (kind === 'glasses') s.appendChild(sv('circle', { cx: x, cy: 35, r: 5.6, fill: 'none', stroke: INK, 'stroke-width': 1.7 }));
      if (kind === 'sharp') s.appendChild(sv('path', { d: k ? 'M' + (x + 4.6) + ' 28.4l-8 2.6' : 'M' + (x - 4.6) + ' 28.4l8 2.6', stroke: INK, 'stroke-width': 2.3, 'stroke-linecap': 'round' }));
    });
  }

  // 캐릭터 — 색 몸 + 눈(배지·축) + 모자(직업) + 손에 든 상징 이모지(오른쪽 아래)
  function face(p) {
    const c = Number.isInteger(p.c) && p.c >= 0 && p.c < 10 ? p.c : 0;
    const s = sv('svg', { viewBox: '0 0 48 56', class: 'ch bt-c' + c, 'aria-hidden': 'true' });
    s.appendChild(sv('ellipse', { cx: 24, cy: 53.5, rx: 12, ry: 2.3, fill: 'currentColor', opacity: 0.12 }));
    s.appendChild(sv('circle', { class: 'bt-bd', cx: 24, cy: 35, r: 16 }));
    s.appendChild(sv('ellipse', { cx: 17.5, cy: 26.5, rx: 5.5, ry: 3, fill: '#fff', opacity: 0.35 }));
    const kind = str(p.y);
    eyes(s, kind);
    s.appendChild(sv('circle', { class: 'bt-ck', cx: 13.5, cy: 41, r: 2.6 }));
    s.appendChild(sv('circle', { class: 'bt-ck', cx: 34.5, cy: 41, r: 2.6 }));
    if (kind === 'wide') s.appendChild(sv('ellipse', { cx: 24, cy: 43.2, rx: 2.4, ry: 2.8, fill: INK }));
    else if (kind === 'smile') s.appendChild(sv('path', { d: 'M19.5 41.2h9q-4.5 6.4 -9 0Z', fill: INK }));
    else s.appendChild(sv('path', { class: 'bt-mo', d: 'M20 42.2q4 3.8 8 0' }));
    (own(HATS, p.x) ? HATS[p.x] : HATS.sprout).forEach(([tag, attrs]) => s.appendChild(sv(tag, attrs)));
    const prop = sv('text', { x: 40, y: 54, 'font-size': 17, 'text-anchor': 'middle' });
    prop.textContent = str(p.e) || '⭐';
    s.appendChild(prop);
    return s;
  }

  function tile(p) {
    const t = el('div', 'bt-tile');
    t.tabIndex = 0;
    const tip = [str(p.k), str(p.t) || str(p.j)].filter(Boolean).join(' · ');
    if (tip) t.setAttribute('title', tip);
    const inn = el('div', 'bt-in');
    inn.appendChild(face(p));
    const nm = el('div', 'bt-name');
    nm.appendChild(el('span', 'bt-full', p.n));
    nm.appendChild(el('span', 'bt-short', shortName(p.n)));
    inn.appendChild(nm);
    if (str(p.j)) inn.appendChild(el('div', 'bt-job', p.j));
    inn.appendChild(el('div', 'bt-rank', '주간 활동 ' + p.rank + '위'));
    const ic = el('div', 'bt-icons');
    ic.setAttribute('title', '발화 ' + p.u + '건 · 산출물 ' + p.a + '건 · 활동 ' + p.d + '일');
    ic.appendChild(el('span', '', '💬' + p.u));
    ic.appendChild(el('span', '', '📎' + p.a));
    ic.appendChild(el('span', '', '📅' + p.d));
    inn.appendChild(ic);
    t.appendChild(inn);
    const st = el('div', 'bt-stats');
    st.appendChild(el('b', '', str(p.e) + ' ' + (str(p.t) || str(p.j) || p.n)));
    const stats = (Array.isArray(p.s) ? p.s : []).filter((x) => Array.isArray(x) && typeof x[0] === 'string' && isNum(x[1]));
    stats.forEach(([label, v]) => {
      const n = Math.max(1, Math.min(5, Math.round(v)));
      const row = el('span', '', label);
      row.appendChild(el('i', '', '●'.repeat(n) + '○'.repeat(5 - n)));
      st.appendChild(row);
    });
    if (!stats.length) st.appendChild(el('span', '', '스탯은 아직 수집 중'));
    t.appendChild(st);
    t.appendChild(el('span', 'bt-stamp', '탈락'));
    return t;
  }

  function basis(p) { return p.n + ' 발화 ' + p.u + '·산출물 ' + p.a + '·활동 ' + p.d + '일'; }

  function render() {
    timers.forEach(clearTimeout);
    timers = [];
    const res = Ladder.run(players);
    const R = res.rounds.length;
    const leaves = [];
    res.rounds[0].matches.forEach((m) => { leaves.push(m.a); if (m.b) leaves.push(m.b); });
    const n = leaves.length, W = GUT + n * COL, H = TOP + R * STEP, cx = GUT + n * COL / 2;
    const levelY = (r) => TOP + (R - r) * STEP;

    board.replaceChildren();
    board.style.setProperty('--n', String(n));
    const fin = el('div');
    fin.style.paddingLeft = pct(GUT, W);
    fin.appendChild(el('div', 'bt-final', '결승전'));
    board.appendChild(fin);
    const region = el('div', 'bt-region');
    const svg = sv('svg', { viewBox: '0 0 ' + W + ' ' + H, 'aria-hidden': 'true' });
    const ghosts = sv('g', {}), lines = sv('g', {});
    svg.appendChild(ghosts);
    svg.appendChild(lines);
    region.appendChild(svg);
    board.appendChild(region);
    const tiles = el('div', 'bt-tiles');
    tiles.style.paddingLeft = pct(GUT, W);
    board.appendChild(tiles);

    // 꺾쇠 선 한 줄 = 한 사람이 한 경기 분기점까지 올라가는 길. 회색 바탕선 위에 결과 선을 덧그린다
    function seg(d, cls) {
      ghosts.appendChild(sv('path', { d, class: 'bt-ghost' }));
      const s = sv('path', { d, pathLength: 1, class: 'bt-seg ' + cls });
      lines.appendChild(s);
      return s;
    }
    const pos = {}, parts = {};
    leaves.forEach((p, k) => {
      pos[p.i] = { x: GUT + k * COL + COL / 2, y: H };
      const t = tile(p);
      parts[p.i] = { tile: t, minis: [] };
      tiles.appendChild(t);
    });
    const steps = [];
    res.rounds.forEach((rd, ri) => {
      const r = ri + 1, y = levelY(r);
      if (r < R) {
        const lv = el('div', 'bt-lvl', rd.name);
        lv.style.left = pct(GUT / 2, W);
        lv.style.top = pct(y, H);
        region.appendChild(lv);
      }
      const drawn = [], reveal = [];
      rd.matches.forEach((m) => {
        const pa = pos[m.a.i];
        if (m.bye) {  // 부전승 — 가로대 없이 곧장 다음 라운드 높이로
          drawn.push(seg('M' + pa.x + ' ' + pa.y + 'V' + y, 'win'));
          pos[m.a.i] = { x: pa.x, y };
          return;
        }
        const pb = pos[m.b.i];
        const x = (pa.x + pb.x) / 2;
        const loser = m.w === m.a ? m.b : m.a;
        drawn.push(seg('M' + pa.x + ' ' + pa.y + 'V' + y + 'H' + x, m.w === m.a ? 'win' : 'lose'));
        drawn.push(seg('M' + pb.x + ' ' + pb.y + 'V' + y + 'H' + x, m.w === m.b ? 'win' : 'lose'));
        const mini = el('div', 'bt-mini');
        mini.style.left = pct(x, W);
        mini.style.top = pct(y, H);
        mini.setAttribute('title', m.w.n);
        mini.appendChild(face(m.w));
        region.appendChild(mini);
        parts[m.w.i].minis.push(mini);
        reveal.push(() => {
          mini.classList.add('bt-on');
          parts[loser.i].tile.classList.add('out');
          parts[loser.i].minis.forEach((e) => e.classList.add('out'));
        });
        pos[m.w.i] = { x, y };
      });
      steps.push({ drawn, reveal });
    });
    const champ = res.champion, pc = pos[champ.i];
    const crown = seg('M' + pc.x + ' ' + pc.y + 'V' + TOP / 2 + 'H' + cx + 'V0', 'top');

    champBox.classList.remove('bt-on');
    const nameBox = el('div');
    const cname = el('div', 'bt-cname');
    const href = safeHref(champ.h);
    if (href) {
      const a = el('a', '', champ.n);
      a.setAttribute('href', href);
      cname.appendChild(a);
    } else {
      cname.textContent = champ.n;
    }
    nameBox.appendChild(cname);
    const title = [str(champ.e), str(champ.t) || str(champ.j)].filter(Boolean).join(' ');
    nameBox.appendChild(el('div', 'bt-cline', title + ' · 끝까지 올라온 한 주, 수고 많았어요 👏'));
    who.replaceChildren(face(champ), nameBox);

    log.replaceChildren();
    res.rounds.forEach((rd) => {
      const blk = el('div', 'bt-rd');
      blk.appendChild(el('div', 'bt-rdname', rd.name));
      const ul = el('ul', 'bt-mlist');
      rd.matches.forEach((m) => {
        const li = el('li');
        if (m.bye) {
          li.appendChild(el('span', 'w', m.a.n));
          li.appendChild(el('span', 'l', '부전승'));
          li.appendChild(el('small', '', basis(m.a)));
        } else {
          const aw = m.w === m.a;
          li.appendChild(el('span', aw ? 'w' : 'l', m.a.n + ' ' + m.pa));
          li.appendChild(el('span', 'vs', '⚔'));
          li.appendChild(el('span', aw ? 'l' : 'w', m.pb + ' ' + m.b.n));
          li.appendChild(el('small', '', basis(m.a) + '  vs  ' + basis(m.b)));
        }
        ul.appendChild(li);
      });
      blk.appendChild(ul);
      log.appendChild(blk);
    });

    // 라운드마다 아래→위: 선이 올라가고, 잠시 뒤 진 쪽에 탈락 도장 · 이긴 쪽 미니 캐릭터가 분기점에 선다
    let t = 300;
    steps.forEach((st) => {
      later(() => st.drawn.forEach((s) => s.classList.add('bt-on')), t);
      later(() => st.reveal.forEach((f) => f()), t + 600);
      t += 1100;
    });
    later(() => {
      crown.classList.add('bt-on');
      parts[champ.i].tile.classList.add('champ');
      champBox.classList.add('bt-on');
    }, t);

    requestAnimationFrame(() => {
      const over = scroller.scrollWidth - scroller.clientWidth;
      scroller.scrollLeft = over > 0 ? over / 2 : 0;
      if (swipe) swipe.textContent = over > 2 ? '대진표는 옆으로 밀어 볼 수 있어요' : '';
    });
  }

  again.hidden = false;
  again.addEventListener('click', render);
  render();
})();
"""


def battle_axis_order(d: dict[str, Any]) -> list[str]:
    """profile.axes 의 축 키를 높은 값 순으로. 동점이면 BATTLE_AXES 순서 (정렬이 안정적이라 그대로 지켜진다)."""
    axes = sub(sub(d, "profile"), "axes")
    keys = [row[0] for row in BATTLE_AXES if row[0] in axes]
    return sorted(keys, key=lambda k: -num(axes.get(k)))


def battle_hero(d: dict[str, Any], taken: set[str] | frozenset[str] = frozenset()) -> dict[str, str]:
    """캐릭터 — e 상징 이모지 · j 직업 · t 수식어 붙인 전체 직업 · x 모자 · y 눈.

    직업은 말투 배지 마커로, 배지가 없으면 최고 축으로, 둘 다 없으면 신입 모험가.
    수식어는 최고 축에서 붙이되 직업과 같은 뜻의 축이면 두 번째 축으로 — 같은 직업끼리 겹치지 않게.
    그래도 앞 참가자(`taken`)와 전체 직업이 같으면 다음 축 수식어로 넘어간다.
    """
    badge = sub(sub(d, "signals"), "badge")
    marker, quiet = text(badge.get("marker")), text(badge.get("kind")) == "안씀"
    order = battle_axis_order(d)
    axes = {row[0]: row for row in BATTLE_AXES}
    if marker in BATTLE_HEROES:
        emoji, many, few, hat, job_axis = BATTLE_HEROES[marker]
        job = few if quiet else many
    elif order:
        job_axis = order[0]
        emoji, job, hat = axes[job_axis][3:6]
    else:
        (emoji, job, hat), job_axis = BATTLE_DEFAULT_HERO, ""
    mods = [k for k in order if k != job_axis]
    titles = [f"{axes[k][2]} {job}" for k in mods] or [job]
    title = next((t for t in titles if t not in taken), titles[0])
    shown = "" if quiet else marker  # 안 쓰는 배지는 표정에 반영하지 않는다
    if shown in ("웃음ㅋ", "웃음ㅎ"):
        eye = "smile"
    elif order and order[0] == "verification":
        eye = "glasses"
    elif order and order[0] == "exploration":
        eye = "sparkle"
    else:
        eye = BATTLE_EYES.get(shown, "dot")
    return {"e": emoji, "j": job, "t": title, "x": hat, "y": eye}


def battle_colors(names: list[str]) -> dict[str, int]:
    """몸 색 번호. 이름 해시에서 시작해 이미 쓴 색은 건너뛴다 — 참가자끼리 최대한 겹치지 않게, 이름 순이라 결정적이다."""
    used: set[int] = set()
    out: dict[str, int] = {}
    for name in sorted(names):
        if len(used) == BATTLE_PALETTE:  # 팔레트보다 많으면 한 바퀴 돌고 다시
            used = set()
        i = sum(ord(ch) for ch in name) % BATTLE_PALETTE
        while i in used:
            i = (i + 1) % BATTLE_PALETTE
        used.add(i)
        out[name] = i
    return out


def battle_icons(primary: dict[str, str]) -> dict[str, str]:
    """이름 → 상징 이모지. 이름 순으로 자기 이모지 → 같은 테마 대체 → 예비 순으로 아직 안 쓴 것을 고른다.

    몸 색처럼 참가자끼리 겹치지 않게, 이름 순이라 결정적이다. 후보가 바닥나면 자기 이모지를 그대로 쓴다.
    """
    used: set[str] = set()
    out: dict[str, str] = {}
    for name in sorted(primary):
        own = primary[name]
        pick = next((e for e in (own, *BATTLE_ICON_ALTS.get(own, ()), *BATTLE_ICON_SPARE) if e not in used), own)
        used.add(pick)
        out[name] = pick
    return out


def battle_week(d: dict[str, Any]) -> dict[str, float] | None:
    """signals.week 의 발화·산출물·활동일. 하나라도 없거나 0 이상 숫자가 아니면 None — 결투에서 뺀다."""
    week = sub(sub(d, "signals"), "week")
    out: dict[str, float] = {}
    for key, short in BATTLE_WEEK_KEYS:
        v = week.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            return None
        out[short] = int(v) if float(v).is_integer() else v
    return out


def battle_section(profiles: list[dict[str, Any]], member_hrefs: dict[str, str]) -> str:
    """활동 월드컵 — 최근 7일 활동점수로 붙는 토너먼트. 맨 위 재미 섹션 (2026-10-15).
    승패는 활동점수로만 가린다(운 없음, 2026-10-15 결정) — 대진은 로드마다 바뀌어도 우승자는 늘 1위다.

    HTML 에는 빈 대진표 틀과 참가자 JSON 만 싣고, 대진·경기는 브라우저가 로드마다 새로 돌린다.
    JSON 에는 이름·주간 숫자·상세 링크·캐릭터(공개된 배지·5축·별명에서 고른 값)·몸 색 번호만 싣는다.
    week 가 있는 사람이 2명 미만이면 섹션을 그리지 않는다.
    """
    entrants = [(d, battle_week(d)) for d in profiles if text(d.get("name"))]
    entrants = [(d, w) for d, w in entrants if w is not None]
    if len(entrants) < 2:
        return ""
    colors = battle_colors([text(d.get("name")) for d, _ in entrants])
    players: list[dict[str, Any]] = []
    taken: set[str] = set()
    for d, week in entrants:
        name = text(d.get("name"))
        fun = d.get("fun") if isinstance(d.get("fun"), dict) else {}
        axes = sub(sub(d, "profile"), "axes")
        stats = [[row[1], int(round(max(1.0, min(5.0, num(axes.get(row[0]))))))] for row in BATTLE_AXES if row[0] in axes]
        hero = battle_hero(d, taken)
        taken.add(hero["t"])
        players.append({
            "n": name, **week, "h": member_hrefs.get(name, ""), **hero,
            "k": text(fun.get("nickname")), "c": colors[name], "s": stats,
        })
    icons = battle_icons({p["n"]: p["e"] for p in players})
    for p in players:
        p["e"] = icons[p["n"]]
    week0 = sub(sub(entrants[0][0], "signals"), "week")
    span = (text(week0.get("from")), text(week0.get("to")))
    blob = json.dumps({"from": span[0], "to": span[1], "players": players}, ensure_ascii=False)
    # </script> 탈출 방지 — JSON 안의 < > & 를 유니코드 이스케이프로
    blob = blob.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    period = f"최근 7일({day_ko_str(span[0])}~{day_ko_str(span[1])})" if span[0] and span[1] else "최근 7일"
    body = (
        '<div class="btwrap" id="battle">'
        '<div class="bt-champ" aria-live="polite"><div class="bt-trophy" aria-hidden="true">🏆</div>'
        '<div class="bt-kicker">이번 주 활동왕</div><div class="bt-wait">대진 추첨 중…</div><div class="bt-who"></div></div>'
        '<div class="bt-scroll"><div class="bt-board"></div></div>'
        '<div class="bt-foot"><button type="button" class="bt-again" hidden>다시 붙기 🔄</button>'
        '<span class="bt-hint">활동점수 = 수다(발화) + 5×산출물 + 10×활동일 · 점수가 높은 쪽이 이긴다 · 타일에 올리거나 누르면 RPG 스탯</span>'
        '<span class="bt-hint bt-swipe"></span></div>'
        '<details class="bt-log"><summary>경기 기록 · 점수</summary><div class="bt-rounds"></div></details>'
        '<noscript><p class="empty">자바스크립트를 켜야 대진표가 그려진다.</p></noscript>'
        "</div>"
        f'<script type="application/json" id="battle-data">{blob}</script>'
        f"<script>{BATTLE_JS}</script>"
    )
    subtitle = f"{period} 활동점수 · 새로고침하면 대진만 바뀌고 우승자는 그대로 · 재미로만, 평가와 무관"
    return sec("sec-battle", "재미 · 활동점수", "이번 주 활동 월드컵", body, subtitle)


# ───────────────────────────────────────────────────────────────────── 목록·빌드


def render_index(
    profiles: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    skipped: list[Card],
    part: str,
    built: str,
    generated: str,
    schedule: dict[str, Any] | None = None,
    changelog: dict[str, Any] | None = None,
    today: date | None = None,
    daily: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
) -> str:
    today = today or datetime.now(KST).date()
    member_hrefs = {text(d.get("name")): href_for("m", d) for d in profiles}
    project_hrefs = {text(d.get("name")): href_for("p", d) for d in projects}
    chips = [f'<span class="chip"><b>멤버</b>{len(profiles)}명</span>', f'<span class="chip"><b>프로젝트</b>{len(projects)}건</span>']
    near = upcoming_count(schedule, today)
    if schedule is not None:
        chips.append(f'<span class="chip"><b>임박 일정</b>{near}건</span>')
    if budget is not None:
        left = sum(r["remaining"] for r in budget_rows(budget.get("members")))
        chips.append(f'<span class="chip"><b>네트워킹비 잔액</b>{esc(fmt_int(left))}원</span>')
    chips.append(f'<span class="chip"><b>빌드</b>{esc(built)}</span>')
    if generated:
        chips.append(f'<span class="chip"><b>데이터 기준</b>{esc(generated)}</span>')
    low_n = sum(1 for d in profiles if text(sub(d, "signals").get("confidence")) == "낮음")
    if low_n:
        chips.append(f'<span class="chip"><b>표본 부족</b>{low_n}명</span>')
    hero = (
        '<header class="hero"><div class="in"><div class="hero-eyebrow">활동 월드컵 · 업무 요약 · 파트 일정 · 프로젝트 · 멤버 · 네트워킹비</div>'
        f'<h1 class="hero-title">{esc(part)}</h1>'
        '<div class="hero-sub">동기화 때 정리한 업무 요약과 파트 일정, 진행 중인 과제 요약, 파트 텔레그램 방에서 집계한 말투 신호로 만든 파트원 카드, 사람별 네트워킹비 잔액. '
        "위키 본문과 원본 대화는 여기에 실리지 않는다.</div>"
        f'<div class="hero-chips">{"".join(chips)}</div></div></header>'
    )
    if profiles:
        cards = f'<div class="cards">{"".join(member_card(d, href_for("m", d)) for d in profiles)}</div>'
    else:
        cards = card("", '<p class="empty" style="margin:0">아직 프로필 카드가 없다. 동기화로 신호가 쌓인 뒤 salesplus-wiki 의 data/profiles/ 에 채운다.</p>')
    s_members = sec("sec-members", "관측 신호", "멤버", cards, "카드를 누르면 상세로")
    if projects:
        pcards = f'<div class="pcards">{"".join(project_card(d, href_for("p", d)) for d in projects)}</div>'
    else:
        pcards = card("", '<p class="empty" style="margin:0">아직 프로젝트 카드가 없다. salesplus-wiki 의 data/projects/ 에 채운다.</p>')
    s_projects = '<div id="projects"></div>' + sec("sec-proj", "위키 요약", "프로젝트", pcards, "진행률은 마일스톤 완료 수 · 확정 계획이 아니다")
    # 순서는 활동 월드컵(재미) → 오늘 업무 요약 → 파트 일정(오늘·내일) → 프로젝트 → 멤버 → 네트워킹비 → 최근 변경
    # (2026-09-17 결정, 창은 2026-09-22, 네트워킹비는 개인카드 아래에 2026-09-22 추가, 활동 월드컵은 맨 위에 2026-10-15 추가)
    s_battle = battle_section(profiles, member_hrefs)
    s_daily = daily_section(daily, today)
    s_budget = budget_section(budget, member_hrefs)
    s_chg = changelog_section(changelog, today, member_hrefs, project_hrefs)
    s_sched = schedule_section(schedule, today, member_hrefs)
    skips = ""
    if skipped:
        items = "".join(f'<li><b>{esc(c.file)}</b> — {esc(c.errors[0] if c.errors else "빈 파일")}{esc(f" (외 {len(c.errors) - 1}건)" if len(c.errors) > 1 else "")}</li>' for c in skipped)
        skips = (
            f'<div class="warnbox skips"><h3>검증에서 건너뛴 파일 {len(skipped)}건</h3><ul>{items}</ul>'
            '<p style="margin-top:9px">한 건 때문에 전체가 막히지 않도록 그 파일만 빼고 빌드했다. 고치면 다음 빌드에 다시 들어온다.</p></div>'
        )
    body = hero + banner_html() + '<main class="wrap">' + s_battle + s_daily + s_sched + s_projects + s_members + s_budget + s_chg + skips + footer_html(part, built) + "</main>"
    return html_doc(f"{part} 멤버 프로필 · 프로젝트", body)


def href_for(prefix: str, d: dict[str, Any]) -> str:
    return f"{prefix}/{quote(text(d.get('name')))}.html"


def _project_key(d: dict[str, Any]) -> tuple[int, float, str]:
    return PROJECT_STATUS_ORDER.get(text(d.get("status")), 9), num(d.get("order"), 100), text(d.get("name"))


def build(cards: list[Card], out_dir: Path) -> None:
    profiles = sorted([c.data for c in cards if c.ok and c.kind == "profile" and c.data is not None], key=lambda d: text(d.get("name")))
    projects = sorted([c.data for c in cards if c.ok and c.kind == "project" and c.data is not None], key=_project_key)
    schedule = next((c.data for c in cards if c.ok and c.kind == "schedule" and c.data is not None), None)
    changelog = next((c.data for c in cards if c.ok and c.kind == "changelog" and c.data is not None), None)
    daily = next((c.data for c in cards if c.ok and c.kind == "daily" and c.data is not None), None)
    budget = next((c.data for c in cards if c.ok and c.kind == "budget" and c.data is not None), None)
    skipped = [c for c in cards if not c.ok]
    part = next((text(d.get("part")) for d in profiles + projects if text(d.get("part"))), DEFAULT_PART)
    singles = [d for d in (daily, schedule, changelog, budget) if d is not None]
    generated = max((text(d.get("generated")) for d in profiles + projects + singles), default="")
    now = datetime.now(KST)
    built = now.strftime("%Y-%m-%d %H:%M KST")

    member_hrefs = {text(d.get("name")): href_for("m", d) for d in profiles}
    (out_dir / "m").mkdir(parents=True, exist_ok=True)
    (out_dir / "p").mkdir(parents=True, exist_ok=True)
    for d in profiles:
        (out_dir / "m" / f"{text(d.get('name'))}.html").write_text(render_person(d, built), encoding="utf-8")
    for d in projects:
        (out_dir / "p" / f"{text(d.get('name'))}.html").write_text(render_project(d, built, member_hrefs), encoding="utf-8")
    index = render_index(profiles, projects, skipped, part, built, generated, schedule, changelog, now.date(), daily, budget)
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")  # Jekyll 후처리 방지
    near = upcoming_count(schedule, now.date())
    print(
        f"{out_dir}/index.html 생성 — 멤버 {len(profiles)}명 · 프로젝트 {len(projects)}건 · "
        f"임박 일정 {near}건 · 네트워킹비 {'있음' if budget is not None else '없음'} · 건너뜀 {len(skipped)}건",
        file=sys.stderr,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="salesplus-wiki 카드 JSON 으로 GitHub Pages 정적 사이트를 만든다")
    ap.add_argument("--out", default="_site", metavar="DIR", help="빌드 결과 디렉터리 (기본 _site)")
    ap.add_argument("--local", metavar="DIR", help="salesplus-wiki 로컬 클론 경로 (API 대신 파일을 읽는다)")
    args = ap.parse_args()

    org = os.environ.get("ORG", "sales-part-poc-project")
    repo = os.environ.get("WIKI_REPO", "salesplus-wiki")
    ref = os.environ.get("WIKI_REF", "main")

    if args.local:
        cards = load_local(Path(args.local))
    else:
        token = os.environ.get("GH_TOKEN")
        if not token:
            raise SystemExit("GH_TOKEN 이 없습니다 (ORG_READ_TOKEN). 토큰 없이 확인하려면 --local DIR")
        cards = load_remote(GitHub(token), org, repo, ref)
    for c in cards:
        if not c.ok:
            warn(f"건너뜀 {c.file}: " + " / ".join(c.errors))
    build(cards, Path(args.out))


if __name__ == "__main__":
    main()
