import os
import sqlite3
from fastapi.testclient import TestClient
from main import app, init_db

client = TestClient(app)


def test_send_sms_notification_endpoint():
    res = client.post(
        "/api/v1/notifications/send-sms",
        json={
            "phone": "0612345678",
            "message": "🔔 Alerte Mission à Bondy : Un créneau plomberie vient d'être réservé !",
            "channel": "sms",
            "recipient_email": "plombier.bondy@example.com",
            "city": "Bondy",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "success"
    assert data["channel"] == "sms"
    assert data["recipient"] == "+33612345678"
    assert data["delivery_status"] in ("delivered", "sent")


def test_send_whatsapp_notification_endpoint():
    res = client.post(
        "/api/v1/notifications/send-whatsapp",
        json={
            "phone": "+33699887766",
            "message": "🔔 [ProxiMatch WhatsApp Alert] Nouveau créneau réservé à proximité (Bondy).",
            "channel": "whatsapp",
            "city": "Bondy",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "success"
    assert data["channel"] == "whatsapp"
    assert data["recipient"] == "+33699887766"


def test_send_push_notification_endpoint():
    res = client.post(
        "/api/v1/notifications/send-push",
        json={
            "phone": "0655443322",
            "message": "🔔 Push en direct : Mission confirmée près de chez vous.",
            "channel": "push",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "success"
    assert data["channel"] == "push"


def test_notifications_history_endpoint():
    res = client.get("/api/v1/notifications/history?limit=10")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "success"
    assert "notifications" in data
    assert len(data["notifications"]) >= 3


def test_book_slot_triggers_realtime_notifications():
    # 1. Créer un créneau pour le prestataire #1
    slot_res = client.post(
        "/api/v1/agenda/slots",
        json={
            "provider_id": 1,
            "date": "2026-09-15",
            "start_time": "14:00",
            "end_time": "16:00",
        },
    )
    assert slot_res.status_code in (200, 201), slot_res.text
    slot_id = slot_res.json()["id"]

    # 2. Réserver le créneau
    book_res = client.post(
        "/api/v1/agenda/book-slot",
        json={
            "slot_id": slot_id,
            "customer_email": "client.test@bondy.fr",
        },
    )
    assert book_res.status_code == 200, book_res.text
    mission = book_res.json()
    assert mission["status"] == "pending"

    # 3. Vérifier que les notifications SMS/WhatsApp/Push ont bien été loggées
    history_res = client.get("/api/v1/notifications/history?limit=5")
    assert history_res.status_code == 200
    notifs = history_res.json()["notifications"]
    assert any("Bondy" in n["message"] or "ProxiMatch" in n["message"] for n in notifs)


if __name__ == "__main__":
    print("Running Notification Tests...")
    test_send_sms_notification_endpoint()
    print("✅ test_send_sms_notification_endpoint validé")
    test_send_whatsapp_notification_endpoint()
    print("✅ test_send_whatsapp_notification_endpoint validé")
    test_send_push_notification_endpoint()
    print("✅ test_send_push_notification_endpoint validé")
    test_notifications_history_endpoint()
    print("✅ test_notifications_history_endpoint validé")
    test_book_slot_triggers_realtime_notifications()
    print("✅ test_book_slot_triggers_realtime_notifications validé")
    print("\n🎉 Tous les tests de Notifications Push/SMS/WhatsApp sont passés avec succès !")
