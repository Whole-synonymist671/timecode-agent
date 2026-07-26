# 런타임 설정 — 설치·백엔드·프로필·캐시

SKILL.md 본문은 실행 전 확인(`command -v`)만 다룬다. 설치가 없거나, 백엔드가
빠졌거나, 사용자가 실행 정책·캐시 위치를 바꾸려 할 때 이 문서를 읽는다.

## 설치

- `va`가 없고 패키지 체크아웃이 있으면 저장소 루트에서 `uv tool install .`로
  설치한다. 화자분리까지 쓰려면 `uv tool install --editable '.[diarize]'` —
  bare `.`는 pyannote를 빼고 sherpa로만 동작한다.
- 스킬만 설치된 환경에서는 로컬 경로를 추측하지 말고 배포판 README의 CLI
  설치 절차를 안내한다.

## 백엔드 준비

기본 설치는 faster-whisper·Apple Silicon MLX Whisper·OCR·Sound
Analysis·pyannote/sherpa 화자분리·OTIO 백엔드를 모두 포함하고
`va runtime prepare`로 무게이트 기본 모델까지 준비한다. pyannote 원격 모델만
계정 토큰/약관 게이트이며, 토큰이 없어도 sherpa가 즉시 동작한다.

**지원 플랫폼의 백엔드 import 실패는 선택 기능 부재가 아니다.** 강등하지 말고
같은 배포본의 설치 절차를 다시 실행해 복구한다.

## 실행 정책 (기능·프로필)

기능은 전부 on으로 시작한다. 사용자가 끄거나 실행 정책을 바꾸려는 경우에만
아래를 쓴다.

```bash
va runtime status
va runtime set feature.<name> on|off
va runtime set profile balanced|low-power|quality
```

`balanced`는 실측 안정 경로(faster-whisper·정밀 클립 software)를 유지하고,
`low-power`만 Apple Silicon에서 MLX·VideoToolbox를 자동 선택한다.

## URL 입력

URL에서만 yt-dlp를 확인한다. Instagram 쿠키는 사용자가
`--cookies-from-browser chrome`을 명시했을 때만 쓰고, Threads는 브라우저로
저장한 로컬 파일을 ingest한다.

## 캐시 위치

공유·샌드박스별 캐시 위치가 필요하면 실행 전에 `VIDEO_AGENT_CACHE_DIR`를
설정한다. Linux는 `XDG_CACHE_HOME`도 인식한다.

## Windows

잠금은 msvcrt 폴백(공유→배타 강등)이고 CI 스모크(설치·락·CLI)까지만
검증됐다. ffmpeg 기반 E2E는 미검증이다.
