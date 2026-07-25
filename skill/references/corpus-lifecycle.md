# 코퍼스 수명주기 — 재개·검색·교정·위키·정리

기존 워크스페이스를 재개하거나 여러 영상의 기억을 검색·갱신·정리할 때만
읽는다. 영상 1편의 최초 분석 절차는 `SKILL.md`, 검증 도구는
`verification-toolbox.md`, 위키 라벨링은 `wiki-schema.md`가 정본이다.

## 재개와 검색

- `<ws>/manifest.json`이 있으면 재-ingest하지 말고 `va brief <ws>`로 재개한다.
- “그 장면이 어느 영상이었지?”는 워크스페이스를 순회하지 말고
  `va search "<검색어>"`를 먼저 쓴다. 검색 결과의 `hypothesized`는 탐색
  기록이지 확정 사실이 아니므로 답변 근거로 승격하지 않는다.
- 세션 종료 시 `va index`로 `va-out/INDEX.md`, 워크스페이스 장면 로그,
  그룹 허브를 재생성한다. 다음 세션은 `INDEX.md`에서 시작한다.
- `va view`의 HTML은 Markdown 원장에서 다시 만드는 파생 뷰다. 삭제돼도
  원장이 손상되지 않으며, 미디어가 정리된 경우 썸네일 폴백을 쓴다.

## 전사 교정과 glossary

시각 근거로 ASR 오청을 확정했을 때만 `<ws>/corrections.jsonl`에 기록한다.

```json
{"span":[12.0,14.2],"asr":"기태 먹었어요","corrected":"큐 키트 먹었어요","basis":"프레임 근거"}
```

- 전사 교정이 아닌 주석은 `corrected`를 `(`로 시작한다. 주석 낱말이
  hotwords로 유입되는 것을 막는 제외 신호다.
- glossary 후보는 고유명사·도메인 용어·채널 표기 관행으로 제한한다.
  일반어 교정은 해당 영상 산출물에만 반영한다.
- 세션 끝에 `va glossary --all` 또는 `va glossary <ws>...`로 갱신한다.
  영상 한 편 전용 용어는 `va ingest --hotwords "용어1 용어2"`로 주입한다.
- 같은 오인식이 세 번 이상 재발하고 hotwords로도 고쳐지지 않을 때만
  파인튜닝 후보로 올린다. 전체 용어사전을 모든 영상에 주입하지 말고
  현재 영상·프로젝트 문맥에 맞는 후보만 사용한다.

## 위키 승격

- 세션 종료 시 `va index && va wiki`를 실행한다.
- active 의미층에는 현재 해소 가능한 시각·전사 support가 있는
  `verified`/`corrected` 체크포인트만 승격한다.
- 원장 밖에서 보존되는 수기 서술 블록은 재생성 전 입력이다. 위키 페이지의
  `tca:notes`와 장면 로그의 narrative 블록을 임의 삭제하지 않는다.
- 라벨·관계·서사 작성과 index-first 질의 규칙은
  [wiki schema](wiki-schema.md)를 읽고 적용한다.

## 배치

영상이 3편 이상이면 [batch ingest contract](batch-ingest.md)를 읽는다.
병렬화 범위는 ingest와 결정적 신호 추출뿐이다. 가설·프레임 판독·체크포인트
쓰기·최종 selection은 메인 에이전트가 순차 수행한다.

## 스토리지 위생

먼저 `va gc`의 리포트 모드로 크기를 확인한다. 삭제는 사용자가 범위를
지정한 뒤에만 실행한다.

```bash
va gc --purge captures --yes
va gc --purge media --yes
va gc --purge workspace --keep-days 30 --yes
```

- `--yes`가 없으면 dry-run이다.
- `captures`는 재생성 가능한 캡처·필름스트립이다.
- `media`는 다운로드 소스다. 삭제 뒤 재캡처할 수 없고 URL 재다운로드만
  가능하다.
- `clips`는 납품물이므로 사용자가 명시했을 때만 정리한다.
- `workspace`는 `--keep-days N`이 필수인 전체 워크스페이스 정리다.
- manifest·transcript·checkpoints·corrections·glossary·markers 같은 텍스트
  원장은 카테고리 purge에서 보존한다.
