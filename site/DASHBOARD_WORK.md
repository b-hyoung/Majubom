# 대시보드 디자인 작업 가이드 (dashboard_design.html)

> 2026-07-05. **작업 파일 = `site/dashboard_design.html`** (원본 index.html의 디자인용 복사본).
> 목적: 실배치(사진) 기준 평면도 + mmWave 실위치 포인터. **CSI는 시스템에서 제외됨(패널 전부 삭제).**

---

## 1. 보는 법 / 서버

```bash
# 디자인 미리보기 (편집 → 새로고침)
python3 -m http.server 8091 --bind 127.0.0.1 --directory site
# → http://localhost:8091/dashboard_design.html
```

mmWave 실위치까지 보려면 (위치 파이프라인):
```bash
# ① 위치 수신 서버 (5002)
cd server && python3 mmw_server.py

# ② 센서 리더 (USB 연결 후) — cfg는 센서가 조용할 때만 --cfg 로
cd mmWave && python3 send_mmw.py --cli /dev/cu.usbserial-011D1AD60 --data /dev/cu.usbserial-011D1AD61
#   센서 멈췄으면 먼저: python3 -c "import sys;sys.path.insert(0,'.');from send_mmw import send_config;send_config('/dev/cu.usbserial-011D1AD60','AOP_bed_2m7_d15.cfg')"

# ③ 위치값 직접 확인
curl -s localhost:5002/mmw/live | python3 -m json.tool
```

⚠️ **USB 불안정**: DATA가 자주 멈춤 → cfg 재전송으로 복구, 안 되면 USB 둘 다 뽑고 10초 후 재연결.
⚠️ **send_mmw 실행 중 --cfg 재전송하면 다시 죽는 패턴** 있었음 → 스트리밍 확인 후 --cfg 없이 실행 권장.

---

## 2. 완료된 것

- ✅ CSI 완전 제거 (심박/호흡·z-score·판정흐름·상태상세 패널, 칩·배지·범례) + JS null 가드
- ✅ 병실 평면도 실배치 재작성 (`<svg id="roomSvg">`): 침대(하단·가로·머리=좌) · ToF 머리/발 · **mmWave 천장 마운트(좌하단, ↓)** · 모니터링/라운지(우상단) · 보행경로(침대→모니터링) — 실험실 사진 + 손그림 기준
- ✅ 레이아웃: 평면도 가로 크게(상단) + 위험정보 아래
- ✅ **mmWave 실위치 포인터**: `/mmw/live` 0.5초 폴링 → 노란 점(`#mmw-pointer`) 이동. **실센서 연동 확인됨** (타겟 3명 잡힘, 움직임 반영)

## 3. ❗ 해야 할 것 (우선순위)

### ① 좌표 매핑 캘리브레이션 — 지금 위치가 어긋남
파일 안 `pollMmwPos` 근처:
```js
const MMW_ORIGIN={x:37,y:178}, MMW_SCALE=32;   // 원점(센서 SVG좌표) · px/m — 임시값!
let X=MMW_ORIGIN.x + py*MMW_SCALE, Y=MMW_ORIGIN.y - px*MMW_SCALE;  // 축·부호 — 한 번 스왑함
```
**방법**: 사람이 ①침대 정중앙 ②침대 발치 옆 ③모니터링 데스크 앞 에 서서, 각 지점의 `/mmw/live` 좌표(x,y)를 기록 → 평면도 SVG 좌표와 대응시켜 ORIGIN/SCALE/축·부호 확정.
- 대응: 센서좌표 y=거리(m), x=좌우(m). 평면도는 340×200 viewBox.
- 증상별: 90도 꺾여 움직임 → px/py 스왑. 반대로 움직임 → 부호 반전. 너무 크게/작게 → SCALE 조정.

### ② 환자만 추적 (지금은 targets[0]=아무나)
방에 여러 명이면 팀원을 따라감. `send_mmw`가 이미 **환자 특정**(침대 이력 기반 main_tid)을 계산함 → `/mmw/latest`의 presence(main_tid)와 매칭해 그 tid만 포인터로.

### ③ (선택) 옛 시뮬 애니메이션 재배선
`rvLoop()` 안 애니메이션(환자점·스캔콘)은 옛 세로 좌표라 **비활성화해둠**. 새 가로 배치 좌표로 다시 그리면 시뮬 모드가 더 살아남.

### ④ (선택) mmWave 점군 뷰어 패널
`/mmw/viz`(top view 렌더)가 mmw_server에 이미 있음. **유현기 crop_to_roi(침대 위 사람만)** 점군을 `/mmw/live`의 points로 POST하면 침대 인원만 표시 가능.

---

## 4. 파일 안 주요 위치

| 뭐 | 어디 |
|---|---|
| 병실 평면도 | `<svg id="roomSvg">` (침대·센서·데스크·보행경로) |
| 실위치 포인터 | `<circle id="mmw-pointer">` (SVG 안) |
| 위치 폴링·매핑 | `pollMmwPos()` + `MMW_ORIGIN`/`MMW_SCALE` |
| 시뮬 목데이터 | `toggleSim()` / `simTick()` (자동 ON 스크립트 맨 아래) |
| 위험 게이지 | `renderCsi()` (이름만 csi, 실제 융합 게이지 — CSI 요소는 가드됨) |

## 5. 참고 파일

| 파일 | 내용 |
|---|---|
| `site/index.html` | **원본 라이브 대시보드** (건드리지 말 것 — 8090) |
| `server/mmw_server.py` | 5002. `/mmw/live`(위치 POST/GET) · `/mmw/viz`(점군 렌더) · `/mmw/latest`(보행+presence) |
| `mmWave/send_mmw.py` | 센서 리더. TLV 1010 트랙 → 위치 · **환자 특정(main_tid)** · live POST |
| `mmWave/unified_reader.py` | 보행+자세 단일 리더 (포트 1개) — 유현기 |
| `mmWave/posture_probe.py` | `crop_to_roi`(침대 크롭) · 자세 특징(z_cent/n_hi) |
| `mmWave/POSTURE_NOTES.md` | 3m/15° 실측·자세규칙·방어 5종 (유현기) |
| `mmWave/exit_seq/HANDOFF.md` | 이탈예측(LSTM) 전체 인수인계 |
| `cloud_dashboard.py` | 클라우드/프록시 대시보드 (참고) |

## 6. 실배치 (사진 기준 — 평면도가 반영한 것)

- **mmWave**: 천장 근처 삼각대(~3m, 15° 틸트) — cfg `AOP_bed_2m7_d15.cfg`(sensorPosition 3 0 15)
- **ToF ×2**: 침대 머리맡(20cm) + 다리맡(50cm) 스탠드 — 파이(192.168.6.10:5001)
- **침대**: 벽쪽, 파란 시트. 보행 시나리오 = 침대에서 나와 모니터링 데스크 쪽으로 걷기
- **CSI**: 제거됨 (단일안테나 심박 한계 — esp32/CSI 문서 참조)
