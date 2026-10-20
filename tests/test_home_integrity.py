import unittest
from fastapi.testclient import TestClient
import main

class TestHomeIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def test_home_page_status_and_content(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text

        # 1. Headline e Proposta de Valor
        self.assertIn("Chega de planilhas confusas", html)
        self.assertIn("auditor financeiro inteligente", html.lower())

        # 2. Hero Search Bar e Autocomplete
        self.assertIn('id="campo-busca"', html)
        self.assertIn('id="sugestoes-busca"', html)
        self.assertIn('action="/analisar"', html)

        # 3. Badges de Atalhos Rápidos (PETR4, VALE3, ITUB4, MXRF11)
        for ticker in ["PETR4", "VALE3", "ITUB4", "MXRF11"]:
            self.assertIn(f"/analisar?ticker={ticker}", html)

        # 4. Três Pilares de Valor
        self.assertTrue(
            "auditoria" in html.lower() or "balanços" in html.lower(),
            "Pilar de Auditoria não encontrado"
        )
        self.assertTrue(
            "valuation" in html.lower(),
            "Pilar de Valuation não encontrado"
        )
        self.assertTrue(
            "fluxo" in html.lower(),
            "Pilar de Fluxo não encontrado"
        )

        # 5. CTAs de Conversão
        self.assertIn("/cadastro", html)
        self.assertIn("/login", html)

    def test_api_autocomplete_endpoint(self):
        response = self.client.get("/api/buscar-ativos?q=vale")
        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertIn("resultados", dados)
        self.assertIsInstance(dados["resultados"], list)

    def test_auth_pages_render(self):
        resp_login = self.client.get("/login")
        self.assertEqual(resp_login.status_code, 200)
        self.assertIn("Entrar na Plataforma", resp_login.text)

        resp_cadastro = self.client.get("/cadastro")
        self.assertEqual(resp_cadastro.status_code, 200)
        self.assertIn("Criar Conta Segura", resp_cadastro.text)

    def test_termos_page_renders(self):
        resp = self.client.get("/termos")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Termos de Uso", resp.text)

if __name__ == "__main__":
    unittest.main()
