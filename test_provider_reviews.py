import sqlite3
from fastapi.testclient import TestClient
from main import app, init_db

client = TestClient(app)


def setup_module(module):
    init_db()


def test_get_provider_reviews_initial():
    res = client.get("/api/v1/providers/1/reviews")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["provider_id"] == 1
    assert "average_rating" in data
    assert "total_reviews" in data
    assert "reviews" in data
    assert isinstance(data["reviews"], list)


def test_create_provider_review_and_recalc_avg():
    # 1. Poster un nouvel avis 5 étoiles pour le prestataire #1
    res = client.post(
        "/api/v1/providers/1/reviews",
        json={
            "provider_id": 1,
            "client_email": "julie.lemoine@example.com",
            "rating": 5,
            "comment": "Prestation exceptionnelle de dépannage à Bondy. Très minutieux !",
        },
    )
    assert res.status_code == 201, res.text
    review = res.json()
    assert review["rating"] == 5
    assert review["client_email"] == "julie.lemoine@example.com"

    # 2. Vérifier que la liste mise à jour contient le nouvel avis
    list_res = client.get("/api/v1/providers/1/reviews")
    assert list_res.status_code == 200
    data = list_res.json()
    assert data["total_reviews"] >= 1
    assert any(r["client_email"] == "julie.lemoine@example.com" for r in data["reviews"])
    assert 1.0 <= data["average_rating"] <= 5.0


def test_create_review_invalid_rating():
    res = client.post(
        "/api/v1/providers/1/reviews",
        json={
            "provider_id": 1,
            "client_email": "test.invalid@example.com",
            "rating": 6,  # Doit échouer (max 5)
            "comment": "Note impossible",
        },
    )
    assert res.status_code == 422  # Erreur de validation Pydantic


def test_create_review_non_existent_provider():
    res = client.post(
        "/api/v1/providers/999999/reviews",
        json={
            "provider_id": 999999,
            "client_email": "test@example.com",
            "rating": 4,
            "comment": "Artisan fantôme",
        },
    )
    assert res.status_code == 404


if __name__ == "__main__":
    print("Running Provider Reviews Tests...")
    test_get_provider_reviews_initial()
    print("✅ test_get_provider_reviews_initial validé")
    test_create_provider_review_and_recalc_avg()
    print("✅ test_create_provider_review_and_recalc_avg validé")
    test_create_review_invalid_rating()
    print("✅ test_create_review_invalid_rating validé")
    test_create_review_non_existent_provider()
    print("✅ test_create_review_non_existent_provider validé")
    print("\n🎉 Tous les tests du module Avis & Évaluations Clients sont passés avec succès !")
