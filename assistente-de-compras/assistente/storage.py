"""Persistencia em SQLite: lista de desejos, historico de precos e estado.

Um arquivo unico (data/wishlist.db) guarda tudo. SQLite foi escolhido no
lugar de um JSON solto porque o assistente escreve de dois lugares
diferentes (a rotina do cron e o bot do Telegram) e porque o historico de
precos cresce a cada verificacao.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

ESQUEMA = """
CREATE TABLE IF NOT EXISTS produtos (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    nome               TEXT    NOT NULL,
    url                TEXT    NOT NULL UNIQUE,
    preco_alvo         REAL    NOT NULL,
    status             TEXT    NOT NULL DEFAULT 'ativo',
    css_selector       TEXT,
    ultimo_preco       REAL,
    menor_preco        REAL,
    ultima_verificacao TEXT,
    ultimo_erro        TEXT,
    alerta_preco       REAL,
    alerta_em          TEXT,
    criado_em          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS historico (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    produto_id    INTEGER NOT NULL REFERENCES produtos(id) ON DELETE CASCADE,
    preco         REAL    NOT NULL,
    verificado_em TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_historico_produto ON historico(produto_id);

CREATE TABLE IF NOT EXISTS estado (
    chave TEXT PRIMARY KEY,
    valor TEXT
);
"""

STATUS_ATIVO = "ativo"
STATUS_PAUSADO = "pausado"


def agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(momento: datetime) -> str:
    return momento.astimezone(timezone.utc).isoformat(timespec="seconds")


def _de_iso(texto: str | None) -> datetime | None:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


@dataclass
class Produto:
    """Um item monitorado da lista de desejos."""

    id: int
    nome: str
    url: str
    preco_alvo: float
    status: str
    css_selector: str | None = None
    ultimo_preco: float | None = None
    menor_preco: float | None = None
    ultima_verificacao: str | None = None
    ultimo_erro: str | None = None
    alerta_preco: float | None = None
    alerta_em: str | None = None
    criado_em: str | None = None

    @property
    def ativo(self) -> bool:
        return self.status == STATUS_ATIVO

    @classmethod
    def de_linha(cls, linha: sqlite3.Row) -> "Produto":
        return cls(**{chave: linha[chave] for chave in linha.keys()})


class Storage:
    """Camada fina sobre o SQLite. Use como context manager."""

    def __init__(self, caminho: Path | str) -> None:
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.conexao = sqlite3.connect(str(self.caminho))
        self.conexao.row_factory = sqlite3.Row
        self.conexao.execute("PRAGMA foreign_keys = ON")
        self.conexao.executescript(ESQUEMA)
        self.conexao.commit()

    # ------------------------------------------------------------------ ciclo
    def fechar(self) -> None:
        self.conexao.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *_exc) -> None:
        self.fechar()

    @contextmanager
    def _transacao(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conexao
            self.conexao.commit()
        except Exception:
            self.conexao.rollback()
            raise

    # --------------------------------------------------------------- produtos
    def adicionar(
        self,
        nome: str,
        url: str,
        preco_alvo: float,
        css_selector: str | None = None,
    ) -> Produto:
        """Cadastra um produto. Levanta ValueError se a URL ja existir."""
        if preco_alvo <= 0:
            raise ValueError("O preço alvo precisa ser maior que zero.")
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("A URL precisa começar com http:// ou https://.")
        if self.obter_por_url(url):
            raise ValueError("Essa URL já está na lista.")
        with self._transacao() as con:
            cursor = con.execute(
                "INSERT INTO produtos (nome, url, preco_alvo, status, css_selector,"
                " criado_em) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    nome.strip() or url,
                    url,
                    float(preco_alvo),
                    STATUS_ATIVO,
                    css_selector,
                    _iso(agora()),
                ),
            )
        return self.obter(int(cursor.lastrowid))  # type: ignore[arg-type]

    def obter(self, produto_id: int) -> Produto | None:
        linha = self.conexao.execute(
            "SELECT * FROM produtos WHERE id = ?", (produto_id,)
        ).fetchone()
        return Produto.de_linha(linha) if linha else None

    def obter_por_url(self, url: str) -> Produto | None:
        linha = self.conexao.execute(
            "SELECT * FROM produtos WHERE url = ?", (url.strip(),)
        ).fetchone()
        return Produto.de_linha(linha) if linha else None

    def listar(self, apenas_ativos: bool = False) -> list[Produto]:
        sql = "SELECT * FROM produtos"
        parametros: tuple = ()
        if apenas_ativos:
            sql += " WHERE status = ?"
            parametros = (STATUS_ATIVO,)
        sql += " ORDER BY id"
        return [Produto.de_linha(l) for l in self.conexao.execute(sql, parametros)]

    def remover(self, produto_id: int) -> bool:
        with self._transacao() as con:
            cursor = con.execute("DELETE FROM produtos WHERE id = ?", (produto_id,))
        return cursor.rowcount > 0

    def definir_status(self, produto_id: int, status: str) -> bool:
        if status not in (STATUS_ATIVO, STATUS_PAUSADO):
            raise ValueError(f"Status inválido: {status}")
        with self._transacao() as con:
            cursor = con.execute(
                "UPDATE produtos SET status = ? WHERE id = ?", (status, produto_id)
            )
        return cursor.rowcount > 0

    def definir_alvo(self, produto_id: int, preco_alvo: float) -> bool:
        if preco_alvo <= 0:
            raise ValueError("O preço alvo precisa ser maior que zero.")
        with self._transacao() as con:
            cursor = con.execute(
                "UPDATE produtos SET preco_alvo = ? WHERE id = ?",
                (float(preco_alvo), produto_id),
            )
        return cursor.rowcount > 0

    def definir_nome(self, produto_id: int, nome: str) -> bool:
        with self._transacao() as con:
            cursor = con.execute(
                "UPDATE produtos SET nome = ? WHERE id = ?", (nome.strip(), produto_id)
            )
        return cursor.rowcount > 0

    def definir_seletor(self, produto_id: int, seletor: str | None) -> bool:
        with self._transacao() as con:
            cursor = con.execute(
                "UPDATE produtos SET css_selector = ? WHERE id = ?",
                (seletor, produto_id),
            )
        return cursor.rowcount > 0

    # ---------------------------------------------------------------- precos
    def registrar_preco(self, produto_id: int, preco: float) -> None:
        """Grava a leitura no historico e atualiza ultimo/menor preco."""
        momento = _iso(agora())
        with self._transacao() as con:
            con.execute(
                "INSERT INTO historico (produto_id, preco, verificado_em)"
                " VALUES (?, ?, ?)",
                (produto_id, float(preco), momento),
            )
            con.execute(
                """
                UPDATE produtos
                   SET ultimo_preco = ?,
                       menor_preco = CASE
                           WHEN menor_preco IS NULL OR menor_preco > ?
                           THEN ? ELSE menor_preco END,
                       ultima_verificacao = ?,
                       ultimo_erro = NULL
                 WHERE id = ?
                """,
                (float(preco), float(preco), float(preco), momento, produto_id),
            )

    def registrar_erro(self, produto_id: int, erro: str) -> None:
        with self._transacao() as con:
            con.execute(
                "UPDATE produtos SET ultimo_erro = ?, ultima_verificacao = ?"
                " WHERE id = ?",
                (erro[:500], _iso(agora()), produto_id),
            )

    def registrar_alerta(self, produto_id: int, preco: float) -> None:
        with self._transacao() as con:
            con.execute(
                "UPDATE produtos SET alerta_preco = ?, alerta_em = ? WHERE id = ?",
                (float(preco), _iso(agora()), produto_id),
            )

    def historico(self, produto_id: int, limite: int = 30) -> list[tuple[str, float]]:
        linhas = self.conexao.execute(
            "SELECT verificado_em, preco FROM historico WHERE produto_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (produto_id, limite),
        )
        return [(l["verificado_em"], l["preco"]) for l in linhas]

    def deve_alertar(
        self, produto: Produto, preco: float, horas_entre_alertas: float
    ) -> bool:
        """Regra anti-spam.

        Alerta quando o preco esta no alvo E (a) nunca alertamos, (b) o preco
        caiu ainda mais do que no ultimo alerta, ou (c) ja passou a janela de
        silencio configurada.
        """
        if preco > produto.preco_alvo:
            return False
        if produto.alerta_preco is None:
            return True
        if preco < produto.alerta_preco - 0.009:
            return True
        ultimo = _de_iso(produto.alerta_em)
        if ultimo is None:
            return True
        return agora() - ultimo >= timedelta(hours=horas_entre_alertas)

    # ---------------------------------------------------------------- estado
    def obter_estado(self, chave: str) -> str | None:
        linha = self.conexao.execute(
            "SELECT valor FROM estado WHERE chave = ?", (chave,)
        ).fetchone()
        return linha["valor"] if linha else None

    def definir_estado(self, chave: str, valor: str) -> None:
        with self._transacao() as con:
            con.execute(
                "INSERT INTO estado (chave, valor) VALUES (?, ?)"
                " ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
                (chave, str(valor)),
            )

    def importar(self, itens: Iterable[dict]) -> int:
        """Importa uma lista vinda de JSON. Ignora URLs ja cadastradas."""
        total = 0
        for item in itens:
            try:
                self.adicionar(
                    nome=str(item.get("nome") or item.get("name") or ""),
                    url=str(item["url"]),
                    preco_alvo=float(item.get("preco_alvo") or item["preco"]),
                    css_selector=item.get("css_selector"),
                )
                total += 1
            except (KeyError, ValueError):
                continue
        return total
