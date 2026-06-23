# 📤 서버로 보내는 CSI 데이터 (2026-06-15)

> `POST /csi`로 가는 JSON 형식 설명.
> **CSI는 측정값(raw + quality)만 보냄. baseline·z-score·알람단계는 서버가 DB로 계산.**

---

## 무엇이 바뀌었나

`/csi`가 받는 데이터 형식이 바뀜.

**전 (옛날 — ESP32가 진폭 통계를 직접 쏘던 형식)**
```json
{ "seq", "rssi", "channel", "noise_floor", "amp_mean", "amp_std" }
```

**후 (지금 — 분석된 측정값을 보냄)**
```json
{ "timestamp", "bed_id", "sensor",
  "raw": { "hr_bpm", "resp_rpm", "autocorr_strength" },
  "quality": { "reliable", "samples_count", "duration_sec" } }
```

→ `amp_mean/amp_std` 기준 수신·대시보드 코드를 **아래 새 형식 기준으로 교체**.

---

## CSI가 보내는 JSON (각 필드 설명)

```jsonc
{
  "timestamp": "2026-06-15T14:30:00Z",   // 측정 시각 (UTC, ISO 8601)
  "bed_id": "bed_03",                     // 침대 식별자 (어느 환자인지)
  "sensor": "csi",                        // 센서 종류 (항상 "csi")

  "raw": {                                // ── 원본 측정값 ──
    "hr_bpm": 78,                         // 심박수 (분당 박동수)
    "resp_rpm": 16,                       // 호흡수 (분당 호흡수)
    "autocorr_strength": 0.41             // 신호 품질 0~1 (1에 가까울수록 깨끗)
  },

  "quality": {                            // ── 측정 신뢰도 ──
    "reliable": true,                     // 믿을 만한 측정인가 (false면 무시)
    "samples_count": 8800,                // 받은 WiFi 패킷 수
    "duration_sec": 90                    // 측정 시간(초)
  },

  "presence": {                           // ── 인원수 (방에 몇 명) ──
    "count": 1,                           // 1=환자 혼자, 2=보호자/간호사 동석
    "confidence": null,                   // CNN 확신도 (지금은 모델 미학습 → null)
    "gate_active": true                   // 측정 신뢰 게이트 (2명이면 false → 알람 보류)
  }
}
```

> 숫자는 예시값. 매 측정(60초)마다 채워져 옴.
> **`presence`는 현재 임시 기본값**(`count:1, gate_active:true`). 재실감지 CNN 학습(Phase 3) 후 실제 1/2명 추론값으로 채워짐.

---

## 서버가 DB로 계산할 값

CSI는 위 raw만 보냄. **평소값(baseline)·변화량(z-score)·알람단계는 서버가 DB에 쌓인 과거로 계산**.
(baseline은 결국 과거 데이터라 DB가 원본 — CSI가 또 들고 있을 필요 없음.)

### 1) baseline — 환자별 평소값 (최근 N일 raw로 집계)
| 값 | 계산 |
|---|---|
| `hr_mu`, `hr_sigma` | `hr_bpm`의 평균 / 표준편차 |
| `resp_mu`, `resp_sigma` | `resp_rpm`의 평균 / 표준편차 |
| `strength_mu`, `strength_sigma` | `autocorr_strength`의 평균 / 표준편차 |
| `age_days` | 누적 일수 (30일↑이면 안정) |

### 2) z-score — 평소 대비 변화량 (생체 신호 2개만)
```
z_hr      = (hr_bpm   - hr_mu)   / hr_sigma
z_resp    = (resp_rpm - resp_mu) / resp_sigma
total_abs = |z_hr| + |z_resp|     # 종합 위험점수 (심박·호흡만)
```
> ⚠️ **신호강도(autocorr_strength)는 위험점수에서 제외.** 생체 신호가 아니라 측정 품질 지표라,
> 이상탐지 점수에 넣으면 환경 노이즈가 가짜 경고를 유발. → **신뢰 게이트(reliable)로만 사용** (강도 ≥ 0.30이면 측정 채택, 아니면 무시).

### 3) alert_level — total_abs로 단계 + 색상
| 단계 | 조건 (`total_abs`) | 색 | 행동 |
|---|---|---|---|
| `normal` | < 2 | 🟢 | 표시만 |
| `caution` | 2 ~ 4 | 🟡 | 표시만 |
| `warning` | 4 ~ 6 | 🟠 | 확인 권장 |
| `critical` | ≥ 6 | 🔴 | 즉시 알람 |

### 예외 처리 (필수)
- `presence.gate_active == false` (2명 동석) → 신호 섞여 측정 신뢰 X → **알람 보류** (화면엔 "보호자 방문 중" 표시)
- `quality.reliable == false` → 그 측정 무시 (알람·갱신 X)
- `age_days < 14` (학습 초기) → z-score 신뢰 낮음, 정식 알람 보류
- **절대 임계**: `hr_bpm > 140` 또는 `< 40` → baseline 무관 즉시 알람
