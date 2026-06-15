"""
mmWave 레이더 데이터 수신 서버 (포트: 5002)
TODO: ESP32 / 레이더 모듈 연결 후 구현
"""

# from flask import Flask, request, jsonify
# from flask_cors import CORS
# from datetime import datetime
#
# app = Flask(__name__)
# CORS(app)
#
# latest = {"distance_mm": None, "speed": None, "received_at": None}
#
# @app.route("/mmw", methods=["POST"])
# def receive_mmw():
#     data = request.get_json(silent=True)
#     if not data:
#         return jsonify({"error": "JSON body required"}), 400
#     # TODO: 필드 정의 후 파싱
#     return jsonify({"ok": True}), 200
#
# @app.route("/mmw/latest", methods=["GET"])
# def get_latest():
#     return jsonify(latest)
#
# if __name__ == "__main__":
#     print("mmWave 서버 시작 → http://0.0.0.0:5002")
#     app.run(host="0.0.0.0", port=5002, debug=True)

if __name__ == "__main__":
    print("mmw_server.py — 아직 미구현 (주석 해제 후 사용)")
