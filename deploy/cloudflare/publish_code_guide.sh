#!/usr/bin/env bash
# HTML 문서 하나를 Cloudflare Worker로 올린다(기본: docs/CODE_GUIDE_LIVE.html → majubom-code-guide).
# 사용: CF_TOKEN=<Cloudflare API 토큰> bash deploy/cloudflare/publish_code_guide.sh [문서.html] [워커 이름]
#   예) AI 가이드: ... publish_code_guide.sh docs/AI_GUIDE.html majubom-ai-guide
# 결과 주소: https://<워커 이름>.minhyuk6512.workers.dev/
set -euo pipefail
: "${CF_TOKEN:?CF_TOKEN 환경 변수에 Cloudflare API 토큰을 넣어 주세요}"
DIR=$(cd "$(dirname "$0")" && pwd)
DOC=${1:-$DIR/../../docs/CODE_GUIDE_LIVE.html}
N=${2:-majubom-code-guide}
WJS="$DIR/$N-worker.js"; [ "$N" = majubom-code-guide ] && WJS="$DIR/code-guide-worker.js"
# 올리기 직전에 항상 최신 docs/CODE_GUIDE_LIVE.html로 워커를 다시 만든다(예전엔 손으로 만든 사본이 남아 옛 문서가 올라갈 뻔했다)
python3 - "$DOC" "$WJS" <<'PY'
import json, sys
html = open(sys.argv[1], encoding="utf-8").read()
open(sys.argv[2], "w", encoding="utf-8").write(
    'export default { async fetch() { return new Response(HTML, { headers: { "content-type": "text/html; charset=utf-8", '
    '"cache-control": "no-cache" } }); } };\nconst HTML = ' + json.dumps(html, ensure_ascii=False) + ';\n')
print(f"워커 생성: {len(html)//1024} KB 문서")
PY
A=https://api.cloudflare.com/client/v4/accounts/ce62972277aa65229cb2f3d4b14dd480
META=$(mktemp); echo '{"main_module":"worker.js","compatibility_date":"2026-10-01"}' > "$META"
# 업로드 파일 이름이 main_module(worker.js)과 같아야 한다(filename 지정 없으면 'No such module: worker.js'로 실패, 2026-10-13 확인)
OUT=$(curl -s -X PUT -H "Authorization: Bearer $CF_TOKEN" "$A/workers/scripts/$N" \
  -F "metadata=@$META;type=application/json" \
  -F "worker.js=@$WJS;filename=worker.js;type=application/javascript+module")
echo "$OUT" | grep -q '"success": *true' && echo "업로드 완료" || { echo "업로드 실패: $OUT" | head -c 600; exit 1; }
OUT=$(curl -s -X POST -H "Authorization: Bearer $CF_TOKEN" -H "Content-Type: application/json" \
  "$A/workers/scripts/$N/subdomain" -d '{"enabled":true,"previews_enabled":false}')
echo "$OUT" | grep -q '"success": *true' && echo "workers.dev 공개 완료" || { echo "공개 설정 실패: $OUT" | head -c 600; exit 1; }
echo "https://$N.minhyuk6512.workers.dev/"
