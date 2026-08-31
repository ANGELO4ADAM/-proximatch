# 🚀 ProxiMatch — Marketplace Omnicanal & Matching Intelligent

Plateforme SaaS intelligente de mise en relation pour prestations de services à domicile et artisanat (ménage, plomberie, électricité, jardinage, peinture, déménagement), propulsée par **FastAPI**, **Google Gemini 2.5 Flash**, **Meta WhatsApp Cloud API v25.0** et un moteur géodésique **Haversine**.

---

## 🌟 Fonctionnalités Clés

1. **🔐 Authentification JWT & Sécurité RBAC**
   - Hachage cryptographique des mots de passe avec `bcrypt`.
   - Jetons d'accès `JWT (HS256)` avec expiration paramétrable.
   - Contrôle d'accès par rôles (`customer`, `provider`, `admin`).
   - Endpoints `/auth/register`, `/token`, `/auth/login`, `/auth/me`, `/api/v1/protected-requests`.

2. **🛰️ Moteur Géodésique & Formule de Haversine**
   - Calcul de distance orthodromique réelle ($R = 6371.0\text{ km}$).
   - Géocodage et résolution automatique des coordonnées GPS.
   - Filtrage strict par rayon d'intervention maximal (`service_radius_km`).
   - Endpoint dédié `GET /providers/match-geo`.

3. **📱 WhatsApp Business Cloud API v25.0 Premium**
   - Messages interactifs natifs avec boutons cliquables (`button_reply`, Quick Reply).
   - Bannières haute résolution Unsplash dynamiques selon le métier.
   - Webhook Meta sécurisé avec challenge de vérification (`hub.verify_token`).
   - Commandes textuelles conversationnelles (`mes annonces`, `supprimer [ID]`).
   - Mockup et simulateur de smartphone en direct.

4. **🛡️ Module de Modération & Anti-Désintermédiation**
   - Détection des numéros de téléphone (FR/internationaux, espacés, épelés en lettres).
   - Détection des adresses e-mails (standards et obfusquées).
   - Filtrage des coordonnées bancaires (IBAN), liens externes et réseaux sociaux.
   - Filtrage lexical contre les injures et les tentatives de contournement.
   - Modes modulables : `mask` (masquage automatique), `block` (rejet HTTP 400), `audit`.

5. **🧠 Extraction IA & Hybridation NLP**
   - Structuration JSON stricte via **Google Gemini 2.5 Flash** (`google-genai`).
   - Moteur heuristique de secours haute précision sans dépendance API externe.

---

## 🛠️ Installation & Démarrage

### 1. Cloner le projet et créer l'environnement virtuel
```bash
git clone https://github.com/ANGELO4ADAM/proximatch.git
cd proximatch
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Variables d'Environnement (`.env`)
Créez un fichier `.env` à la racine :
```env
# Clé secrète JWT
SECRET_KEY=super_secret_key_proximatch_change_me_in_prod
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# IA Google Gemini (optionnel, fallback heuristique actif si absent)
GEMINI_API_KEY=votre_cle_gemini_ici

# Meta WhatsApp Cloud API
WHATSAPP_TOKEN=TON_TOKEN_PERMANENT_META
PHONE_NUMBER_ID=TON_PHONE_NUMBER_ID
VERIFY_TOKEN=proximatch_secure_verify_token

# Paiements Séquestre Stripe (Hold & Escrow)
STRIPE_SECRET_KEY=sk_test_51...

# Serveur & Port
PORT=8001
HOST=127.0.0.1
```

### 3. Lancer l'Application
```bash
python main.py
# ou via uvicorn
uvicorn main.py:app --host 0.0.0.0 --port 8001 --reload
```
- **Dashboard Web :** `http://127.0.0.1:8001/`
- **Documentation OpenAPI (Swagger UI) :** `http://127.0.0.1:8001/docs`

---

## 🧪 Exécution des Tests

Le projet dispose d'une suite de tests automatisée complète :
```bash
./venv/bin/python test_moderation.py
./venv/bin/python test_whatsapp_premium.py
./venv/bin/python test_stripe_escrow.py
./venv/bin/python test_agenda_slots.py
./venv/bin/python test_websocket_chat.py
```

---

## 📚 Endpoints Principaux

| Méthode | Route | Description |
| :---: | :--- | :--- |
| `POST` | `/auth/register` | Inscription nouvel utilisateur |
| `POST` | `/token` | Connexion OAuth2 Form (Bearer Token) |
| `POST` | `/auth/login` | Connexion JSON |
| `GET` | `/auth/me` | Profil utilisateur connecté |
| `POST` | `/requests/ai-parse` | Extraction NLP & Matching instantané |
| `GET` | `/providers/match-geo` | Recherche de prestataires par GPS & Haversine |
| `WS` | `/ws/conversations/{id}` | Messagerie temps réel WebSocket bidirectionnelle avec modération en direct |
| `POST` | `/conversations/{id}/messages` | Envoi message HTTP (avec broadcast WebSocket automatique) |
| `GET` | `/conversations/{id}/messages` | Historique complet des messages d'une conversation |
| `POST` | `/api/v1/agenda/slots` | Création de créneaux de disponibilité pour un artisan |
| `GET` | `/api/v1/agenda/providers/{id}/slots` | Consultation des créneaux (filtrage date / disponibilité) |
| `POST` | `/api/v1/agenda/book-slot` | Réservation directe d'un créneau et création de mission |
| `GET` | `/api/v1/agenda/missions` | Suivi et listing des missions d'agenda |
| `POST` | `/api/v1/payments/create-escrow-intent` | Création séquestre Stripe (`capture_method='manual'`) |
| `POST` | `/api/v1/payments/release-escrow/{id}` | Validation mission & libération des fonds pour l'artisan |
| `POST` | `/api/v1/payments/cancel-escrow/{id}` | Annulation de l'empreinte bancaire sans débit client |
| `GET` | `/api/v1/payments/intent/{id}` | Consultation du statut de l'intention de paiement |
| `GET/POST` | `/webhook` | Webhook Meta WhatsApp Cloud API |
| `POST` | `/whatsapp/simulate` | Simulateur de messages/boutons WhatsApp |
| `POST` | `/moderation/check` | Analyse et filtrage de sécurité |

---

## 📄 Licence
Projet sous licence MIT — Tous droits réservés © 2026 ProxiMatch.
