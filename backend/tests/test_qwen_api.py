from unittest.mock import AsyncMock, patch

from app.qwen_service import QwenUnavailable


def test_ai_status_is_exposed(client):
    with patch("app.main.qwen_status", new=AsyncMock(return_value={"available": False, "model": "qwen3-vl:8b"})):
        response = client.get("/api/ai/status")
    assert response.status_code == 200
    assert response.json()["model"] == "qwen3-vl:8b"


def test_image_result_is_pending_review(client):
    result = {"document_type": "site_report", "entries": [], "warnings": ["Needs review"]}
    with patch("app.main.analyse_image", new=AsyncMock(return_value=result)):
        response = client.post("/api/ai/analyse-image", files={"file": ("report.png", b"fake-image", "image/png")})
    assert response.status_code == 200
    assert response.json()["status"] == "pending_review"
    assert response.json()["result"] == result


def test_image_endpoint_reports_missing_ollama(client):
    with patch("app.main.analyse_image", new=AsyncMock(side_effect=QwenUnavailable("start Ollama"))):
        response = client.post("/api/ai/analyse-image", files={"file": ("report.jpg", b"fake-image", "image/jpeg")})
    assert response.status_code == 503
    assert "Ollama" in response.json()["detail"]


def test_image_endpoint_rejects_non_image(client):
    response = client.post("/api/ai/analyse-image", files={"file": ("notes.txt", b"text", "text/plain")})
    assert response.status_code == 415
