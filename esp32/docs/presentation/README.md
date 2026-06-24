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
