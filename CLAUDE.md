# CLAUDE.md

세일즈플러스파트 카드 사이트 빌더. 데이터는 `salesplus-wiki/data/` 이고 여기에는 코드만 둔다. 설명은 [README.md](README.md).

- `scripts/cards_data.py` — 적재(`load_local`·`load_remote`)와 스키마 검증. `.github-private/scripts/update_cards.py` 와 같은 규칙이다. **둘을 같이 고친다.**
- `scripts/build_site.py` — HTML 렌더링만. 표준 라이브러리, CSS 인라인, JS·CDN 없음. 모든 카드 문자열은 `esc()` 를 거친다.
- CSS 색 토큰은 파트 로고 실측값이다 — 정본은 `salesplus-wiki/knowhow/파트-브랜드-컬러.md`. 글자에는 `*-ink`(대비 4.5:1 이상), 면·막대에는 원색을 쓴다. 색을 바꾸면 두 곳을 같이 고친다.
- **public 저장소·public 사이트다.** 위키 본문·대화 원문·조직 저장소 통계를 넣는 코드를 쓰지 않는다. 금지 패턴은 `cards_data.py` 의 `FORBIDDEN_*`.
- 한 건이 어긋나면 그 파일만 건너뛴다. 0건이어도 빌드는 성공한다.
- 로컬 확인: `python3 scripts/build_site.py --local ../salesplus-wiki --out _site`.
- 테스트는 `scripts/test_cards_data.py` 에 두고 커밋한다 — 위키·README 빌더와 창 규칙이 어긋나지 않게 지키는 장치다. `python3 -m unittest discover -s scripts -p 'test_*.py'`
- 커밋 메시지는 gitmoji 로 시작한다 (✨ 기능, 🐛 버그, ♻️ 리팩터링, 📝 문서, 🔧 설정).
