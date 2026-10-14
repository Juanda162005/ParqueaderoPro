import unittest

from backend.main import crear_token, decodificar_token


class JwtAuthTests(unittest.TestCase):
    def test_crear_y_decodificar_token(self):
        token = crear_token(usuario_id=7, username="admin")
        payload = decodificar_token(token)

        self.assertEqual(payload["sub"], "7")
        self.assertEqual(payload["username"], "admin")


if __name__ == "__main__":
    unittest.main()
