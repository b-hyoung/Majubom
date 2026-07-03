# 3D 뷰어 로컬 개발용 서버 (같은 출처 중계)
# 브라우저는 이 PC(:8000)하고만 통신 → 크롬 사설망 차단(PNA)·CORS 회피.
# median 필터는 이제 뷰어(JS)에 내장돼 있어, 여기선 라즈베리파이로 그대로 중계만 한다.
# (라즈베리파이가 /tof/3d 로 직접 서빙할 땐 이 프록시 자체가 불필요)
from flask import Flask, Response, send_from_directory
import urllib.request
import os

RPI = "http://192.168.6.10:5001"
HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)


@app.route("/")
def index():
    return send_from_directory(HERE, "tof_3d_posture.html")


@app.route("/tof/<path:sub>")
def proxy_tof(sub):
    try:
        with urllib.request.urlopen(f"{RPI}/tof/{sub}", timeout=4) as r:
            data = r.read()
            ct = r.headers.get("Content-Type", "application/json")
        return Response(data, content_type=ct)
    except Exception as e:
        return Response('{"error":"%s"}' % e, status=502,
                        content_type="application/json")


@app.route("/<path:fn>")
def files(fn):
    return send_from_directory(HERE, fn)


if __name__ == "__main__":
    print("=" * 52)
    print("  3D 뷰어(개발) : http://<이 PC IP>:8000/")
    print(f"  중계 → {RPI}  (median은 뷰어 내장)")
    print("=" * 52)
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
