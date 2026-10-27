import hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from src.ml.model import PhishingModel
from src.api.routes.statistics import model_evaluation
from src.database.models import init_db, get_session, PhishTankEntry


@pytest.fixture(scope="module")
def trained():
    return PhishingModel(Path("data/models/phishing_model.pkl"))


@pytest.mark.parametrize("url,scoped", [
    ("https://www.github.com/microsoft/vscode", False),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", True),
    ("https://weixin.qq.com/", True),
    ("https://g00gle-secure.tk/account/verify", True),
])
def test_local_contributions_reconstruct_actual_prediction(trained, url, scoped):
    explanation = trained.explain(url)
    assert explanation["specialist_active"] is scoped
    assert explanation["score"] == pytest.approx(trained.predict(url)[1], abs=1e-12)
    assert explanation["baseline"] + sum(f["contribution"] for f in explanation["features"]) == pytest.approx(explanation["score"], abs=1e-10)
    assert explanation["reconstructed_score"] == pytest.approx(explanation["score"], abs=1e-10)
    amounts = [abs(f["contribution"]) for f in explanation["features"]]
    assert amounts == sorted(amounts, reverse=True)
    assert len(explanation["features"]) == len(trained.feature_extractor.get_feature_names())


def test_evaluation_orientation_and_metrics_are_derived_from_loaded_matrix():
    model = SimpleNamespace(metrics={"confusion_matrix": [[90, 10], [20, 80]], "recall": 999},
                            threshold=.4, model_version="test")
    report = model_evaluation(model)
    assert report["samples"] == 200
    assert report["recall"] == .8
    assert report["precision"] == pytest.approx(80 / 90)
    assert report["f1"] == pytest.approx(160 / 190)
    assert report["false_positive_rate"] == .1
    assert model_evaluation(None)["status"] == "unavailable"
    model.metrics = {}
    assert model_evaluation(model)["status"] == "unavailable"
    model.metrics = {"confusion_matrix": [[0, 0], [0, 0]]}
    assert model_evaluation(model)["status"] == "unavailable"


def test_detection_explanation_history_and_evaluation_api(trained, tmp_path, monkeypatch):
    import src.api.app as app_module
    engine = init_db("sqlite:///" + (tmp_path / "test.db").as_posix())
    monkeypatch.setattr(app_module, "model", trained)
    monkeypatch.setattr(app_module, "engine", engine)
    client = TestClient(app_module.app)
    url = "https://www.github.com/microsoft/vscode"
    response = client.post("/api/v1/detect", json={"url": url})
    assert response.status_code == 200
    result = response.json()
    assert result["explanation"]["score"] == pytest.approx(result["ml_score"])
    assert result["explanation"]["model_version"] == trained.model_version
    assert client.get("/api/v1/detect/history?limit=1").json()[0]["url"] == url
    assert client.get("/api/v1/stats/model").json()["version"] == trained.model_version
    history_before = client.get("/api/v1/detect/history").json()
    bridge = client.post("/api/v1/analyze/url", json={"url": url})
    assert bridge.status_code == 200
    assert bridge.json()["ml_score"] == pytest.approx(result["ml_score"])
    assert bridge.json()["model_version"] == trained.model_version
    assert client.get("/api/v1/detect/history").json() == history_before
    # Database evidence remains separate from ML contribution accounting.
    session = get_session(engine)
    session.add(PhishTankEntry(phish_id=999, url=url, url_hash=hashlib.sha256(url.encode()).hexdigest(), target="test"))
    session.commit()
    session.close()
    database_result = client.post("/api/v1/detect", json={"url": url}).json()
    assert database_result["phishtank_match"] and database_result["is_phishing"]
    assert database_result["explanation"]["score"] == pytest.approx(result["ml_score"])
    monkeypatch.setattr(app_module, "model", None)
    assert client.post("/api/v1/analyze/url", json={"url": url}).status_code == 503
    assert client.get("/api/v1/stats/model").json()["status"] == "unavailable"
    client.close()
    engine.dispose()
