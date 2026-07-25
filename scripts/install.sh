#!/usr/bin/env bash
# TIMECODE-AGENT 원클릭 설치 — 어느 머신·어느 경로에서도 동작한다.
#
# 설치 위치를 고정하지 않는다: 리포 루트는 이 스크립트의 실제 위치에서
# 역산한다(고정된 홈 체크아웃 가정 금지 — 배포본은 어디에나 클론된다).
#
# 설치 대상:
#   1) 런타임 의존 — uv · ffmpeg/ffprobe (URL ingest 시 yt-dlp)
#   2) va/tca 전체 런타임 — 비-editable uv tool install (이 리포에서)
#   3) 에이전트 스킬 복사 — Claude Code(~/.claude/skills) · Codex(~/.agents/skills)
#   4) Obsidian 앱 (선택) — 코퍼스를 사람이 읽는 뷰어. 필수 아님.
#   5) vault 부트스트랩 — 코퍼스가 쓰는 코어 플러그인만 켠다
#   6) Media Extended (선택) — 타임스탬프 링크로 영상 시점 재생
#
# usage: scripts/install.sh [옵션]
#        scripts/install.sh --all            # 전부 (Obsidian·플러그인 포함)
#        scripts/install.sh --dry-run --all  # 무엇을 할지만 출력
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"

# ── Media Extended 4.2.7 자산 핀 (GitHub 릴리스 API digest 실측) ────────
# GitHub의 `releases/latest`는 이 리포에서 플러그인이 아니라 별도 모듈
# (main-daemon)을 가리킨다 — latest를 쓰면 잘못된 파일을 받는다. 태그 고정.
MX_VERSION="4.2.7"
MX_BASE="https://github.com/aidenlx/media-extended/releases/download/${MX_VERSION}"
MX_SHA_MAIN_JS="aa4a31a5d41db7ed0183aba9328af71630b2597b777fa8acce9932f684ccfe20"
MX_SHA_MANIFEST="3461eb18af2e0959bbd9198db5f441a1539cf036ec4b362c2d70db6d77d806f3"
MX_SHA_STYLES="b71d1716758b86e7772ba0f81f869d3466cb2ee65ab0027043ae82c40a21d48c"

# 코퍼스 산출물이 실제로 의존하는 코어 플러그인만 켠다.
#   graph          — `va index`가 .obsidian/graph.json 프리셋을 쓴다
#                    (--graph-reset = 표준값 강제 덮어쓰기)
#   properties     — scene-log·wiki의 frontmatter 표시
#   backlink /
#   outgoing-link  — 체크포인트·위키 사이의 [[링크]] 관계
#   switcher /
#   global-search  — 파일명 기반 회상(코퍼스는 제목을 파일명에 싣는다)
#   file-explorer  — vault 트리
#   bases          — frontmatter 스키마를 표로 거르는 코어 DB 뷰
CORE_PLUGINS="file-explorer global-search switcher graph backlink outgoing-link properties bases"

vault=""
with_obsidian=0
with_plugins=0
skip_skills=0
skip_cli=0
skip_models=0
link_skills=0
dry_run=0
cli_ready=0

usage() {
  cat <<'USAGE'
usage: scripts/install.sh [옵션]

  --vault PATH      Obsidian vault 경로 (기본: <repo>/va-out)
  --with-obsidian   Obsidian 앱 설치 시도 (macOS: brew --cask obsidian)
  --with-plugins    Media Extended 커뮤니티 플러그인 설치 (버전·sha256 고정)
  --all             --with-obsidian --with-plugins
  --skip-skills     Claude/Codex 스킬 설치 생략
  --skip-cli        va/tca CLI 설치 생략
  --skip-models     기본 Whisper·MLX·화자분리 모델 사전 준비 생략
  --link-skills     개발 모드: 스킬을 복사하지 않고 현재 checkout에 링크
  --dry-run         실행하지 않고 계획만 출력
  -h, --help        이 도움말

Obsidian은 선택 사항이다. 코퍼스 산출물은 순수 마크다운이라 Obsidian이
없어도 `va` 전체 흐름과 검색이 동작한다.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --vault) vault="${2:?--vault 에 경로가 필요합니다}"; shift 2 ;;
    --with-obsidian) with_obsidian=1; shift ;;
    --with-plugins) with_plugins=1; shift ;;
    --all) with_obsidian=1; with_plugins=1; shift ;;
    --skip-skills) skip_skills=1; shift ;;
    --skip-cli) skip_cli=1; shift ;;
    --skip-models) skip_models=1; shift ;;
    --link-skills) link_skills=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage >&2; exit 2 ;;
  esac
done

vault="${vault:-$root/va-out}"

ok=0
warn=0
fatal=0
step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
say() { printf '  %s\n' "$1"; }
good() { printf '  \033[32m✓\033[0m %s\n' "$1"; ok=$((ok + 1)); }
miss() { printf '  \033[33m!\033[0m %s\n' "$1"; warn=$((warn + 1)); }
run() {
  if [ "$dry_run" -eq 1 ]; then
    printf '  \033[2m$ %s\033[0m\n' "$*"
    return 0
  fi
  "$@"
}

case "$(uname -s)" in
  Darwin) platform="macos" ;;
  Linux) platform="linux" ;;
  *) platform="other" ;;
esac

have() { command -v "$1" >/dev/null 2>&1; }

# brew 로 설치하고, brew 가 없으면 수동 안내로 강등한다(중단하지 않는다).
brew_install() {
  local pkg="$1" kind="${2:-formula}" hint="$3"
  if have brew; then
    if [ "$kind" = "cask" ]; then
      run brew install --cask "$pkg" && return 0
    else
      run brew install "$pkg" && return 0
    fi
  fi
  miss "$hint"
  return 1
}

python_bin() {
  if have python3; then echo "python3"
  elif have python; then echo "python"
  else echo ""
  fi
}

sha256_of() {
  if have shasum; then shasum -a 256 "$1" | awk '{print $1}'
  elif have sha256sum; then sha256sum "$1" | awk '{print $1}'
  else echo ""
  fi
}

echo "TIMECODE-AGENT 설치"
say "리포: $root"
say "플랫폼: $platform"
[ "$dry_run" -eq 1 ] && say "모드: dry-run (아무것도 바꾸지 않습니다)"

# ── 1. 런타임 의존 ─────────────────────────────────────────────────────
step "1/7 런타임 의존"

if have uv; then
  good "uv $(uv --version 2>/dev/null | awk '{print $2}')"
else
  miss "uv 없음 — https://docs.astral.sh/uv/getting-started/installation/"
  say "  curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

for bin in ffmpeg ffprobe; do
  if have "$bin"; then
    good "$bin"
  else
    case "$platform" in
      macos) brew_install ffmpeg formula "ffmpeg 없음 — brew install ffmpeg" || true ;;
      linux) miss "$bin 없음 — sudo apt install ffmpeg (또는 dnf/pacman)" ;;
      *) miss "$bin 없음 — https://ffmpeg.org/download.html" ;;
    esac
    break
  fi
done

if have yt-dlp; then
  good "yt-dlp (URL ingest 가능)"
else
  miss "yt-dlp 없음 — URL ingest만 비활성. 로컬 파일은 그대로 동작"
  case "$platform" in
    macos) say "  brew install yt-dlp" ;;
    *) say "  uv tool install yt-dlp" ;;
  esac
fi

# ── 2. va / tca CLI ────────────────────────────────────────────────────
step "2/7 va · tca CLI"

if [ "$skip_cli" -eq 1 ]; then
  say "건너뜀 (--skip-cli)"
elif [ "$dry_run" -eq 1 ]; then
  run uv tool install --python 3.12 --force \
    --reinstall-package video-agent "$root"
  cli_ready=1
  good "va · tca 전체 런타임 standalone 설치 계획"
elif ! have uv; then
  miss "uv 가 없어 CLI 설치를 건너뜁니다 — uv 설치 후 이 스크립트를 다시 실행하세요"
  fatal=$((fatal + 1))
else
  # 기본 설치는 checkout 수명과 분리한다. 같은 버전의 로컬 소스 변경도
  # uv 빌드 캐시에 가리지 않도록 프로젝트 패키지는 항상 새로 빌드한다.
  if run uv tool install --python 3.12 --force \
    --reinstall-package video-agent "$root"; then
    cli_ready=1
    good "va · tca 전체 런타임 설치 (standalone build from: $root)"
    [ "$dry_run" -eq 0 ] && have va && say "  $(va --version 2>/dev/null || echo 'va 준비됨')"
  else
    miss "CLI 설치 실패 — scripts/install.sh --skip-skills 를 다시 실행해 로그를 확인하세요"
    fatal=$((fatal + 1))
  fi
fi

# ── 2b. 기본 모델 자산 ──────────────────────────────────────────────────
step "3/7 기본 모델 자산 (오프라인 실행 준비)"

if [ "$skip_models" -eq 1 ]; then
  say "건너뜀 (--skip-models)"
elif [ "$skip_cli" -eq 1 ]; then
  say "건너뜀 (--skip-cli: 이번 실행에서 설치한 va가 없음)"
elif [ "$cli_ready" -ne 1 ]; then
  say "건너뜀 (이번 실행의 CLI 설치가 완료되지 않음)"
elif [ "$dry_run" -eq 1 ]; then
  run va runtime prepare
  good "faster-whisper · MLX(Apple Silicon) · sherpa 모델 준비 계획"
else
  va_bin="$(command -v va || true)"
  if [ -z "$va_bin" ] && have uv; then
    uv_bin_dir="$(uv tool dir --bin 2>/dev/null || true)"
    if [ -n "$uv_bin_dir" ] && [ -x "$uv_bin_dir/va" ]; then
      va_bin="$uv_bin_dir/va"
    fi
  fi
  if [ -n "$va_bin" ] && run "$va_bin" runtime prepare; then
    good "기본 모델 준비 완료 (pyannote 게이트 시 sherpa 즉시 사용)"
  else
    miss "기본 모델 준비 실패 — 네트워크를 확인한 뒤 `va runtime prepare` 재실행"
    fatal=$((fatal + 1))
  fi
fi

# ── 3. 에이전트 스킬 설치 ──────────────────────────────────────────────
step "4/7 에이전트 스킬 (Claude Code · Codex)"

install_skill() {
  local dest="$1" label="$2"
  local parent
  parent="$(dirname "$dest")"

  if [ ! -d "$parent" ]; then
    run mkdir -p "$parent"
  fi

  if [ -e "$dest" ] || [ -L "$dest" ]; then
    if [ "$link_skills" -eq 1 ] \
      && [ -L "$dest" ] \
      && [ "$(readlink "$dest")" = "$root/skill" ]; then
      good "$label — 이미 현재 checkout에 연결됨"
      return 0
    fi
    if [ "$link_skills" -eq 0 ] \
      && [ -d "$dest" ] \
      && [ ! -L "$dest" ] \
      && diff -qr "$root/skill" "$dest" >/dev/null 2>&1; then
      good "$label — 동일한 독립 복사본이 이미 설치됨"
      return 0
    fi
    # 사용자 수정 또는 다른 설치일 수 있으므로 자동 교체하지 않는다.
    miss "$label — 기존 경로를 보존해 건너뜁니다: $dest"
    return 0
  fi

  if [ "$link_skills" -eq 1 ]; then
    if run ln -s "$root/skill" "$dest"; then
      good "$label → $root/skill (개발 링크)"
    else
      miss "$label 링크 실패: $dest"
    fi
  elif run cp -R "$root/skill" "$dest"; then
    good "$label — 독립 복사본 설치: $dest"
  else
    miss "$label 복사 실패: $dest"
  fi
}

if [ "$skip_skills" -eq 1 ]; then
  say "건너뜀 (--skip-skills)"
else
  install_skill "$HOME/.claude/skills/timecode-agent" "Claude Code"
  install_skill "$HOME/.agents/skills/timecode-agent" "Codex"
  say "명시 호출: Claude Code /timecode-agent · Codex \$timecode-agent"
fi

# ── 4. Obsidian 앱 (선택) ──────────────────────────────────────────────
step "5/7 Obsidian (선택 — 코퍼스 뷰어)"

obsidian_present=0
if [ -d "/Applications/Obsidian.app" ] \
  || have obsidian \
  || [ -d "$HOME/Applications/Obsidian.app" ]; then
  obsidian_present=1
fi

if [ "$with_obsidian" -eq 0 ] && [ "$with_plugins" -eq 0 ]; then
  say "건너뜀 — --with-obsidian, --with-plugins 또는 --all로 활성화"
elif [ "$obsidian_present" -eq 1 ]; then
  good "Obsidian 설치됨"
elif [ "$with_obsidian" -eq 1 ]; then
  case "$platform" in
    macos)
      brew_install obsidian cask \
        "Obsidian 자동 설치 실패 — https://obsidian.md/download 에서 받으세요" \
        && good "Obsidian 설치" || true
      ;;
    linux)
      miss "Linux 는 배포판마다 패키지가 달라 자동 설치를 하지 않습니다"
      say "  AppImage/Flatpak: https://obsidian.md/download"
      ;;
    *)
      miss "https://obsidian.md/download 에서 직접 설치하세요"
      ;;
  esac
else
  miss "Obsidian 미설치 — 선택 사항입니다 (코퍼스는 순수 마크다운)"
  say "  설치: --with-obsidian · 직접 받기: https://obsidian.md/download"
fi

# ── 5. vault 부트스트랩 ────────────────────────────────────────────────
step "6/7 vault 부트스트랩"

py="$(python_bin)"
if [ "$with_obsidian" -eq 0 ] && [ "$with_plugins" -eq 0 ]; then
  say "건너뜀 — 기본 설치는 vault 설정을 변경하지 않습니다"
elif [ -z "$py" ]; then
  miss "python3 없음 — vault 설정 병합을 건너뜁니다"
elif [ "$dry_run" -eq 1 ]; then
  say "\$ (python) $vault/.obsidian/core-plugins.json 병합: $CORE_PLUGINS"
else
  mkdir -p "$vault/.obsidian"
  # 기존 설정은 보존한다. 필요한 코어만 켜고, 나머지 키는 손대지 않는다.
  # Obsidian 1.9+ 는 object 형식, 구버전은 배열 — 둘 다 받는다.
  core_rc=0
  # shellcheck disable=SC2086  # 개별 인자로 넘겨야 하므로 단어 분할이 의도
  core_changed="$("$py" - "$vault/.obsidian/core-plugins.json" $CORE_PLUGINS <<'PY'
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
wanted = sys.argv[2:]

if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"기존 core-plugins.json을 읽을 수 없습니다: {exc}")
    if not isinstance(data, (list, dict)):
        raise SystemExit("기존 core-plugins.json은 배열 또는 객체여야 합니다")
else:
    data = {}

changed = []
if isinstance(data, list):
    for name in wanted:
        if name not in data:
            data.append(name)
            changed.append(name)
elif isinstance(data, dict):
    for name in wanted:
        if data.get(name) is not True:
            data[name] = True
            changed.append(name)

if changed:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(json.dumps(data, indent=2) + "\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)
print(",".join(changed) if changed else "-")
PY
)" || core_rc=1
  if [ "$core_rc" -eq 0 ]; then
    good "코어 플러그인 활성: $vault/.obsidian/core-plugins.json"
    if [ "$core_changed" = "-" ]; then
      say "  이미 모두 켜져 있음 — 기존 설정을 바꾸지 않았습니다"
    else
      say "  새로 켬: $core_changed"
    fi
  else
    miss "vault 설정 병합 실패: $vault"
  fi
fi

# ── 6. Media Extended (선택) ───────────────────────────────────────────
step "7/7 Media Extended $MX_VERSION (선택 — 타임스탬프 재생)"

install_mx_asset() {
  local name="$1" want_sha="$2" dest_dir="$3"
  local tmp="$dest_dir/.$name.part"

  if ! curl -fsSL "$MX_BASE/$name" -o "$tmp"; then
    miss "$name 다운로드 실패"
    rm -f "$tmp"
    return 1
  fi

  local got
  got="$(sha256_of "$tmp")"
  if [ -z "$got" ]; then
    miss "sha256 도구가 없어 무결성 검증을 못했습니다 — 설치를 중단합니다"
    rm -f "$tmp"
    return 1
  fi
  if [ "$got" != "$want_sha" ]; then
    miss "$name sha256 불일치 — 설치 중단 (기대 $want_sha / 실제 $got)"
    rm -f "$tmp"
    return 1
  fi

  mv "$tmp" "$dest_dir/$name"
  return 0
}

validate_community_plugins() {
  local path="$1"
  "$py" - "$path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(0)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (json.JSONDecodeError, OSError) as exc:
    raise SystemExit(f"기존 community-plugins.json을 읽을 수 없습니다: {exc}")
if not isinstance(data, list):
    raise SystemExit("기존 community-plugins.json은 배열이어야 합니다")
PY
}

if [ "$with_plugins" -eq 0 ]; then
  say "건너뜀 — 설치하려면 --with-plugins"
  say "  또는 Obsidian 안에서: 설정 → 커뮤니티 플러그인 → 'Media Extended' 검색"
elif ! have curl; then
  miss "curl 없음 — Obsidian 커뮤니티 플러그인 브라우저로 직접 설치하세요"
elif [ "$dry_run" -eq 1 ]; then
  say "\$ curl $MX_BASE/{main.js,manifest.json,styles.css}"
  say "\$ (sha256 검증 후) $vault/.obsidian/plugins/media-extended/ 에 배치"
else
  mx_parent="$vault/.obsidian/plugins"
  mx_dir="$mx_parent/media-extended"
  community_plugins="$vault/.obsidian/community-plugins.json"
  mkdir -p "$mx_parent"
  mx_ok=1

  # 설정이 손상되어 있으면 플러그인 파일을 건드리기 전에 중단한다.
  if [ -n "$py" ] && ! validate_community_plugins "$community_plugins"; then
    miss "community-plugins.json이 유효하지 않아 기존 파일을 보존하고 설치를 중단합니다"
    mx_ok=0
  fi

  mx_stage=""
  if [ "$mx_ok" -eq 1 ]; then
    mx_stage="$(mktemp -d "$mx_parent/.media-extended.stage.XXXXXX")"
    install_mx_asset "manifest.json" "$MX_SHA_MANIFEST" "$mx_stage" || mx_ok=0
    [ "$mx_ok" -eq 1 ] && { install_mx_asset "main.js" "$MX_SHA_MAIN_JS" "$mx_stage" || mx_ok=0; }
    [ "$mx_ok" -eq 1 ] && { install_mx_asset "styles.css" "$MX_SHA_STYLES" "$mx_stage" || mx_ok=0; }
  fi

  # 세 자산을 모두 검증한 뒤에만 live 디렉터리를 교체한다. 교체 실패 시
  # 직전 디렉터리를 되돌려 혼합 버전이 남지 않게 한다.
  mx_backup=""
  if [ "$mx_ok" -eq 1 ]; then
    if [ -e "$mx_dir" ] || [ -L "$mx_dir" ]; then
      mx_backup="$mx_parent/.media-extended.backup.$$.$RANDOM"
      if ! mv "$mx_dir" "$mx_backup"; then
        miss "기존 Media Extended를 백업하지 못해 설치를 중단합니다"
        mx_ok=0
      fi
    fi
  fi
  if [ "$mx_ok" -eq 1 ] && ! mv "$mx_stage" "$mx_dir"; then
    miss "검증된 Media Extended 디렉터리를 배치하지 못했습니다"
    mx_ok=0
    if [ -n "$mx_backup" ] && [ -e "$mx_backup" ]; then
      mv "$mx_backup" "$mx_dir" || true
      mx_backup=""
    fi
  fi
  if [ "$mx_ok" -eq 1 ] && [ -n "$mx_backup" ]; then
    rm -rf -- "$mx_backup"
    mx_backup=""
  fi
  if [ "$mx_ok" -eq 0 ]; then
    [ -n "$mx_stage" ] && [ -e "$mx_stage" ] && rm -rf -- "$mx_stage"
    if [ -n "$mx_backup" ] && [ -e "$mx_backup" ]; then
      mv "$mx_backup" "$mx_dir" || true
    fi
  fi

  if [ "$mx_ok" -eq 1 ]; then
    good "Media Extended $MX_VERSION → $mx_dir"
    if [ -n "$py" ]; then
      community_rc=0
      "$py" - "$community_plugins" "media-extended" <<'PY' || community_rc=1
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
plugin_id = sys.argv[2]

if path.exists():
    try:
        enabled = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"기존 community-plugins.json을 읽을 수 없습니다: {exc}")
    if not isinstance(enabled, list):
        raise SystemExit("기존 community-plugins.json은 배열이어야 합니다")
else:
    enabled = []

if plugin_id not in enabled:
    enabled.append(plugin_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(json.dumps(enabled, indent=2) + "\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)
PY
      if [ "$community_rc" -eq 0 ]; then
        good "community-plugins.json 에 media-extended 등록"
      else
        miss "community-plugins.json 등록 실패 — 플러그인은 설치됐지만 수동 활성화가 필요합니다"
      fi
    fi
    say "  Obsidian 이 실행 중이면 재시작해야 플러그인이 로드됩니다"
  else
    miss "Media Extended 설치 실패 — Obsidian 커뮤니티 플러그인 브라우저를 쓰세요"
  fi
fi

# ── 요약 ───────────────────────────────────────────────────────────────
step "요약"
say "완료 ${ok}건 · 확인 필요 ${warn}건"
if [ "$dry_run" -eq 0 ]; then
  if [ "$with_obsidian" -eq 1 ] || [ "$with_plugins" -eq 1 ]; then
    say ""
    say "vault 열기:"
    if [ "$platform" = "macos" ] && [ -n "$py" ]; then
      say "  open \"obsidian://open?path=$("$py" -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$vault")\""
    else
      say "  Obsidian → 폴더를 vault로 열기 → $vault"
    fi
  fi
  say ""
  say "첫 실행:"
  say "  va ingest <video-path> --signals"
fi
[ "$warn" -gt 0 ] && say "확인 필요 항목은 위 ! 표시를 보세요"
if [ "$fatal" -gt 0 ]; then
  say "필수 설치 ${fatal}건이 실패했습니다"
  exit 1
fi
exit 0
