"""Configuracao central do assistente.

Tudo e lido de variaveis de ambiente (ou de um arquivo .env na raiz do
projeto), para que nenhum token fique escrito no codigo.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv e opcional: sem ele, usa apenas o ambiente do sistema
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - caminho exercitado so sem a lib
    load_dotenv = None

# Raiz do projeto (pasta que contem main.py)
BASE_DIR = Path(__file__).resolve().parent.parent

# User-Agent de navegador real: a maioria dos bloqueios simples olha so isso.
USER_AGENT_PADRAO = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _texto(nome: str, padrao: str = "") -> str:
    return (os.getenv(nome) or padrao).strip()


def _booleano(nome: str, padrao: bool) -> bool:
    bruto = _texto(nome)
    if not bruto:
        return padrao
    return bruto.lower() in {"1", "true", "t", "sim", "s", "yes", "y", "on"}


def _numero(nome: str, padrao: float) -> float:
    bruto = _texto(nome)
    if not bruto:
        return padrao
    try:
        return float(bruto.replace(",", "."))
    except ValueError:
        logging.getLogger(__name__).warning(
            "Valor invalido em %s=%r; usando o padrao %s", nome, bruto, padrao
        )
        return padrao


def _inteiro(nome: str, padrao: int) -> int:
    return int(_numero(nome, padrao))


@dataclass(frozen=True)
class Config:
    """Parametros de execucao, ja validados e com valores padrao."""

    telegram_token: str
    telegram_chat_id: str
    # Chats autorizados a mandar comandos. Vazio = so o telegram_chat_id.
    chats_autorizados: frozenset[str]
    caminho_banco: Path
    timeout_requisicao: float
    tentativas: int
    espera_min: float
    espera_max: float
    respeitar_robots: bool
    horas_entre_alertas: float
    notificar_erros: bool
    user_agent: str
    usar_playwright: bool
    nivel_log: str

    @property
    def telegram_configurado(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)


def carregar_config(env_file: Path | None = None) -> Config:
    """Le o .env (se existir) e devolve a configuracao efetiva."""
    if load_dotenv is not None:
        load_dotenv(env_file or BASE_DIR / ".env", override=False)

    chat_id = _texto("TELEGRAM_CHAT_ID")
    brutos = [c.strip() for c in _texto("TELEGRAM_CHATS_AUTORIZADOS").split(",")]
    autorizados = {c for c in brutos if c}
    if chat_id:
        autorizados.add(chat_id)

    banco = _texto("CAMINHO_BANCO") or str(BASE_DIR / "data" / "wishlist.db")

    return Config(
        telegram_token=_texto("TELEGRAM_TOKEN"),
        telegram_chat_id=chat_id,
        chats_autorizados=frozenset(autorizados),
        caminho_banco=Path(banco).expanduser(),
        timeout_requisicao=_numero("TIMEOUT_REQUISICAO", 20.0),
        tentativas=max(1, _inteiro("TENTATIVAS", 3)),
        espera_min=max(0.0, _numero("ESPERA_MIN_SEGUNDOS", 3.0)),
        espera_max=max(0.0, _numero("ESPERA_MAX_SEGUNDOS", 7.0)),
        respeitar_robots=_booleano("RESPEITAR_ROBOTS", True),
        horas_entre_alertas=_numero("HORAS_ENTRE_ALERTAS", 24.0),
        notificar_erros=_booleano("NOTIFICAR_ERROS", False),
        user_agent=_texto("USER_AGENT", USER_AGENT_PADRAO),
        usar_playwright=_booleano("USAR_PLAYWRIGHT", False),
        nivel_log=_texto("NIVEL_LOG", "INFO").upper(),
    )


def configurar_log(nivel: str = "INFO") -> None:
    """Log simples em stdout, com data - bom para cron/Agendador."""
    logging.basicConfig(
        level=getattr(logging, nivel.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
