#!/usr/bin/env python3
"""
test_admin_dashboard.py - Tests automatisés pour le module Dashboard Admin et Métriques Globales.
"""

import unittest
from fastapi.testclient import TestClient
from main import app, engine, SessionLocal


class TestAdminDashboard(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_sqlalchemy_engine_configuration(self):
        """Vérifie que le moteur SQLAlchemy et SessionLocal sont correctement initialisés."""
        self.assertIsNotNone(engine)
        self.assertIsNotNone(SessionLocal)
        print("✅ test_sqlalchemy_engine_configuration validé")

    def test_get_admin_dashboard_stats(self):
        """Teste l'endpoint des statistiques et métriques consolidées du dashboard admin."""
        res = self.client.get("/api/v1/admin/dashboard-stats")
        self.assertEqual(res.status_code, 200, f"Erreur: {res.text}")
        data = res.json()
        self.assertEqual(data["status"], "success")
        metrics = data["metrics"]
        self.assertIn("total_providers", metrics)
        self.assertIn("active_missions", metrics)
        self.assertIn("escrow_volume_euros", metrics)
        self.assertIn("platform_commission_euros", metrics)
        self.assertIn("moderation_alerts_blocked", metrics)
        self.assertIn("database_engine", metrics)
        self.assertTrue(metrics["total_providers"] >= 1)
        self.assertTrue(metrics["escrow_volume_euros"] > 0)
        self.assertTrue(metrics["platform_commission_euros"] > 0)
        print(f"✅ test_get_admin_dashboard_stats validé (Moteur: {metrics['database_engine']}, Artisans: {metrics['total_providers']}, Volume: {metrics['escrow_volume_euros']}€)")


if __name__ == "__main__":
    unittest.main()
