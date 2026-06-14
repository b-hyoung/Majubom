"""
작업 2 (재실감지) — CNN 학습/추론
=====================================

논문 C (Yakub et al., IEEE ICETSIS 2024) 구조 100% 이식.
파일명으로 라벨 자동 인식:
  csi_1p_*.csv → 라벨 0 (1명)
  csi_2p_*.csv → 라벨 1 (2명+)

사용:
  python presence_cnn.py --train       # 가진 데이터로 학습
  python presence_cnn.py --predict X.csv  # 새 측정 추론
  python presence_cnn.py --features    # 학습 X, 특징만 시각화 (1명 데이터만 있을 때)

의존:
  - PyTorch 2.x (CPU 가능)
  - scikit-learn
  - matplotlib

데이터 형식:
  각 CSI csv → (2, 52, 2000) 텐서로 변환
    채널 2 = 진폭 + 위상
    서브캐리어 52
    시간 샘플 2000 (= 20초 × 100Hz)
"""
import sys, re, argparse, json
from pathlib import Path
import numpy as np

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.decomposition import PCA
from sklearn.svm import OneClassSVM
from sklearn.model_selection import train_test_split


DATA_DIR = Path(__file__).parent
OUT_DIR = DATA_DIR / "presence_out"
OUT_DIR.mkdir(exist_ok=True)

FS = 100.0
WIN_SEC = 20.0
WIN_SAMPLES = int(FS * WIN_SEC)
N_SUBCARRIER = 52


# ──────────────────────────────────────────────
# 데이터 파이프라인
# ──────────────────────────────────────────────
def parse(path):
    rec = []
    for line in open(path, errors="ignore"):
        if "CSI_DATA" not in line:
            continue
        line = line[line.index("CSI_DATA"):]
        m = re.search(r'"\[(.*?)\]"', line)
        if not m:
            continue
        try:
            arr = [int(x) for x in m.group(1).split(",")]
        except ValueError:
            continue
        if len(arr) < 128:
            continue
        p = line.split(",", 24)
        try:
            ts = int(p[18])
        except (ValueError, IndexError):
            continue
        rec.append((ts, arr[:128]))
    if not rec:
        return None, None
    ts = np.array([r[0] for r in rec], float)
    raw = np.array([r[1] for r in rec])
    I = raw[:, 0::2].astype(float)
    Q = raw[:, 1::2].astype(float)
    return ts, I + 1j * Q


def to_window(comp, win_samples=WIN_SAMPLES):
    """복소 CSI → (2, 52, T) 텐서. 진폭+위상 z-score 정규화."""
    amp = np.abs(comp)
    phase = np.unwrap(np.angle(comp), axis=0)
    # 길이 맞춤
    if amp.shape[0] < win_samples:
        pad = win_samples - amp.shape[0]
        amp = np.pad(amp, ((0, pad), (0, 0)), mode="edge")
        phase = np.pad(phase, ((0, pad), (0, 0)), mode="edge")
    amp = amp[:win_samples]
    phase = phase[:win_samples]
    # 서브캐리어 정규화
    if amp.shape[1] > N_SUBCARRIER:
        amp = amp[:, :N_SUBCARRIER]
        phase = phase[:, :N_SUBCARRIER]
    elif amp.shape[1] < N_SUBCARRIER:
        pad = N_SUBCARRIER - amp.shape[1]
        amp = np.pad(amp, ((0, 0), (0, pad)))
        phase = np.pad(phase, ((0, 0), (0, pad)))
    amp = (amp - amp.mean(0)) / (amp.std(0) + 1e-9)
    phase = (phase - phase.mean(0)) / (phase.std(0) + 1e-9)
    return np.stack([amp.T, phase.T])  # (2, 52, T)


def load_dataset():
    """csi_1p_*.csv → 라벨 0, csi_2p_*.csv → 라벨 1 자동 수집."""
    samples, labels, fnames = [], [], []
    for f in sorted(DATA_DIR.glob("csi_*p_*.csv")):
        m = re.match(r"csi_(\d)p_", f.name)
        if not m:
            continue
        label = 0 if m.group(1) == "1" else 1
        ts, comp = parse(f)
        if ts is None:
            print(f"  ✗ {f.name} 로드 실패")
            continue
        x = to_window(comp)
        samples.append(x)
        labels.append(label)
        fnames.append(f.name)
        print(f"  ✓ {f.name}  label={label}  shape={x.shape}")
    if not samples:
        return None, None, None
    return np.stack(samples), np.array(labels), fnames


def load_1p_only():
    """1명 데이터만 로드 (학습 X, 특징 시각화용)."""
    samples, fnames = [], []
    pattern = ["csi_1p_*.csv", "csi_rest*.csv", "csi_watch*.csv", "csi_hold*.csv"]
    for pat in pattern:
        for f in sorted(DATA_DIR.glob(pat)):
            ts, comp = parse(f)
            if ts is None:
                continue
            samples.append(to_window(comp))
            fnames.append(f.name)
    return (np.stack(samples), fnames) if samples else (None, None)


# ──────────────────────────────────────────────
# CNN 모델 (논문 C 구조)
# ──────────────────────────────────────────────
if HAS_TORCH:
    class PresenceCNN(nn.Module):
        def __init__(self, n_classes=2, n_subcarrier=N_SUBCARRIER, t_samples=WIN_SAMPLES):
            super().__init__()
            in_ch = 2 * n_subcarrier
            self.conv1 = nn.Conv1d(in_ch, 64, kernel_size=10, padding=5)
            self.bn1 = nn.BatchNorm1d(64)
            self.pool1 = nn.MaxPool1d(4)
            self.conv2 = nn.Conv1d(64, 128, kernel_size=5, padding=2)
            self.bn2 = nn.BatchNorm1d(128)
            self.pool2 = nn.MaxPool1d(4)
            t_out = t_samples // 16
            self.fc1 = nn.Linear(128 * t_out, 256)
            self.bn3 = nn.BatchNorm1d(256)
            self.do1 = nn.Dropout(0.5)
            self.fc2 = nn.Linear(256, 128)
            self.bn4 = nn.BatchNorm1d(128)
            self.do2 = nn.Dropout(0.5)
            self.fc3 = nn.Linear(128, n_classes)

        def forward(self, x):
            b, c, sub, t = x.shape
            x = x.reshape(b, c * sub, t)
            x = torch.relu(self.bn1(self.conv1(x)))
            x = self.pool1(x)
            x = torch.relu(self.bn2(self.conv2(x)))
            x = self.pool2(x)
            x = x.flatten(1)
            feat = torch.relu(self.bn3(self.fc1(x)))
            x = self.do1(feat)
            x = torch.relu(self.bn4(self.fc2(x)))
            x = self.do2(x)
            return self.fc3(x), feat


# ──────────────────────────────────────────────
# 학습
# ──────────────────────────────────────────────
def train_mode():
    if not HAS_TORCH:
        print("ERROR: PyTorch 미설치. pip install torch 후 재시도.")
        sys.exit(1)
    print("=" * 60)
    print("📦 데이터셋 로드 (csi_1p_*.csv + csi_2p_*.csv)")
    print("=" * 60)
    X, y, fnames = load_dataset()
    if X is None:
        print("\n❌ 데이터 없음. csi_1p_01.csv, csi_2p_01.csv 같은 파일이 필요.")
        sys.exit(1)
    print(f"\n총 {len(X)}개 샘플 (1명 {(y==0).sum()}개 / 2명 {(y==1).sum()}개)")
    if (y == 0).sum() < 2 or (y == 1).sum() < 2:
        print("⚠️  각 클래스 2개 이상 필요. 측정 더 진행 후 재실행.")
        sys.exit(1)

    Xt = torch.tensor(X, dtype=torch.float32)
    yt = torch.tensor(y, dtype=torch.long)
    n_eval = max(2, len(X) // 5)
    idx = np.random.RandomState(42).permutation(len(X))
    Xtr, Xte = Xt[idx[:-n_eval]], Xt[idx[-n_eval:]]
    ytr, yte = yt[idx[:-n_eval]], yt[idx[-n_eval:]]
    print(f"학습 {len(Xtr)}개 / 검증 {len(Xte)}개")

    model = PresenceCNN()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    crit = nn.CrossEntropyLoss()

    print("\n" + "=" * 60)
    print("🧠 CNN 학습")
    print("=" * 60)
    epochs = 50
    train_losses, val_accs = [], []
    for ep in range(epochs):
        model.train()
        opt.zero_grad()
        logits, _ = model(Xtr)
        loss = crit(logits, ytr)
        loss.backward()
        opt.step()
        # 검증
        model.eval()
        with torch.no_grad():
            v_logits, _ = model(Xte)
            v_acc = (v_logits.argmax(1) == yte).float().mean().item()
        train_losses.append(loss.item())
        val_accs.append(v_acc)
        if (ep + 1) % 10 == 0 or ep == 0:
            print(f"  epoch {ep+1:>3} | loss {loss.item():.4f} | val acc {v_acc:.2%}")

    # 최종 평가
    model.eval()
    with torch.no_grad():
        all_logits, all_feat = model(Xt)
        all_pred = all_logits.argmax(1).numpy()
        confm = np.zeros((2, 2), int)
        for t, p in zip(y, all_pred):
            confm[t, p] += 1
        acc = (all_pred == y).mean()

    print("\n" + "=" * 60)
    print(f"✅ 학습 완료")
    print(f"  최종 검증 정확도: {val_accs[-1]:.2%}")
    print(f"  전체 정확도:     {acc:.2%}")
    print(f"  Confusion Matrix:")
    print(f"           pred 1명  pred 2명+")
    print(f"    real 1명:  {confm[0,0]:>4}    {confm[0,1]:>4}")
    print(f"    real 2명+: {confm[1,0]:>4}    {confm[1,1]:>4}")

    # 모델 저장
    model_path = OUT_DIR / "presence_cnn.pt"
    torch.save(model.state_dict(), model_path)
    print(f"\n💾 모델 저장: {model_path}")

    # 시각화
    plot_train_result(train_losses, val_accs, confm, all_feat.numpy(), y, fnames)


def predict_mode(csv_path):
    if not HAS_TORCH:
        print("ERROR: PyTorch 미설치.")
        sys.exit(1)
    model_path = OUT_DIR / "presence_cnn.pt"
    if not model_path.exists():
        print(f"ERROR: 학습된 모델 없음 ({model_path}). 먼저 --train 실행.")
        sys.exit(1)
    print(f"📥 추론: {csv_path}")
    ts, comp = parse(Path(csv_path))
    if ts is None:
        print("ERROR: 파일 파싱 실패")
        sys.exit(1)
    x = to_window(comp)
    Xt = torch.tensor(x[None], dtype=torch.float32)
    model = PresenceCNN()
    model.load_state_dict(torch.load(model_path))
    model.eval()
    with torch.no_grad():
        logits, _ = model(Xt)
        probs = torch.softmax(logits, dim=1).numpy()[0]
    pred = "1명" if probs[0] > probs[1] else "2명+"
    conf = max(probs) * 100
    print(f"\n결과: {pred} ({conf:.1f}% 확률)")
    print(f"  1명 확률   : {probs[0]*100:.1f}%")
    print(f"  2명+ 확률  : {probs[1]*100:.1f}%")


def features_mode():
    """1명 데이터만으로 특징 시각화 (학습 X)."""
    if not HAS_TORCH:
        print("PyTorch 없이 진행 — PCA 시각화만")
    print("=" * 60)
    print("📊 1명 데이터 특징 분포 (학습 X)")
    print("=" * 60)
    X, fnames = load_1p_only()
    if X is None:
        print("❌ 1명 데이터 없음")
        sys.exit(1)
    print(f"\n{len(X)}개 측정 로드")
    # 간단 특징: 채널별 평균/표준편차
    feats = np.concatenate([X.mean(axis=(2, 3)), X.std(axis=(2, 3))], axis=1)
    pca = PCA(n_components=2).fit_transform(feats)
    ocsvm = OneClassSVM(nu=0.2, gamma="scale").fit(feats)
    pred = ocsvm.predict(feats)

    fig, ax = plt.subplots(figsize=(8, 6))
    colors = ["#10b981" if v == 1 else "#ef4444" for v in pred]
    ax.scatter(pca[:, 0], pca[:, 1], c=colors, s=200, edgecolor="black")
    for i, name in enumerate(fnames):
        ax.annotate(name.replace("csi_", "").replace(".csv", ""),
                    (pca[i, 0], pca[i, 1]),
                    xytext=(6, 4), textcoords="offset points", fontsize=9)
    ax.set_xlabel("PC1"); ax.set_ylabel("PC2")
    ax.set_title(f"1명 데이터 PCA (n={len(X)})\n초록=정상, 빨강=이상치(One-Class SVM)")
    ax.grid(alpha=0.3)
    out = OUT_DIR / "features_1p_only.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    print(f"\n📸 저장: {out}")


def plot_train_result(losses, accs, confm, feat, y, fnames):
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    # 학습 곡선
    ax = axes[0]
    ax.plot(losses, label="train loss", color="#ef4444")
    ax2 = ax.twinx()
    ax2.plot(accs, label="val acc", color="#10b981")
    ax.set_xlabel("epoch"); ax.set_ylabel("loss", color="#ef4444")
    ax2.set_ylabel("accuracy", color="#10b981")
    ax.set_title("Training")
    ax.grid(alpha=0.3)

    # Confusion matrix
    ax = axes[1]
    im = ax.imshow(confm, cmap="Blues")
    ax.set_xticks([0, 1]); ax.set_yticks([0, 1])
    ax.set_xticklabels(["pred 1p", "pred 2p+"])
    ax.set_yticklabels(["real 1p", "real 2p+"])
    for i in range(2):
        for j in range(2):
            ax.text(j, i, confm[i, j], ha="center", va="center",
                    color="white" if confm[i, j] > confm.max() / 2 else "black",
                    fontsize=18)
    ax.set_title("Confusion Matrix")

    # PCA 임베딩
    ax = axes[2]
    pca = PCA(n_components=2).fit_transform(feat)
    for label, color, name in [(0, "#3b82f6", "1명"), (1, "#ef4444", "2명+")]:
        mask = y == label
        ax.scatter(pca[mask, 0], pca[mask, 1], c=color, s=120,
                   edgecolor="black", label=name)
    ax.set_xlabel("PC1"); ax.set_ylabel("PC2")
    ax.set_title("CNN Embedding (PCA)")
    ax.legend(); ax.grid(alpha=0.3)

    out = OUT_DIR / "train_result.png"
    fig.tight_layout()
    fig.savefig(out, dpi=130, bbox_inches="tight")
    print(f"📸 저장: {out}")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="작업 2 (재실감지) CNN — 논문 C 기반")
    p.add_argument("--train", action="store_true", help="1명+2명 데이터로 학습")
    p.add_argument("--predict", metavar="CSV", help="새 측정 CSV 추론")
    p.add_argument("--features", action="store_true", help="1명 데이터 특징 시각화")
    args = p.parse_args()

    if args.train:
        train_mode()
    elif args.predict:
        predict_mode(args.predict)
    elif args.features:
        features_mode()
    else:
        p.print_help()
        print("\n💡 빠른 시작:")
        print("  python presence_cnn.py --features    # 가진 1명 데이터로 분포 확인")
        print("  python presence_cnn.py --train       # 1명+2명 측정 후 학습")
        print("  python presence_cnn.py --predict X.csv  # 학습 후 새 측정 추론")


if __name__ == "__main__":
    main()
