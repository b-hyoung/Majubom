# CSI 서버 연동 설계 — 2026-06-15

> CSI 파트 → 서버 파트 데이터 연동
> 방향: **A** (서버를 CSI 명세 형식에 맞춤) · CSI는 raw+quality만 전송, 분석값(z-score/baseline/alert)은 서버가 DB로 계산

## 배경 / 문제

- `vital_csi3.py`는 HR·호흡·신호강도를 계산해 **화면 출력만** 함 → 서버로 보내는 경로 없음.
- pull 받은 `server/tof_server.py`의 `/csi`는 **옛날 형식**(`amp_mean/amp_std/seq/rssi/channel`)을 기대 → 내 명세(`hr_bpm/resp_rpm/autocorr_strength`)와 충돌.
- 결정: 서버 `/csi`를 **CSI 명세 형식으로 변경**(방향 A). 단 서버 파일은 서버 파트 소유 → **문서로 변경 지시**하고 서버 파트가 적용.

## 핵심 설계 결정

1. **CSI는 raw + quality만 전송.** baseline·z-score·alert_level은 **서버가 DB로 계산**.
   근거: baseline(평소 μ·σ)은 결국 DB에 쌓인 과거 → DB 가진 쪽이 계산하는 게 단일 출처.
   CSI는 baseline 누적 로직을 만들 필요 없음. z-score/임계 공식은 문서로 전달.
2. **서버 파일 직접 수정 안 함.** 정확한 변경 명세 문서를 만들어 서버 파트가 적용.
3. **계산 알고리즘 불변.** `vital_csi3.py`는 함수 추출 리팩터만, 수식 한 줄도 안 바꿈.

## 데이터 계약 (송신부 → `/csi`)

```json
{
  "timestamp": "2026-06-15T14:30:00Z",
  "bed_id": "bed_01",
  "sensor": "csi",
  "raw":      { "hr_bpm": 73, "resp_rpm": 14, "autocorr_strength": 0.45 },
  "quality":  { "reliable": true, "samples_count": 9000, "duration_sec": 90 },
  "presence": { "count": 1, "confidence": null, "gate_active": true }
}
```

- `timestamp`: UTC ISO 8601.
- `presence`: 재실감지 CNN 추론값. CNN 미학습이라 현재 임시 기본값(`count:1, gate_active:true`) → Phase 3 학습 후 실제값.
- 서버가 DB로 계산: `baseline`(μ·σ), `zscore`(`(x−μ)/σ`, `total_abs`=절댓값 합), `alert_level`(2/4/6 임계). 공식은 `서버연동_변경사항.md`.

## 컴포넌트

### ① `esp32/vital_csi3.py` 리팩터
- 상단 실행부(`parse → PCA → HR/호흡/강도`)를 `analyze(path, use_full=False) -> dict`로 추출.
- 반환(정밀 float, 라운딩은 송신부가): `hr_bpm, resp_rpm, autocorr_strength, reliable, samples_count, duration_sec` + `_detail`(CLI/디버그용 hold·hrv 등).
- `if __name__ == "__main__"` 블록이 `analyze()` 결과로 **기존과 동일하게 출력**.
- **검증 기준**: 리팩터 전후 `python3 vital_csi3.py csi_hold4.csv` / `csi_rest.csv` 출력 동일.
  - 기준값: hold4 = HR 67 / 강도 0.47 / 호흡 6.0 / 4506패킷·45초. rest = HR 62 / 강도 0.62 / 호흡 6.0 / 9949패킷·90초.

### ② `esp32/send_csi.py` (신설)
- 의존성 추가 없음(stdlib `urllib.request`로 POST).
- 사용:
  - `python3 send_csi.py csi_rest.csv` — CSV 분석 → 1회 POST
  - `python3 send_csi.py csi_rest.csv --dry` — POST 안 하고 JSON만 출력(검증용)
  - `python3 send_csi.py csi_rest.csv --bed bed_02` — 침대 지정
  - `python3 send_csi.py --loop` — ESP32 실시간 90초 캡처→분석→POST 반복
- 상수: `SERVER_URL = "http://192.168.0.22:5003/csi"` (csi_server.py 포트 5003), `BED_ID = "bed_01"`, `DURATION = 90`.
- `analyze()` 결과 → 계약 JSON 조립(raw + quality) → POST.
- `--loop`: 시리얼 캡처를 임시 CSV로 모은 뒤 `analyze` (record_csi 로직 재사용, 포트 자동검출).

### ③ `esp32/서버연동_변경사항.md` (신설, 서버 담당용)
- `/csi` 형식 변경점(옛 amp → 새 계약 raw+quality) + 위 JSON 계약.
- **서버가 DB로 계산할 값** + 공식: baseline(μ·σ), z-score(`(x−μ)/σ`, `total_abs`), alert_level(2/4/6).
- 예외 처리: `reliable=false` 무시 / `age_days<14` 알람 보류 / 절대 임계(HR>140·<40).
- 대시보드 카드 변경 지시(amp 표시 → HR/호흡/강도/reliable/alert_level).

### ④ `.gitignore`
- `*.csv` 무시 추가(향후 측정데이터 커밋 방지). 검증용 `csi_hold4.csv` 등은 `!` 예외로 유지.
- 근거: `.git` 137M의 주원인이 커밋된 CSV(~32MB). esp-idf/esp-csi는 submodule이라 git 용량과 무관 → 건드리지 않음.

## 범위 밖 (YAGNI)

- z-score/baseline/alert 계산 — 서버가 DB로 수행(CSI 범위 밖).
- 서버 코드/대시보드 직접 수정 — 서버 파트 담당(문서로 전달).
- esp-idf/esp-csi submodule 제거 — git 용량과 무관, 펌웨어 빌드 위험.
- 재실감지(Phase 3 presence) — 발표 후.
