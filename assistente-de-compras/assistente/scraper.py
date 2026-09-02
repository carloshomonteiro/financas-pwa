"""Busca e extracao de preco a partir da pagina do produto.

A extracao tenta, nesta ordem:

1. seletor CSS especifico do produto (definido por voce com /seletor);
2. dados estruturados JSON-LD (schema.org Product/Offer) - o caminho mais
   confiavel, usado pela maioria das lojas grandes;
3. metatags de preco (og:price:amount, product:price:amount, itemprop=price);
4. microdados itemprop="price" no corpo da pagina;
5. heuristica: elementos cuja classe/id parece de preco, ignorando os que
   parecem preco antigo ou parcelamento.

Cada resultado carrega a estrategia usada, para voce conferir se o numero
veio de onde deveria.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from .config import Config

log = logging.getLogger(__name__)

# Classes/ids que costumam marcar o preco principal...
PADRAO_PRECO = re.compile(r"(pre[cç]o|price|valor|amount|sales?)", re.I)
# ...e os que marcam preco antigo, parcela ou frete (que nao queremos).
PADRAO_RUIDO = re.compile(
    r"(old|antig|risc|strike|from|de-por|installment|parcel|juros|frete|"
    r"shipping|desconto|discount|economi|cashback|assinante|subscri)",
    re.I,
)


class ErroDeBusca(Exception):
    """Falha ao baixar a pagina (rede, bloqueio, status HTTP ruim)."""


class PrecoNaoEncontrado(Exception):
    """A pagina baixou, mas nenhum preco pode ser extraido."""


@dataclass
class Resultado:
    """Preco extraido com a origem do dado."""

    preco: float
    estrategia: str
    titulo: str | None = None


def parse_preco(texto: Any) -> float | None:
    """Converte '  R$ 1.299,90 ' -> 1299.9, tolerando formato pt-BR e en-US."""
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        valor = float(texto)
        return valor if valor > 0 else None

    bruto = str(texto).replace("\xa0", " ").strip()
    achado = re.search(r"\d[\d.,\s]*\d|\d", bruto)
    if not achado:
        return None

    numero = re.sub(r"\s", "", achado.group(0))
    tem_virgula, tem_ponto = "," in numero, "." in numero

    if tem_virgula and tem_ponto:
        # O separador decimal e o ultimo que aparece: 1.299,90 ou 1,299.90
        if numero.rfind(",") > numero.rfind("."):
            numero = numero.replace(".", "").replace(",", ".")
        else:
            numero = numero.replace(",", "")
    elif tem_virgula:
        decimais = numero.rsplit(",", 1)[1]
        numero = numero.replace(",", ".") if len(decimais) == 2 else numero.replace(",", "")
    elif tem_ponto:
        decimais = numero.rsplit(".", 1)[1]
        # "1.299" em pt-BR e milhar, nao decimal; "1299.90" e decimal.
        if len(decimais) == 3 or numero.count(".") > 1:
            numero = numero.replace(".", "")

    try:
        valor = float(numero)
    except ValueError:
        return None
    return valor if valor > 0 else None


def _percorrer_json(dado: Any) -> Iterable[dict]:
    """Gera todos os dicionarios de uma estrutura JSON aninhada."""
    if isinstance(dado, dict):
        yield dado
        for valor in dado.values():
            yield from _percorrer_json(valor)
    elif isinstance(dado, list):
        for item in dado:
            yield from _percorrer_json(item)


def _preco_do_json_ld(sopa: BeautifulSoup) -> tuple[float, str] | None:
    for bloco in sopa.find_all("script", type=lambda t: t and "ld+json" in t.lower()):
        conteudo = bloco.string or bloco.get_text() or ""
        try:
            dado = json.loads(conteudo.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for objeto in _percorrer_json(dado):
            for campo in ("price", "lowPrice", "highPrice"):
                if campo in objeto:
                    preco = parse_preco(objeto[campo])
                    if preco:
                        return preco, f"json-ld:{campo}"
    return None


SELETORES_META = (
    ('meta[itemprop="price"]', "content"),
    ('meta[property="product:price:amount"]', "content"),
    ('meta[property="og:price:amount"]', "content"),
    ('meta[name="twitter:data1"]', "content"),
)


def _preco_das_metatags(sopa: BeautifulSoup) -> tuple[float, str] | None:
    for seletor, atributo in SELETORES_META:
        for tag in sopa.select(seletor):
            preco = parse_preco(tag.get(atributo))
            if preco:
                return preco, f"meta:{seletor}"
    return None


def _preco_dos_microdados(sopa: BeautifulSoup) -> tuple[float, str] | None:
    for tag in sopa.select('[itemprop="price"], [data-price], [data-product-price]'):
        for origem in (
            tag.get("content"),
            tag.get("data-price"),
            tag.get("data-product-price"),
            tag.get_text(" ", strip=True),
        ):
            preco = parse_preco(origem)
            if preco:
                return preco, "microdados:itemprop=price"
    return None


def _preco_por_heuristica(sopa: BeautifulSoup) -> tuple[float, str] | None:
    """Ultimo recurso: procura elementos que 'cheiram' a preco principal."""
    candidatos: list[tuple[float, str]] = []
    for tag in sopa.find_all(["span", "div", "p", "strong", "b", "h1", "h2", "h3"]):
        assinatura = " ".join(
            filter(None, [" ".join(tag.get("class") or []), tag.get("id") or ""])
        )
        if not assinatura or not PADRAO_PRECO.search(assinatura):
            continue
        if PADRAO_RUIDO.search(assinatura):
            continue
        texto = tag.get_text(" ", strip=True)
        if len(texto) > 40:  # bloco grande demais para ser so o preco
            continue
        preco = parse_preco(texto)
        if preco:
            candidatos.append((preco, assinatura.strip()))

    if not candidatos:
        return None
    log.debug("Candidatos por heuristica: %s", candidatos[:5])
    preco, assinatura = candidatos[0]  # o preco principal costuma vir primeiro
    return preco, f"heuristica:{assinatura[:40]}"


def _titulo(sopa: BeautifulSoup) -> str | None:
    tag_og = sopa.select_one('meta[property="og:title"]')
    if tag_og and tag_og.get("content"):
        return tag_og["content"].strip()[:120]
    if sopa.title and sopa.title.string:
        return sopa.title.string.strip()[:120]
    h1 = sopa.find("h1")
    return h1.get_text(" ", strip=True)[:120] if h1 else None


def extrair_preco(html: str, css_selector: str | None = None) -> Resultado:
    """Roda as estrategias em ordem e devolve o primeiro preco plausivel."""
    sopa = BeautifulSoup(html, "html.parser")
    titulo = _titulo(sopa)

    if css_selector:
        for tag in sopa.select(css_selector):
            preco = parse_preco(tag.get("content") or tag.get_text(" ", strip=True))
            if preco:
                return Resultado(preco, f"seletor:{css_selector}", titulo)
        log.warning("Seletor %r nao encontrou preco; tentando as demais estrategias.",
                    css_selector)

    for estrategia in (
        _preco_do_json_ld,
        _preco_das_metatags,
        _preco_dos_microdados,
        _preco_por_heuristica,
    ):
        achado = estrategia(sopa)
        if achado:
            preco, origem = achado
            return Resultado(preco, origem, titulo)

    raise PrecoNaoEncontrado(
        "Nenhum preço encontrado na página. Use /seletor <id> <css> para "
        "apontar o elemento certo."
    )


class Buscador:
    """Faz o download das paginas com cabecalhos, retentativas e educacao."""

    def __init__(self, config: Config, sessao: requests.Session | None = None) -> None:
        self.config = config
        self.sessao = sessao or requests.Session()
        self.sessao.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                          "image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Upgrade-Insecure-Requests": "1",
            }
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._ultima_requisicao = 0.0

    # ------------------------------------------------------------- educacao
    def _pode_acessar(self, url: str) -> bool:
        if not self.config.respeitar_robots:
            return True
        partes = urlparse(url)
        base = f"{partes.scheme}://{partes.netloc}"
        if base not in self._robots:
            leitor = RobotFileParser()
            leitor.set_url(f"{base}/robots.txt")
            try:
                leitor.read()
            except Exception as erro:  # rede fora, 404, etc: nao bloqueia
                log.debug("robots.txt de %s indisponivel (%s)", base, erro)
                leitor = None
            self._robots[base] = leitor
        leitor = self._robots[base]
        return True if leitor is None else leitor.can_fetch(self.config.user_agent, url)

    def _esperar(self) -> None:
        """Intervalo aleatorio entre requisicoes (nao martelar o site)."""
        if self.config.espera_max <= 0:
            return
        minimo = min(self.config.espera_min, self.config.espera_max)
        alvo = random.uniform(minimo, self.config.espera_max)
        decorrido = time.monotonic() - self._ultima_requisicao
        if self._ultima_requisicao and decorrido < alvo:
            time.sleep(alvo - decorrido)

    # -------------------------------------------------------------- download
    def obter_html(self, url: str) -> str:
        if not self._pode_acessar(url):
            raise ErroDeBusca(
                "Bloqueado pelo robots.txt do site. Se for uso pessoal e você "
                "assumir a responsabilidade, defina RESPEITAR_ROBOTS=false no .env."
            )

        ultimo_erro: Exception | None = None
        for tentativa in range(1, self.config.tentativas + 1):
            self._esperar()
            self._ultima_requisicao = time.monotonic()
            try:
                resposta = self.sessao.get(
                    url, timeout=self.config.timeout_requisicao, allow_redirects=True
                )
                if resposta.status_code in (403, 429, 503):
                    raise ErroDeBusca(
                        f"HTTP {resposta.status_code}: o site recusou a requisição "
                        "(anti-bot). Tente USAR_PLAYWRIGHT=true."
                    )
                resposta.raise_for_status()
                # Deixa o requests adivinhar o encoding certo antes de ler o texto
                if not resposta.encoding or resposta.encoding.lower() == "iso-8859-1":
                    resposta.encoding = resposta.apparent_encoding
                return resposta.text
            except Exception as erro:
                ultimo_erro = erro
                log.warning("Tentativa %d/%d falhou em %s: %s",
                            tentativa, self.config.tentativas, url, erro)
                if tentativa < self.config.tentativas:
                    time.sleep(2 ** tentativa)  # backoff exponencial

        raise ErroDeBusca(str(ultimo_erro) or "Falha desconhecida ao baixar a página.")

    def obter_html_renderizado(self, url: str) -> str:
        """Renderiza com Playwright - para lojas que montam o preco via JS."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as erro:  # pragma: no cover - depende de lib opcional
            raise ErroDeBusca(
                "Playwright não instalado. Rode: pip install playwright && "
                "playwright install chromium"
            ) from erro

        with sync_playwright() as p:  # pragma: no cover - precisa de navegador
            navegador = p.chromium.launch(headless=True)
            try:
                pagina = navegador.new_page(
                    user_agent=self.config.user_agent, locale="pt-BR"
                )
                pagina.goto(url, timeout=self.config.timeout_requisicao * 1000,
                            wait_until="domcontentloaded")
                pagina.wait_for_timeout(2500)  # deixa o JS montar o preco
                return pagina.content()
            finally:
                navegador.close()

    def buscar_preco(self, url: str, css_selector: str | None = None) -> Resultado:
        """Baixa a pagina e extrai o preco, com fallback para Playwright."""
        if self.config.usar_playwright:
            return extrair_preco(self.obter_html_renderizado(url), css_selector)

        html = self.obter_html(url)
        try:
            return extrair_preco(html, css_selector)
        except PrecoNaoEncontrado:
            log.info("Sem preco no HTML puro de %s; tentando renderizar.", url)
            try:
                return extrair_preco(self.obter_html_renderizado(url), css_selector)
            except ErroDeBusca:
                raise  # Playwright ausente: mensagem dele ja e clara
