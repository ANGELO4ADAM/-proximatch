#!/usr/bin/env python3
"""
test_websocket_chat.py - Tests automatisés pour le module WebSocket temps réel et ConnectionManager.
"""

import json
import unittest
from fastapi.testclient import TestClient
from main import app, manager


class TestWebSocketChat(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_websocket_connect_and_broadcast(self):
        """Teste l'envoi et la réception en direct via WebSocket."""
        conv_id = 99
        with self.client.websocket_connect(f"/ws/conversations/{conv_id}") as ws:
            # 1. Envoi d'un message standard
            payload = {
                "sender_id": 1,
                "content": "Bonjour, êtes-vous disponible demain à 14h ?",
                "mode": "mask"
            }
            ws.send_text(json.dumps(payload))
            data_raw = ws.receive_text()
            msg = json.loads(data_raw)
            self.assertEqual(msg["conversation_id"], conv_id)
            self.assertEqual(msg["sender_id"], 1)
            self.assertIn("Bonjour", msg["content"])
            print(f"✅ test_websocket_connect_and_broadcast: reçu '{msg['content']}'")

    def test_websocket_moderation_masking(self):
        """Teste le masquage automatique des numéros privés via WebSocket."""
        conv_id = 99
        with self.client.websocket_connect(f"/ws/conversations/{conv_id}") as ws:
            payload = {
                "sender_id": 2,
                "content": "Appelle-moi vite au 0611223344 ou par email test@example.com",
                "mode": "mask"
            }
            ws.send_text(json.dumps(payload))
            data_raw = ws.receive_text()
            msg = json.loads(data_raw)
            self.assertIn("[NUMÉRO MASQUÉ]", msg["content"])
            self.assertIn("[EMAIL MASQUÉ]", msg["content"])
            print("✅ test_websocket_moderation_masking validé")

    def test_websocket_moderation_blocking(self):
        """Teste le blocage immédiat des messages avec mode block."""
        conv_id = 99
        with self.client.websocket_connect(f"/ws/conversations/{conv_id}") as ws:
            payload = {
                "sender_id": 2,
                "content": "Paiement en espèces hors appli direct espèce d'escroc",
                "mode": "block"
            }
            ws.send_text(json.dumps(payload))
            data_raw = ws.receive_text()
            res = json.loads(data_raw)
            self.assertEqual(res.get("status"), "blocked")
            self.assertEqual(res.get("type"), "error")
            print("✅ test_websocket_moderation_blocking validé")


    def test_websocket_custom_sender_name_and_raw_text(self):
        """Teste l'envoi de texte brut avec sender_name personnalisé."""
        conv_id = 88
        with self.client.websocket_connect(f"/ws/conversations/{conv_id}?sender_name=Thomas") as ws:
            # ws envoie du texte brut
            ws.send_text("Bonjour, je suis l'artisan Thomas.")
            
            # ws reçoit l'écho broadcasté
            data = json.loads(ws.receive_text())
            self.assertEqual(data["sender"], "Thomas")
            self.assertEqual(data["message"], "Bonjour, je suis l'artisan Thomas.")
            self.assertEqual(data["flagged"], False)
            print("✅ test_websocket_custom_sender_name_and_raw_text validé")


if __name__ == "__main__":
    unittest.main()

