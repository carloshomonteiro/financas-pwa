"""Rotina de verificacao: consulta os precos e dispara os alertas.

E o coracao do modo autonomo (`python main.py --check`) e tambem o que o
comando /checar do bot executa sob demanda.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Config
from .notifier import Alerta, ClienteTelegram, ErroTelegram, moeda, montar_alerta
from .scraper import Buscador, ErroDeBusca, PrecoNaoEncontrado, Resultado
from .storage import Produto, Storage

log = logging.getLogger(__name__)


@dataclass
class Resumo:
    """O que aconteceu em uma rodada de verificacao."""

    verificados: int = 0
    alertas: int = 0
    falhas: list[tuple[str, str]] = field(default_factory=list)
    linhas: list[str] = field(default_factory=list)

    def texto(self) -> str:
        partes = [f"{self.verificados} produto(s) verificado(s)",
                  f"{self.alertas} alerta(s) enviado(s)"]
        if self.falhas:
            partes.append(f"{len(self.falhas)} falha(s)")
        cabecalho = " · ".join(partes)
        return "\n".join([cabecalho, *self.linhas]) if self.linhas else cabecalho


def _nome_melhor(produto: Produto, resultado: Resultado) -> str | None:
    """Se o produto foi cadastrado so com a URL, adota o titulo da pagina."""
    if resultado.titulo and produto.nome.strip() in ("", produto.url):
        return resultado.titulo
    return None


def verificar(
    storage: Storage,
    config: Config,
    buscador: Buscador | None = None,
    cliente: ClienteTelegram | None = None,
    produtos: list[Produto] | None = None,
    forcar_alerta: bool = False,
) -> Resumo:
    """Verifica os produtos ativos e envia alerta para quem bateu o alvo.

    `buscador` e `cliente` sao injetaveis para facilitar os testes; em uso
    normal sao criados a partir da configuracao.
    """
    buscador = buscador or Buscador(config)
    if cliente is None and config.telegram_configurado:
        cliente = ClienteTelegram(config.telegram_token, config.timeout_requisicao)

    itens = produtos if produtos is not None else storage.listar(apenas_ativos=True)
    resumo = Resumo()

    for produto in itens:
        log.info("Verificando #%s %s", produto.id, produto.nome)
        try:
            resultado = buscador.buscar_preco(produto.url, produto.css_selector)
        except (ErroDeBusca, PrecoNaoEncontrado) as erro:
            log.warning("Falha em #%s: %s", produto.id, erro)
            storage.registrar_erro(produto.id, str(erro))
            resumo.falhas.append((produto.nome, str(erro)))
            continue

        anterior = produto.ultimo_preco
        storage.registrar_preco(produto.id, resultado.preco)
        novo_nome = _nome_melhor(produto, resultado)
        if novo_nome:
            storage.definir_nome(produto.id, novo_nome)
        resumo.verificados += 1

        variacao = ""
        if anterior and abs(anterior - resultado.preco) > 0.009:
            seta = "🔻" if resultado.preco < anterior else "🔺"
            variacao = f" {seta} (era {moeda(anterior)})"
        resumo.linhas.append(
            f"#{produto.id} {novo_nome or produto.nome}: "
            f"{moeda(resultado.preco)}{variacao} "
            f"[alvo {moeda(produto.preco_alvo)}]"
        )
        log.info(
            "#%s preco %s via %s (alvo %s)",
            produto.id, resultado.preco, resultado.estrategia, produto.preco_alvo,
        )

        deve = forcar_alerta and resultado.preco <= produto.preco_alvo
        deve = deve or storage.deve_alertar(
            produto, resultado.preco, config.horas_entre_alertas
        )
        if not deve:
            continue

        # menor_preco ja considera a leitura recem-gravada
        atualizado = storage.obter(produto.id) or produto
        mensagem = montar_alerta(
            Alerta(
                nome=novo_nome or produto.nome,
                url=produto.url,
                preco=resultado.preco,
                preco_alvo=produto.preco_alvo,
                preco_anterior=anterior,
                menor_preco=atualizado.menor_preco,
            )
        )
        if cliente is None:
            log.warning("Telegram nao configurado; alerta so no log:\n%s", mensagem)
            continue
        try:
            cliente.enviar_mensagem(config.telegram_chat_id, mensagem, preview=True)
            storage.registrar_alerta(produto.id, resultado.preco)
            resumo.alertas += 1
        except ErroTelegram as erro:
            log.error("Nao consegui enviar o alerta de #%s: %s", produto.id, erro)
            resumo.falhas.append((produto.nome, f"Telegram: {erro}"))

    if resumo.falhas and config.notificar_erros and cliente is not None:
        detalhes = "\n".join(f"• {nome}: {erro}" for nome, erro in resumo.falhas[:10])
        try:
            cliente.enviar_mensagem(
                config.telegram_chat_id,
                f"⚠️ <b>Falhas na verificação</b>\n{detalhes}",
            )
        except ErroTelegram as erro:  # pragma: no cover - so log
            log.error("Falha ao notificar erros: %s", erro)

    return resumo
