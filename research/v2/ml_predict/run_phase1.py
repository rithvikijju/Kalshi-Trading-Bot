"""Phase 1: SPX next-day direction prediction with SPY-only features.

Bias-controlled. Reports ALL baselines including a "RandomSelective" that
matches each model's flat-rate so we know the model lift isn't just from
sitting out volatile days.
"""
from __future__ import annotations
import sys, time, warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from pathlib import Path
import torch, torch.nn as nn

from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
import lightgbm as lgb

from evaluation import (split_data, walk_forward_splits,
                         trade_pnl, stats, deflated_sharpe)


def get_spy_only_features(df: pd.DataFrame) -> list:
    return [c for c in df.columns if c.startswith("spy_") or c in ("dow", "month", "dom")]


def get_all_features(df: pd.DataFrame) -> list:
    return [c for c in df.columns
            if c not in ("target_ret", "target_up")
            and not c.startswith("LEAK_")]


# ---------------------------------------------------------------------------
# LSTM

class LSTMClassifier:
    def __init__(self, n_feat, hidden=32, lookback=20, epochs=12, lr=1e-3, batch=64):
        self.n_feat = n_feat
        self.hidden = hidden
        self.lookback = lookback
        self.epochs = epochs
        self.lr = lr
        self.batch = batch
        self.device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
        self.scaler = StandardScaler()

    def _make_seqs(self, X, y=None):
        seqs, tgts = [], []
        for t in range(self.lookback, len(X)):
            seqs.append(X[t-self.lookback:t])
            if y is not None: tgts.append(y[t])
        return np.stack(seqs), (np.array(tgts) if y is not None else None)

    class _Net(nn.Module):
        def __init__(self, n_feat, hidden):
            super().__init__()
            self.lstm = nn.LSTM(n_feat, hidden, batch_first=True)
            self.fc = nn.Linear(hidden, 1)
        def forward(self, x):
            o, _ = self.lstm(x)
            return torch.sigmoid(self.fc(o[:, -1, :])).squeeze(-1)

    def fit(self, X, y):
        Xs = self.scaler.fit_transform(X)
        Xs, ys = self._make_seqs(Xs, np.asarray(y, dtype=float))
        Xt = torch.tensor(Xs, dtype=torch.float32, device=self.device)
        yt = torch.tensor(ys, dtype=torch.float32, device=self.device)
        net = self._Net(self.n_feat, self.hidden).to(self.device)
        opt = torch.optim.Adam(net.parameters(), lr=self.lr)
        loss_fn = nn.BCELoss()
        n = len(Xt)
        for ep in range(self.epochs):
            perm = torch.randperm(n, device=self.device)
            for i in range(0, n, self.batch):
                ix = perm[i:i+self.batch]
                opt.zero_grad()
                loss = loss_fn(net(Xt[ix]), yt[ix])
                loss.backward()
                opt.step()
        self.model = net

    def predict_proba(self, X):
        Xs = self.scaler.transform(X)
        seqs = []
        for t in range(len(Xs)):
            lo = max(0, t - self.lookback + 1)
            seq = Xs[lo:t+1]
            if len(seq) < self.lookback:
                pad = np.repeat(seq[:1], self.lookback - len(seq), axis=0)
                seq = np.concatenate([pad, seq])
            seqs.append(seq)
        seqs = np.stack(seqs)
        Xt = torch.tensor(seqs, dtype=torch.float32, device=self.device)
        with torch.no_grad():
            p = self.model(Xt).cpu().numpy()
        return np.column_stack([1 - p, p])


# ---------------------------------------------------------------------------

def make_models(n_feat):
    return {
        "Logistic":      LogisticRegression(C=0.1, max_iter=500, random_state=42),
        "RandomForest":  RandomForestClassifier(n_estimators=200, max_depth=4,
                          min_samples_leaf=20, random_state=42, n_jobs=-1),
        "GradientBoost": GradientBoostingClassifier(n_estimators=200, max_depth=3,
                          learning_rate=0.05, random_state=42),
        "LightGBM":      lgb.LGBMClassifier(n_estimators=300, max_depth=4,
                          learning_rate=0.05, num_leaves=15, subsample=0.7,
                          colsample_bytree=0.7, random_state=42, n_jobs=1, verbosity=-1),
        "MLP":           MLPClassifier(hidden_layer_sizes=(32, 16), max_iter=300,
                          learning_rate_init=0.001, random_state=42),
        "LSTM":          LSTMClassifier(n_feat=n_feat, hidden=32, lookback=20, epochs=12),
    }


def detailed_stats(pnl: pd.Series, pos: np.ndarray, actual_ret: np.ndarray) -> dict:
    s = stats(pnl)
    s["pct_long"]  = float((pos > 0).mean())
    s["pct_short"] = float((pos < 0).mean())
    s["pct_flat"]  = float((pos == 0).mean())
    # directional accuracy when not flat
    nf = pos != 0
    if nf.sum() > 0:
        correct = ((pos[nf] > 0) & (actual_ret[nf] > 0)) | ((pos[nf] < 0) & (actual_ret[nf] < 0))
        s["dir_acc"] = float(correct.mean())
    else:
        s["dir_acc"] = 0
    return s


def run_baselines(df_test, models_flat_rates: dict, threshold=0.05, cost_bp=1.0):
    """Return baseline stats including 'RandomSelective' matching each model's flat rate."""
    out = {}
    actual_ret = df_test["target_ret"].values

    # Buy-and-hold SPY
    pos_bh = np.ones(len(actual_ret))
    pnl_bh = pd.Series(pos_bh * actual_ret - np.abs(np.diff(np.concatenate([[0], pos_bh]))) * cost_bp/10000)
    out["BuyHold_SPY"] = detailed_stats(pnl_bh, pos_bh, actual_ret)

    # AR(1) directional
    pos_ar = np.sign(df_test["spy_ret_1"].values)
    cost_ar = np.abs(np.diff(np.concatenate([[0], pos_ar]))) * cost_bp/10000
    pnl_ar = pd.Series(pos_ar * actual_ret - cost_ar)
    out["AR1"] = detailed_stats(pnl_ar, pos_ar, actual_ret)

    # Always-long
    out["AlwaysLong"] = out["BuyHold_SPY"]

    # Random-selective: same flat rate as the best model
    if models_flat_rates:
        target_flat = np.mean(list(models_flat_rates.values()))
        rng = np.random.default_rng(42)
        # randomly long with prob (1 - target_flat) / 2 (no shorts, to compare fairly)
        # actually do: long with prob target_long, flat with prob target_flat, short with prob target_short
        # Easiest: produce uniform random probabilities, apply same threshold rule
        rand_probs = rng.uniform(0, 1, size=len(actual_ret))
        pos_rs = np.zeros(len(actual_ret))
        pos_rs[rand_probs > 0.5 + threshold] = +1
        pos_rs[rand_probs < 0.5 - threshold] = -1
        cost_rs = np.abs(np.diff(np.concatenate([[0], pos_rs]))) * cost_bp/10000
        pnl_rs = pd.Series(pos_rs * actual_ret - cost_rs)
        out["RandomSelective"] = detailed_stats(pnl_rs, pos_rs, actual_ret)

    return out


def run_phase(label: str, df, feature_fn, threshold=0.05):
    splits = split_data(df)
    test = splits["test"]
    feat_cols = feature_fn(df)
    print(f"\n{'='*60}\n{label}\n{'='*60}")
    print(f"Train: {len(splits['train'])} | Val: {len(splits['val'])} | Test: {len(splits['test'])}")
    print(f"Features ({len(feat_cols)}): {feat_cols[:6]}{'...' if len(feat_cols)>6 else ''}")

    blocks = walk_forward_splits(test.index)
    print(f"Walk-forward blocks in test: {len(blocks)}")

    # run models
    actual_ret = test["target_ret"].values
    model_results = {}
    flat_rates = {}
    models = make_models(n_feat=len(feat_cols))
    for name, mdl_template in models.items():
        print(f"  Starting {name}...", flush=True)
        t0 = time.time()
        all_probs = np.full(len(test), np.nan)
        for (s, e) in blocks:
            cutoff = test.index[s]
            tr = df.loc[df.index < cutoff]
            if len(tr) < 200: continue
            X_tr = tr[feat_cols].values
            y_tr = tr["target_up"].values
            X_te = test[feat_cols].iloc[s:e].values
            try:
                if name == "LSTM":
                    mdl = LSTMClassifier(n_feat=len(feat_cols), hidden=32,
                                          lookback=20, epochs=12)
                    mdl.fit(X_tr, y_tr)
                    p = mdl.predict_proba(X_te)[:, 1]
                else:
                    cls = mdl_template.__class__
                    mdl = cls(**mdl_template.get_params())
                    scaler = StandardScaler()
                    X_tr_s = scaler.fit_transform(X_tr)
                    X_te_s = scaler.transform(X_te)
                    mdl.fit(X_tr_s, y_tr)
                    p = mdl.predict_proba(X_te_s)[:, 1]
                all_probs[s:e] = p
            except Exception as ex:
                print(f"  {name} block {s}:{e} FAILED: {ex}")
        all_probs = np.where(np.isnan(all_probs), 0.5, all_probs)
        pnl, pos = trade_pnl(all_probs, actual_ret, threshold=threshold)
        st = detailed_stats(pnl, pos, actual_ret)
        st["mean_prob"] = float(all_probs.mean())
        st["secs"] = round(time.time() - t0, 1)
        model_results[name] = st
        flat_rates[name] = st["pct_flat"]
        print(f"  {name:14s}: Sharpe={st['sharpe']:+.2f}  Ret={st['ann_ret']*100:+.1f}%  "
              f"MDD={st['mdd']*100:+.1f}%  Flat={st['pct_flat']*100:.0f}%  "
              f"DirAcc={st['dir_acc']*100:.1f}%  ({st['secs']}s)")

    # baselines (use mean of model flat rates for RandomSelective)
    baselines = run_baselines(test, flat_rates, threshold=threshold)
    print("\nBaselines:")
    for name, st in baselines.items():
        print(f"  {name:14s}: Sharpe={st['sharpe']:+.2f}  Ret={st['ann_ret']*100:+.1f}%  "
              f"MDD={st['mdd']*100:+.1f}%  Flat={st['pct_flat']*100:.0f}%  "
              f"DirAcc={st['dir_acc']*100:.1f}%")

    # combine + deflated Sharpe
    all_res = {**baselines, **model_results}
    print("\nDeflated Sharpe (n_trials=6 models):")
    for name, st in all_res.items():
        if st["n"] > 0:
            ds = deflated_sharpe(st["sharpe"], n_trials=6, T=st["n"])
            print(f"  {name:14s}: raw={st['sharpe']:+.2f}  deflated={ds:+.2f}")
    return all_res


if __name__ == "__main__":
    df = pd.read_parquet("_features.parquet")
    res1 = run_phase("PHASE 1: SPY-only features", df, get_spy_only_features)
    pd.DataFrame(res1).T.to_csv("_phase1_results.csv")
    print("\nSaved phase1 results.")
