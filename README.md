# salesplus-cards — 세일즈플러스파트 업무 요약 · 일정 · 프로젝트 · 멤버 카드 사이트

**https://sales-part-poc-project.github.io/salesplus-cards/**

[`salesplus-wiki`](https://github.com/sales-part-poc-project/salesplus-wiki)(private) 의 `data/profiles/*.json` · `data/projects/*.json` ·
`data/daily.json` · `data/schedule.json` · `data/changelog.json` · `data/budget.json` 을 읽어
정적 HTML 로 그리고 GitHub Pages 로 배포한다. 이 저장소에는 **코드만** 있고 데이터는 없다 — 카드 내용은 위키 저장소에서 고친다.

> ⚠️ 이 저장소와 사이트는 **public** 이다. 조직 private 저장소의 Pages 는 유료 플랜에서만 되기 때문에 사이트만 따로 뗐다.
> 그래서 위키 본문·대화 원문·조직 저장소 통계는 여기에 절대 싣지 않는다. 검색 엔진 색인은 `noindex` 로 막았지만 링크를 아는 사람은 누구나 본다.

## 어떻게 돌아가나

```
salesplus-wiki/data/profiles/*.json  ─┐
salesplus-wiki/data/projects/*.json  ─┤
salesplus-wiki/data/schedule.json    ─┤→ cards_data.py (검증) → build_site.py → _site/ → GitHub Pages
salesplus-wiki/data/changelog.json   ─┤
salesplus-wiki/data/budget.json      ─┘
```

index 의 순서는 **업무 요약 → 파트 일정 → 프로젝트 → 멤버 → 네트워킹비 → 최근 변경** 이다 (2026-09-16 결정 — 변경사항은 줄 수가 많아 맨 아래로 내렸다.
2026-09-22 — 개인카드 아래에 사람별 네트워킹비 잔액을 붙였다. 위키 `파트예산.md` 의 개별 네트워킹비 표를 `build_budget.py` 가 `data/budget.json` 으로 만든다).
일정의 "업무일 2일" 창은 JSON 에 없고 **빌드 시각 기준으로 여기서 계산한다** — 위키의 `build_schedule.py` ·
`.github-private` 의 `update_cards.py` 와 규칙이 같아야 한다 (`scripts/cards_data.py` 의 `business_window` · `events_in_window`).
기준일이 업무일이면 그날이 첫째 날, 아니면 다음 업무일이 첫째 날이고, 창은 거기서부터 업무일 2일이다 (목 → 목·금, 금 → 금·월, 토 → 월·화).

`.github/workflows/pages.yml` 의 주 트리거는 salesplus-wiki 의 `wiki-data-updated` 신호다 — 위키에 데이터가
늘 들어오지는 않으므로 시간 단위로 긁지 않고 바뀐 순간에만 다시 그린다.
신호가 끊겨도 매일 09:37 KST cron 이 안전망으로 돌고, 수동 실행(Actions → Run workflow)과 `scripts/` 변경 push 로도 돈다.
조직 프로필 README 의 카드 블록(`.github-private`, 조직 멤버 전용)과 같은 데이터·같은 검증 규칙이다.

## 처음 설정 (한 번만)

1. **Secret** — Settings → Secrets and variables → Actions → Secrets 에 `ORG_READ_TOKEN`
   (fine-grained PAT, Resource owner = 조직, 저장소 salesplus-wiki, **Contents: Read**). `.github-private` 에 쓰는 것과 같은 토큰이면 된다.
2. **Pages** — Settings → Pages → Build and deployment → Source = **GitHub Actions**.
3. **Dispatch 토큰** — salesplus-wiki 의 Secret 에 `CARDS_DISPATCH_TOKEN`
   (fine-grained PAT, Resource owner = 조직, 저장소 `.github-private` + `salesplus-cards`, **Contents: Read and write**).
   이 토큰이 데이터 변경을 즉시 반영시킨다. 없거나 만료되면 반영이 최대 하루 늦고, 위키 쪽 워크플로가 노란불로 알린다.

## 무엇이 나가나

- 무엇을 싣고 뺄지는 salesplus-wiki 의 `docs/PRIVACY.md` · `docs/PROFILE_SCHEMA.md` · `docs/PROJECT_SCHEMA.md` · `docs/DAILY_SCHEMA.md` · `docs/SCHEDULE_SCHEMA.md` · `docs/CHANGELOG_SCHEMA.md` · `docs/BUDGET_SCHEMA.md` 가 정한다
- 네트워킹비 카드는 파트원 이름·할당·사용·잔액 숫자뿐이다 — 파트공용비 내역(회식 장소 등)은 위키에만 있다
- 링크·전화번호·이메일·주민번호 형태·원문 인용 키가 있으면 **그 파일만** 건너뛰고 index 하단에 사유를 남긴다
- MBTI·나이대는 추측이라 근거 강도가 붙는다. 본인이 원하면 위키에서 자기 JSON 의 `fun` 을 `null` 로 둔다
- 카드가 0건이어도 사이트는 만들어진다

## e스포츠 전적실 (`esports.html`)

점심 스타크래프트 "별들의 전쟁" 전적과 점심 운동 출석을 그리는 재미 페이지다 (2026-10-16 추가). 위키 카드와 달리
**데이터가 이 저장소의 `esports/records.json` 에 있다** — 업무와 무관한 기록이라 위키에 쌓지 않고, e스포츠 방 사람들이 이 파일만 고쳐 유지보수한다.
`esports/**` 를 push 하면 다시 배포된다.

- **경기를 했으면** `matches` 에 한 회차를 넣고, `games` 에 판마다 `{"win": [...], "lose": [...]}` 한 줄씩. 상대를 모르면 `lose` 는 빈 목록
- **점심마다** `lunch` 에 하루 한 줄 — `gym`·`run`·`star`·`skip` 중 한 곳에 이름을 넣는다. 휴가·병가로 자리에 없던 날은 **아예 적지 않는다**(분모에서 빠진다). 불참 사유는 싣지 않는다
- **새 멤버**는 `members` 에 `id`(방에서 부르는 이름, 성 없이)·`race`·`unit` 등을 넣는다. `unit` 은 `esports/assets/` 의 파일명(확장자 없이)
- 승률·최고/최악의 파트너·천적·출석률은 빌드가 다시 계산한다. 규칙은 `scripts/build_esports.py`, 테스트는 `scripts/test_esports.py`
- 유닛·건물 이미지는 Blizzard Entertainment 의 저작물이다 — 비영리 팬 페이지 용도로만 쓰고 페이지 하단에 고지한다

```bash
python3 scripts/build_esports.py --out _site   # 위키 없이 이 페이지만
```

## 로컬에서 확인

```bash
python3 scripts/build_site.py --local ../salesplus-wiki --out _site   # 옆에 위키 클론이 있을 때, 토큰 불필요
open _site/index.html
```

업무일 창·보관 기간 규칙은 `python3 -m unittest discover -s scripts -p 'test_*.py'` 로 확인한다.
테스트는 `scripts/test_cards_data.py` 에 두고 커밋한다 — 위키·README 빌더와 창 규칙이 어긋나지 않게 지키는 장치다.

표준 라이브러리만 쓴다 (Python 3.12+). `_site/` 는 커밋하지 않는다.
