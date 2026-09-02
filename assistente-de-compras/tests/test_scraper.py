"""Testes da extracao de precos (sem rede: HTML fixo)."""

import unittest

from assistente.scraper import PrecoNaoEncontrado, extrair_preco, parse_preco


class TestParsePreco(unittest.TestCase):
    def test_formato_brasileiro(self):
        self.assertEqual(parse_preco("R$ 1.299,90"), 1299.90)
        self.assertEqual(parse_preco("299,90"), 299.90)
        self.assertEqual(parse_preco("R$ 12.345.678,99"), 12345678.99)

    def test_formato_americano(self):
        self.assertEqual(parse_preco("1,299.90"), 1299.90)
        self.assertEqual(parse_preco("1299.90"), 1299.90)

    def test_milhar_sem_centavos(self):
        # "1.299" em loja brasileira e mil duzentos e noventa e nove
        self.assertEqual(parse_preco("R$ 1.299"), 1299.0)

    def test_numeros_e_espacos(self):
        self.assertEqual(parse_preco(199.9), 199.9)
        self.assertEqual(parse_preco(" R$\xa0 89,00 "), 89.0)

    def test_invalidos(self):
        for entrada in (None, "", "sem preco", "R$ --", 0, -5):
            self.assertIsNone(parse_preco(entrada), entrada)


HTML_JSON_LD = """
<html><head><title>Fone XYZ | Loja</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Fone XYZ",
 "offers":{"@type":"Offer","price":"289.90","priceCurrency":"BRL"}}
</script></head><body><span class="preco-antigo">R$ 499,00</span></body></html>
"""

HTML_META = """
<html><head><meta property="og:title" content="Monitor 27">
<meta property="product:price:amount" content="1799.00"></head>
<body><div>preco sob consulta</div></body></html>
"""

HTML_HEURISTICA = """
<html><head><title>Cafeteira</title></head><body>
  <span class="price-old">De R$ 899,00</span>
  <span class="price-value">R$ 649,90</span>
  <span class="installment-price">12x de R$ 54,15</span>
</body></html>
"""

HTML_SELETOR = """
<html><body><div class="bloco"><b data-x="1">R$ 77,70</b></div>
<script type="application/ld+json">{"@type":"Product","offers":{"price":"999.00"}}</script>
</body></html>
"""


class TestExtrairPreco(unittest.TestCase):
    def test_json_ld_tem_prioridade_sobre_texto_solto(self):
        r = extrair_preco(HTML_JSON_LD)
        self.assertEqual(r.preco, 289.90)
        self.assertTrue(r.estrategia.startswith("json-ld"))
        self.assertEqual(r.titulo, "Fone XYZ | Loja")

    def test_metatag(self):
        r = extrair_preco(HTML_META)
        self.assertEqual(r.preco, 1799.00)
        self.assertTrue(r.estrategia.startswith("meta"))
        self.assertEqual(r.titulo, "Monitor 27")

    def test_heuristica_ignora_preco_antigo_e_parcela(self):
        r = extrair_preco(HTML_HEURISTICA)
        self.assertEqual(r.preco, 649.90)
        self.assertTrue(r.estrategia.startswith("heuristica"))

    def test_seletor_manual_vence_o_json_ld(self):
        r = extrair_preco(HTML_SELETOR, css_selector="div.bloco b")
        self.assertEqual(r.preco, 77.70)
        self.assertTrue(r.estrategia.startswith("seletor"))

    def test_seletor_invalido_cai_nas_demais_estrategias(self):
        r = extrair_preco(HTML_SELETOR, css_selector="span.nao-existe")
        self.assertEqual(r.preco, 999.00)

    def test_pagina_sem_preco(self):
        with self.assertRaises(PrecoNaoEncontrado):
            extrair_preco("<html><body><p>indisponivel</p></body></html>")


if __name__ == "__main__":
    unittest.main()
