import unittest
import os
import re

class TestVisualConsistency(unittest.TestCase):
    def setUp(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.base_html_path = os.path.join(base_dir, "templates", "base.html")
        self.home_html_path = os.path.join(base_dir, "templates", "home.html")
        
        with open(self.base_html_path, "r", encoding="utf-8") as f:
            self.base_html = f.read()
        with open(self.home_html_path, "r", encoding="utf-8") as f:
            self.home_html = f.read()

    def test_dark_mode_tokens(self):
        """Verifica se os tokens CSS do dark mode Fintech Premium estão declarados."""
        required_tokens = [
            "--bg", "--card", "--borda", "--destaque", "--pos", "--neg", "--suave"
        ]
        for token in required_tokens:
            self.assertIn(token, self.base_html, f"Token {token} ausente em base.html")

    def test_viewport_meta(self):
        """Garante que a meta tag de viewport para mobile está configurada."""
        self.assertIn('name="viewport"', self.base_html)
        self.assertIn('width=device-width', self.base_html)

    def test_responsive_media_queries(self):
        """Garante que existem regras @media para responsividade."""
        self.assertIn("@media", self.base_html)

    def test_tabular_numbers_or_financial_typography(self):
        """Verifica se há regra de tabular-nums ou estilização financeira."""
        self.assertTrue(
            "tabular-nums" in self.base_html or "font-feature-settings" in self.base_html,
            "Regra para alinhamento numérico financeiro (tabular-nums) ausente"
        )

    def test_semantic_status_classes(self):
        """Verifica se as classes semânticas .pos e .neg estão presentes."""
        self.assertIn(".pos", self.base_html)
        self.assertIn(".neg", self.base_html)

if __name__ == "__main__":
    unittest.main()
