"""Comandos do bot: gerencie a lista de desejos direto pelo Telegram.

Nao usa webhook - le as mensagens por getUpdates (long polling). Assim da
para rodar tanto como servico (`--bot`) quanto de tempos em tempos pelo
cron (`--comandos`), sem expor porta nenhuma na sua rede.
"""

from __future__ import annotations

import logging
import shlex
from typing import Callable

from .config import Config
from .notifier import ClienteTelegram, ErroTelegram, escapar, moeda
from .scraper import parse_preco
from .storage import STATUS_ATIVO, STATUS_PAUSADO, Storage

log = logging.getLogger(__name__)

CHAVE_OFFSET = "telegram_offset"

AJUDA = """🛒 <b>Assistente de compras</b>

<b>/adicionar</b> &lt;url&gt; &lt;preço alvo&gt; [nome]
   ex: <code>/adicionar https://loja.com/fone 299,90 Fone XYZ</code>
<b>/listar</b> — mostra a lista com preço atual e alvo
<b>/remover</b> &lt;id&gt; — tira o item da lista
<b>/pausar</b> &lt;id&gt; · <b>/retomar</b> &lt;id&gt;
<b>/alvo</b> &lt;id&gt; &lt;preço&gt; — muda o preço desejado
<b>/seletor</b> &lt;id&gt; &lt;css|auto&gt; — quando o preço vier errado
<b>/checar</b> [id] — verifica agora, sem esperar o horário
<b>/status</b> — resumo da lista
<b>/ajuda</b> — esta mensagem"""


class Comandos:
    """Traduz o texto recebido no Telegram em operacoes na lista."""

    def __init__(
        self,
        storage: Storage,
        config: Config,
        executar_check: Callable[[list | None], str] | None = None,
    ) -> None:
        self.storage = storage
        self.config = config
        # injetado pelo main para evitar import circular com o checker
        self.executar_check = executar_check

    # ------------------------------------------------------------ utilidades
    def _produto_por_argumento(self, argumento: str):
        if not argumento.isdigit():
            return None, "Informe o <b>id</b> numérico (veja em /listar)."
        produto = self.storage.obter(int(argumento))
        if produto is None:
            return None, f"Não achei o item #{escapar(argumento)}."
        return produto, None

    # -------------------------------------------------------------- comandos
    def adicionar(self, argumentos: list[str]) -> str:
        if len(argumentos) < 2:
            return "Uso: <code>/adicionar &lt;url&gt; &lt;preço alvo&gt; [nome]</code>"
        url, bruto_preco, *resto = argumentos
        preco = parse_preco(bruto_preco)
        if preco is None:
            return f"Não entendi o preço {escapar(bruto_preco)}. Ex: 299,90"
        nome = " ".join(resto).strip()
        try:
            produto = self.storage.adicionar(nome or url, url, preco)
        except ValueError as erro:
            return f"❌ {escapar(str(erro))}"
        return (
            f"✅ Item <b>#{produto.id}</b> adicionado.\n"
            f"📦 {escapar(produto.nome)}\n"
            f"🎯 Alvo: {moeda(produto.preco_alvo)}\n"
            f"<i>Use /checar {produto.id} para testar a leitura do preço agora.</i>"
        )

    def listar(self) -> str:
        produtos = self.storage.listar()
        if not produtos:
            return "Sua lista está vazia. Use /adicionar para começar."
        linhas = ["🛒 <b>Sua lista</b>", ""]
        for p in produtos:
            marca = "🟢" if p.ativo else "⏸"
            atual = moeda(p.ultimo_preco) if p.ultimo_preco else "sem leitura"
            if p.ultimo_preco and p.ultimo_preco <= p.preco_alvo:
                atual = f"{atual} 🔥"
            linhas.append(
                f"{marca} <b>#{p.id}</b> {escapar(p.nome)}\n"
                f"    atual: {atual} · alvo: {moeda(p.preco_alvo)}"
            )
            if p.ultimo_erro:
                linhas.append(f"    ⚠️ {escapar(p.ultimo_erro[:90])}")
        return "\n".join(linhas)

    def remover(self, argumentos: list[str]) -> str:
        if not argumentos:
            return "Uso: <code>/remover &lt;id&gt;</code>"
        produto, erro = self._produto_por_argumento(argumentos[0])
        if erro:
            return erro
        self.storage.remover(produto.id)
        return f"🗑 <b>#{produto.id}</b> {escapar(produto.nome)} removido."

    def mudar_status(self, argumentos: list[str], status: str) -> str:
        if not argumentos:
            return f"Uso: <code>/{'pausar' if status == STATUS_PAUSADO else 'retomar'} &lt;id&gt;</code>"
        produto, erro = self._produto_por_argumento(argumentos[0])
        if erro:
            return erro
        self.storage.definir_status(produto.id, status)
        icone = "⏸" if status == STATUS_PAUSADO else "🟢"
        rotulo = "pausado" if status == STATUS_PAUSADO else "reativado"
        return f"{icone} <b>#{produto.id}</b> {escapar(produto.nome)} {rotulo}."

    def alvo(self, argumentos: list[str]) -> str:
        if len(argumentos) < 2:
            return "Uso: <code>/alvo &lt;id&gt; &lt;preço&gt;</code>"
        produto, erro = self._produto_por_argumento(argumentos[0])
        if erro:
            return erro
        preco = parse_preco(argumentos[1])
        if preco is None:
            return f"Não entendi o preço {escapar(argumentos[1])}."
        self.storage.definir_alvo(produto.id, preco)
        return f"🎯 Novo alvo de <b>#{produto.id}</b>: {moeda(preco)}"

    def seletor(self, argumentos: list[str]) -> str:
        if len(argumentos) < 2:
            return (
                "Uso: <code>/seletor &lt;id&gt; &lt;css&gt;</code>\n"
                "Ex: <code>/seletor 3 span.price-value</code> · "
                "<code>/seletor 3 auto</code> volta ao automático."
            )
        produto, erro = self._produto_por_argumento(argumentos[0])
        if erro:
            return erro
        css = " ".join(argumentos[1:]).strip()
        if css.lower() in ("auto", "automatico", "automático", "limpar"):
            self.storage.definir_seletor(produto.id, None)
            return f"🔄 <b>#{produto.id}</b> voltou à detecção automática."
        self.storage.definir_seletor(produto.id, css)
        return (
            f"🎯 Seletor de <b>#{produto.id}</b>: <code>{escapar(css)}</code>\n"
            f"<i>Rode /checar {produto.id} para conferir.</i>"
        )

    def checar(self, argumentos: list[str]) -> str:
        if self.executar_check is None:  # pragma: no cover - so em uso parcial
            return "Verificação sob demanda indisponível neste modo."
        alvos = None
        if argumentos:
            produto, erro = self._produto_por_argumento(argumentos[0])
            if erro:
                return erro
            alvos = [produto]
        return self.executar_check(alvos)

    def status(self) -> str:
        produtos = self.storage.listar()
        ativos = [p for p in produtos if p.ativo]
        no_alvo = [p for p in ativos if p.ultimo_preco and p.ultimo_preco <= p.preco_alvo]
        com_erro = [p for p in ativos if p.ultimo_erro]
        ultima = max((p.ultima_verificacao or "" for p in produtos), default="")
        return (
            f"📊 <b>Status</b>\n"
            f"Itens: {len(produtos)} ({len(ativos)} ativos)\n"
            f"No alvo agora: {len(no_alvo)}\n"
            f"Com erro na última leitura: {len(com_erro)}\n"
            f"Última verificação: {escapar(ultima[:19].replace('T', ' ')) or '—'}"
        )

    # ------------------------------------------------------------ despachante
    def responder(self, texto: str) -> str:
        """Recebe o texto cru da mensagem e devolve a resposta em HTML."""
        texto = (texto or "").strip()
        if not texto.startswith("/"):
            return "Use /ajuda para ver os comandos."
        try:
            partes = shlex.split(texto)
        except ValueError:
            partes = texto.split()
        comando = partes[0].lower().split("@")[0]  # /listar@MeuBot -> /listar
        argumentos = partes[1:]

        despacho = {
            "/adicionar": lambda: self.adicionar(argumentos),
            "/add": lambda: self.adicionar(argumentos),
            "/listar": self.listar,
            "/lista": self.listar,
            "/remover": lambda: self.remover(argumentos),
            "/pausar": lambda: self.mudar_status(argumentos, STATUS_PAUSADO),
            "/retomar": lambda: self.mudar_status(argumentos, STATUS_ATIVO),
            "/alvo": lambda: self.alvo(argumentos),
            "/seletor": lambda: self.seletor(argumentos),
            "/checar": lambda: self.checar(argumentos),
            "/status": self.status,
            "/ajuda": lambda: AJUDA,
            "/help": lambda: AJUDA,
            "/start": lambda: AJUDA,
        }
        acao = despacho.get(comando)
        if acao is None:
            return f"Não conheço {escapar(comando)}. Use /ajuda."
        try:
            return acao()
        except Exception as erro:  # rede/banco: nunca derruba o loop do bot
            log.exception("Erro ao executar %s", comando)
            return f"❌ Erro ao executar {escapar(comando)}: {escapar(str(erro))}"


def processar_updates(
    cliente: ClienteTelegram,
    comandos: Comandos,
    storage: Storage,
    config: Config,
    espera: int = 0,
) -> int:
    """Le as mensagens pendentes, responde e devolve quantas foram tratadas.

    O offset do Telegram fica salvo no banco: mensagens ja respondidas nao
    voltam, mesmo que o processo reinicie.
    """
    salvo = storage.obter_estado(CHAVE_OFFSET)
    offset = int(salvo) + 1 if salvo else None

    updates = cliente.obter_updates(offset=offset, timeout=espera)
    tratadas = 0
    for update in updates:
        storage.definir_estado(CHAVE_OFFSET, str(update["update_id"]))
        mensagem = update.get("message") or {}
        texto = mensagem.get("text")
        chat_id = str((mensagem.get("chat") or {}).get("id", ""))
        if not texto or not chat_id:
            continue

        # Lista vazia nao libera geral: sem TELEGRAM_CHAT_ID ninguem comanda o
        # bot. A resposta abaixo e justamente como voce descobre o seu id.
        if chat_id not in config.chats_autorizados:
            log.warning("Mensagem de chat nao autorizado (%s) ignorada.", chat_id)
            try:
                cliente.enviar_mensagem(
                    chat_id,
                    "🔒 Este bot é de uso pessoal.\nSeu chat id é "
                    f"<code>{escapar(chat_id)}</code> — coloque em "
                    "<code>TELEGRAM_CHAT_ID</code> no arquivo .env para liberar.",
                )
            except ErroTelegram:
                pass
            continue

        resposta = comandos.responder(texto)
        try:
            cliente.enviar_mensagem(chat_id, resposta)
            tratadas += 1
        except ErroTelegram as erro:  # pragma: no cover - depende da rede
            log.error("Falha ao responder %s: %s", chat_id, erro)

    return tratadas
