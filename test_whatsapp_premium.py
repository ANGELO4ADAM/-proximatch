"""
Tests automatisés des fonctionnalités WhatsApp Premium pour ProxiMatch :
- Images d'en-tête thématiques haute résolution
- Typographie et mise en forme avancée
- Messages interactifs avec boutons cliquables (Acceptation, Détails, Refus, Support)
- Gestion des webhooks Meta entrants et simulation
"""

from fastapi.testclient import TestClient
from main import app, init_db
from whatsapp_service import (
    SERVICE_IMAGES,
    get_service_image,
    build_premium_match_interactive,
    build_premium_confirmation_interactive,
    build_premium_details_interactive,
    build_premium_security_alert_interactive,
    SIMULATED_MESSAGES_LOG,
)

client = TestClient(app)


def setup_database():
    init_db()


def test_service_images_mapping():
    """Vérifie que les images haute résolution sont correctement attribuées selon le métier."""
    assert "photo-1581578731548" in get_service_image("Ménage et repassage")
    assert "photo-1585704032915" in get_service_image("Fuite de plomberie salle de bain")
    assert "photo-1558904541" in get_service_image("Tonte de jardin et pelouse")
    assert "photo-1621905251189" in get_service_image("Panne tableau électrique")
    assert "photo-1589939705384" in get_service_image("Peinture murale salon")
    assert "photo-1600585152220" in get_service_image("Déménagement cartons")
    print("✅ test_service_images_mapping validé")


def test_premium_interactive_payload_structure():
    """Vérifie que la structure des messages interactifs respecte les spécifications Meta WhatsApp Cloud API."""
    payload = build_premium_match_interactive(
        recipient_phone="33612345678",
        request_id=42,
        request_title="Ménage 3 pièces",
        provider_name="Marie Dupont",
        match_score=98.5,
        hourly_rate=24.0,
        location="Paris 11e (75011)",
        skills="cleaning",
    )

    assert payload["messaging_product"] == "whatsapp"
    assert payload["type"] == "interactive"
    assert payload["to"] == "33612345678"

    interactive = payload["interactive"]
    assert interactive["type"] == "button"
    assert interactive["header"]["type"] == "image"
    assert "https://" in interactive["header"]["image"]["link"]
    assert "Marie Dupont" in interactive["body"]["text"]
    assert "98.5%" in interactive["body"]["text"]
    assert "24.00 €" in interactive["body"]["text"]

    buttons = interactive["action"]["buttons"]
    assert len(buttons) == 3
    # Vérification de la contrainte Meta : titre du bouton <= 20 caractères
    for btn in buttons:
        assert len(btn["reply"]["title"]) <= 20
        assert "accept_req_42" in buttons[0]["reply"]["id"]
    print("✅ test_premium_interactive_payload_structure validé")


def test_incoming_text_creates_request_and_sends_premium_match():
    """Vérifie qu'un message entrant WhatsApp texte déclenche la réponse interactive Premium."""
    setup_database()

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry_premium_1",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "33611223344",
                                    "id": "wamid.test_prem_1",
                                    "timestamp": "1724610000",
                                    "type": "text",
                                    "text": {"body": "Bonjour, je cherche un plombier pour réparer un robinet à Bondy budget 100€"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Vérifier que le message envoyé est interactif avec image d'en-tête
    assert len(SIMULATED_MESSAGES_LOG) > 0
    latest_msg = SIMULATED_MESSAGES_LOG[-1]
    assert latest_msg["recipient"] == "33611223344"
    assert latest_msg["type"] == "interactive"
    assert latest_msg["payload"]["interactive"]["header"]["type"] == "image"
    assert "photo-1585704032915" in latest_msg["payload"]["interactive"]["header"]["image"]["link"]
    print("✅ test_incoming_text_creates_request_and_sends_premium_match validé")


def test_interactive_button_click_accept_mission():
    """Vérifie le traitement d'un clic sur le bouton 'Accepter' (changement de statut + confirmation festive)."""
    setup_database()

    # 1. Créer une demande via l'API pour avoir un ID connu
    req_resp = client.post(
        "/requests",
        json={
            "customer_phone": "33699887766",
            "title": "Nettoyage après déménagement",
            "postal_code": "75011",
            "city": "Paris",
            "max_hourly_rate": 28.0,
            "duration_hours": 3.0,
        },
    )
    req_id = req_resp.json()["id"]

    # 2. Simuler le clic sur le bouton interactif 'accept_req_{req_id}'
    btn_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "entry_btn_1",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "33699887766",
                                    "id": "wamid.btn_click_1",
                                    "timestamp": "1724610001",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {
                                            "id": f"accept_req_{req_id}",
                                            "title": "✅ Accepter le profil",
                                        },
                                    },
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    response = client.post("/webhook", json=btn_payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "success"
    assert res_data["action"] == "mission_accepted"

    # Vérifier que la confirmation interactive Premium a été envoyée
    latest_msg = SIMULATED_MESSAGES_LOG[-1]
    assert latest_msg["recipient"] == "33699887766"
    assert latest_msg["type"] == "interactive"
    assert "🎉 *MISSION CONFIRMÉE & ASSIGNÉE !* 🎉" in latest_msg["payload"]["interactive"]["body"]["text"]
    assert "photo-1560518883" in latest_msg["payload"]["interactive"]["header"]["image"]["link"]

    # Vérifier que le statut en BDD est bien passé à 'assigned'
    check_req = client.get(f"/requests/{req_id}")
    assert check_req.json()["status"] == "assigned"
    print("✅ test_interactive_button_click_accept_mission validé")


def test_interactive_button_click_details():
    """Vérifie le traitement d'un clic sur 'Voir les détails'."""
    setup_database()

    req_resp = client.post(
        "/requests",
        json={
            "customer_phone": "33699887766",
            "title": "Peinture couloir",
            "description": "2 couches de blanc mat",
            "postal_code": "93100",
            "city": "Montreuil",
            "max_hourly_rate": 30.0,
        },
    )
    req_id = req_resp.json()["id"]

    sim_res = client.post(
        "/whatsapp/simulate",
        json={"sender_phone": "33699887766", "button_id": f"details_req_{req_id}"},
    )
    assert sim_res.status_code == 200
    latest_msg = SIMULATED_MESSAGES_LOG[-1]
    assert f"DÉTAILS COMPLETS DE LA DEMANDE #{req_id}" in latest_msg["payload"]["interactive"]["body"]["text"]
    assert "Peinture couloir" in latest_msg["payload"]["interactive"]["body"]["text"]
    print("✅ test_interactive_button_click_details validé")


def test_whatsapp_moderation_security_alert():
    """Vérifie que les propos interdits ou tentatives de contournement reçoivent la carte de sécurité Premium."""
    sim_res = client.post(
        "/whatsapp/simulate",
        json={"sender_phone": "33699887766", "text": "Contacte moi au 0612345678 en direct sans passer par l'application espèce d'escroc"},
    )
    assert sim_res.status_code == 200
    latest_msg = SIMULATED_MESSAGES_LOG[-1]
    assert "AVERTISSEMENT DE SÉCURITÉ PROXIMATCH" in latest_msg["payload"]["interactive"]["body"]["text"]
    assert "photo-1563986768609" in latest_msg["payload"]["interactive"]["header"]["image"]["link"]
    print("✅ test_whatsapp_moderation_security_alert validé")


def test_get_latest_whatsapp_messages_endpoint():
    """Vérifie la récupération de l'historique des messages pour le Dashboard."""
    res = client.get("/whatsapp/messages/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert isinstance(data["messages"], list)
    assert len(data["messages"]) > 0
    print("✅ test_get_latest_whatsapp_messages_endpoint validé")


def test_whatsapp_commands_list_and_delete():
    """Vérifie le fonctionnement des commandes WhatsApp 'mes annonces' et 'supprimer [ID]'."""
    setup_database()
    test_phone = "33688776655"

    # 1. Créer une annonce par WhatsApp
    sim_res = client.post(
        "/whatsapp/simulate",
        json={"sender_phone": test_phone, "text": "Recherche électricien pour tableau électrique à Paris 11ème"},
    )
    assert sim_res.status_code == 200
    created_id = sim_res.json()["result"]["request_id"]

    # 2. Tester la commande "mes annonces"
    list_res = client.post(
        "/whatsapp/simulate",
        json={"sender_phone": test_phone, "text": "mes annonces"},
    )
    assert list_res.status_code == 200
    assert list_res.json()["result"]["action"] == "list_requests"
    latest_msg = SIMULATED_MESSAGES_LOG[-1]
    assert "Vos dernières annonces" in latest_msg["payload"]["text"]["body"]
    assert f"#{created_id}" in latest_msg["payload"]["text"]["body"]

    # 3. Tester la commande "supprimer [ID]"
    del_res = client.post(
        "/whatsapp/simulate",
        json={"sender_phone": test_phone, "text": f"supprimer {created_id}"},
    )
    assert del_res.status_code == 200
    assert del_res.json()["result"]["action"] == "delete_request"
    latest_msg = SIMULATED_MESSAGES_LOG[-1]
    assert f"L'annonce #{created_id} a bien été annulée" in latest_msg["payload"]["text"]["body"]

    # Vérifier que le statut en BDD est 'cancelled'
    check_req = client.get(f"/requests/{created_id}")
    assert check_req.json()["status"] == "cancelled"
    print("✅ test_whatsapp_commands_list_and_delete validé")


if __name__ == "__main__":
    setup_database()
    test_service_images_mapping()
    test_premium_interactive_payload_structure()
    test_incoming_text_creates_request_and_sends_premium_match()
    test_interactive_button_click_accept_mission()
    test_interactive_button_click_details()
    test_whatsapp_moderation_security_alert()
    test_get_latest_whatsapp_messages_endpoint()
    test_whatsapp_commands_list_and_delete()
    print("\n🌟 Tous les tests WhatsApp Premium (Images, Commandes, Boutons Cliquables, Webhook & Sécurité) ont réussi avec brio !")

