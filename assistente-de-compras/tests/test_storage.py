"""Testes da persistencia (banco temporario em disco)."""

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from assistente.storage import STATUS_PAUSADO, Storage, agora


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "wishlist.db")

    def tearDown(self):
        self.storage.fechar()
        self.tmp.cleanup()

    def test_adicionar_e_listar(self):
        p = self.storage.adicionar("Fone", "https://loja.com/fone", 299.90)
        self.assertEqual(p.id, 1)
        self.assertTrue(p.ativo)
        self.assertEqual([x.url for x in self.storage.listar()], ["https://loja.com/fone"])

    def test_url_duplicada_e_recusada(self):
        self.storage.adicionar("Fone", "https://loja.com/fone", 299.90)
        with self.assertRaises(ValueError):
            self.storage.adicionar("Outro", "https://loja.com/fone", 199.0)

    def test_validacoes(self):
        with self.assertRaises(ValueError):
            self.storage.adicionar("X", "loja.com/sem-esquema", 10.0)
        with self.assertRaises(ValueError):
            self.storage.adicionar("X", "https://loja.com/x", 0)

    def test_pausar_sai_da_lista_de_ativos(self):
        p = self.storage.adicionar("Fone", "https://loja.com/fone", 299.90)
        self.storage.definir_status(p.id, STATUS_PAUSADO)
        self.assertEqual(self.storage.listar(apenas_ativos=True), [])
        self.assertEqual(len(self.storage.listar()), 1)

    def test_registrar_preco_atualiza_ultimo_e_menor(self):
        p = self.storage.adicionar("Fone", "https://loja.com/fone", 299.90)
        self.storage.registrar_preco(p.id, 350.0)
        self.storage.registrar_preco(p.id, 310.0)
        self.storage.registrar_preco(p.id, 320.0)
        atual = self.storage.obter(p.id)
        self.assertEqual(atual.ultimo_preco, 320.0)
        self.assertEqual(atual.menor_preco, 310.0)
        self.assertEqual(len(self.storage.historico(p.id)), 3)

    def test_erro_e_limpo_na_leitura_seguinte(self):
        p = self.storage.adicionar("Fone", "https://loja.com/fone", 299.90)
        self.storage.registrar_erro(p.id, "HTTP 403")
        self.assertEqual(self.storage.obter(p.id).ultimo_erro, "HTTP 403")
        self.storage.registrar_preco(p.id, 280.0)
        self.assertIsNone(self.storage.obter(p.id).ultimo_erro)

    def test_remover(self):
        p = self.storage.adicionar("Fone", "https://loja.com/fone", 299.90)
        self.assertTrue(self.storage.remover(p.id))
        self.assertFalse(self.storage.remover(p.id))
        self.assertEqual(self.storage.historico(p.id), [])

    def test_estado_sobrevive_a_reabertura(self):
        self.storage.definir_estado("telegram_offset", "42")
        self.storage.definir_estado("telegram_offset", "43")
        self.storage.fechar()
        self.storage = Storage(Path(self.tmp.name) / "wishlist.db")
        self.assertEqual(self.storage.obter_estado("telegram_offset"), "43")

    def test_importar_ignora_duplicados_e_invalidos(self):
        total = self.storage.importar([
            {"nome": "A", "url": "https://loja.com/a", "preco_alvo": 10},
            {"nome": "A de novo", "url": "https://loja.com/a", "preco_alvo": 9},
            {"nome": "sem url"},
        ])
        self.assertEqual(total, 1)


class TestRegraDeAlerta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "wishlist.db")
        self.p = self.storage.adicionar("Fone", "https://loja.com/fone", 300.0)

    def tearDown(self):
        self.storage.fechar()
        self.tmp.cleanup()

    def _atual(self):
        return self.storage.obter(self.p.id)

    def test_nao_alerta_acima_do_alvo(self):
        self.assertFalse(self.storage.deve_alertar(self._atual(), 350.0, 24))

    def test_alerta_no_primeiro_toque_do_alvo(self):
        self.assertTrue(self.storage.deve_alertar(self._atual(), 300.0, 24))

    def test_nao_repete_dentro_da_janela_de_silencio(self):
        self.storage.registrar_alerta(self.p.id, 290.0)
        self.assertFalse(self.storage.deve_alertar(self._atual(), 290.0, 24))

    def test_repete_quando_o_preco_cai_mais(self):
        self.storage.registrar_alerta(self.p.id, 290.0)
        self.assertTrue(self.storage.deve_alertar(self._atual(), 279.0, 24))

    def test_repete_depois_da_janela(self):
        self.storage.registrar_alerta(self.p.id, 290.0)
        antigo = (agora() - timedelta(hours=30)).isoformat(timespec="seconds")
        self.storage.conexao.execute(
            "UPDATE produtos SET alerta_em = ? WHERE id = ?", (antigo, self.p.id))
        self.storage.conexao.commit()
        self.assertTrue(self.storage.deve_alertar(self._atual(), 290.0, 24))


if __name__ == "__main__":
    unittest.main()
