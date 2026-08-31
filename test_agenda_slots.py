#!/usr/bin/env python3
"""
test_agenda_slots.py - Tests automatisés pour le module Agenda, Créneaux et Réservations de Missions.
"""

import unittest
from fastapi.testclient import TestClient
from main import app


class TestAgendaSlots(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_create_and_get_slots(self):
        """Teste la création de créneaux individuels et par lot, puis leur récupération."""
        # 1. Création slot individuel
        slot_payload = {
            "provider_id": 1,
            "date": "2026-09-05",
            "start_time": "08:00",
            "end_time": "10:00"
        }
        res = self.client.post("/api/v1/agenda/slots", json=slot_payload)
        self.assertEqual(res.status_code, 201, f"Erreur création: {res.text}")
        data = res.json()
        self.assertEqual(data["provider_id"], 1)
        self.assertEqual(data["is_booked"], False)
        slot_id = data["id"]

        # 2. Création batch
        batch_payload = [
            {"provider_id": 1, "date": "2026-09-05", "start_time": "10:30", "end_time": "12:30"},
            {"provider_id": 1, "date": "2026-09-06", "start_time": "14:00", "end_time": "17:00"}
        ]
        batch_res = self.client.post("/api/v1/agenda/slots/batch", json=batch_payload)
        self.assertEqual(batch_res.status_code, 201)
        self.assertEqual(len(batch_res.json()), 2)

        # 3. Récupération des créneaux
        slots_res = self.client.get("/api/v1/agenda/providers/1/slots?date=2026-09-05")
        self.assertEqual(slots_res.status_code, 200)
        slots = slots_res.json()
        self.assertTrue(len(slots) >= 2)
        print("✅ test_create_and_get_slots validé")

    def test_book_slot_and_missions_flow(self):
        """Teste le flux complet de réservation de créneau et cycle de vie de la mission."""
        # 1. Créer un créneau
        slot_payload = {
            "provider_id": 1,
            "date": "2026-09-10",
            "start_time": "14:00",
            "end_time": "16:00"
        }
        res = self.client.post("/api/v1/agenda/slots", json=slot_payload)
        self.assertEqual(res.status_code, 201)
        slot_id = res.json()["id"]

        # 2. Réserver le créneau
        book_payload = {
            "slot_id": slot_id,
            "customer_email": "client.agenda@example.com"
        }
        book_res = self.client.post("/api/v1/agenda/book-slot", json=book_payload)
        self.assertEqual(book_res.status_code, 200, f"Erreur réservation: {book_res.text}")
        mission = book_res.json()
        self.assertEqual(mission["provider_id"], 1)
        self.assertEqual(mission["customer_email"], "client.agenda@example.com")
        self.assertEqual(mission["status"], "pending")
        mission_id = mission["id"]

        # 3. Vérifier que le créneau est désormais marqué 'is_booked' = True
        slots_res = self.client.get("/api/v1/agenda/providers/1/slots?date=2026-09-10")
        booked_slot = next(s for s in slots_res.json() if s["id"] == slot_id)
        self.assertTrue(booked_slot["is_booked"])

        # 4. Tenter de réserver à nouveau le même créneau (doit échouer avec HTTP 409 Conflict)
        conflict_res = self.client.post("/api/v1/agenda/book-slot", json=book_payload)
        self.assertEqual(conflict_res.status_code, 409)

        # 5. Mettre à jour le statut de la mission
        patch_res = self.client.patch(f"/api/v1/agenda/missions/{mission_id}/status?status=confirmed")
        self.assertEqual(patch_res.status_code, 200)
        self.assertEqual(patch_res.json()["status"], "confirmed")

        # 6. Lister les missions
        list_res = self.client.get("/api/v1/agenda/missions?customer_email=client.agenda@example.com")
        self.assertEqual(list_res.status_code, 200)
        self.assertTrue(len(list_res.json()) >= 1)
        print("✅ test_book_slot_and_missions_flow validé")


if __name__ == "__main__":
    unittest.main()
