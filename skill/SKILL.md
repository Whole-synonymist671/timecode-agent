---
name: timecode-agent
description: Use when a user provides a local file or video URL for timestamped video understanding, editing handoff, or reusable indexing; or asks to recall a scene, speaker, quote, event, or evidence from an existing video-agent corpus (기존 영상 기억·장면·화자·대사 검색), even when no new video is supplied. Do not use for a quick one-shot summary that needs no reusable index or editing output.
license: MIT
---

# TIMECODE-AGENT

전사로 먼저 가설을 세우고, 불확실한 시점만 시각 검증한 뒤 체크포인트로
영속화한다. 모든 프레임을 캡처하지 말고, 캡처마다 검증할 가설을 명시한다.
기기·데이터셋 한정 실측 수치를 스킬의 보편 규칙으로 승격하지 않는다.

## 필요할 때만 읽는 참조

| 상황 | 참조 |
|---|---|
| OCR·화자분리·faces·scene 감별·정밀 시각검증 | [검증 도구](references/verification-toolbox.md) |
| 하이라이트 채점·경계·리프레임·NLE 인계 | [산출 인계](references/output-handoff.md) |
| 재개·검색·glossary·위키·스토리지 | [코퍼스 수명주기](references/corpus-lifecycle.md) |
| 위키 라벨·관계·서사·질의 | [위키 스키마](references/wiki-schema.md) |
| 3편 이상 ingest | [배치 계약](references/batch-ingest.md) |
| Claude Code 외 하네스 | [하네스 매트릭스](references/harness-matrix.md) |

## 요청 → 루프 라우팅

사용자 말을 먼저 아래로 접는다. 절차 번호는 그다음이다.

| 사용자가 말하면 | 루프 | 진입 |
|---|---|---|
| 새 영상·이해·분석·"무슨 내용" | **가져오기**(1차 현상)→**큐브릭**(이해) | 절차 0 `va ingest --signals`→`va brief` |
| 하이라이트·쇼츠·"잘라줘" | 큐브릭→**쿨레쇼프**(편집)의 **컷 워크플로우** | 구간 특정 요청은 그 구간만 수렴(절차 4)·개방형 발굴은 전역 수렴 후 |
| "그 장면 어디였지"·기억 회상 | **검색**(회상) | `va search` — **재-ingest 금지** |
| 프리미어·편집 인계·마커 | 브릿지 | 5의 `va export` |
| 목록·위키·브라우저·외부 vault 갱신 | **아카이브**(지식 투영) | `va index`·`va wiki`·`va view`·`va bridge` |

파이프라인: 가져오기(현상)→검색(회상)→큐브릭(이해)→쿨레쇼프(편집)→
컷(조립)→아카이브(보존).

큐브릭은 사실 원장(`checkpoints.jsonl`), 쿨레쇼프는 편집 결정 원장
(`sequences.jsonl`)을 쓴다. **쿨레쇼프는 사실 원장을 절대 수정하지 않는다.**
사실 오류는 체크포인트를 정정한 뒤 편집안으로 다시 내려온다.

## Requirements

- Python 3.12 on macOS or Linux를 사용한다. Windows는 실험 지원 —
  잠금은 msvcrt 폴백(공유→배타 강등)이고 CI 스모크(설치·락·CLI)까지만
  검증됐다. ffmpeg 기반 E2E는 미검증.
- 풀 모드는 로컬 이미지 입력 도구와 이미지 입력 모델이 필요하다. 둘 중
  하나라도 없으면 아래의 Degraded 모드로 진행한다.

```bash
command -v va
command -v ffmpeg
command -v ffprobe
```

입력이 HTTP(S) URL일 때만 추가 확인한다:

```bash
command -v yt-dlp
```

- `va`가 없고 패키지 체크아웃이 있으면 저장소 루트에서 `uv tool install .`로
  설치한다. 스킬만 설치된 환경에서는 로컬 경로를 추측하지 말고 배포판
  README의 CLI 설치 절차를 안내한다.
- URL에서만 yt-dlp를 확인한다. Instagram 쿠키는 사용자가
  `--cookies-from-browser chrome`을 명시했을 때만 쓰고, Threads는 브라우저로
  저장한 로컬 파일을 ingest한다.
- 기본 설치는 faster-whisper·Apple Silicon MLX Whisper·OCR·Sound
  Analysis·pyannote/sherpa 화자분리·OTIO 백엔드를 모두 포함하고
  `va runtime prepare`로 무게이트 기본 모델까지 준비한다. pyannote 원격
  모델만 계정 토큰/약관 게이트이며, 토큰이 없어도 sherpa가 즉시 동작한다.
  지원 플랫폼에서 Python 백엔드 import가 실패하면
  정상적인 선택 기능 부재로 강등하지 말고 같은 배포본의 설치 절차를 다시
  실행해 불완전 설치를 복구한다.
- 기능은 전부 on으로 시작한다. 사용자가 끄거나 실행 정책을 바꾸려는 경우
  `va runtime status`, `va runtime set feature.<name> on|off`,
  `va runtime set profile balanced|low-power|quality`를 사용한다.
  `balanced`는 실측 안정 경로(faster-whisper·정밀 클립 software)를 유지하고
  `low-power`만 Apple Silicon에서 MLX·VideoToolbox를 자동 선택한다.
- 공유·샌드박스별 캐시 위치가 필요하면 실행 전에
  `VIDEO_AGENT_CACHE_DIR`를 설정한다. Linux는 `XDG_CACHE_HOME`도 인식한다.

## 루프 절차

### 0. ingest — 음원분석 → 타임스탬프 자막 + 신호 일괄

```bash
va ingest "<video-path>" --model small --signals
va ingest "<URL>" --signals -o "<absolute-workspace>"
```

로컬 기본 workspace는 CWD의 `./va-out/<stem>`, URL 기본값은
`./va-out/url-<md5-prefix>`다. URL에는 재개 경로를 알 수 있도록 절대 `-o`를
쓴다. `manifest.json`이 있으면 재-ingest하지 말고 `va brief <ws>`로 재개한다.
URL은 수동 자막 > 업로더 자동자막(원어) > whisper 순으로 빠른 소스를 먼저
쓴다. 타임스탬프 정밀도가 필요하면 `--force-whisper`를 **첫 ingest에서**
선택하거나 새 workspace(`-o` 다른 경로)로 받는다 — 체크포인트가 생긴
workspace에 재전사를 덮으면 기존 근거가 다른 전사에 얹힌다.

### 0-1. 콘텐츠 유형 판별 — 신호 가중치 결정 (도그푸딩 교훈)

`va brief`의 추천은 placement 신호다. transcript·highlights·scenes를 대조해
에이전트가 mode를 확정한다.

| 유형 | 판별 신호 | 주력 신호 | 실례 |
|---|---|---|---|
| 발화 주도 | 발화 밀도 높음 | transcript + 버스트 |
| 시각 주도 | 발화 희소, 버스트·컷 다수 | 조망 + 버스트 + 장면전환 |
| 무발화 | transcript 빈약/없음 | 전체 조망 + 장면전환 |
| 텍스트-온-스크린 | 화면 자막 중심 | transcript + OCR 교정 |
| 루프 모션그래픽 | scenes ≤ 1, highlights 0 | 필름스트립 1회 |

방송 영상은 첫 full-res 캡처에서 오버레이·명패·마이크 플래그를 확인한다.
ingest·조망 예외는 [검증 도구](references/verification-toolbox.md)를 읽는다.

### 1. 1차 상황 추론 — 전사문만으로 (프레임 0장)

brief에 `chapters:`가 있으면 그 경계를 span 초안으로 쓰고, 전사와 어긋나는
챕터만 재분할한다. transcript를 의미 전환 단위로 나눠 체크포인트를 기록한다.

```bash
va checkpoint add <ws> --json-file - <<'EOF'
{"id": "cp-001", "span": [0.0, 42.5], "segments": [0,1,2,3],
 "status": "hypothesized",
 "hypothesis": "MC가 퀴즈 규칙을 설명하는 오프닝. 화자 1명 추정",
 "confidence": 0.55}
EOF
```

- id는 `cp-001` 연번, span은 초 단위다.
- 전 구간을 커버해 `va status <ws>`의 gap을 없앤다.
- hypothesis는 정적 목록보다 직전 구간에서 무엇이 변했는지 쓴다.

### 2. 검증 포인트 선정 — 어디를 눈으로 볼 것인가

다음 중 하나가 있는 지점만 본다: confidence < 0.7, ASR `conf < 0.6`,
화자·장소 전환, 20초 이상 무발화/gap, 모호한 지시어, 출연 구성 변화,
버스트·장면전환. 화자 구조가 중요하면 `va diarize <ws>`를 쓴다.

긴 미지 구간은 개별 캡처 전에 오버뷰 스캔으로 좁힌다.

```bash
va filmstrip <ws> --auto
va filmstrip <ws> --auto --start 120 --end 400
```

타일은 절대경로로 열어(Claude Code: `Read` · Codex: `view_image`) 고해상
확인 가치만 고른다. 화자 단서·화면 문자는 full-res로 확정한다.

- **결말 보강 의무**: 조망 뒤 `--legible-endcard`가 고른 꼬리 1장을 본다.
- **시간 참조 구간 정밀 프로토콜**: 해당 span의 전사 전량, 1~2초 밀집
  조망, 경계 전후 프레임을 확인한다.
- **공간 방향 질문 이중 해석 규약**: 카메라·피사체 기준을 모두 검토한다.
  근거가 없으면 **카메라(시청자) 기준을 디폴트**로 쓰고 확신도를 낮춘다.
- **미세 디테일 판독**: 단일 이미지 대신 **연속 3~5프레임**을 비교한다.
- 캡처 예산은 모드별 차등: 발화 주도 **≤15장**, 시각 주도·무발화
  **≤25장**, 한 라운드는 **캡처 6장 이하**다. 초과 시
  `va keyframes <ws> --budget N`을 쓴다.
- UI 텍스트는 `va ocr`, 출연 구성은 `va faces`를 먼저 쓴다.

밀도(칸 간격이 7초를 넘지 않게, 타일 112초 이하), `duration-1` 폴백 위험,
OCR crop과 선명도 게이트는 [검증 도구](references/verification-toolbox.md)에
따른다.

### 2-1. 신호 기반 검증 포인트 (placement 신호 2종)

```bash
va highlights <ws> --json      # 오디오 에너지 버스트 스팬 (highlights.json)
va scenes <ws> --json          # 장면전환 타임스탬프, 프레임 추출 없음 (scenes.json)
va audioevents <ws> --json     # 학습 모델의 웃음/박수/환호/비명 후보 — macOS Sound Analysis
```

신호는 placement(언제)만 정하고 selection(무엇)은 에이전트가 판단한다.
무발화는 scenes, 편집 목적의 버스트 의미는 audioevents를 우선한다.
세 신호가 모두 조용하면 캡처를 아낀다. scene 오탐 감별은
[검증 도구](references/verification-toolbox.md)를 읽는다.

### 3. capture → 비전 더블체크 → 체크포인트 갱신

```bash
va capture <ws> -t 18.2 -t 95.0 --reason "burst-95s"   # 검증 대상 구간의 대표 시점 1~2장씩
```

`--reason`에 캡처를 촉발한 신호를 남긴다. 프레임을 절대경로로 연다
(Claude Code: `Read`, Codex: `view_image`). 가설과 대조해 맞으면
`verified`, 틀리면 `corrected`와 차이를 기록한다.
같은 id를 다시 쓰면 새 리비전이다. `visual_evidence`는 `<ws>` 상대경로다.
support 조건은 [검증 도구](references/verification-toolbox.md)에 따른다.

**사람이 읽는 필드는 사람 말로 쓴다.** `hypothesis`·`situation`·`note`·
`--reason`은 vault에서 비개발자가 그대로 읽는다. 화면에서 본 것을 쓰고,
도구 이름·내부 지표(`full-res`·`span`·`score`·`OCR`·`버스트`)로 대신하지
않는다 — "최대 버스트(1195-1208 score 163) 스팬과 일치"가 아니라 "소리가
가장 커지는 대목과 겹친다". 렌더러가 남은 도구 어휘를 일부 순화하지만
원장에 적힌 말이 정본이므로 처음부터 사람 말로 적는 편이 낫다.

### 4. 수렴 판단 — 자답-후-성찰 (VideoAgent ECCV'24 패턴)

먼저 현재 인덱스만으로 답하고, 결론을 바꿀 핵심 주장과 부족한 근거를
분리한다. 모델의 자답 확신만으로 종료하지 않는다. 핵심 주장에 현재
해소 가능한 support가 있고, 관련 감사 경고·미해결 모순이 없거나 답변에
명시됐으며, 추가 관측이 결론을 바꿀 가능성이 낮을 때 종료한다.

```bash
va status <ws> --json    # covered_ratio == 1.0 + gap 없음 + verified_ratio ≥ 0.6이 보조 기준
```

`readiness`는 종료 보조 신호다. 감사 경고는 `va audit <roots>`로 본다 —
`converged`여도 감사 경고·핵심 근거 공백이 있으면 계속한다. 예산이 먼저 소진되면 강제 승격하지 말고
`provisional`/미해결 범위를 답변에 명시한다.

**편집 범위 수렴**: 구간·사건이 **특정된** 편집 요청만 — 컷 span
전체가 terminal+support로 덮이면(게이트가 기계 강제) 전역 미수렴이어도
진입. 개방형 하이라이트 발굴은 후보 비교 범위의 전역 수렴이
먼저다. 상세는 [산출 인계](references/output-handoff.md).

### 5. 산출 — 이해·편집·가공

- 질의응답은 checkpoint와 transcript의 타임스탬프를 인용한다. 질문 유형별
  1차 경로는 [위키 스키마](references/wiki-schema.md)를 따른다.
- 하이라이트·쇼츠 요청은 후보를 검증한 뒤 의도가 다른 2~3안을 제시한다.
  채점·경계·리프레임은 [산출 인계](references/output-handoff.md)를 읽는다.
- 클립: `va clip <ws> --start 1:23 --end 2:05 --accurate`.
  `low-power` 프로필 또는 `clip-encoder` 명시 설정은 macOS에서
  VideoToolbox+AudioToolbox를 쓴다. `balanced` 기본과 최종 납품은 소프트웨어
  정밀 인코딩을 유지하고, 일회성 `--hw`는 미리보기/저전력 작업에 쓴다.
- 컷 경계: `va boundary-eval <ws> --sequence <id>` 뒤
  경계 전후 프레임을 열고, 잘렸으면 단어/세그먼트 경계로 재스냅한다.
- 인계: `va export <ws> --format xml|otio|fcpxml|srt|edl|md`.
  CapCut은 srt만 쓰고 비공식 draft JSON 조작은 금지한다.
  `--ids cp-004,cp-007`의 **지정 순서=컷 순서**다.
- 재사용할 편집안은 `va sequence add <ws> --json-file -`로 기록하고
  `va export <ws> --format otio --sequence seq-001`로 인계한다.
  편집안은 사실 원장(체크포인트)을 절대 수정하지 않는다.

## 대량 배치·코퍼스 규약

- `va search "<검색어>"`로 찾고 적중 workspace를 `va brief`로 연다.
- 세션 종료 시 `va index && va wiki`; 공간 확인은 `va gc` 리포트 모드다.
- 교정·glossary·active wiki·삭제 범위는
  [코퍼스 수명주기](references/corpus-lifecycle.md)를 읽는다.
- 3편 이상이면 [배치 계약](references/batch-ingest.md)에 따라 ingest·신호만
  병렬화한다. 가설·검증·판정은 메인 에이전트가 순차 수행한다.

## 하네스 분기 (실행 환경별)

로컬 이미지를 모델 입력으로 여는 도구와 이미지 입력 모델이 모두 있어야 풀
루프다. 불명이면 degraded로 시작해 첫 판독이 성립한 뒤 승격한다. 도구 이름을
하드코딩하지 말고 현재 하네스의 동등 기능을 사용한다(Claude Code: `Read`,
Codex: `view_image`). 그 밖의 하네스는
[하네스 매트릭스](references/harness-matrix.md)를 읽는다.

**Degraded 모드(비전 불가)**: OCR·faces·audioevents·highlights·scenes·diarize
결과만 쓰고, OCR 물증 예외 외에는 `hypothesized` + confidence ≤ 0.7을
지킨다. 발언 내용과 영상 사실을 구분하고, 발언만 근거면 “화자 주장”이라고
쓴다.

## 주의

- JSON에 한글·따옴표가 섞이므로 `--json-file -` + heredoc 사용을 기본으로 (셸 이스케이프 사고 방지).
- 캡처 캐시는 같은 타임스탬프의 프레임 생성·디코딩 반복을 줄인다.
  이미지 열기와 비전 모델 추론 비용은 별도다.
- 인물 식별은 화면 내 시각 단서(자막·명찰·위치)로만 라벨링하고, 실명 추정은 하지 않는다.
