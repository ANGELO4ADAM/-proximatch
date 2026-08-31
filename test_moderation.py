from datetime import datetime
from fastapi.testclient import TestClient
from main import app, init_db, get_db
from moderation import moderate_message, detect_violations, mask_sensitive_data


def setup_database():
    init_db()


client = TestClient(app)


# ----------------------------------------------------------------------
# 1. Tests Unitaires du Moteur de Modération (moderation.py)
# ----------------------------------------------------------------------
def test_phone_number_moderation():
    test_cases = [
        ("Mon numéro est 0612345678", True, "phone_number"),
        ("Appelle au 06 12 34 56 78 stp", True, "phone_number"),
        ("Contact : 06.12.34.56.78", True, "phone_number"),
        ("Mon tel : +33612345678", True, "phone_number"),
        ("Dispo au +33 6 12 34 56 78", True, "phone_number"),
        ("Message normal sans numéro", False, None),
    ]
    for text, expected_flagged, expected_reason in test_cases:
        res = moderate_message(text, default_mode="mask")
        assert res.is_flagged == expected_flagged
        if expected_flagged:
            assert expected_reason in res.reasons
            assert "[NUMÉRO MASQUÉ]" in res.filtered_content
    print("✅ test_phone_number_moderation passé")


def test_email_moderation():
    test_cases = [
        ("Ecris-moi à contact@example.com", True),
        ("Mon mail perso : user.test@gmail.com", True),
        ("Voici mon adresse : user [at] domain.com", True),
        ("Discutons par user arobase hotmail point fr", True),
    ]
    for text, expected_flagged in test_cases:
        res = moderate_message(text, default_mode="mask")
        assert res.is_flagged is True
        assert "email" in res.reasons
        assert "[EMAIL MASQUÉ]" in res.filtered_content
    print("✅ test_email_moderation passé")


def test_links_and_socials_moderation():
    test_cases = [
        ("Rejoins moi sur https://t.me/mon_profil", "external_link"),
        ("Mon compte snap: @super_user", "external_link"),
        ("Visite www.mon-site-direct.fr", "external_link"),
    ]
    for text, expected_reason in test_cases:
        res = moderate_message(text, default_mode="mask")
        assert res.is_flagged is True
        assert expected_reason in res.reasons
        assert "[LIEN EXTERNE MASQUÉ]" in res.filtered_content
    print("✅ test_links_and_socials_moderation passé")


def test_bypass_phrases_moderation():
    text = "On peut faire ça en direct sans l'app et sans commission ?"
    res = moderate_message(text, default_mode="mask")
    assert res.is_flagged is True
    assert "platform_bypass_phrase" in res.reasons
    print("✅ test_bypass_phrases_moderation passé")


def test_toxic_content_moderation():
    text = "Tu es vraiment un connard incompétent"
    res = moderate_message(text, default_mode="mask")
    assert res.is_flagged is True
    assert "inappropriate_language" in res.reasons
    assert "[CONTENU MODÉRÉ]" in res.filtered_content
    print("✅ test_toxic_content_moderation passé")


def test_blocking_mode():
    text = "Contacte moi au 06 12 34 56 78"
    res = moderate_message(text, default_mode="block")
    assert res.is_flagged is True
    assert res.action == "block"
    print("✅ test_blocking_mode passé")


# ----------------------------------------------------------------------
# 2. Tests d'Intégration API FastAPI
# ----------------------------------------------------------------------
def test_api_moderation_check():
    payload = {
        "content": "Bonjour, mon numéro est 07 89 00 11 22 et mon mail test@test.com",
        "action_mode": "mask"
    }
    response = client.post("/moderation/check", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["is_flagged"] is True
    assert "phone_number" in data["reasons"]
    assert "email" in data["reasons"]
    assert "[NUMÉRO MASQUÉ]" in data["filtered_content"]
    assert "[EMAIL MASQUÉ]" in data["filtered_content"]
    print("✅ test_api_moderation_check passé")


def test_api_conversation_message_moderation():
    setup_database()

    # 1. Créer une demande
    req_payload = {
        "customer_id": 1,
        "title": "Ménage 3 pièces",
        "service_date": "2026-09-01T10:00:00",
        "duration_hours": 2.0,
        "address": "15 Rue de Paris",
        "postal_code": "75011",
        "city": "Paris",
        "max_hourly_rate": 30.0
    }
    req_resp = client.post("/requests", json=req_payload)
    assert req_resp.status_code == 201
    req_id = req_resp.json()["id"]

    # 2. Créer un prestataire
    prov_payload = {
        "name": "Marie Test",
        "skills": "ménage, repassage",
        "postal_codes": "75011, Paris",
        "hourly_rate": 22.0,
        "is_active": 1
    }
    prov_resp = client.post("/providers", json=prov_payload)
    assert prov_resp.status_code == 201

    # 3. Lancer le matching
    match_resp = client.post(f"/requests/{req_id}/match")
    assert match_resp.status_code == 200

    # 4. Créer la conversation pour le match
    conv_resp = client.post("/matches/1/conversation")
    assert conv_resp.status_code == 201
    conv_id = conv_resp.json()["id"]

    # 5. Envoyer un message contenant un numéro de téléphone avec mode 'mask'
    msg_payload = {
        "sender_id": 1,
        "content": "Bonjour ! Voici mon 06 99 88 77 66 pour qu'on s'arrange.",
        "mode": "mask"
    }
    msg_resp = client.post(f"/conversations/{conv_id}/messages", json=msg_payload)
    assert msg_resp.status_code == 201
    msg_data = msg_resp.json()
    assert msg_data["is_flagged"] == 1
    assert "phone_number" in msg_data["moderation_reasons"]
    assert "[NUMÉRO MASQUÉ]" in msg_data["content"]
    assert "06 99 88 77 66" not in msg_data["content"]

    # 6. Envoyer un message en mode 'block' -> doit lever HTTP 400
    block_msg_payload = {
        "sender_id": 1,
        "content": "Rejoins moi sur whatsapp 06 00 11 22 33",
        "mode": "block"
    }
    block_resp = client.post(f"/conversations/{conv_id}/messages", json=block_msg_payload)
    assert block_resp.status_code == 400
    assert "Message bloqué" in block_resp.json()["detail"]["error"]
    print("✅ test_api_conversation_message_moderation passé")


# ----------------------------------------------------------------------
# 3. Tests Webhook WhatsApp Cloud API
# ----------------------------------------------------------------------
def test_whatsapp_webhook_verification():
    # 1. Vérification avec token valide
    response = client.get("/webhook?hub.mode=subscribe&hub.verify_token=proximatch_secure_verify_token&hub.challenge=1158201444")
    assert response.status_code == 200
    assert response.text == "1158201444"

    # 2. Vérification avec token invalide
    response = client.get("/webhook?hub.mode=subscribe&hub.verify_token=invalid_token&hub.challenge=1158201444")
    assert response.status_code == 403

    # 3. Vérification avec paramètres manquants
    response = client.get("/webhook")
    assert response.status_code == 400
    print("✅ test_whatsapp_webhook_verification passé")


def test_whatsapp_incoming_message():
    setup_database()

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "33123456789", "phone_number_id": "987654321"},
                            "contacts": [{"profile": {"name": "Sid Test"}, "wa_id": "33699887766"}],
                            "messages": [
                                {
                                    "from": "33699887766",
                                    "id": "wamid.HBgLMzM2OTk4ODc3NjYVAgASGBgyMzg1",
                                    "timestamp": "1724610000",
                                    "text": {"body": "Bonjour, j'ai besoin d'un ménage 3h à Paris 11ème."},
                                    "type": "text"
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }

    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

    # Vérification que l'utilisateur et la demande ont été créés
    import sqlite3
    with sqlite3.connect("database.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, phone FROM users WHERE phone LIKE '%33699887766%'")
        user = cursor.fetchone()
        assert user is not None
        customer_id = user[0]

        cursor.execute("SELECT id, title, customer_id FROM requests WHERE customer_id = ?", (customer_id,))
        req = cursor.fetchone()
        assert req is not None
        assert "ménage" in req[1].lower()

def test_ai_parse_endpoint():
    setup_database()

    # 1. Test d'une demande en langage naturel (Plomberie à Bondy)
    prompt_payload = {
        "text": "Salut, je cherche quelqu'un pour refaire la plomberie de ma salle de bain à Bondy, mon budget est de 150 max.",
        "customer_phone": "0699112233"
    }

    response = client.post("/requests/ai-parse", json=prompt_payload)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "success"
    assert "parsed_data" in data
    assert "request" in data
    assert data["parsed_data"]["skills_required"] == "plumbing"
    assert "bondy" in data["parsed_data"]["location"].lower()
    assert data["parsed_data"]["max_budget"] == 150.0

    # 2. Test avec texte toxique -> rejet modération HTTP 400
    toxic_payload = {
        "text": "Espèce de connard viens faire le travail",
        "customer_phone": "0699112233"
    }
    toxic_resp = client.post("/requests/ai-parse", json=toxic_payload)
    assert toxic_resp.status_code == 400

    print("✅ test_ai_parse_endpoint passé")


def test_get_latest_request():
    setup_database()

    # Créer une demande
    client.post("/requests/ai-parse", json={
        "text": "Besoin d'un jardinier à Paris 11ème pour tailler les haies budget 90€",
        "customer_phone": "0611223344"
    })

    response = client.get("/requests/latest")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["request"] is not None
    assert "jardin" in data["request"]["description"].lower()
    print("✅ test_get_latest_request passé")


def test_list_requests():
    setup_database()

    response = client.get("/requests")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    print("✅ test_list_requests passé")


def test_accept_mission_endpoint():
    setup_database()
    
    # 1. Créer une demande
    req_resp = client.post(
        "/requests",
        json={
            "customer_phone": "0612345678",
            "title": "Mission test acceptation",
            "address": "Paris 75011",
            "postal_code": "75011",
            "city": "Paris",
            "max_hourly_rate": 35.0,
            "duration_hours": 2.0
        }
    )
    assert req_resp.status_code == 201
    req_id = req_resp.json()["id"]

    # 2. Accepter la mission
    accept_resp = client.post(f"/requests/{req_id}/accept")
    assert accept_resp.status_code == 200
    data = accept_resp.json()
    assert data["status"] == "success"
    assert data["new_status"] == "assigned"
    print("✅ test_accept_mission_endpoint passé")


def test_haversine_distance_and_geo_scoring():
    from main import calculate_haversine_distance, calculate_location_score

    # 1. Calcul de distance exacte : Paris 11e (48.8590, 2.3780) -> Bondy (48.9022, 2.4828)
    dist_paris_bondy = calculate_haversine_distance(48.8590, 2.3780, 48.9022, 2.4828)
    assert 8.5 <= dist_paris_bondy <= 9.5, f"Distance inattendue: {dist_paris_bondy}"

    # 2. Test du score géographique basé sur Haversine
    score, dist = calculate_location_score(
        postal_codes="75011, Paris",
        postal_code="75011",
        city="Paris",
    )
    assert score == 30.0
    assert dist is not None and dist <= 3.0

    # 3. Distance plus éloignée (ex: Paris vers Bondy ~9km -> score 24/30)
    score_bondy, dist_bondy = calculate_location_score(
        postal_codes="93140, Bondy",
        postal_code="75011",
        city="Paris",
    )
    assert score_bondy >= 20.0
    assert dist_bondy is not None and 8.0 <= dist_bondy <= 10.0
    print("✅ test_haversine_distance_and_geo_scoring passé")


def test_find_best_matching_providers_geo_endpoint():
    setup_database()
    
    # 1. Requête GET sur /providers/match-geo (Paris 11e : 48.8590, 2.3780)
    response = client.get("/providers/match-geo?lat=48.8590&lon=2.3780&skill=menage&max_distance_km=20")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "providers" in data
    assert isinstance(data["providers"], list)
    
    # 2. Vérification du tri par distance croissante
    if len(data["providers"]) > 1:
        distances = [p["distance_km"] for p in data["providers"]]
        assert distances == sorted(distances), f"La liste n'est pas triée par distance: {distances}"
    print("✅ test_find_best_matching_providers_geo_endpoint passé")


def test_jwt_authentication_flow():
    setup_database()
    
    unique_email = f"user_{datetime.now().timestamp()}@proximatch.fr"
    pwd = "MonSuperPassword123!"

    # 1. Inscription d'un nouvel utilisateur
    reg_resp = client.post(
        "/auth/register",
        json={
            "email": unique_email,
            "password": pwd,
            "first_name": "Jean",
            "last_name": "Dupont",
            "phone": "0612345678",
            "role": "customer",
        },
    )
    assert reg_resp.status_code == 201, f"Erreur inscription: {reg_resp.text}"
    user_data = reg_resp.json()
    assert user_data["email"] == unique_email
    assert user_data["role"] == "customer"
    assert "phone_masked" in user_data

    # 2. Tentative de double inscription avec le même email (doit échouer en 400)
    reg_duplicate = client.post(
        "/auth/register",
        json={
            "email": unique_email,
            "password": pwd,
            "first_name": "Autre",
            "last_name": "Nom",
            "phone": "0600000000",
        },
    )
    assert reg_duplicate.status_code == 400

    # 3. Connexion OAuth2 Form standard (/token)
    token_resp = client.post(
        "/token",
        data={"username": unique_email, "password": pwd},
    )
    assert token_resp.status_code == 200, f"Erreur token: {token_resp.text}"
    token_data = token_resp.json()
    assert "access_token" in token_data
    assert token_data["token_type"] == "bearer"
    jwt_token = token_data["access_token"]

    # 4. Connexion JSON (/auth/login)
    json_login_resp = client.post(
        "/auth/login",
        json={"email": unique_email, "password": pwd},
    )
    assert json_login_resp.status_code == 200
    assert "access_token" in json_login_resp.json()

    # 5. Tentative de connexion avec mauvais mot de passe (doit échouer)
    wrong_login = client.post(
        "/auth/login",
        json={"email": unique_email, "password": "WrongPassword!"},
    )
    assert wrong_login.status_code == 401

    # 6. Consultation du profil connecté (/auth/me) avec le header Authorization
    auth_headers = {"Authorization": f"Bearer {jwt_token}"}
    me_resp = client.get("/auth/me", headers=auth_headers)
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["email"] == unique_email
    assert me_data["first_name"] == "Jean"

    # 7. Accès à la route protégée /api/v1/protected-requests
    protected_resp = client.get("/api/v1/protected-requests", headers=auth_headers)
    assert protected_resp.status_code == 200
    assert "Bienvenue Jean Dupont" in protected_resp.json()["message"]

    # 8. Accès sans token -> doit être rejeté en 401 Unauthorized
    unauth_resp = client.get("/api/v1/protected-requests")
    assert unauth_resp.status_code == 401
    
    print("✅ test_jwt_authentication_flow passé avec succès")


if __name__ == "__main__":
    setup_database()
    test_jwt_authentication_flow()
    test_haversine_distance_and_geo_scoring()
    test_find_best_matching_providers_geo_endpoint()
    test_phone_number_moderation()
    test_email_moderation()
    test_links_and_socials_moderation()
    test_bypass_phrases_moderation()
    test_toxic_content_moderation()
    test_blocking_mode()
    test_api_moderation_check()
    test_api_conversation_message_moderation()
    test_whatsapp_webhook_verification()
    test_whatsapp_incoming_message()
    test_ai_parse_endpoint()
    test_get_latest_request()
    test_list_requests()
    test_accept_mission_endpoint()
    print("\n🎉 Tous les tests (Authentification JWT, Sécurité, Modération, WhatsApp, Parser IA, Haversine GPS & Mission) ont réussi avec succès !")


