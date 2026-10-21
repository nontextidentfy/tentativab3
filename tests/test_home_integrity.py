import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

import unittest
from fastapi.testclient import TestClient

import banco
import main


class TestHomeIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        banco.Base.metadata.create_all(bind=banco.engine)
        cls.client = TestClient(main.app)

    def test_home_page_status_and_content(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text

        # 1. Headline Anti-Guru e Proposta de Valor
        self.assertIn("Viu um guru prometendo renda fácil na Bolsa", html)
        self.assertIn("Chega de planilhas confusas", html)

        # 2. Hero Search Bar e Autocomplete
        self.assertIn('id="campo-busca"', html)
        self.assertIn('id="sugestoes-busca"', html)
        self.assertIn('action="/analisar"', html)

        # 3. Badges de Atalhos Rápidos (PETR4, VALE3, ITUB4, MXRF11 e Marcas)
        for ticker in ["PETR4", "VALE3", "ITUB4", "MXRF11"]:
            self.assertIn(f"/analisar?ticker={ticker}", html)
        self.assertIn("/analisar?ticker=Magalu", html)
        self.assertIn("Posto+Ipiranga", html)
        self.assertIn("/analisar?ticker=Sicredi", html)

        # 4. Três Pilares de Valor
        self.assertIn("balanços", html.lower())
        self.assertIn("valuation", html.lower())
        self.assertIn("volume", html.lower())

        # 5. CTAs de Conversão
        self.assertIn("/cadastro", html)
        self.assertIn("/login", html)

    def test_marcas_cotidiano_e_cooperativas_resolucao(self):
        # 1. Resolução direta de marcas
        resp_magalu = self.client.get("/analisar?ticker=Magalu")
        self.assertEqual(resp_magalu.status_code, 200)

        resp_ipiranga = self.client.get("/analisar?ticker=Posto+Ipiranga")
        self.assertEqual(resp_ipiranga.status_code, 200)

        # 2. Resolução didática de cooperativas (Sicredi)
        resp_sicredi = self.client.get("/analisar?ticker=Sicredi")
        self.assertEqual(resp_sicredi.status_code, 200)
        html_sicredi = resp_sicredi.text
        self.assertIn("Cooperativa de Crédito", html_sicredi)
        self.assertIn("BBAS3", html_sicredi)
        self.assertIn("ITUB4", html_sicredi)
        self.assertIn("BBDC4", html_sicredi)

        # 3. Autocomplete de marcas e cooperativas
        resp_auto_magalu = self.client.get("/api/buscar-ativos?q=magalu")
        self.assertEqual(resp_auto_magalu.status_code, 200)
        dados_magalu = resp_auto_magalu.json()
        self.assertTrue(any(r["ticker"] == "MGLU3" for r in dados_magalu["resultados"]))

        resp_auto_sicredi = self.client.get("/api/buscar-ativos?q=sicredi")
        self.assertEqual(resp_auto_sicredi.status_code, 200)
        dados_sicredi = resp_auto_sicredi.json()
        self.assertTrue(any("Sicredi" in r["ticker"] for r in dados_sicredi["resultados"]))
        self.assertTrue(any(r.get("tipo") == "cooperativa" for r in dados_sicredi["resultados"]))

    def test_api_autocomplete_endpoint(self):
        response = self.client.get("/api/buscar-ativos?q=vale")
        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertIn("resultados", dados)
        self.assertIsInstance(dados["resultados"], list)
        self.assertGreaterEqual(len(dados["resultados"]), 1)
        self.assertEqual(dados["resultados"][0]["ticker"], "VALE3")

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
