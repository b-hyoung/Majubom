"""
CSI (Wi-Fi Channel State Information) 데이터 수신 서버 (포트: 5003)
TODO: CSI 모듈 연결 후 구현
"""

# from flask import Flask, request, jsonify
# from flask_cors import CORS
# from datetime import datetime
#
# app = Flask(__name__)
# CORS(app)
#
# latest = {"amplitude": None, "phase": None, "received_at": None}
#
# @app.route("/csi", methods=["POST"])
# def receive_csi():
#     data = request.get_json(silent=True)
#     if not data:
#         return jsonify({"error": "JSON body required"}), 400
#     # TODO: 필드 정의 후 파싱
#     return jsonify({"ok": True}), 200
#
# @app.route("/csi/latest", methods=["GET"])
# def get_latest():
#     return jsonify(latest)
#
# if __name__ == "__main__":
#     print("CSI 서버 시작 → http://0.0.0.0:5003")
#     app.run(host="0.0.0.0", port=5003, debug=True)

if __name__ == "__main__":
    print("csi_server.py — 아직 미구현 (주석 해제 후 사용)")
