"""ML Model for phishing URL detection."""
import joblib
import numpy as np
from typing import Optional, Tuple, List, Dict, Any
from pathlib import Path
from datetime import datetime

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, classification_report, confusion_matrix
)
from sklearn.preprocessing import StandardScaler

from .feature_extractor import FeatureExtractor, URLFeatures


class PhishingModel:
    """Phishing detection ML model wrapper."""

    def __init__(self, model_path: Optional[Path] = None):
        self.model: Optional[RandomForestClassifier] = None
        self.scaler: Optional[StandardScaler] = None
        self.feature_extractor = FeatureExtractor()
        self.feature_names: List[str] = []
        self.model_version: str = ""
        self.metrics: Dict[str, float] = {}
        self.threshold = 0.5
        self.specialist = None
        self.specialist_weight = 0.5

        if model_path and Path(model_path).exists():
            self.load(model_path)

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        test_size: float = 0.2,
        random_state: int = 42
    ) -> Dict[str, Any]:
        """
        Train the phishing detection model.

        Args:
            X: Feature matrix
            y: Labels (1 for phishing, 0 for legitimate)
            test_size: Fraction of data to use for testing
            random_state: Random seed for reproducibility

        Returns:
            Dictionary containing training metrics
        """
        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=random_state, stratify=y
        )

        # Scale features
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Train model
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=20,
            min_samples_split=5,
            min_samples_leaf=2,
            random_state=random_state,
            n_jobs=-1,
            class_weight='balanced'
        )
        self.model.fit(X_train_scaled, y_train)

        # Evaluate
        y_pred = self.model.predict(X_test_scaled)
        y_proba = self.model.predict_proba(X_test_scaled)[:, 1]

        # Calculate metrics
        self.metrics = {
            'accuracy': accuracy_score(y_test, y_pred),
            'precision': precision_score(y_test, y_pred),
            'recall': recall_score(y_test, y_pred),
            'f1_score': f1_score(y_test, y_pred),
            'auc_roc': roc_auc_score(y_test, y_proba)
        }

        # Cross-validation score
        cv_scores = cross_val_score(
            self.model, X_train_scaled, y_train, cv=5, scoring='f1'
        )
        self.metrics['cv_f1_mean'] = cv_scores.mean()
        self.metrics['cv_f1_std'] = cv_scores.std()

        # Confusion matrix
        cm = confusion_matrix(y_test, y_pred)
        self.metrics['confusion_matrix'] = cm.tolist()

        # Classification report
        self.metrics['classification_report'] = classification_report(
            y_test, y_pred, target_names=['Legitimate', 'Phishing']
        )

        # Set version
        self.model_version = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self.feature_names = FeatureExtractor.get_feature_names()

        return {
            'metrics': self.metrics,
            'version': self.model_version,
            'training_samples': len(X_train),
            'test_samples': len(X_test),
            'phishing_samples': int(y.sum()),
            'legitimate_samples': int(len(y) - y.sum())
        }

    def predict(self, url: str) -> Tuple[bool, float]:
        return self.predict_batch([url])[0]

    def predict_with_features(self, url: str) -> Tuple[bool, float, URLFeatures]:
        features = self.feature_extractor.extract(url)
        score = float(self.score_features([url], [features])[0])
        return bool(score >= self.threshold), score, features

    def predict_batch(self, urls: List[str]) -> List[Tuple[bool, float]]:
        if not urls:
            return []
        features = [self.feature_extractor.extract(url) for url in urls]
        scores = self.score_features(urls, features)
        return [(bool(score >= self.threshold), float(score)) for score in scores]

    @staticmethod
    def specialist_scope(url, features):
        from urllib.parse import urlsplit, parse_qs
        import re
        from .feature_extractor import DOMAIN_EXTRACTOR
        parsed = urlsplit(url)
        subdomain = DOMAIN_EXTRACTOR(parsed.hostname or '').subdomain
        subdomain_home = subdomain not in ('', 'www') and parsed.path in ('', '/') and not parsed.query
        values = parse_qs(parsed.query)
        opaque_query = (len(values) == 1 and len(next(iter(values.values()), [])) == 1
                        and bool(re.fullmatch(r'[A-Za-z0-9_-]{6,24}', next(iter(values.values()), [''])[0]))
                        and features.path_token_count <= 2)
        return features.has_obfuscated_brand_host or subdomain_home or opaque_query

    def score_features(self, urls, features):
        if self.model is None:
            raise ValueError('Model not loaded')
        matrix = np.asarray([self.feature_extractor.to_feature_vector(f) for f in features])
        base = matrix[:, :self.model.n_features_in_]
        if self.scaler is not None:
            base = self.scaler.transform(base)
        scores = self.model.predict_proba(base)[:, 1]
        if self.specialist is not None:
            active = np.array([self.specialist_scope(u, f) for u, f in zip(urls, features)])
            if active.any():
                other = self.specialist.predict_proba(matrix[active])[:, 1]
                scores[active] = (1 - self.specialist_weight) * scores[active] + self.specialist_weight * other
        return scores

    def explain(self, url: str) -> dict:
        """Tree-path decomposition of this prediction, conditional on the active route.

        Baseline + all signed contributions = ML score. This is not SHAP or causality.
        """
        features = self.feature_extractor.extract(url)
        raw = np.asarray(self.feature_extractor.to_feature_vector(features))
        names = self.feature_extractor.get_feature_names()
        def decompose(forest, row):
            row = np.asarray(row, dtype=np.float32)
            contributions = np.zeros(len(names))
            baseline = 0.0
            positive = int(np.flatnonzero(forest.classes_ == 1)[0])
            for estimator in forest.estimators_:
                tree = estimator.tree_
                values = tree.value[:, 0, :]
                probabilities = values[:, positive] / values.sum(axis=1)
                baseline += probabilities[0]
                node = 0
                while tree.children_left[node] != tree.children_right[node]:
                    feature = tree.feature[node]
                    child = tree.children_left[node] if row[feature] <= tree.threshold[node] else tree.children_right[node]
                    contributions[feature] += probabilities[child] - probabilities[node]
                    node = child
            return baseline / len(forest.estimators_), contributions / len(forest.estimators_)
        base_row = raw[:self.model.n_features_in_]
        if self.scaler is not None:
            base_row = self.scaler.transform([base_row])[0]
        baseline, contributions = decompose(self.model, base_row)
        specialist_active = self.specialist is not None and self.specialist_scope(url, features)
        if specialist_active:
            other_base, other = decompose(self.specialist, raw)
            weight = self.specialist_weight
            baseline = (1 - weight) * baseline + weight * other_base
            contributions = (1 - weight) * contributions + weight * other
        score = float(self.score_features([url], [features])[0])
        order = np.argsort(-np.abs(contributions), kind="stable")
        return {
            "method": "tree_path_probability_decomposition",
            "model_version": self.model_version, "threshold": self.threshold,
            "specialist_active": specialist_active,
            "specialist_weight": self.specialist_weight if specialist_active else 0.0,
            "baseline": float(baseline), "score": score,
            "reconstructed_score": float(baseline + contributions.sum()),
            "features": [{"name": names[i], "value": features.to_dict()[names[i]],
                          "contribution": float(contributions[i])} for i in order],
        }

    def get_feature_importance(self, top_n: int = 20) -> List[Tuple[str, float]]:
        """Get top N most important features."""
        if self.model is None or not self.feature_names:
            return []

        importances = self.model.feature_importances_
        indices = np.argsort(importances)[::-1][:top_n]

        return [
            (self.feature_names[i], float(importances[i]))
            for i in indices
        ]

    def save(self, path: Path) -> None:
        """Save model to disk."""
        model_data = {
            'model': self.model,
            'scaler': self.scaler,
            'feature_names': self.feature_names,
            'version': self.model_version,
            'metrics': self.metrics,
            'threshold': self.threshold,
            'specialist': self.specialist,
            'specialist_weight': self.specialist_weight
        }
        joblib.dump(model_data, path)

    def load(self, path: Path) -> None:
        """Load model from disk."""
        model_data = joblib.load(path)
        self.model = model_data['model']
        self.scaler = model_data.get('scaler')
        self.feature_names = model_data.get('feature_names', [])
        self.model_version = model_data.get('version', 'unknown')
        self.metrics = model_data.get('metrics', {})
        self.threshold = model_data.get('threshold', 0.5)
        self.specialist = model_data.get('specialist')
        self.specialist_weight = model_data.get('specialist_weight', 0.5)
