import sqlite3
from fastapi.testclient import TestClient
from main import app, init_db

client = TestClient(app)
ADMIN_KEY = "mon_cle_admin_secrete_2026"


def setup_module(module):
    init_db()


def test_admin_auth_forbidden():
    res = client.get("/api/v1/admin/users-list?admin_key=cle_invalide")
    assert res.status_code == 403

    res2 = client.post("/api/v1/admin/users/1/toggle-status?admin_key=bad_key")
    assert res2.status_code == 403

    res3 = client.get("/api/v1/admin/dashboard?admin_key=bad_key")
    assert res3.status_code == 403


def test_admin_get_users_list_and_dashboard():
    # 1. Vérifier le dashboard
    res_dash = client.get(f"/api/v1/admin/dashboard?admin_key={ADMIN_KEY}")
    assert res_dash.status_code == 200, res_dash.text
    data_dash = res_dash.json()
    assert "stats" in data_dash
    assert "total_users" in data_dash["stats"]
    assert "premium_subscribers" in data_dash["stats"]

    # 2. Vérifier la liste des utilisateurs
    res_users = client.get(f"/api/v1/admin/users-list?admin_key={ADMIN_KEY}")
    assert res_users.status_code == 200, res_users.text
    data_users = res_users.json()
    assert data_users["status"] == "success"
    assert "users" in data_users
    assert len(data_users["users"]) > 0
    first_user = data_users["users"][0]
    assert "id" in first_user
    assert "email" in first_user
    assert "role" in first_user
    assert "is_active" in first_user


def test_admin_toggle_user_status():
    # Récupérer le premier utilisateur
    res_users = client.get(f"/api/v1/admin/users-list?admin_key={ADMIN_KEY}")
    users = res_users.json()["users"]
    target_user = users[0]
    uid = target_user["id"]
    initial_status = target_user["is_active"]

    # Basculer le statut
    res_toggle1 = client.post(f"/api/v1/admin/users/{uid}/toggle-status?admin_key={ADMIN_KEY}")
    assert res_toggle1.status_code == 200, res_toggle1.text
    data1 = res_toggle1.json()
    assert data1["is_active"] != initial_status

    # Rebasculer à l'état initial
    res_toggle2 = client.post(f"/api/v1/admin/users/{uid}/toggle-status?admin_key={ADMIN_KEY}")
    assert res_toggle2.status_code == 200
    data2 = res_toggle2.json()
    assert data2["is_active"] == initial_status


def test_admin_toggle_user_not_found():
    res = client.post(f"/api/v1/admin/users/999999/toggle-status?admin_key={ADMIN_KEY}")
    assert res.status_code == 404


if __name__ == "__main__":
    print("Running Admin Back-Office Tests...")
    test_admin_auth_forbidden()
    print("✅ test_admin_auth_forbidden validé")
    test_admin_get_users_list_and_dashboard()
    print("✅ test_admin_get_users_list_and_dashboard validé")
    test_admin_toggle_user_status()
    print("✅ test_admin_toggle_user_status validé")
    test_admin_toggle_user_not_found()
    print("✅ test_admin_toggle_user_not_found validé")
    print("\n🎉 Tous les tests du Back-Office Administrateur sont validés à 100% !")
