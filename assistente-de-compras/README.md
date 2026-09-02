# 🛒 Assistente de compras

Monitor de preços em Python: acompanha os produtos da sua lista de desejos,
compara com o preço que você quer pagar e avisa no Telegram quando a promoção
aparece. Também dá para gerenciar a lista inteira pelo celular, por comandos no
próprio chat do bot.

- **Roda sozinho**: `python main.py --check` é feito para cron / Agendador de Tarefas.
- **Sem servidor**: o bot usa long polling, então não precisa de webhook, porta aberta nem IP fixo.
- **Sem banco externo**: um arquivo SQLite guarda a lista, o histórico de preços e o estado do bot.

---

## Estrutura

```
assistente-de-compras/
├── main.py                  # linha de comando: --check, --bot, --adicionar, ...
├── requirements.txt
├── .env.example             # copie para .env e preencha
├── wishlist.exemplo.json    # formato aceito por --importar
├── assistente/
│   ├── config.py            # configuração via variáveis de ambiente
│   ├── storage.py           # SQLite: produtos, histórico, estado
│   ├── scraper.py           # download + extração do preço
│   ├── notifier.py          # cliente do Telegram e formatação do alerta
│   ├── commands.py          # comandos do bot (/adicionar, /listar, ...)
│   └── checker.py           # rotina de verificação e disparo dos alertas
├── tests/                   # 48 testes, todos offline
└── data/wishlist.db         # criado na primeira execução (fora do git)
```

---

## 1. Instalação

```bash
cd assistente-de-compras
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Requer Python 3.10 ou superior.

---

## 2. Configurar o bot do Telegram

**a) Criar o bot e pegar o token**

1. No Telegram, abra uma conversa com [@BotFather](https://t.me/BotFather).
2. Envie `/newbot` e siga as perguntas (nome e username, que precisa terminar em `bot`).
3. O BotFather responde com um token no formato `123456789:AAAbbb...`.
4. Cole esse token no `.env`:

```env
TELEGRAM_TOKEN=123456789:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll
```

**b) Descobrir o seu chat id**

1. Abra a conversa com o **seu** bot e mande qualquer mensagem (`/start`).
2. Com o `TELEGRAM_TOKEN` já no `.env`, rode:

```bash
python main.py --bot
```

3. O bot responde: *"Este bot é de uso pessoal. Seu chat id é `987654321`"*.
   Esse número é o que falta. Pare com `Ctrl+C`, coloque no `.env` e pronto:

```env
TELEGRAM_CHAT_ID=987654321
```

> Enquanto `TELEGRAM_CHAT_ID` estiver vazio, **nenhum** chat consegue comandar o
> bot — é essa trava que faz a descoberta acima ser segura. Para liberar mais de
> um chat (o seu e o de alguém da casa, por exemplo), use
> `TELEGRAM_CHATS_AUTORIZADOS=111,222`.

**c) Testar a conexão**

```bash
python main.py --testar-telegram
```

Deve chegar uma mensagem de confirmação no seu chat.

---

## 3. Adicionar os primeiros produtos

**Pelo terminal:**

```bash
python main.py --adicionar "https://www.loja.com.br/fone-xyz/p" 299,90 --nome "Fone XYZ"
python main.py --listar
```

**Pelo Telegram** (com o bot rodando em `--bot`):

```
/adicionar https://www.loja.com.br/fone-xyz/p 299,90 Fone XYZ
```

**Importando uma lista pronta** (veja `wishlist.exemplo.json`):

```bash
python main.py --importar wishlist.exemplo.json
```

Depois de cadastrar, confira se o preço está sendo lido certo:

```bash
python main.py --check --id 1
```

A saída mostra o preço encontrado; com `-v` você vê também **de onde** ele veio
(`json-ld`, `meta`, `heuristica`...).

---

## 4. Comandos do bot

| Comando | O que faz |
|---|---|
| `/adicionar <url> <preço> [nome]` | cadastra um produto |
| `/listar` | lista com preço atual, alvo e status |
| `/remover <id>` | tira o item da lista |
| `/pausar <id>` · `/retomar <id>` | suspende ou retoma o monitoramento |
| `/alvo <id> <preço>` | muda o preço desejado |
| `/seletor <id> <css\|auto>` | corrige a leitura quando o preço vem errado |
| `/checar [id]` | verifica agora, sem esperar o horário |
| `/status` | resumo da lista |
| `/ajuda` | lista os comandos |

O alerta chega assim:

```
🔥 PREÇO ALVO ATINGIDO

📦 Fone Bluetooth XYZ
💰 R$ 249,90  (seu alvo: R$ 299,90)
📉 -29% desde a última checagem (R$ 349,00)
🏆 Menor preço já visto por aqui

🛒 Abrir a página do produto
```

Para não virar spam, o mesmo alerta só se repete se o preço **cair ainda mais**
ou depois da janela de silêncio (`HORAS_ENTRE_ALERTAS`, padrão 24h).

---

## 5. Execução automática

### Linux / macOS (cron)

Edite com `crontab -e` e use **caminhos absolutos** (o cron não conhece o seu venv):

```cron
# Verifica os preços todo dia às 9h e às 21h
0 9,21 * * * cd /home/voce/assistente-de-compras && .venv/bin/python main.py --check >> data/check.log 2>&1

# Lê os comandos enviados pelo Telegram a cada 10 minutos
*/10 * * * * cd /home/voce/assistente-de-compras && .venv/bin/python main.py --comandos >> data/bot.log 2>&1
```

Se preferir o bot respondendo na hora, rode `--bot` como serviço e deixe só o
`--check` no cron. Exemplo de unit do systemd (`/etc/systemd/system/assistente-compras.service`):

```ini
[Unit]
Description=Assistente de compras (bot do Telegram)
After=network-online.target

[Service]
User=voce
WorkingDirectory=/home/voce/assistente-de-compras
ExecStart=/home/voce/assistente-de-compras/.venv/bin/python main.py --bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now assistente-compras
```

### Windows (Agendador de Tarefas)

1. Abra o **Agendador de Tarefas** → *Criar Tarefa* (não "tarefa básica").
2. Aba **Geral**: marque *Executar estando o usuário conectado ou não*.
3. Aba **Disparadores**: novo disparador *Diariamente*, no horário desejado.
4. Aba **Ações**: *Iniciar um programa*
   - Programa: `C:\caminho\assistente-de-compras\.venv\Scripts\python.exe`
   - Argumentos: `main.py --check`
   - Iniciar em: `C:\caminho\assistente-de-compras`
5. Aba **Condições**: desmarque *Iniciar a tarefa somente se o computador estiver ligado na energia* se for notebook.

Para os comandos do Telegram, crie uma segunda tarefa igual com o argumento
`main.py --comandos` repetindo a cada 10 minutos.

---

## 6. Quando o preço vem errado

A extração tenta, nesta ordem: **seletor CSS que você definiu → JSON-LD
(schema.org) → metatags de preço → microdados → heurística** por classe/id.
Se o número vier errado (pegou o preço antigo, ou o valor da parcela):

1. Abra a página no navegador, clique com o botão direito no preço → *Inspecionar*.
2. Anote a classe do elemento, por exemplo `<span class="price-value">R$ 249,90</span>`.
3. No Telegram: `/seletor 1 span.price-value` (ou `--seletor` ao adicionar pelo terminal).
4. Confira com `/checar 1`.

Se a loja monta o preço por JavaScript (a página vem sem o valor no HTML),
instale o navegador e ligue o modo renderizado:

```bash
pip install playwright && playwright install chromium
```

```env
USAR_PLAYWRIGHT=true
```

Com `NIVEL_LOG=DEBUG` o log mostra os candidatos a preço encontrados na página.

---

## 7. Configuração (arquivo `.env`)

| Variável | Padrão | Para que serve |
|---|---|---|
| `TELEGRAM_TOKEN` | — | token do BotFather |
| `TELEGRAM_CHAT_ID` | — | seu chat; também é quem pode comandar o bot |
| `TELEGRAM_CHATS_AUTORIZADOS` | vazio | chats extras autorizados, separados por vírgula |
| `CAMINHO_BANCO` | `data/wishlist.db` | onde fica o SQLite |
| `TIMEOUT_REQUISICAO` | `20` | segundos por requisição |
| `TENTATIVAS` | `3` | retentativas com backoff exponencial |
| `ESPERA_MIN_SEGUNDOS` / `ESPERA_MAX_SEGUNDOS` | `3` / `7` | intervalo aleatório entre produtos |
| `RESPEITAR_ROBOTS` | `true` | respeita o `robots.txt` da loja |
| `USAR_PLAYWRIGHT` | `false` | renderiza a página com navegador |
| `HORAS_ENTRE_ALERTAS` | `24` | janela de silêncio entre alertas repetidos |
| `NOTIFICAR_ERROS` | `false` | avisa no Telegram quando uma leitura falha |
| `NIVEL_LOG` | `INFO` | `DEBUG` para investigar extração |

---

## 8. Boas práticas (e limites)

- O projeto é para **uso pessoal**, na sua própria lista de desejos. Ele faz uma
  requisição por produto por execução, com intervalo aleatório entre elas — não
  aponte para dezenas de itens de minuto em minuto.
- Por padrão o `robots.txt` da loja é respeitado. Quando ele bloqueia a página, a
  mensagem de erro diz exatamente isso; ignorar (`RESPEITAR_ROBOTS=false`) é uma
  decisão sua e pode conflitar com os termos de uso do site.
- Nenhum site é "oficialmente suportado": lojas mudam o HTML. Por isso existem o
  `/seletor` e o histórico de leituras — se um item começar a falhar, o `/listar`
  mostra o erro da última tentativa.
- O `.env` e a pasta `data/` estão no `.gitignore`. Não versione o seu token.

---

## 9. Testes

```bash
python -m unittest discover -s tests -v
```

48 testes cobrindo parsing de preço (pt-BR e en-US), as estratégias de extração,
o SQLite, a regra anti-spam de alertas, os comandos do bot e a rotina de
verificação. Nenhum deles acessa a rede.
