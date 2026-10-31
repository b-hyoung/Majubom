# 로컬 대시보드 리버스 프록시 (Cloudflare 터널 공개용)
# 이 PC가 대시보드를 "같은 출처"로 서빙 + 라즈베리파이 데이터 중계 →
# 단일 URL(터널)만으로 대시보드+실시간 데이터가 모두 동작.
#   대시보드의 url(kind,path) 를 same-origin(path) 으로 바꿔 서빙하고,
#   /tof/* → RPi:5001, /mmw* → RPi:5002 로 프록시.
from flask import Flask, Response, request, send_from_directory
import urllib.request, urllib.error
import os, mimetypes

RPI   = "http://192.168.6.10"
PORTS = {"tof": 5001, "mmw": 5002, "mmwave": 5002}
REPO  = os.path.dirname(os.path.abspath(__file__))
SITE  = os.path.join(REPO, "site", "index.html")
PORT  = 8080

app = Flask(__name__)


def dashboard_html():
    with open(SITE, "r", encoding="utf-8") as f:
        html = f.read()
    # 모든 요청을 같은 출처(터널)로 → url()이 포트 대신 상대경로 반환하게 치환
    html = html.replace(
        "function url(kind,path){return 'http://'+host()+':'+PORTS[kind]+path;}",
        "function url(kind,path){return path;}")
    return Response(html, content_type="text/html; charset=utf-8")


@app.route("/")
@app.route("/dashboard")
@app.route("/dashboard_design.html")
def dash():
    return dashboard_html()


@app.route("/dashboard-assets/<path:filename>")
def dashboard_asset(filename):
    return send_from_directory(os.path.join(REPO, "site", "dashboard-assets"), filename, max_age=0)


@app.route("/tof/3d")
def tof3d():
    # 최신 로컬 3D 뷰어를 서빙(같은 출처). 데이터 fetch는 /tof/* 프록시로 RPi 중계.
    with open(os.path.join(REPO, "TOF", "tof_3d_posture.html"), "r", encoding="utf-8") as f:
        return Response(f.read(), content_type="text/html; charset=utf-8")


def _target(path):
    for k, port in PORTS.items():
        if path == "/" + k or path.startswith("/" + k + "/"):
            return f"{RPI}:{port}{path}"
    if path == "/beds" or any(path.startswith(prefix) for prefix in
                              ("/patients/", "/api/dashboard/", "/agent/", "/kb/")):
        return f"{RPI}:{PORTS['tof']}{path}"
    return None


@app.route("/<path:path>", methods=["GET", "POST"])
def proxy(path):
    full = "/" + path
    tgt = _target(full)
    if tgt is None:                                   # site/ 정적 파일 폴백
        return send_from_directory(os.path.join(REPO, "site"), path)
    if request.query_string:
        tgt += "?" + request.query_string.decode()
    try:
        body = request.get_data() if request.method == "POST" else None
        req = urllib.request.Request(tgt, data=body, method=request.method)
        req.add_header("Content-Type", request.headers.get("Content-Type", "application/json"))
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
            ct = r.headers.get("Content-Type", "application/json")
            status = r.status
        return Response(data, status=status, content_type=ct)
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type="application/json")
    except Exception as e:
        return Response('{"error":"%s"}' % e, status=502, content_type="application/json")


if __name__ == "__main__":
    print("=" * 56)
    print(f"  대시보드 프록시 : http://localhost:{PORT}")
    print(f"  데이터 중계     : {RPI} (tof:5001 mmw:5002)")
    print("=" * 56)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
