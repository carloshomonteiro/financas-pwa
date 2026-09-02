"""Testes da rotina de verificacao (buscador e Telegram falsos)."""

import tempfile
import unittest
from pathlib import Path

from assistente.checker import verificar
from assistente.scraper import ErroDeBusca, Resultado
from assistente.storage import Storage

from tests.test_commands import ClienteFalso, config_de_teste


class BuscadorFalso:
    """Devolve precos programados por URL, sem tocar na rede."""

    def __init__(self, precos, titulo=None):
        self.precos = precos
        self.titulo = titulo
        self.chamadas = []

    def buscar_preco(self, url, css_selector=None):
        self.chamadas.append(url)
        valor = self.precos[url]
        if isinstance(valor, Exception):
            raise valor
        return Resultado(valor, "teste", self.titulo)


class TestVerificar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "w.db")
        self.config = config_de_teste()
        self.cliente = ClienteFalso()

    def tearDown(self):
        self.storage.fechar()
        self.tmp.cleanup()

    def _rodar(self, buscador, **extra):
        return verificar(self.storage, self.config, buscador=buscador,
                         cliente=self.cliente, **extra)

    def test_alerta_quando_preco_bate_o_alvo(self):
        self.storage.adicionar("Fone", "https://loja.com/fone", 300.0)
        resumo = self._rodar(BuscadorFalso({"https://loja.com/fone": 289.9}))
        self.assertEqual((resumo.verificados, resumo.alertas), (1, 1))
        chat, texto = self.cliente.enviadas[0]
        self.assertEqual(chat, "123")
        self.assertIn("PREÇO ALVO ATINGIDO", texto)
        self.assertIn("R$ 289,90", texto)
        self.assertIn("Menor preço já visto", texto)

    def test_sem_alerta_acima_do_alvo_mas_grava_historico(self):
        p = self.storage.adicionar("Fone", "https://loja.com/fone", 200.0)
        resumo = self._rodar(BuscadorFalso({"https://loja.com/fone": 289.9}))
        self.assertEqual((resumo.verificados, resumo.alertas), (1, 0))
        self.assertEqual(self.cliente.enviadas, [])
        self.assertEqual(self.storage.obter(p.id).ultimo_preco, 289.9)

    def test_nao_repete_alerta_na_rodada_seguinte(self):
        self.storage.adicionar("Fone", "https://loja.com/fone", 300.0)
        buscador = BuscadorFalso({"https://loja.com/fone": 289.9})
        self._rodar(buscador)
        segundo = self._rodar(buscador)
        self.assertEqual(segundo.alertas, 0)
        self.assertEqual(len(self.cliente.enviadas), 1)

    def test_alerta_de_novo_quando_o_preco_cai_mais(self):
        self.storage.adicionar("Fone", "https://loja.com/fone", 300.0)
        self._rodar(BuscadorFalso({"https://loja.com/fone": 289.9}))
        resumo = self._rodar(BuscadorFalso({"https://loja.com/fone": 249.0}))
        self.assertEqual(resumo.alertas, 1)
        self.assertIn("-14%", self.cliente.enviadas[1][1])  # queda desde a leitura anterior

    def test_forcar_reenvia_dentro_da_janela(self):
        self.storage.adicionar("Fone", "https://loja.com/fone", 300.0)
        buscador = BuscadorFalso({"https://loja.com/fone": 289.9})
        self._rodar(buscador)
        resumo = self._rodar(buscador, forcar_alerta=True)
        self.assertEqual(resumo.alertas, 1)

    def test_pausado_fica_de_fora(self):
        self.storage.adicionar("Fone", "https://loja.com/fone", 300.0)
        self.storage.definir_status(1, "pausado")
        buscador = BuscadorFalso({"https://loja.com/fone": 1.0})
        self.assertEqual(self._rodar(buscador).verificados, 0)
        self.assertEqual(buscador.chamadas, [])

    def test_falha_de_um_nao_derruba_os_outros(self):
        self.storage.adicionar("Fone", "https://loja.com/fone", 300.0)
        self.storage.adicionar("Mouse", "https://loja.com/mouse", 100.0)
        resumo = self._rodar(BuscadorFalso({
            "https://loja.com/fone": ErroDeBusca("HTTP 403"),
            "https://loja.com/mouse": 89.0,
        }))
        self.assertEqual((resumo.verificados, resumo.alertas), (1, 1))
        self.assertEqual(len(resumo.falhas), 1)
        self.assertEqual(self.storage.obter(1).ultimo_erro, "HTTP 403")

    def test_nome_vem_do_titulo_quando_so_a_url_foi_cadastrada(self):
        url = "https://loja.com/fone"
        self.storage.adicionar(url, url, 300.0)
        self._rodar(BuscadorFalso({url: 289.9}, titulo="Fone XYZ Bluetooth"))
        self.assertEqual(self.storage.obter(1).nome, "Fone XYZ Bluetooth")

    def test_sem_telegram_configurado_nao_quebra(self):
        # sem token/chat o alerta so vai para o log, e a verificacao segue
        sem_telegram = config_de_teste(telegram_token="", telegram_chat_id="",
                                       chats_autorizados=frozenset())
        self.storage.adicionar("Fone", "https://loja.com/fone", 300.0)
        resumo = verificar(self.storage, sem_telegram,
                           buscador=BuscadorFalso({"https://loja.com/fone": 289.9}),
                           cliente=None)
        self.assertEqual((resumo.verificados, resumo.alertas), (1, 0))
        self.assertEqual(resumo.falhas, [])


if __name__ == "__main__":
    unittest.main()
