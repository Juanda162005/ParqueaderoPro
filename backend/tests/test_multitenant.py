import importlib
import os
import tempfile
import unittest

from backend import database, main


class MultiTenantTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_parqueadero.db")
        os.environ["DB_FILE"] = self.db_path
        importlib.reload(database)
        importlib.reload(main)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_normalizar_slug(self):
        self.assertEqual(main.normalizar_slug(" Parqueadero Norte "), "parqueadero-norte")

    def test_autenticar_usuario_respecta_tenant(self):
        tenant = main.crear_tenant("Parqueadero Uno", "parqueadero-uno")
        main.crear_usuario_para_tenant(
            tenant_id=tenant["id"],
            username="admin",
            password="admin123",
            nombre="Administrador",
            rol="Admin",
            permisos={},
        )

        user = main.autenticar_usuario("admin", "admin123", "parqueadero-uno")
        self.assertIsNotNone(user)
        self.assertEqual(user["tenant_slug"], "parqueadero-uno")
        self.assertIsNone(main.autenticar_usuario("admin", "admin123", "parqueadero-dos"))


if __name__ == "__main__":
    unittest.main()
