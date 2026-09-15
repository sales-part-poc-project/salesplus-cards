# salesplus-cards — 세일즈플러스파트 멤버 · 프로젝트 카드 사이트

**https://sales-part-poc-project.github.io/salesplus-cards/**

[`salesplus-wiki`](https://github.com/sales-part-poc-project/salesplus-wiki)(private) 의 `data/profiles/*.json` · `data/projects/*.json` 을 읽어
정적 HTML 로 그리고 GitHub Pages 로 배포한다. 이 저장소에는 **코드만** 있고 데이터는 없다 — 카드 내용은 위키 저장소에서 고친다.

> ⚠️ 이 저장소와 사이트는 **public** 이다. 조직 private 저장소의 Pages 는 유료 플랜에서만 되기 때문에 사이트만 따로 뗐다.
> 그래서 위키 본문·대화 원문·조직 저장소 통계는 여기에 절대 싣지 않는다. 검색 엔진 색인은 `noindex` 로 막았지만 링크를 아는 사람은 누구나 본다.

## 어떻게 돌아가나

```
salesplus-wiki/data/profiles/*.json ─┐
salesplus-wiki/data/projects/*.json ─┴→ cards_data.py (검증) → build_site.py → _site/ → GitHub Pages
```

`.github/workflows/pages.yml` 이 매일 09:20 KST, 수동 실행(Actions → Run workflow), salesplus-wiki 의 `wiki-data-updated` 신호,
`scripts/` 변경 push 때 돈다. 조직 프로필 README 의 카드 블록(`.github-private`, 조직 멤버 전용)과 같은 데이터·같은 검증 규칙이다.

## 처음 설정 (한 번만)

1. **Secret** — Settings → Secrets and variables → Actions → Secrets 에 `ORG_READ_TOKEN`
   (fine-grained PAT, Resource owner = 조직, 저장소 salesplus-wiki, **Contents: Read**). `.github-private` 에 쓰는 것과 같은 토큰이면 된다.
2. **Pages** — Settings → Pages → Build and deployment → Source = **GitHub Actions**.
3. (선택) salesplus-wiki 의 `CARDS_DISPATCH_TOKEN` 에 이 저장소 Contents: Read and write 를 주면 데이터 변경 즉시 반영된다. 없어도 매일 돈다.

## 무엇이 나가나

- 무엇을 싣고 뺄지는 salesplus-wiki 의 `docs/PRIVACY.md` · `docs/PROFILE_SCHEMA.md` · `docs/PROJECT_SCHEMA.md` 가 정한다
- 링크·전화번호·이메일·주민번호 형태·원문 인용 키가 있으면 **그 파일만** 건너뛰고 index 하단에 사유를 남긴다
- MBTI·나이대는 추측이라 근거 강도가 붙는다. 본인이 원하면 위키에서 자기 JSON 의 `fun` 을 `null` 로 둔다
- 카드가 0건이어도 사이트는 만들어진다

## 로컬에서 확인

```bash
python3 scripts/build_site.py --local ../salesplus-wiki --out _site   # 옆에 위키 클론이 있을 때, 토큰 불필요
open _site/index.html
```

표준 라이브러리만 쓴다 (Python 3.12+). `_site/` 는 커밋하지 않는다.
