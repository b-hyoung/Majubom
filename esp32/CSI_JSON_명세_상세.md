# 📤 박형석(CSI) → 서버 JSON 명세 + 한글 해석

> 김도경(서버) 공유용 상세 명세.
> 시스템 전체 흐름은 [`시스템_데이터흐름_웹명세.md`](./시스템_데이터흐름_웹명세.md) 참조.
> 작성: 2026-06-15

---

## 📡 엔드포인트

```
POST http://192.168.0.48:5001/csi
Content-Type: application/json
주기      : 60초마다 1회 (또는 알람 이벤트 시 즉시)
송신 주체  : Pi 5 측 Python 분석기 (ESP32에서 받은 raw CSI를 60초 누적·분석 후 송신)
```

---

## 🎯 Phase 1 — 현재 단계 (raw 값만)

### JSON (한글 주석 포함)
```js
{
  "timestamp": "2026-06-15T14:30:00Z",  // 측정 시점 (UTC, ISO 8601)
  "bed_id": "bed_01",                    // 침대 식별자 (병동 + 번호, 예: ward1_bed01)
  "sensor": "csi",                       // 센서 종류 (서버가 라우팅용으로 사용)

  "raw": {
    "hr_bpm": 73,                        // 심박수 (분당, 자기상관 기반)
    "resp_rpm": 14,                      // 호흡수 (분당, Welch PSD 기반)
    "autocorr_strength": 0.45            // 신호 품질 점수 (0~1, 높을수록 깨끗)
  },

  "quality": {
    "reliable": true,                    // 신뢰 가능 여부 (강도 ≥ 0.30이면 true)
    "samples_count": 9000,               // 90초 동안 수신한 CSI 패킷 수 (참고용)
    "duration_sec": 90                   // 실제 측정 시간 (초)
  }
}
```

### 필드별 상세 해석

| 필드 | 타입 | 단위 | 의미 | 어떻게 계산? |
|---|---|---|---|---|
| `timestamp` | string | ISO 8601 | 측정 시점 (UTC) | 측정 종료 시 `datetime.utcnow().isoformat()` |
| `bed_id` | string | - | 환자/침대 식별자 | 설정값. 침대마다 고유 |
| `sensor` | string | - | "csi" 고정 | 서버가 엔드포인트 분기용 |
| `raw.hr_bpm` | int | bpm | 1분당 심박수 | 위상 자기상관 peak lag → `60 × 100 / lag` |
| `raw.resp_rpm` | float | rpm | 1분당 호흡수 | Welch PSD 0.1~0.5Hz peak × 60 |
| `raw.autocorr_strength` | float | 0~1 | 신호에서 심박 박자가 얼마나 또렷한가 | `ac[lag] / ac[0]` |
| `quality.reliable` | bool | - | 측정 신뢰 가능 여부 | 강도 ≥ 0.30이면 true |
| `quality.samples_count` | int | 개 | 90초 누적 CSI 패킷 수 | 보통 8000~11000개 |
| `quality.duration_sec` | int | 초 | 측정 길이 | 보통 90초 (또는 60초 슬라이딩) |

---

## 🎯 Phase 2 — 베이스라인 학습 후 (z-score 추가)

### JSON
```js
{
  "timestamp": "2026-06-15T14:30:00Z",
  "bed_id": "bed_01",
  "sensor": "csi",

  "raw": {
    "hr_bpm": 73,                        // 현재 측정 심박수
    "resp_rpm": 14,                      // 현재 측정 호흡수
    "autocorr_strength": 0.45            // 현재 신호 품질
  },

  "zscore": {                            // 평소(베이스라인) 대비 변화량
    "hr": 0.4,                           // HR z-score (평소 평균 - 표준편차 단위)
    "resp": -0.3,                        // 호흡 z-score
    "strength": 0.2,                     // 신호 안정성 z-score
    "total_abs": 0.9                     // 통합 위험 점수 (Σ|z|, 합계)
  },

  "baseline": {                          // 현재 베이스라인 정보
    "age_days": 7,                       // 누적 일수 (30일 권장)
    "hr_mu": 71.5,                       // 학습된 HR 평균
    "hr_sigma": 5.8,                     // 학습된 HR 표준편차
    "resp_mu": 13.7,                     // 호흡 평균
    "resp_sigma": 2.1,                   // 호흡 표준편차
    "strength_mu": 0.42,                 // 강도 평균
    "strength_sigma": 0.08               // 강도 표준편차
  },

  "alert_level": "normal",               // 알람 단계 (normal/caution/warning/critical)

  "quality": {
    "reliable": true,
    "samples_count": 9000,
    "duration_sec": 90
  }
}
```

### z-score 필드 해석

| 필드 | 의미 | 양수 의미 | 음수 의미 |
|---|---|---|---|
| `zscore.hr` | "평소 대비 HR이 얼마나 다른가" | 평소보다 빠름 (각성/스트레스) | 평소보다 느림 (이완) |
| `zscore.resp` | 호흡 변화 | 평소보다 빠른 호흡 | 평소보다 느린 호흡 |
| `zscore.strength` | 신호 안정성 변화 | 평소보다 깨끗 (좋은 환경) | 평소보다 노이즈 많음 |
| `zscore.total_abs` | \|z\| 합계 = **종합 위험도** | (절댓값 합이라 항상 ≥ 0) | - |

### baseline 필드 해석

| 필드 | 의미 |
|---|---|
| `baseline.age_days` | 베이스라인 학습 누적 일수. 30일 권장. **1주 미만이면 z-score 신뢰도 낮음** |
| `baseline.X_mu` | 30일 평균 (그 환자의 "평소" 값) |
| `baseline.X_sigma` | 표준편차 (평소 자연 변동 폭) |

### alert_level 4단계

| 단계 | 조건 | 의미 | UI 색상 권장 |
|---|---|---|---|
| `normal` | Σ\|z\| < 2 | 평소와 다르지 않음 | 🟢 초록 |
| `caution` | 2 ≤ Σ\|z\| < 4 | 평소보다 약간 다름 (관찰) | 🟡 노랑 |
| `warning` | 4 ≤ Σ\|z\| < 6 | 명확한 변화 (경고, 간병인 확인 권장) | 🟠 주황 |
| `critical` | Σ\|z\| ≥ 6 | 큰 변화 (즉시 대응 알람) | 🔴 빨강 |

> ⚠️ **임계값은 잠정**. 30일 운영 후 false positive 비율 보고 조정 필요.

---

## 🎯 Phase 3 — 작업 2 (재실감지) 통합 후

### JSON
```js
{
  "timestamp": "2026-06-15T14:30:00Z",
  "bed_id": "bed_01",
  "sensor": "csi",

  "raw": { /* Phase 1 동일 */ },
  "zscore": { /* Phase 2 동일 */ },
  "baseline": { /* Phase 2 동일 */ },

  "presence": {                          // 재실 감지 결과 (CNN 출력)
    "count": 1,                          // 인원수 (1 = 환자만, 2 = 환자+다른 사람)
    "confidence": 0.96,                  // CNN 분류 확률 (0~1)
    "gate_active": true                  // HRV z-score 신뢰 게이트
                                         //   true  = 1명 감지 → z-score 신뢰
                                         //   false = 2명+ 감지 → z-score 무효화
                                         //           (간병인/방문자 있어서 측정 오염)
  },

  "alert_level": "normal",               // gate_active=false이면 무조건 "normal"

  "quality": { /* Phase 1 동일 */ }
}
```

### presence 필드 해석

| 필드 | 의미 |
|---|---|
| `presence.count` | 방에 있는 사람 수. **현재 모델은 1명 vs 2명+ 이진 분류** |
| `presence.confidence` | CNN softmax 출력 확률. 0.9+ 권장 임계 |
| `presence.gate_active` | **이게 false면 z-score 신뢰 X** → 서버는 알람 보류 |

### 게이트 로직 (서버 측 처리 권장)

```python
# Pi 5 서버에서 받았을 때
if not data["presence"]["gate_active"]:
    # 2명+ 감지 → HRV 측정 오염 → 알람 무시
    log_event("CSI: 2명 감지 — HRV 측정 보류")
    return
else:
    # 1명 단독 → z-score 평가 진행
    process_alert(data["alert_level"], data["zscore"])
```

---

## 📊 시나리오 3가지 — 실제 JSON 예시

### 시나리오 1 — 평소 정상 상태
```js
{
  "timestamp": "2026-06-15T14:30:00Z",
  "bed_id": "bed_01",
  "sensor": "csi",
  "raw": {
    "hr_bpm": 72,                  // 평소 HR과 거의 같음
    "resp_rpm": 14,                // 평소 호흡과 거의 같음
    "autocorr_strength": 0.45      // 신호 깨끗
  },
  "zscore": {
    "hr": -0.1,                    // 평소 거의 동일
    "resp": 0.2,                   // 평소 거의 동일
    "strength": 0.3,
    "total_abs": 0.6               // < 2 → normal
  },
  "presence": { "count": 1, "confidence": 0.97, "gate_active": true },
  "alert_level": "normal"
}
// → 🟢 평소대로. 알람 없음.
```

### 시나리오 2 — 자율신경 흔들림 (주의)
```js
{
  "timestamp": "2026-06-15T15:00:00Z",
  "bed_id": "bed_01",
  "sensor": "csi",
  "raw": {
    "hr_bpm": 84,                  // 평소보다 12 빨라짐
    "resp_rpm": 18,                // 평소보다 4 빨라짐
    "autocorr_strength": 0.38      // 신호 약간 흔들림
  },
  "zscore": {
    "hr": 2.1,                     // 평소보다 +2.1σ
    "resp": 2.0,                   // 평소보다 +2.0σ
    "strength": -0.5,
    "total_abs": 4.6               // 4~6 → warning
  },
  "presence": { "count": 1, "confidence": 0.95, "gate_active": true },
  "alert_level": "warning"
}
// → 🟠 자율신경 흥분 의심. 간병인 확인 권장.
```

### 시나리오 3 — 간병인 방문 (게이트 차단)
```js
{
  "timestamp": "2026-06-15T16:00:00Z",
  "bed_id": "bed_01",
  "sensor": "csi",
  "raw": {
    "hr_bpm": 95,                  // 환자+간병인 신호 섞임 → 부정확
    "resp_rpm": 22,                // 마찬가지
    "autocorr_strength": 0.18      // 신호 매우 흔들림
  },
  "zscore": {
    "hr": 4.0,                     // 매우 큼 (하지만 false)
    "resp": 4.5,
    "strength": -3.0,
    "total_abs": 11.5              // 매우 높음
  },
  "presence": {
    "count": 2,                    // ← 2명 감지!
    "confidence": 0.93,
    "gate_active": false           // ← 게이트 차단
  },
  "alert_level": "normal"          // ← 게이트 false면 강제 normal
}
// → ⚪ 알람 발생 X. "간병인 방문 중, 측정 보류" 로그만 기록.
```

---

## 🔄 데이터 처리 순서 (CSI 측 내부)

```
[ESP32 RX]
  CSI raw 데이터 수신 (100Hz, 60초 = 6000 패킷)
       ↓
  WiFi/MQTT로 Pi 5에 전달
       ↓
[Pi 5 측 Python 분석기]
  raw CSV 수신
       ↓
  [1단계] 신호 추출
    - 진폭 + 위상 추출
    - PCA로 차원 축소
    - BPF 0.8~2.0Hz (심박)
    - Welch PSD (호흡)
       ↓
  [2단계] 핵심 값 계산
    - HR = 위상 자기상관 lag → 60 × FS / lag
    - 호흡 = Welch peak frequency × 60
    - 강도 = ac[lag] / ac[0]
       ↓
  [3단계] 베이스라인 비교 (Phase 2+)
    - baseline.json 로드
    - z = (현재 - μ) / σ
    - alert_level 결정
       ↓
  [4단계] 재실 분류 (Phase 3+)
    - presence_cnn.pt 모델 추론
    - count + confidence
    - gate_active 결정
       ↓
  [5단계] JSON 패키징 + POST
    - 위 정보 모두 JSON으로
    - POST /csi
```

---

## ⏱ 타이밍 정리

| 항목 | 주기 |
|---|---|
| CSI raw 수신 (ESP32 → Pi) | 실시간 (~100Hz) |
| 분석 실행 | **60초마다** |
| POST /csi | **60초마다 1회** (or 알람 시 즉시) |
| baseline.json 갱신 | 1일 1회 (새 측정 누적) |
| 30일 베이스라인 학습 완료 | 입소 후 30일 |

---

## 🛠 김도경(서버) 측 처리 권장

### 1. 수신 후 저장
```python
@app.route("/csi", methods=["POST"])
def receive_csi():
    data = request.json
    db.insert("csi_log", data)        # 모든 데이터 저장 (추세용)
    return "ok", 200
```

### 2. 게이트 + 알람
```python
if not data["presence"]["gate_active"]:
    return  # 2명+ → 알람 보류

level = data["alert_level"]
if level in ["warning", "critical"]:
    notify_caregiver(data)
```

### 3. 대시보드 갱신
```python
update_dashboard(data["bed_id"], {
    "hr": data["raw"]["hr_bpm"],
    "resp": data["raw"]["resp_rpm"],
    "zscore": data["zscore"]["total_abs"],
    "level": data["alert_level"],
    "presence": data["presence"]["count"]
})
```

---

## ❓ FAQ (예상 질문 답변)

### Q1. baseline_age_days < 7이면 어떻게?
→ z-score 신뢰도 낮음. UI에서 "학습 중 (X일째)" 표시. 알람 발생해도 "관찰 권장" 정도로 약하게.

### Q2. presence.confidence가 0.6 같이 낮으면?
→ 게이트 active=true로 두되, 알람 임계 더 보수적으로. 또는 UI에 "분류 불확실" 표시.

### Q3. 30일 베이스라인 학습 중인데 환자가 갑자기 위험 상태면?
→ **절대 위험 임계 병행**:
```python
if data["raw"]["hr_bpm"] > 140 or data["raw"]["hr_bpm"] < 40:
    emergency_alert()  # 베이스라인 무관 즉시 알람
```

### Q4. CSI 측정 실패 시 (Hz 너무 낮음 등)?
→ `quality.reliable = false`로 보냄. 서버는 그 측정 무시.

### Q5. 다른 침대 환자 신호 섞이지 않나?
→ ESP32 페어를 침대마다 다른 WiFi 채널 사용. softAP 채널 1, 6, 11 분리.

---

## 📂 관련 파일

| 파일 | 내용 |
|---|---|
| `시스템_데이터흐름_웹명세.md` | 전체 시스템 + 다른 센서 명세 |
| `presence_cnn.py` | CNN 학습/추론 코드 |
| `vital_csi3.py` | CSI 분석기 (HR/호흡/강도 추출) |
| `baseline.json` | 30일 베이스라인 저장 (Pi 5 측) |
| **`CSI_JSON_명세_상세.md`** | **이 문서** |

---

## 📜 이력

| 날짜 | 변경 |
|---|---|
| 2026-06-15 | 작성. Phase 1/2/3별 JSON + 시나리오 3개 + 한글 해석 |
