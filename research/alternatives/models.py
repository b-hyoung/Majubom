"""Small CPU baselines with explicit abstention and train-only fitting.

CMP: contextual AB-join of multivariate windows, inspired by Bijlani et al.
2022, doi:10.2196/38211. Occupancy replaces their daily household context;
global train scaling replaces daily normalization. This is NOT a replication.
LMM: Gaussian random intercept, as discussed by Roshan et al. 2025,
doi:10.1371/journal.pone.0323133; REML fit rather than their EM estimator.
"""
from __future__ import annotations

import numpy as np


class ContextualProfile:
    def fit(self, windows, contexts):
        x = np.asarray(windows, dtype=float)
        if x.ndim != 3 or len(x) < 2 or len(contexts) != len(x) or not np.isfinite(x).all():
            raise ValueError("Finite [sample,time,feature] reference windows required")
        self.mean = x.mean(axis=(0, 1))
        self.scale = np.maximum(x.std(axis=(0, 1)), .01)
        self.shape = x.shape[1:]
        z = (x - self.mean) / self.scale
        self.references = {c: z[np.asarray(contexts) == c].copy() for c in set(contexts)}
        self.thresholds = {}
        return self

    def distance(self, window, context):
        if context not in self.references:
            return None
        x = np.asarray(window, dtype=float)
        if x.shape != self.shape or not np.isfinite(x).all():
            return None
        z = (x - self.mean) / self.scale
        # Exact AB-join: closest prior reference; no self/overlapping window in reference.
        return float(np.sqrt(np.min(np.mean((self.references[context] - z) ** 2, axis=(1, 2)))))

    def calibrate(self, windows, contexts, quantile=.99):
        for context in self.references:
            distances = [self.distance(w, c) for w, c in zip(windows, contexts) if c == context]
            distances = [d for d in distances if d is not None]
            if len(distances) >= 2:
                self.thresholds[context] = float(np.quantile(distances, quantile))
        return self

    def predict(self, window, context):
        score = self.distance(window, context)
        threshold = self.thresholds.get(context)
        return {"score": score, "threshold": threshold,
                "status": "insufficient_reference" if score is None or threshold is None else
                          "anomaly_candidate" if score > threshold else "normal",
                "clinical_prediction": False, "method": "contextual_ab_join_train_scaled_v1"}


class RandomInterceptLMM:
    """Population parameters need known, independent subjects, not recording IDs."""
    def fit(self, values, subjects):
        from scipy.optimize import minimize
        y = np.asarray(values, dtype=float)
        if y.ndim != 1 or len(y) != len(subjects) or not np.isfinite(y).all():
            raise ValueError("Finite observations and matching subject IDs required")
        if any(s is None or not str(s).strip() for s in subjects):
            raise ValueError("Known subject IDs required; sessions are not subjects")
        groups = [y[np.asarray(subjects) == s] for s in set(subjects)]
        if len(groups) < 3 or any(len(g) < 3 for g in groups):
            raise ValueError("Need >=3 known subjects, each with >=3 reference observations")

        def objective(log_var, details=False):
            within, between = np.exp(log_var)
            weights = np.array([len(g) / (within + len(g) * between) for g in groups])
            means = np.array([g.mean() for g in groups])
            beta = float(np.sum(weights * means) / weights.sum())
            logdet = sum((len(g)-1)*np.log(within) + np.log(within+len(g)*between) for g in groups)
            quad = sum(float(np.sum((g-g.mean())**2))/within +
                       len(g)*(float(g.mean())-beta)**2/(within+len(g)*between) for g in groups)
            loss = .5 * (logdet + np.log(weights.sum()) + quad)
            return (beta, within, between) if details else loss

        v = max(float(np.var(y)), 1e-4)
        fit = minimize(objective, np.log([v*.5, v*.5]), method="L-BFGS-B", bounds=[(-20, 20)]*2)
        if not fit.success:
            raise ValueError(f"LMM fitting failed: {fit.message}")
        self.mean, self.within, self.between = objective(fit.x, details=True)
        return self

    def score(self, value, history):
        h = np.asarray(history, dtype=float)
        if not np.isfinite(value) or h.ndim != 1 or not np.isfinite(h).all():
            raise ValueError("Finite past-only observations required")
        n = len(h)
        posterior_var = 1.0 / (1.0 / self.between + n / self.within)
        offset = posterior_var * float(np.sum(h - self.mean)) / self.within
        expected = self.mean + offset
        sd = np.sqrt(self.within + posterior_var)
        return {"expected": expected, "predictive_sd": float(sd), "z": float((value-expected)/sd),
                "interval_95": [float(expected-1.96*sd), float(expected+1.96*sd)],
                "method": "random_intercept_reml_plugin_interval", "clinical_prediction": False}


def train_autoencoder(train, calibration, test, epochs=40):
    """Fresh CPU-only LSTM-AE; never loads or saves the deployment checkpoint.

    Malhotra et al. 2016 encoder-decoder idea; MSE scoring follows the existing
    project, not the paper's fitted reconstruction-error likelihood.
    """
    import torch
    from torch import nn
    torch.set_num_threads(1)
    torch.manual_seed(42)
    np.random.seed(42)
    mean, scale = train.mean((0, 1)), np.maximum(train.std((0, 1)), .01)
    tensors = [torch.tensor((x-mean)/scale, dtype=torch.float32, device="cpu") for x in (train, calibration, test)]

    class AE(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = nn.LSTM(train.shape[-1], 16, batch_first=True)
            self.decoder = nn.LSTM(16, 16, batch_first=True)
            self.output = nn.Linear(16, train.shape[-1])

        def forward(self, x):
            _, (h, _) = self.encoder(x)
            z, _ = self.decoder(h[-1].unsqueeze(1).repeat(1, x.shape[1], 1))
            return self.output(z)

    model = AE().cpu()
    opt = torch.optim.Adam(model.parameters(), lr=.003)
    for _ in range(epochs):
        opt.zero_grad()
        loss = ((model(tensors[0])-tensors[0])**2).mean()
        loss.backward()
        opt.step()
    model.eval()
    with torch.no_grad():
        scores = [((model(x)-x)**2).mean((1, 2)).numpy() for x in tensors[1:]]
    return float(np.quantile(scores[0], .99)), scores[1]
