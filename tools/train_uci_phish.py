# -*- coding: utf-8 -*-
"""
train_uci_phish.py
Train phishing website classifiers on the UCI Phishing Websites dataset (id=327).

Pipeline:
  1. Load datasets/uci_phishing/uci_phishing.csv (11055 samples, 30 features).
  2. Compare 3 models with 5-fold stratified cross-validation:
       RandomForest, XGBoost, LogisticRegression.
  3. Pick the best model by mean F1, retrain on all data, save joblib.
  4. Dump metrics JSON + feature importance for the report.

Outputs:
  model/uci_phishing_clf.joblib          best model (retrained on full data)
  model/uci_phishing_metrics.json        5-fold CV metrics for all models
  model/uci_phishing_features.json       30 feature names (model input order)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold
from xgboost import XGBClassifier

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "datasets" / "uci_phishing" / "uci_phishing.csv"
MODEL_DIR = ROOT / "model"
RANDOM_STATE = 42
N_FOLDS = 5


def load_data() -> tuple[np.ndarray, np.ndarray, list[str]]:
    df = pd.read_csv(CSV_PATH)
    label_col = "Result"
    feature_cols = [c for c in df.columns if c != label_col]
    X = df[feature_cols].to_numpy(dtype=np.float32)
    # map -1 (legitimate) -> 0, +1 (phishing) -> 1
    y = (df[label_col].to_numpy(dtype=np.int64) == 1).astype(np.int64)
    assert X.shape == (11055, 30), f"unexpected shape {X.shape}"
    assert df.isna().sum().sum() == 0, "missing values found"
    return X, y, feature_cols


def make_models() -> dict[str, object]:
    return {
        "RandomForest": RandomForestClassifier(
            n_estimators=300, max_depth=None, n_jobs=-1, random_state=RANDOM_STATE
        ),
        "XGBoost": XGBClassifier(
            n_estimators=300, max_depth=6, learning_rate=0.1,
            n_jobs=-1, random_state=RANDOM_STATE, eval_metric="logloss",
        ),
        "LogisticRegression": LogisticRegression(
            max_iter=3000, solver="liblinear", C=1.0, random_state=RANDOM_STATE
        ),
        "HistGradientBoosting": HistGradientBoostingClassifier(
            max_iter=300, random_state=RANDOM_STATE
        ),
    }


def cross_validate(X: np.ndarray, y: np.ndarray, models: dict[str, object]) -> dict:
    skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_STATE)
    results: dict[str, dict] = {}
    for name, model in models.items():
        accs, precs, recs, f1s, aucs = [], [], [], [], []
        for tr_idx, va_idx in skf.split(X, y):
            model_cv = model
            if name == "XGBoost":
                # fresh instance per fold to avoid in-place fit warnings
                model_cv = make_models()[name]
            model_cv.fit(X[tr_idx], y[tr_idx])
            pred = model_cv.predict(X[va_idx])
            proba = model_cv.predict_proba(X[va_idx])[:, 1]
            accs.append(accuracy_score(y[va_idx], pred))
            precs.append(precision_score(y[va_idx], pred, zero_division=0))
            recs.append(recall_score(y[va_idx], pred, zero_division=0))
            f1s.append(f1_score(y[va_idx], pred, zero_division=0))
            aucs.append(roc_auc_score(y[va_idx], proba))
        results[name] = {
            "accuracy_mean": float(np.mean(accs)),
            "accuracy_std": float(np.std(accs)),
            "precision_mean": float(np.mean(precs)),
            "recall_mean": float(np.mean(recs)),
            "f1_mean": float(np.mean(f1s)),
            "auc_mean": float(np.mean(aucs)),
            "folds": [
                {"accuracy": float(a), "precision": float(p), "recall": float(r),
                 "f1": float(f), "auc": float(au)}
                for a, p, r, f, au in zip(accs, precs, recs, f1s, aucs)
            ],
        }
    return results


def feature_importance(model, feature_cols: list[str]) -> list[dict]:
    imp = getattr(model, "feature_importances_", None)
    if imp is None:
        coef = np.abs(getattr(model, "coef_", np.zeros(len(feature_cols)))).ravel()
        imp = coef / (coef.sum() + 1e-12)
    ranked = sorted(
        zip(feature_cols, imp.tolist()), key=lambda kv: kv[1], reverse=True
    )
    return [{"feature": f, "importance": round(float(v), 6)} for f, v in ranked]


def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    X, y, feature_cols = load_data()
    print(f"data: {X.shape[0]} samples, {X.shape[1]} features | "
          f"phishing={int(y.sum())}, legit={int((y == 0).sum())}")

    models = make_models()
    cv_results = cross_validate(X, y, models)

    # ranking table
    print("\n=== 5-fold CV results (mean +/- std) ===")
    rows = []
    for name, r in cv_results.items():
        print(f"{name:<22} acc={r['accuracy_mean']:.4f}+/-{r['accuracy_std']:.4f} "
              f"prec={r['precision_mean']:.4f} rec={r['recall_mean']:.4f} "
              f"f1={r['f1_mean']:.4f} auc={r['auc_mean']:.4f}")
        rows.append((name, r["f1_mean"], r["auc_mean"], r["accuracy_mean"]))
    rows.sort(key=lambda t: (t[1], t[2]), reverse=True)
    best_name = rows[0][0]
    print(f"\nbest model by mean F1: {best_name}")

    # retrain best on full data
    best_model = make_models()[best_name]
    best_model.fit(X, y)
    model_path = MODEL_DIR / "uci_phishing_clf.joblib"
    joblib.dump(best_model, model_path)
    print(f"saved: {model_path}")

    # extra: feature importance also from RandomForest for the report
    rf_imp = None
    if best_name != "RandomForest":
        rf = RandomForestClassifier(
            n_estimators=300, n_jobs=-1, random_state=RANDOM_STATE
        )
        rf.fit(X, y)
        rf_imp = feature_importance(rf, feature_cols)

    metrics = {
        "dataset": "UCI Phishing Websites (id=327)",
        "n_samples": int(X.shape[0]),
        "n_features": int(X.shape[1]),
        "n_phishing": int(y.sum()),
        "n_legitimate": int((y == 0).sum()),
        "cv_folds": N_FOLDS,
        "random_state": RANDOM_STATE,
        "best_model": best_name,
        "models": cv_results,
        "feature_importance_best_model": feature_importance(best_model, feature_cols),
        "feature_importance_random_forest": rf_imp,
        "feature_names": feature_cols,
    }
    metrics_path = MODEL_DIR / "uci_phishing_metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"saved: {metrics_path}")

    feats_path = MODEL_DIR / "uci_phishing_features.json"
    with open(feats_path, "w", encoding="utf-8") as f:
        json.dump({"features": feature_cols}, f, ensure_ascii=False, indent=2)
    print(f"saved: {feats_path}")


if __name__ == "__main__":
    main()