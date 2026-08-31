#!/usr/bin/env python3
"""
test_stripe_escrow.py - Tests automatisés pour le module de paiement séquestre (Stripe Escrow & Hold).
"""

import sys
import unittest
from fastapi.testclient import TestClient
from main import app, MOCK_ESCROW_STORE


class TestStripeEscrow(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        MOCK_ESCROW_STORE.clear()

    def test_create_escrow_payment_intent_success(self):
        """Teste la création d'un séquestre bancaire avec capture manuelle."""
        payload = {
            "mission_id": 101,
            "amount_cents": 4500,  # 45.00 €
            "customer_email": "client@example.com",
            "provider_stripe_account_id": "acct_provider_123",
            "currency": "eur"
        }
        res = self.client.post("/api/v1/payments/create-escrow-intent", json=payload)
        self.assertEqual(res.status_code, 200, f"Erreur: {res.text}")
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["amount_cents"], 4500)
        self.assertEqual(data["capture_method"], "manual")
        self.assertEqual(data["mission_id"], 101)
        self.assertTrue(data["payment_intent_id"].startswith("pi_"))
        self.assertIsNotNone(data["client_secret"])
        print(f"✅ test_create_escrow_payment_intent_success validé (PI: {data['payment_intent_id']})")

    def test_create_escrow_invalid_amount(self):
        """Vérifie le rejet d'un montant invalide (<= 0)."""
        payload = {
            "mission_id": 102,
            "amount_cents": 0,
            "customer_email": "client@example.com",
            "provider_stripe_account_id": "acct_provider_123"
        }
        res = self.client.post("/api/v1/payments/create-escrow-intent", json=payload)
        self.assertEqual(res.status_code, 400)
        print("✅ test_create_escrow_invalid_amount validé")

    def test_release_escrow_funds(self):
        """Teste la libération et capture définitive des fonds pour l'artisan."""
        # 1. Créer le séquestre
        payload = {
            "mission_id": 202,
            "amount_cents": 7500,
            "customer_email": "client.hebergement@example.com",
            "provider_stripe_account_id": "acct_artisan_456"
        }
        create_res = self.client.post("/api/v1/payments/create-escrow-intent", json=payload)
        self.assertEqual(create_res.status_code, 200)
        pi_id = create_res.json()["payment_intent_id"]

        # 2. Libérer le séquestre
        release_res = self.client.post(f"/api/v1/payments/release-escrow/{pi_id}")
        self.assertEqual(release_res.status_code, 200, f"Erreur: {release_res.text}")
        rel_data = release_res.json()
        self.assertEqual(rel_data["status"], "completed")
        self.assertEqual(rel_data["payment_intent_id"], pi_id)
        self.assertEqual(rel_data["amount_captured"], 7500)
        print(f"✅ test_release_escrow_funds validé pour {pi_id}")

    def test_cancel_escrow_funds(self):
        """Teste l'annulation d'un séquestre sans débit client."""
        # 1. Créer le séquestre
        payload = {
            "mission_id": 303,
            "amount_cents": 3000,
            "customer_email": "annulation@example.com",
            "provider_stripe_account_id": "acct_artisan_789"
        }
        create_res = self.client.post("/api/v1/payments/create-escrow-intent", json=payload)
        self.assertEqual(create_res.status_code, 200)
        pi_id = create_res.json()["payment_intent_id"]

        # 2. Annuler le séquestre
        cancel_res = self.client.post(f"/api/v1/payments/cancel-escrow/{pi_id}")
        self.assertEqual(cancel_res.status_code, 200)
        cancel_data = cancel_res.json()
        self.assertEqual(cancel_data["status"], "cancelled")

        # 3. Vérifier statut
        status_res = self.client.get(f"/api/v1/payments/intent/{pi_id}")
        self.assertEqual(status_res.status_code, 200)
        self.assertEqual(status_res.json()["intent"]["status"], "canceled")
        print(f"✅ test_cancel_escrow_funds validé pour {pi_id}")


if __name__ == "__main__":
    unittest.main()
