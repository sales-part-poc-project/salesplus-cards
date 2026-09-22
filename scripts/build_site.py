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
"""

from __future__ import annotations

import argparse
import html
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


def upcoming_count(data: dict[str, Any] | None, today: date) -> int:
    """오늘·내일 창의 일정 수 — 파트 일정 섹션과 같은 앵커."""
    return len(events_in_window(data, schedule_anchor(today))) if data is not None else 0


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
) -> str:
    today = today or datetime.now(KST).date()
    member_hrefs = {text(d.get("name")): href_for("m", d) for d in profiles}
    project_hrefs = {text(d.get("name")): href_for("p", d) for d in projects}
    chips = [f'<span class="chip"><b>멤버</b>{len(profiles)}명</span>', f'<span class="chip"><b>프로젝트</b>{len(projects)}건</span>']
    near = upcoming_count(schedule, today)
    if schedule is not None:
        chips.append(f'<span class="chip"><b>임박 일정</b>{near}건</span>')
    chips.append(f'<span class="chip"><b>빌드</b>{esc(built)}</span>')
    if generated:
        chips.append(f'<span class="chip"><b>데이터 기준</b>{esc(generated)}</span>')
    low_n = sum(1 for d in profiles if text(sub(d, "signals").get("confidence")) == "낮음")
    if low_n:
        chips.append(f'<span class="chip"><b>표본 부족</b>{low_n}명</span>')
    hero = (
        '<header class="hero"><div class="in"><div class="hero-eyebrow">업무 요약 · 파트 일정 · 프로젝트 · 멤버</div>'
        f'<h1 class="hero-title">{esc(part)}</h1>'
        '<div class="hero-sub">동기화 때 정리한 업무 요약과 파트 일정, 진행 중인 과제 요약, 파트 텔레그램 방에서 집계한 말투 신호로 만든 파트원 카드. '
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
    # 순서는 오늘 업무 요약 → 파트 일정(오늘·내일) → 프로젝트 → 멤버 → 최근 변경 (2026-09-17 결정, 창은 2026-09-22)
    s_daily = daily_section(daily, today)
    s_chg = changelog_section(changelog, today, member_hrefs, project_hrefs)
    s_sched = schedule_section(schedule, today, member_hrefs)
    skips = ""
    if skipped:
        items = "".join(f'<li><b>{esc(c.file)}</b> — {esc(c.errors[0] if c.errors else "빈 파일")}{esc(f" (외 {len(c.errors) - 1}건)" if len(c.errors) > 1 else "")}</li>' for c in skipped)
        skips = (
            f'<div class="warnbox skips"><h3>검증에서 건너뛴 파일 {len(skipped)}건</h3><ul>{items}</ul>'
            '<p style="margin-top:9px">한 건 때문에 전체가 막히지 않도록 그 파일만 빼고 빌드했다. 고치면 다음 빌드에 다시 들어온다.</p></div>'
        )
    body = hero + banner_html() + '<main class="wrap">' + s_daily + s_sched + s_projects + s_members + s_chg + skips + footer_html(part, built) + "</main>"
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
    skipped = [c for c in cards if not c.ok]
    part = next((text(d.get("part")) for d in profiles + projects if text(d.get("part"))), DEFAULT_PART)
    singles = [d for d in (daily, schedule, changelog) if d is not None]
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
    index = render_index(profiles, projects, skipped, part, built, generated, schedule, changelog, now.date(), daily)
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")  # Jekyll 후처리 방지
    near = upcoming_count(schedule, now.date())
    print(
        f"{out_dir}/index.html 생성 — 멤버 {len(profiles)}명 · 프로젝트 {len(projects)}건 · "
        f"임박 일정 {near}건 · 건너뜀 {len(skipped)}건",
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
