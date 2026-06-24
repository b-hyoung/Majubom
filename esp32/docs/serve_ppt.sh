#!/usr/bin/env bash
# 마주봄 발표(PPT) 서버 한 번에 켜기
#   ./serve_ppt.sh           → 로컬 발표 서버만 (http://localhost:8088/presentation/)
#   ./serve_ppt.sh --share   → + Cloudflare 터널로 외부 공유 링크 생성
set -e
cd "$(dirname "$0")"   # esp32/docs (발표·대시보드의 서빙 루트)

PORT=8088
URL="http://localhost:${PORT}/presentation/"

# 1) 발표 서버 (8088) — 이미 떠 있으면 재사용
if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/presentation/index.html"; then
  echo "✅ 발표 서버 이미 실행 중 → ${URL}"
else
  python3 -m http.server "${PORT}" --bind 127.0.0.1 >/tmp/ppt_http.log 2>&1 &
  sleep 1
  echo "✅ 발표 서버 시작 → ${URL}"
fi
echo "   (3번 슬라이드 라이브 대시보드는 ../dashboard/?sim=1 시뮬로 자동 동작 — 백엔드 불필요)"

# 2) (선택) 외부 공유 터널
if [ "${1:-}" = "--share" ]; then
  command -v cloudflared >/dev/null 2>&1 || { echo "❌ cloudflared 없음 → brew install cloudflared"; exit 1; }
  echo ""
  echo "🌐 Cloudflare 터널 — 아래 출력되는 https://....trycloudflare.com 뒤에 /presentation/ 를 붙여 공유하세요."
  echo "   (이 창을 닫으면 공유 링크도 끊깁니다. Ctrl+C 로 종료)"
  echo ""
  cloudflared tunnel --url "http://localhost:${PORT}"
else
  echo ""
  echo "브라우저에서 위 주소를 여세요. (← →/Space 로 넘김, F 풀스크린)"
  echo "외부 공유가 필요하면:  ./serve_ppt.sh --share"
fi
