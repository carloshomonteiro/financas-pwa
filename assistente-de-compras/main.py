#!/usr/bin/env python3
"""Assistente de compras - monitor de precos com alertas no Telegram.

Modos de uso (veja o README para os detalhes):

    python main.py --check                 # rotina diaria (cron / Agendador)
    python main.py --bot                   # bot ouvindo comandos (servico)
    python main.py --comandos              # le comandos pendentes e sai (cron)
    python main.py --adicionar URL 299,90 --nome "Fone XYZ"
    python main.py --listar
    python main.py --remover 3
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from assistente import __version__
from assistente.checker import verificar
from assistente.commands import Comandos, processar_updates
from assistente.config import carregar_config, configurar_log
from assistente.notifier import ClienteTelegram, ErroTelegram, moeda
from assistente.scraper import Buscador, parse_preco
from assistente.storage import STATUS_ATIVO, STATUS_PAUSADO, Storage

log = logging.getLogger("assistente")


def montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Monitor de precos com alertas no Telegram.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    acoes = parser.add_argument_group("acoes (escolha uma)")
    acoes.add_argument("--check", action="store_true",
                       help="verifica os precos e envia os alertas (modo autonomo)")
    acoes.add_argument("--bot", action="store_true",
                       help="fica ouvindo comandos do Telegram (long polling)")
    acoes.add_argument("--comandos", action="store_true",
                       help="processa os comandos pendentes e sai (para cron)")
    acoes.add_argument("--listar", action="store_true", help="mostra a lista")
    acoes.add_argument("--adicionar", nargs=2, metavar=("URL", "PRECO_ALVO"),
                       help="cadastra um produto")
    acoes.add_argument("--remover", type=int, metavar="ID", help="remove um produto")
    acoes.add_argument("--pausar", type=int, metavar="ID", help="pausa o monitoramento")
    acoes.add_argument("--retomar", type=int, metavar="ID", help="retoma o monitoramento")
    acoes.add_argument("--importar", metavar="ARQUIVO.json",
                       help="importa uma lista de produtos de um JSON")
    acoes.add_argument("--testar-telegram", action="store_true",
                       help="envia uma mensagem de teste para o seu chat")

    extras = parser.add_argument_group("opcoes")
    extras.add_argument("--nome", help="nome do produto (com --adicionar)")
    extras.add_argument("--seletor", help="seletor CSS do preco (com --adicionar)")
    extras.add_argument("--id", type=int, help="restringe --check a um produto")
    extras.add_argument("--forcar", action="store_true",
                        help="com --check, reenvia o alerta ignorando a janela de silencio")
    extras.add_argument("--env", help="caminho de um .env alternativo")
    extras.add_argument("-v", "--verbose", action="store_true", help="log detalhado")
    extras.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _cliente_ou_none(config) -> ClienteTelegram | None:
    if not config.telegram_configurado:
        log.warning(
            "Telegram nao configurado (TELEGRAM_TOKEN/TELEGRAM_CHAT_ID). "
            "Os alertas so vao aparecer no log."
        )
        return None
    return ClienteTelegram(config.telegram_token, config.timeout_requisicao)


def acao_check(storage, config, apenas_id: int | None, forcar: bool) -> int:
    produtos = None
    if apenas_id is not None:
        produto = storage.obter(apenas_id)
        if produto is None:
            print(f"Item #{apenas_id} não encontrado.")
            return 1
        produtos = [produto]

    resumo = verificar(
        storage, config,
        buscador=Buscador(config),
        cliente=_cliente_ou_none(config),
        produtos=produtos,
        forcar_alerta=forcar,
    )
    print(resumo.texto())
    for nome, erro in resumo.falhas:
        print(f"  ! {nome}: {erro}")
    return 0 if not resumo.falhas else 2


def acao_listar(storage) -> int:
    produtos = storage.listar()
    if not produtos:
        print("Lista vazia. Use --adicionar URL PRECO_ALVO.")
        return 0
    largura = max(len(p.nome) for p in produtos)
    print(f"{'ID':>3}  {'PRODUTO':<{largura}}  {'ATUAL':>12}  {'ALVO':>12}  STATUS")
    for p in produtos:
        atual = moeda(p.ultimo_preco) if p.ultimo_preco else "—"
        marca = "*" if p.ultimo_preco and p.ultimo_preco <= p.preco_alvo else " "
        print(f"{p.id:>3}  {p.nome:<{largura}}  {atual:>12}  "
              f"{moeda(p.preco_alvo):>12}  {p.status}{marca}")
        if p.ultimo_erro:
            print(f"      erro: {p.ultimo_erro[:100]}")
    return 0


def acao_adicionar(storage, url: str, bruto_preco: str, nome, seletor) -> int:
    preco = parse_preco(bruto_preco)
    if preco is None:
        print(f"Preço inválido: {bruto_preco!r}. Ex: 299,90")
        return 1
    try:
        produto = storage.adicionar(nome or url, url, preco, seletor)
    except ValueError as erro:
        print(f"Erro: {erro}")
        return 1
    print(f"Adicionado #{produto.id}: {produto.nome} (alvo {moeda(produto.preco_alvo)})")
    print(f"Confira a leitura do preço com: python main.py --check --id {produto.id}")
    return 0


def acao_bot(storage, config) -> int:
    """Loop de long polling: responde comandos ate voce parar com Ctrl+C."""
    if not config.telegram_token:
        print("Configure TELEGRAM_TOKEN no .env antes de usar --bot.")
        return 1
    cliente = ClienteTelegram(config.telegram_token, config.timeout_requisicao)
    comandos = Comandos(storage, config, criar_executor_check(storage, config, cliente))
    try:
        eu = cliente.obter_me()
        log.info("Bot @%s ouvindo comandos. Ctrl+C para parar.", eu.get("username"))
    except ErroTelegram as erro:
        print(f"Token inválido ou sem rede: {erro}")
        return 1

    while True:
        try:
            processar_updates(cliente, comandos, storage, config, espera=30)
        except ErroTelegram as erro:
            log.error("Telegram indisponivel (%s). Tentando de novo em 15s.", erro)
            time.sleep(15)
        except KeyboardInterrupt:
            print("\nEncerrado.")
            return 0


def criar_executor_check(storage, config, cliente):
    """Devolve a funcao que o comando /checar usa (evita import circular)."""
    def executar(produtos=None) -> str:
        resumo = verificar(
            storage, config,
            buscador=Buscador(config),
            cliente=cliente,
            produtos=produtos,
        )
        texto = resumo.texto()
        if resumo.falhas:
            texto += "\n" + "\n".join(f"⚠️ {n}: {e}" for n, e in resumo.falhas[:5])
        return texto
    return executar


def acao_importar(storage, caminho: str) -> int:
    arquivo = Path(caminho)
    if not arquivo.exists():
        print(f"Arquivo não encontrado: {arquivo}")
        return 1
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    itens = dados.get("produtos", dados) if isinstance(dados, dict) else dados
    total = storage.importar(itens)
    print(f"{total} produto(s) importado(s) de {arquivo}.")
    return 0


def acao_testar_telegram(config) -> int:
    if not config.telegram_configurado:
        print("Defina TELEGRAM_TOKEN e TELEGRAM_CHAT_ID no .env primeiro.")
        return 1
    cliente = ClienteTelegram(config.telegram_token, config.timeout_requisicao)
    try:
        eu = cliente.obter_me()
        cliente.enviar_mensagem(
            config.telegram_chat_id,
            "✅ <b>Assistente de compras conectado.</b>\nUse /ajuda para os comandos.",
        )
    except ErroTelegram as erro:
        print(f"Falhou: {erro}")
        return 1
    print(f"Mensagem enviada por @{eu.get('username')} para o chat {config.telegram_chat_id}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = montar_parser()
    args = parser.parse_args(argv)

    config = carregar_config(Path(args.env) if args.env else None)
    configurar_log("DEBUG" if args.verbose else config.nivel_log)

    with Storage(config.caminho_banco) as storage:
        if args.check:
            return acao_check(storage, config, args.id, args.forcar)
        if args.bot:
            return acao_bot(storage, config)
        if args.comandos:
            cliente = _cliente_ou_none(config)
            if cliente is None:
                return 1
            comandos = Comandos(storage, config,
                                criar_executor_check(storage, config, cliente))
            tratadas = processar_updates(cliente, comandos, storage, config)
            print(f"{tratadas} comando(s) processado(s).")
            return 0
        if args.listar:
            return acao_listar(storage)
        if args.adicionar:
            return acao_adicionar(storage, args.adicionar[0], args.adicionar[1],
                                  args.nome, args.seletor)
        if args.remover is not None:
            ok = storage.remover(args.remover)
            print(f"Item #{args.remover} {'removido' if ok else 'não encontrado'}.")
            return 0 if ok else 1
        if args.pausar is not None:
            ok = storage.definir_status(args.pausar, STATUS_PAUSADO)
            print(f"Item #{args.pausar} {'pausado' if ok else 'não encontrado'}.")
            return 0 if ok else 1
        if args.retomar is not None:
            ok = storage.definir_status(args.retomar, STATUS_ATIVO)
            print(f"Item #{args.retomar} {'reativado' if ok else 'não encontrado'}.")
            return 0 if ok else 1
        if args.importar:
            return acao_importar(storage, args.importar)
        if args.testar_telegram:
            return acao_testar_telegram(config)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
