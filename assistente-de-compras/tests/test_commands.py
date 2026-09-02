"""Testes dos comandos do Telegram (sem rede: cliente falso)."""

import tempfile
import unittest
from pathlib import Path

from assistente.commands import Comandos, processar_updates
from assistente.config import Config
from assistente.storage import Storage


def config_de_teste(**ajustes) -> Config:
    base = dict(
        telegram_token="token-falso",
        telegram_chat_id="123",
        chats_autorizados=frozenset({"123"}),
        caminho_banco=Path("/tmp/nao-usado.db"),
        timeout_requisicao=5.0,
        tentativas=1,
        espera_min=0.0,
        espera_max=0.0,
        respeitar_robots=False,
        horas_entre_alertas=24.0,
        notificar_erros=False,
        user_agent="teste",
        usar_playwright=False,
        nivel_log="CRITICAL",
    )
    base.update(ajustes)
    return Config(**base)


class ClienteFalso:
    """Registra o que seria enviado e devolve updates programados."""

    def __init__(self, updates=None):
        self.updates = updates or []
        self.enviadas = []

    def enviar_mensagem(self, chat_id, texto, preview=False):
        self.enviadas.append((str(chat_id), texto))

    def obter_updates(self, offset=None, timeout=0):
        self.offset_pedido = offset
        return self.updates


class TestComandos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "w.db")
        self.config = config_de_teste()
        self.checados = []
        self.cmd = Comandos(
            self.storage, self.config,
            executar_check=lambda produtos=None: self.checados.append(produtos) or "ok",
        )

    def tearDown(self):
        self.storage.fechar()
        self.tmp.cleanup()

    def test_adicionar_com_preco_brasileiro_e_nome(self):
        resposta = self.cmd.responder('/adicionar https://loja.com/fone 1.299,90 "Fone XYZ"')
        self.assertIn("#1", resposta)
        produto = self.storage.obter(1)
        self.assertEqual(produto.preco_alvo, 1299.90)
        self.assertEqual(produto.nome, "Fone XYZ")

    def test_adicionar_sem_argumentos_explica_o_uso(self):
        self.assertIn("Uso:", self.cmd.responder("/adicionar"))

    def test_adicionar_url_repetida(self):
        self.cmd.responder("/adicionar https://loja.com/fone 100")
        self.assertIn("já está na lista", self.cmd.responder("/adicionar https://loja.com/fone 90"))

    def test_listar_vazio_e_com_itens(self):
        self.assertIn("vazia", self.cmd.responder("/listar"))
        self.cmd.responder("/adicionar https://loja.com/fone 100 Fone")
        self.storage.registrar_preco(1, 89.90)
        resposta = self.cmd.responder("/listar")
        self.assertIn("Fone", resposta)
        self.assertIn("R$ 89,90", resposta)
        self.assertIn("🔥", resposta)  # abaixo do alvo

    def test_remover(self):
        self.cmd.responder("/adicionar https://loja.com/fone 100 Fone")
        self.assertIn("removido", self.cmd.responder("/remover 1"))
        self.assertIsNone(self.storage.obter(1))
        self.assertIn("Não achei", self.cmd.responder("/remover 1"))
        self.assertIn("id", self.cmd.responder("/remover abc"))

    def test_pausar_e_retomar(self):
        self.cmd.responder("/adicionar https://loja.com/fone 100 Fone")
        self.cmd.responder("/pausar 1")
        self.assertFalse(self.storage.obter(1).ativo)
        self.cmd.responder("/retomar 1")
        self.assertTrue(self.storage.obter(1).ativo)

    def test_alvo_e_seletor(self):
        self.cmd.responder("/adicionar https://loja.com/fone 100 Fone")
        self.cmd.responder("/alvo 1 79,90")
        self.assertEqual(self.storage.obter(1).preco_alvo, 79.90)
        self.cmd.responder("/seletor 1 span.price-value")
        self.assertEqual(self.storage.obter(1).css_selector, "span.price-value")
        self.cmd.responder("/seletor 1 auto")
        self.assertIsNone(self.storage.obter(1).css_selector)

    def test_checar_um_item_e_a_lista_toda(self):
        self.cmd.responder("/adicionar https://loja.com/fone 100 Fone")
        self.cmd.responder("/checar")
        self.cmd.responder("/checar 1")
        self.assertIsNone(self.checados[0])
        self.assertEqual(self.checados[1][0].id, 1)

    def test_comando_com_mencao_ao_bot(self):
        self.assertIn("Sua lista", self.cmd.responder("/listar@MeuBot"))

    def test_desconhecido_e_texto_solto(self):
        self.assertIn("Não conheço", self.cmd.responder("/foo"))
        self.assertIn("/ajuda", self.cmd.responder("oi"))


class TestProcessarUpdates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "w.db")
        self.config = config_de_teste()
        self.cmd = Comandos(self.storage, self.config, lambda produtos=None: "ok")

    def tearDown(self):
        self.storage.fechar()
        self.tmp.cleanup()

    @staticmethod
    def _update(update_id, chat_id, texto):
        return {"update_id": update_id,
                "message": {"text": texto, "chat": {"id": chat_id}}}

    def test_responde_e_guarda_o_offset(self):
        cliente = ClienteFalso([self._update(10, 123, "/listar")])
        tratadas = processar_updates(cliente, self.cmd, self.storage, self.config)
        self.assertEqual(tratadas, 1)
        self.assertEqual(self.storage.obter_estado("telegram_offset"), "10")
        # a proxima leitura pede a partir do update seguinte
        cliente.updates = []
        processar_updates(cliente, self.cmd, self.storage, self.config)
        self.assertEqual(cliente.offset_pedido, 11)

    def test_chat_nao_autorizado_nao_altera_a_lista(self):
        cliente = ClienteFalso([self._update(1, 999, "/adicionar https://x.com/a 10")])
        tratadas = processar_updates(cliente, self.cmd, self.storage, self.config)
        self.assertEqual(tratadas, 0)
        self.assertEqual(self.storage.listar(), [])
        self.assertIn("uso pessoal", cliente.enviadas[0][1])

    def test_sem_chat_configurado_ninguem_comanda(self):
        config = config_de_teste(telegram_chat_id="", chats_autorizados=frozenset())
        cliente = ClienteFalso([self._update(1, 123, "/adicionar https://x.com/a 10")])
        tratadas = processar_updates(cliente, self.cmd, self.storage, config)
        self.assertEqual(tratadas, 0)
        self.assertEqual(self.storage.listar(), [])
        self.assertIn("Seu chat id", cliente.enviadas[0][1])

    def test_update_sem_texto_e_ignorado(self):
        cliente = ClienteFalso([{"update_id": 5, "message": {"chat": {"id": 123}}}])
        self.assertEqual(processar_updates(cliente, self.cmd, self.storage, self.config), 0)


if __name__ == "__main__":
    unittest.main()
