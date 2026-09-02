"""Cliente do Telegram (Bot API) e formatacao das mensagens de alerta.

Fala direto com a API HTTP do Telegram via requests: sem dependencia extra e
sem loop assincrono, o que mantem o script facil de rodar no cron.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{metodo}"


class ErroTelegram(Exception):
    """Falha ao falar com a API do Telegram."""


def moeda(valor: float | None) -> str:
    """Formata 1299.9 -> 'R$ 1.299,90' (padrao brasileiro)."""
    if valor is None:
        return "—"
    texto = f"{valor:,.2f}"  # 1,299.90
    texto = texto.replace(",", "#").replace(".", ",").replace("#", ".")
    return f"R$ {texto}"


def escapar(texto: str | None) -> str:
    return html.escape(texto or "", quote=False)


@dataclass
class Alerta:
    """Dados de um alerta de preco pronto para virar mensagem."""

    nome: str
    url: str
    preco: float
    preco_alvo: float
    preco_anterior: float | None = None
    menor_preco: float | None = None


def montar_alerta(alerta: Alerta) -> str:
    """Mensagem no estilo de grupo de promocao, em HTML do Telegram."""
    linhas = [
        "🔥 <b>PREÇO ALVO ATINGIDO</b>",
        "",
        f"📦 <b>{escapar(alerta.nome)}</b>",
        f"💰 <b>{moeda(alerta.preco)}</b>  <i>(seu alvo: {moeda(alerta.preco_alvo)})</i>",
    ]

    anterior = alerta.preco_anterior
    if anterior and anterior > alerta.preco:
        queda = (1 - alerta.preco / anterior) * 100
        linhas.append(f"📉 <b>-{queda:.0f}%</b> desde a última checagem ({moeda(anterior)})")

    if alerta.menor_preco is not None and alerta.preco <= alerta.menor_preco + 0.009:
        linhas.append("🏆 <b>Menor preço já visto por aqui</b>")
    elif alerta.menor_preco is not None:
        linhas.append(f"🏷 Menor preço já visto: {moeda(alerta.menor_preco)}")

    linhas += ["", f'🛒 <a href="{escapar(alerta.url)}">Abrir a página do produto</a>']
    return "\n".join(linhas)


class ClienteTelegram:
    """Wrapper minimo sobre os metodos da Bot API que o assistente usa."""

    def __init__(
        self,
        token: str,
        timeout: float = 20.0,
        sessao: requests.Session | None = None,
    ) -> None:
        if not token:
            raise ErroTelegram("TELEGRAM_TOKEN nao configurado.")
        self.token = token
        self.timeout = timeout
        self.sessao = sessao or requests.Session()

    def _chamar(self, metodo: str, **parametros: Any) -> Any:
        url = API_BASE.format(token=self.token, metodo=metodo)
        # timeout de rede sempre maior que o long polling pedido
        timeout = self.timeout + float(parametros.get("timeout", 0) or 0)
        try:
            resposta = self.sessao.post(url, json=parametros, timeout=timeout)
            dado = resposta.json()
        except requests.RequestException as erro:
            raise ErroTelegram(f"Falha de rede ao chamar {metodo}: {erro}") from erro
        except ValueError as erro:
            raise ErroTelegram(f"Resposta invalida do Telegram em {metodo}") from erro

        if not dado.get("ok"):
            raise ErroTelegram(
                f"{metodo} falhou: {dado.get('description', 'erro desconhecido')}"
            )
        return dado.get("result")

    def enviar_mensagem(
        self, chat_id: str | int, texto: str, preview: bool = False
    ) -> None:
        self._chamar(
            "sendMessage",
            chat_id=chat_id,
            text=texto,
            parse_mode="HTML",
            disable_web_page_preview=not preview,
        )

    def obter_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        """Long polling. timeout=0 devolve na hora o que estiver pendente."""
        parametros: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            parametros["offset"] = offset
        return self._chamar("getUpdates", **parametros) or []

    def obter_me(self) -> dict:
        return self._chamar("getMe")
