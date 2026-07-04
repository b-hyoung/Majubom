# 마주봄 · 중간 발표 (웹 PPT)

자체완결 HTML 발표 자료. 슬라이드 10장 + 3번 슬라이드에 **실시간 통합 대시보드(시뮬)** 임베드.

## 🚀 서버 한 번에 켜기

```bash
cd esp32/docs
./serve_ppt.sh            # 로컬 발표만
./serve_ppt.sh --share    # + 외부 공유 링크(Cloudflare 터널)
```

- 로컬: <http://localhost:8088/presentation/>
- `--share` 시 출력되는 `https://....trycloudflare.com` 뒤에 **`/presentation/`** 를 붙여 공유

> 스크립트 없이 직접 띄우려면:
> ```bash
> cd esp32/docs && python3 -m http.server 8088
> ```

## 📌 알아둘 것

- **서버는 8088 하나면 끝.** 발표(`/presentation/`)와 라이브 대시보드(`/dashboard/`)를 같은 서버가 서빙.
- 3번 슬라이드 라이브 대시보드는 실제 통합 대시보드(`site/index.html` 복사본)를 **`?sim=1` 시뮬레이터**로 띄움 → **CSI/ToF/mmWave 백엔드가 꺼져 있어도** 데모 데이터로 동작. (원격 공유 시에도 클라이언트 사이드라 살아 있음)
- 발표 자료가 켜져 있으려면 **이 맥에서 서버(와 `--share` 시 터널)가 계속 떠 있어야** 함. 절전/종료 시 끊김.
- 조작: `←` `→` 또는 `Space` 로 넘김, `F` 풀스크린.

## 🖼 이미지 자료 (img/)

- `hero_concept.png` — 타이틀 시스템 3D 개념도
- `mmw_monitor.png` — mmWave 보행 모니터(시뮬) 캡처
- `tof_demo1.gif` / `tof_demo2.gif` — ToF 데모(침대 Top view / 라이브 대시보드)
- `method_*.png`, `csi_*.png`, `c_strength_dist.png` — CSI 측정 방식·결과 차트

---

## 📚 전체 자료 인덱스 (PDF·HTML·MD 어떤 파일인지)

> **보는 법** — PDF/HTML: `open <경로>` 또는 로컬서버 `http://localhost:8088/presentation/<파일>`.
> **공유링크(Cloudflare, 영구)**: `https://ppt-list.pages.dev/majubom/refs/<파일>` (GitHub Pages: `https://b-hyoung.github.io/PPT-List/majubom/refs/<파일>`)
> 경로는 모두 `esp32/docs/presentation/` 기준 (mmWave 작업만 별도 표기).

### 🔬 리서치·참고자료 (PDF — 공유 가능)
| 파일 | 내용 | 공유링크(refs/) |
|---|---|---|
| **existing_solutions_mmwave.pdf** | **기존방안 + 상용문제 + 상용레이더문제 + mmWave문제 + 센서별 실전버그 + 오늘 결론 (종합)** | `refs/existing_solutions_mmwave.pdf` |
| bedexit_papers.pdf | 침대이탈 논문 (환각체크 배지) | `refs/bedexit_papers.pdf` |
| mmwave_hw_matched_papers.pdf | 우리 칩(IWR6843) 일치 선행논문 | `refs/mmwave_hw_matched_papers.pdf` |
| mmwave_temporal_approach.pdf | 시계열 접근 (PNHM/mPCT 분석) | `refs/mmwave_temporal_approach.pdf` |
| evidence_onepager.pdf | 치매 낙상 근거 + 시스템 한 장 | `refs/evidence_onepager.pdf` |
| competitors_preview.html | 상용 4개(사이렌케어·Hikvision·Vayyar·SafelyYou) 조사 | `refs/competitors_preview.html` |

### 🖥 발표 덱
| 파일 | 내용 | 위치 |
|---|---|---|
| index.html | 마주봄 중간발표 (흰색, 10슬라이드, 라이브 대시보드) | 로컬 `8088/presentation/` |
| 마주봄 다크 덱 (신규) | 문제→차별→데모→실측, 다크·PPT/PDF 대응 | **PPT-List 레포** → `https://ppt-list.pages.dev/majubom/` |

### 🎬 작업용 미리보기·시연 (HTML)
| 파일 | 내용 | 위치 |
|---|---|---|
| shoot_script.html | **시연 영상 촬영 대본** | `https://ppt-list.pages.dev/majubom/shoot-script.html` |
| shoot_plan.html | 시연 시나리오 + 데모 슬라이드 레이아웃 | 로컬 |
| demo_layout.html | 단일 데모 슬라이드 목업 (좌영상/우대시보드) | 로컬 |
| palette_preview.html / font_preview.html | 덱 팔레트·폰트 미리보기 | 로컬 |

### 🛰 mmWave 이탈예측 작업 (`mmWave/exit_seq/`)
| 파일 | 내용 | 링크 |
|---|---|---|
| **HANDOFF.md** | **인수인계 전체** (상태·결과·다음할일·명령·원칙) | `github.com/b-hyoung/Majubom/blob/main/mmWave/exit_seq/HANDOFF.md` |
| TODO_2026-07-05.md | 내일 할 일 목록 | 로컬 |
| co_log.py / record_seq.py | ToF라벨+mmWave시퀀스 수집기 | 로컬 |
| analyze_timeseries.py / analyze_velocity.py | 시간순 LSTM vs 뭉갬 RF 분석 | 로컬 |
