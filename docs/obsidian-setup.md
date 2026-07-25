# Obsidian 설정 — 코퍼스를 사람이 읽는 뷰

`va index`·`va wiki`가 만드는 코퍼스는 **순수 마크다운**이다. Obsidian은
그것을 링크·그래프·표로 보는 뷰어일 뿐, 어디에도 필수 의존이 아니다
(`src/video_agent/index.py`: *"Obsidian-compatible, but nothing depends on
Obsidian"*). 설치하지 않아도 `va ingest`부터 `va export`까지 전 흐름과
`va search` 회상이 동작한다.

읽는 경험만 달라진다. 아래는 그 경험을 켜는 최소 절차다.

## 한 번에 설치

```bash
scripts/install.sh --all
```

`--all`은 Obsidian 앱(macOS·Homebrew)과 Media Extended 플러그인까지 포함한다.
무엇을 할지 먼저 보려면 `--dry-run`을 붙이고, vault를 코퍼스 밖에 두려면
`--vault <경로>`를 준다. 기본 vault는 `<repo>/va-out`이다.

개별 옵션은 `scripts/install.sh --help`를 본다.

## 1. Obsidian 설치

| 환경 | 방법 |
|---|---|
| macOS | `brew install --cask obsidian` |
| Windows · Linux · 직접 내려받기 | <https://obsidian.md/download> |

무료이며 계정 없이 로컬 vault만으로 쓸 수 있다.

## 2. vault 열기

코퍼스 루트(`va-out/`)를 **그대로 vault로 연다**. 별도 이관이 필요 없다.

- Obsidian 실행 → *다른 vault 열기* → *폴더를 vault로 열기* → `va-out/` 선택
- 또는 딥링크(경로는 URL 인코딩):

  ```bash
  open "obsidian://open?path=%2Fabsolute%2Fpath%2Fto%2Fva-out"
  ```

`scripts/install.sh`는 마지막에 이 vault의 딥링크를 그대로 출력한다.

`obsidian://open`은 공식 URI 액션이다. 플러그인 페이지를 여는 공식 URI는
없으므로, 플러그인은 아래 3절의 방법으로 설치한다.

## 3. 켜야 할 코어 플러그인

전부 Obsidian 기본 탑재라 따로 받을 것이 없다. `scripts/install.sh`가
`<vault>/.obsidian/core-plugins.json`에 아래만 켜고 **기존 설정은 보존**한다
(변경 전 `.json.bak` 백업).

| 코어 플러그인 | 코퍼스에서 쓰는 곳 |
|---|---|
| `graph` | `va index`가 `.obsidian/graph.json` 표시 프리셋을 쓴다 (`--graph-reset`=표준값 강제 덮어쓰기) |
| `properties` | scene-log·위키 문서의 frontmatter 표시 |
| `backlink` · `outgoing-link` | 체크포인트 ↔ 엔티티 `[[링크]]` 관계 추적 |
| `switcher` · `global-search` | 파일명 회상 — 코퍼스는 영상 제목을 파일명에 싣는다 |
| `file-explorer` | 장르별 폴더 트리 |
| `bases` | frontmatter 스키마를 표로 거르는 코어 DB 뷰 |

`bases`는 Obsidian 1.9+ 코어 기능이다. 코퍼스가 frontmatter 키를 빈 값이라도
항상 넣는 이유가 이 필터의 스키마 드리프트 방지다(키 부재 ≠ 빈 값).

## 4. Media Extended (선택, 권장)

타임코드 산출물의 가치는 "그 시점을 바로 여는 것"에 있다. Media Extended는
노트의 타임스탬프 링크에서 영상의 해당 시점을 재생한다.

- 배포처: <https://github.com/aidenlx/media-extended> (id `media-extended`)
- 현재 고정 버전: **4.2.7** (Obsidian 1.12.0+, 데스크톱 전용)

설치 방법 3가지 중 하나를 쓴다.

1. **스크립트** — `scripts/install.sh --with-plugins`
   버전과 sha256을 고정해 내려받고, 검증에 실패하면 설치를 중단한다.
2. **Obsidian 안에서** — 설정 → 커뮤니티 플러그인 → 탐색 → `Media Extended`
3. **수동** — 릴리스의 `main.js`·`manifest.json`·`styles.css`를
   `<vault>/.obsidian/plugins/media-extended/`에 넣고 설정에서 켠다

> **함정**: 이 저장소의 GitHub `releases/latest`는 플러그인이 아니라 별도
> 모듈(`main-daemon`)을 가리킨다. 자동화에서 `latest`를 그대로 쓰면 엉뚱한
> 파일을 받는다 — 반드시 숫자 태그(`4.2.7`)를 고정한다.

설치 후 Obsidian이 실행 중이었다면 재시작해야 로드된다.

## 5. Obsidian 없이 보기

| 하고 싶은 것 | Obsidian 없이 |
|---|---|
| 코퍼스 훑기 | `va-out/INDEX.md`를 아무 마크다운 뷰어로 |
| 검색·회상 | `va search "<검색어>"` (랭킹 검색, 앱 불필요) |
| 워크스페이스 요약 | `va brief <ws>` |
| 편집 인계 | `va export <ws> --format xml\|otio\|fcpxml\|srt\|edl\|md` |

## 6. 문제 해결

- **`.obsidian/`이 자꾸 바뀐다** — 실행 중인 Obsidian이 UI 조작마다
  `workspace.json`·`graph.json`을 다시 쓴다. 정상이며, 공개 스냅샷 조립에서
  `.obsidian/`을 제외하는 이유이기도 하다.
- **링크가 안 걸린다** — 목적지에 원시 공백이 있으면 Obsidian이 해석하지
  못한다. `va audit`(코퍼스 감사)이 이 경우를 깨진 링크로 계상한다.
- **플러그인이 안 보인다** — 데스크톱 전용이다. 모바일에서는 로드되지 않는다.
