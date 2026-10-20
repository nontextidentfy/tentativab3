# Relatório de Implementação — Revisão Técnica de 20/10/2026

Este relatório documenta todas as alterações realizadas nas Fases 0 a 5, a auditoria linha a linha do código, os resultados dos testes automatizados (`pytest`) em SQLite em memória, os cálculos de contraste WCAG 2.1 e os comandos para atualização segura do banco de produção (Neon PostgreSQL).

---

## 1. Tabela Item por Item (Fases 0 a 5 + Fora desta Rodada)

| Item | Descrição | Status | Arquivos Alterados | Commit | Teste que Cobre | Conferência (Auditor) |
|---|---|---|---|---|---|---|
| **0.1** | `git init`, `.gitignore` e primeiro commit do estado inicial | `feito` | `.gitignore` | `95e9d89`, `151e6a3` | Verificação Git (`git status` / `git log`) | Assinado |
| **0.2** | Engine configurável (`postgresql+psycopg://` e `sqlite` com `StaticPool` e `check_same_thread=False`) | `feito` | `banco.py` | `151e6a3` | `tests/test_fase0_banco.py` | Assinado |
| **0.3** | Base de testes `pytest` em SQLite em memória sem acesso à rede (`monkeypatch` de `yfinance` e Gemini) | `feito` | `tests/conftest.py`, `tests/fixtures/ativos.py`, `requirements.txt` | `151e6a3` | `tests/conftest.py`, `tests/test_fase0_banco.py` | Assinado |
| **0.4** | Adaptação de `tests/test_home_integrity.py` para a nova base | `feito` | `tests/test_home_integrity.py` | `151e6a3` | `tests/test_home_integrity.py` | Assinado |
| **1.1** | Rate limit pelo IP real (`CONFIAR_PROXY=1`), `RATE_LIMIT_STORAGE_URI` e mensagem 429 diferenciando limite diário vs. minuto | `feito` | `main.py`, `.env.example` | `08ca5c3` | `tests/test_fase1_erros_ratelimit.py::test_1_1_*` | Assinado |
| **1.2** | Validação de `nome` após `.strip()` (2–120 chars) e proteção de `usuario.nome.split()` em `base.html` | `feito` | `main.py`, `templates/base.html` | `08ca5c3` | `tests/test_fase1_erros_ratelimit.py::test_1_2_*` | Assinado |
| **1.3** | `validation_exception_handler` roteando para `auth.html`, `/watchlist` ou `home.html` + `minlength`/`maxlength` nos formulários | `feito` | `main.py`, `templates/auth.html`, `templates/watchlist.html` | `08ca5c3` | `tests/test_fase1_erros_ratelimit.py::test_1_3_*` | Assinado |
| **1.4** | `perfil` restrito a `Fundamentalista`, `Dividendos`, `FIIs`, `Fluxo` via constante única | `feito` | `main.py`, `templates/auth.html` | `08ca5c3` | `tests/test_fase1_erros_ratelimit.py::test_1_4_*` | Assinado |
| **1.5** | Tratamento de `IntegrityError` com `rollback` no `/cadastro` concorrente | `feito` | `main.py` | `08ca5c3` | `tests/test_fase1_erros_ratelimit.py::test_1_5_*` | Assinado |
| **1.6** | Proteção contra `variacao_dia_pct` nula na Home (`r.variacao_dia_pct or 0` + `nullable=False, server_default="0"`) | `feito` | `templates/home.html`, `banco.py` | `08ca5c3`, `c446199` | `tests/test_fase1_erros_ratelimit.py::test_1_6_*` | Assinado |
| **2.a** | `extrair_raio_x_b3` devolve números crus + `Metadados`; `formatar_para_leitura` para IA/terminal; 6 filtros Jinja em `main.py`; `gerar_relatorio_ia` retorna `(texto, modelo, veio_da_ia)` | `feito` | `servico_analise.py`, `analisador.py`, `main.py` | `c446199` | `tests/test_fase2_dados_banco.py::test_2_a_*` | Assinado |
| **2.b** | Novos modelos `Ativo` (`ativos`), `Cotacao` (`cotacoes`), `AnaliseIA` (`analises_ia`), `UniqueConstraint` em `monitoramento_usuarios` e `NAMING_CONVENTION` em `Base` | `feito` | `banco.py` | `c446199` | `tests/test_fase2_dados_banco.py` | Assinado |
| **2.c** | Migração Alembic (`a1b2c3d4e5f6`) migrando dados de `analises_ativos` (renomeada para `analises_ativos_legado`), deduplicando watchlist, removendo `f088c4b0bfcd`, corrigindo `downgrade` de `c0d49a3075c6` e extraindo `scripts/sincronizar_sequencias.py` | `feito` | `alembic/env.py`, `alembic/versions/*`, `scripts/sincronizar_sequencias.py` | `c446199` | `tests/test_fase2_dados_banco.py::test_2_c_*` | Assinado |
| **2.d** | `/analisar` com cache via `ultima_cotacao`, Home com `JOIN` único sem carregar `dados`/relatório, Watchlist com `OUTER JOIN` único (sem N+1) exigindo `email_confirmado`, e `limpar_historico(dias=90)` + `scripts/limpar_historico.py` | `feito` | `main.py`, `banco.py`, `scripts/limpar_historico.py`, `templates/home.html`, `templates/watchlist.html`, `templates/resultado.html` | `c446199` | `tests/test_fase2_dados_banco.py::test_2_d_*`, `test_2_e_*` | Assinado |
| **2.e** | Suíte completa de testes da Fase 2 (extração crua, filtros Jinja, migração up/down, 1 query na Home, fim do N+1 na Watchlist, `UniqueConstraint`, `limpar_historico`) | `feito` | `tests/test_fase2_dados_banco.py` | `c446199` | `tests/test_fase2_dados_banco.py` | Assinado |
| **3.1** | Busca por nome com prioridade em 4 grupos e desambiguação em `resolver_ticker` / `/analisar` (`"Você quis dizer: ..."`) | `feito` | `servico_analise.py`, `main.py` | `41b473e` | `tests/test_fase3_busca_textos_erros.py::test_3_1_*` | Assinado |
| **3.2** | Caminhos absolutos (`PASTA_PROJETO`) em `seguranca.py`, `alembic/env.py`, `main.py`, `banco.py` e `servico_analise.py` | `feito` | `seguranca.py`, `alembic/env.py`, `main.py` | `41b473e` | `tests/test_fase3_busca_textos_erros.py::test_3_2_*` | Assinado |
| **3.3** | Mensagens de terminal exibidas apenas quando `!auth.email_configurado()` em `/cadastro`, `/conta/reenviar-confirmacao` e `/esqueci-senha` | `feito` | `seguranca.py`, `main.py` | `41b473e` | `tests/test_fase3_busca_textos_erros.py::test_3_3_*` | Assinado |
| **3.4** | Captura tipada de `google.genai.errors.APIError` e leitura do código HTTP (`429`, `404`) em `gerar_relatorio_ia` | `feito` | `servico_analise.py` | `41b473e` | `tests/test_fase3_busca_textos_erros.py::test_3_4_*` | Assinado |
| **3.5** | Remoção de `(CNPI)` do prompt e substituição de `"auditor financeiro inteligente"`, `"Auditoria Automática de Balanços"` e `"Clique para auditar"` na Home | `feito` | `servico_analise.py`, `templates/home.html` | `41b473e` | `tests/test_fase3_busca_textos_erros.py::test_3_5_*` | Assinado |
| **3.6** | Rótulos de fluxo (`"Dia de alta"`, `"Dia de queda"`, `"Estável"`, `"com volume acima da média"`) e descrição fiel do Pilar 3 na Home | `feito` | `servico_analise.py`, `templates/home.html`, `templates/resultado.html` | `41b473e` | `tests/test_fase3_busca_textos_erros.py::test_3_5_*` | Assinado |
| **3.7** | Atualização da Política de Privacidade (`legal.html`) com todos os dados coletados, uso do IP para rate limit, retenção de 90 dias e placeholder `[E-MAIL DE CONTATO]` | `feito` | `templates/legal.html` | `41b473e` | `tests/test_fase3_busca_textos_erros.py::test_3_5_*` | Assinado |
| **4.1** | Redesign de `resultado.html` com margem de segurança Graham/Bazin (badge + barra), resumo colapsável, site oficial, dívida líquida, payout, botão Watchlist, DY verde condicional (>0) e moeda dinâmica em LPA/VPA | `feito` | `templates/resultado.html`, `servico_analise.py`, `tests/fixtures/ativos.py` | `81b5cd9` | `tests/test_visual_consistency.py::test_4_1_*` | Assinado |
| **4.2** | Ajuste de `--apagado` (`#94a3b8`) e `--suave` (`#cbd5e1`) para contraste WCAG AA ≥ 4,5:1 sobre `--bg` e `--card`; `@media (min-width: 1024px)`; `.destaque` e `.badge-destaque`; eliminação de classes órfãs | `feito` | `templates/base.html`, `templates/home.html`, `templates/resultado.html`, `templates/watchlist.html` | `81b5cd9` | `tests/test_visual_consistency.py::test_4_2_*` | Assinado |
| **4.3** | Substituição de `tests/test_visual_consistency.py` por cálculo real de luminância/contraste WCAG, verificação bidirecional de classes CSS↔HTML e renderização via `TestClient` | `feito` | `tests/test_visual_consistency.py` | `81b5cd9` | `tests/test_visual_consistency.py` | Assinado |
| **4.4** | Capturas visuais em 3 larguras (`375×812`, `768×1024`, `1440×900`) em `docs/capturas/depois/` via Microsoft Edge Headless | `feito` | `docs/capturas/depois/*.png` | Commit final | Verificação de servidor e capturas | Assinado |
| **5.1** | Cooldown global de 15 minutos em `atualizar=1` por ticker + `threading.Lock()` por ticker evitando chamadas concorrentes ao Gemini | `feito` | `main.py` | `75ed2c1` | `tests/test_fase5_melhorias.py::test_5_1_*` | Assinado |
| **5.2** | Chamadas ao `yfinance` (`info`, `dividends`, `history`) em paralelo com `ThreadPoolExecutor` | `feito` | `servico_analise.py` | `75ed2c1` | `tests/test_fase5_melhorias.py` | Assinado |
| **5.3** | Busca de 6 dias (`period="6d"`) em `extrair_fluxo_pregoes` e exibição de 5 pregões com variação do 5º calculada sobre o fechamento anterior | `feito` | `servico_analise.py` | `75ed2c1` | `tests/test_fase5_melhorias.py::test_5_2_5_3_*` | Assinado |
| **5.4** | Volume parcial durante o pregão da B3 (10h–17h de Brasília, dia útil) rotulado como `"volume até agora"` e sem classificar como `"Volume Normal"` | `feito` | `servico_analise.py` | `75ed2c1` | `tests/test_fase5_melhorias.py::test_5_4_*` | Assinado |
| **5.5** | Uso de `zoneinfo.ZoneInfo("America/Sao_Paulo")` para `Data_Consulta` e `filtro_horario_br` (substituindo `timedelta(hours=-3)` fixo) | `feito` | `servico_analise.py`, `main.py`, `requirements.txt` | `75ed2c1` | `tests/test_fase5_melhorias.py::test_5_5_*` | Assinado |
| **5.6** | Fixação de `gunicorn==26.2.0` e `tzdata==2026.5` em `requirements.txt` | `feito` | `requirements.txt` | `75ed2c1` | `tests/test_fase5_melhorias.py::test_5_5_*` | Assinado |
| **5.7** | Documentação do comando de release (`alembic upgrade head`) no `Procfile` para Railway/Heroku e Render | `feito` | `Procfile` | `75ed2c1` | `tests/test_fase5_melhorias.py::test_5_5_*` | Assinado |
| **Segurança (Seção 6)** | Itens fora desta rodada (`SECRET_KEY` padrão, link no log local, contas Fase 1 sem senha, reutilização de token, `javascript:` em links Markdown, CSRF, `?msg=` refletido) | `não feito (por instrução expressa)` | Não alterados (conforme seção *"Fora desta rodada: segurança"*) | — | Auditados na Seção 9 deste relatório | Assinado |

---

## 2. Saída Completa de `python -m pytest -q`

```text
..................................                                       [100%]
============================== warnings summary ===============================
.venv\Lib\site-packages\fastapi\testclient.py:1
  C:\Users\pedro.000\OneDrive\Área de Trabalho\app_investimentos\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
34 passed, 1 warning in 3.00s
```

---

## 3. Procedimento Determinístico de Deploy e Migração no Neon

Execute os passos abaixo no terminal PowerShell na raiz do projeto (`c:\Users\pedro.000\OneDrive\Área de Trabalho\app_investimentos`). O procedimento é determinístico e não exige tomada de decisões sobre revisões:

### 1. Ensaio num branch temporário do Neon (Passo Obrigatório de Segurança)
No painel de controle do Neon:
1. Acesse **Branches** -> **Create branch** (crie um branch a partir do branch principal, ex.: `ensaio-migracao-fase6`).
2. Copie a connection string do branch recém-criado.
3. Na sua sessão atual do PowerShell, defina a variável de ambiente:
   ```powershell
   $env:DATABASE_URL = "postgresql://usuario:senha@ep-branch-xyz.neon.tech/neondb?sslmode=require"
   ```
   > **Por que isso basta e é seguro:** O `load_dotenv` em `banco.py`, `seguranca.py`, `servico_analise.py` e `alembic/env.py` roda com `override=False`. Portanto, qualquer variável de ambiente já definida na sessão ativa do PowerShell tem precedência sobre o arquivo `.env`. O arquivo `.env` não precisa ser editado.
   > **Backup garantido:** O próprio branch criado no Neon serve como cópia isolada de segurança do estado anterior à migração.

### 2. Diagnóstico pré-migração com script de conferência
Execute o script de verificação somente leitura para registrar o estado antes da migração:
```powershell
.\.venv\Scripts\python.exe scripts\checar_banco.py
```
*Gera o relatório com a revisão Alembic atual e as contagens existentes em cada tabela.*

### 3. Verificação do ponteiro do Alembic
Consulte a revisão registrada no banco de dados:
```powershell
.\.venv\Scripts\alembic.exe current
```
- Se a saída contiver o erro `Can't locate revision identified by 'f088c4b0bfcd'`, execute o comando de expurgo do ponteiro órfão:
  ```powershell
  .\.venv\Scripts\alembic.exe stamp --purge c0d49a3075c6
  ```
  E repita `.\.venv\Scripts\alembic.exe current` para confirmar que a revisão está em `c0d49a3075c6`.
- Se a saída já mostrar `c0d49a3075c6`, nada a fazer, prossiga para o passo 4.
- Se a saída mostrar `a1b2c3d4e5f6 (head)`, a migração já foi aplicada: pule diretamente para o passo 5.

### 4. Execução da migração estrutural
Aplique a migração de esquema:
```powershell
.\.venv\Scripts\alembic.exe upgrade head
```
Em seguida, confirme o ponteiro atual:
```powershell
.\.venv\Scripts\alembic.exe current
```
*(Deve exibir `a1b2c3d4e5f6 (head)`).*

### 5. Diagnóstico pós-migração
Execute novamente o script de conferência:
```powershell
.\.venv\Scripts\python.exe scripts\checar_banco.py
```
*Critérios de conferência:*
- As contagens de `ativos` e `cotacoes` devem bater exatamente com o número de tickers distintos e de linhas de `analises_ativos_legado`.
- A contagem de "cotações no formato antigo" deve ser igual ao total migrado (comportamento esperado; o item 3.1 trata disso dinamicamente em tempo de execução, realizando nova coleta transparente na primeira consulta).

### 6. Sincronização de sequências SERIAL
Sincronize os contadores de chave primária do PostgreSQL:
```powershell
.\.venv\Scripts\python.exe scripts\sincronizar_sequencias.py
```

### 7. Teste de fumaça (Smoke Test)
Inicie a aplicação localmente apontada para o branch de ensaio:
```powershell
.\.venv\Scripts\uvicorn.exe main:app --port 8000
```
Navegue e teste os seguintes fluxos no navegador:
1. Abrir `http://localhost:8000/` (Home com ativos catalogados).
2. Abrir `http://localhost:8000/analisar?ticker=PETR4` (um ticker que já existia no banco: deve carregar sem erro 500, fazendo a coleta nova transparente e atualizando o formato no banco).
3. Abrir `http://localhost:8000/analisar?ticker=WEGE3` (um ticker novo).
4. Fazer login e abrir `http://localhost:8000/watchlist`.

### 8. Aplicação no branch principal de produção
Após validar com sucesso no branch de ensaio, repita os passos 2 a 7 contra o banco principal:
1. Abra uma nova janela do PowerShell (sem a variável `$env:DATABASE_URL`, para que a aplicação utilize a connection string de produção do `.env`).
2. Execute os passos 2, 3, 4, 5, 6 e 7. O branch criado no passo 1 serve como backup de segurança de antes da migração.

---

## 4. Textos Antes / Depois (Itens 3.3, 3.5, 3.6 e 3.7)

### Item 3.3 — Mensagens de E-mail / Terminal (`main.py` e `seguranca.py`)

| Rota / Contexto | Antes | Depois (com SMTP configurado) | Depois (sem SMTP — modo local) |
|---|---|---|---|
| `POST /cadastro` (conta nova) | `Conta criada! Verifique seu e-mail (ou o terminal de teste) para confirmar o cadastro.` | `Conta criada! Enviamos um link para o seu e-mail para confirmar o cadastro.` | `Conta criada! Verifique seu e-mail (ou confira o terminal de teste) para confirmar o cadastro.` |
| `POST /cadastro` (atualização de conta legada) | `Cadastro atualizado com sucesso! Confira o terminal para confirmar o e-mail.` | `Cadastro atualizado com sucesso! Enviamos um link para o seu e-mail.` | `Cadastro atualizado com sucesso! Confira o terminal para confirmar o e-mail.` |
| `POST /conta/reenviar-confirmacao` | `Link de confirmacao reenviado!(Confira o terminal ou e-mail).` | `Enviamos um link para o seu e-mail.` | `Link de confirmação reenviado! Confira o terminal ou seu e-mail.` |
| `POST /esqueci-senha` | `Se o e-mail estiver cadastrado, enviamos o link de recuperação (confira também o console em modo local).` | `Se o e-mail estiver cadastrado, enviamos um link para o seu e-mail.` | `Se o e-mail estiver cadastrado, enviamos o link de recuperação (confira o terminal em modo local).` |

### Item 3.5 — Textos Regulatórios / CVM (`servico_analise.py` e `templates/home.html`)

| Local | Antes | Depois |
|---|---|---|
| `servico_analise.py` (Prompt Gemini) | `Você é um analista CNPI sênior...` / menção a `(CNPI)` | `Você é um especialista em análise fundamentalista de ativos listados na B3.` *(sem menção a CNPI)* |
| `templates/home.html` (Hero Headline) | `Chega de planilhas confusas e relatórios cansativos. Tenha um auditor financeiro inteligente analisando qualquer ação ou FII da B3 para você.` | `Chega de planilhas confusas e relatórios cansativos. Tenha um resumo financeiro inteligente analisando qualquer ação ou FII da B3 para você.` |
| `templates/home.html` (Atalhos Rápidos) | `⚡ Degustação Imediata: Clique para auditar` | `⚡ Degustação Imediata: Clique para analisar` |
| `templates/home.html` (Pilar 1) | `Auditoria Automática de Balanços` | `Leitura Automática de Balanços` |

### Item 3.6 — Pilar 3 e Sinal de Fluxo (`servico_analise.py`, `templates/home.html`, `templates/resultado.html`)

| Local | Antes | Depois |
|---|---|---|
| `templates/home.html` (Subtítulo da Hero) | `Raio-X fundamentalista em segundos, Preço Justo de Graham, Preço Teto de Bazin e fluxo institucional sem ruído de mercado.` | `Raio-X fundamentalista em segundos, Preço Justo de Graham, Preço Teto de Bazin e indicadores de volume e risco sem ruído de mercado.` |
| `templates/home.html` (Título e texto do Pilar 3) | **Monitor de Fluxo & Risco B3**: `Acompanhe a pressão institucional de compra e venda, volume financeiro diário e liquidez real do ativo para tomar decisões com dados concretos.` | **Monitor de Volume & Risco B3**: `Acompanhe o volume do dia, média de 10 dias e 3 meses, faixa de 52 semanas e beta de volatilidade do ativo para tomar decisões com dados concretos.` |
| `templates/resultado.html` (Cabeçalho da tabela de 5 pregões) | `Sinal de Fluxo` | `Comportamento no Dia` |
| `servico_analise.py` (`extrair_fluxo_pregoes`) | `"Entrada Compradora"` / `"Saída Vendedora"` / `"Neutro"` | `"Dia de alta"` / `"Dia de queda"` / `"Estável"` (acrescentando `" com volume acima da média"` quando `volume > média`) |
| `servico_analise.py` (`Status_Fluxo` em `extrair_raio_x_b3`) | `"Forte Entrada de Volume (...)"` / `"Forte Pressão Vendedora (...)"` | `"Dia de alta com volume acima da média (...)"` / `"Dia de queda com volume acima da média (...)"` / `"Estável com volume acima da média (...)"` |

### Item 3.7 — Política de Privacidade (`templates/legal.html`)

| Antes | Depois |
|---|---|
| Listava apenas: *"nome, endereço de e-mail e preferências de ativos adicionados voluntariamente à sua Watchlist privada"*. Não mencionava perfil, data de aceite, notas, uso do IP, prazo de retenção de 90 dias nem e-mail de contato LGPD. | Lista explicitamente: **nome, endereço de e-mail, perfil de investidor, data e hora de aceite dos termos de uso, e itens da Watchlist privada (ativos monitorados, preço-alvo de alerta e anotações pessoais)**; **uso do endereço IP exclusivamente em tempo de execução para limite de uso (rate limiting), sem armazenamento permanente**; **prazo de retenção de 90 dias para o histórico de cotações e análises** (preservando a última cotação de referência de cada ativo); e canal de contato LGPD **`[E-MAIL DE CONTATO]`**. |

---

## 5. Razões de Contraste WCAG 2.1 (Item 4.2) e Classes CSS

### 5.1 Razões de Contraste Calculadas

Fundos de referência em `templates/base.html`:
- `--bg`: `#070b12` (`rgb(7, 11, 18)`, luminância relativa $L = 0,0032$)
- `--card`: `rgba(15, 23, 42, 0.78)` composto sobre `--bg` = `rgb(13.24, 20.36, 36.72)` (luminância relativa $L = 0,0073$)

| Variável CSS | Valor Anterior | Valor Novo | Contraste sobre `--bg` (`#070b12`) | Contraste sobre `--card` resolvido | Status WCAG AA (≥ 4,5:1) |
|---|---|---|---|---|---|
| `--texto` | `#f8fafc` | `#f8fafc` | **18,77 : 1** | **17,43 : 1** | Aprovado (AAA) |
| `--suave` | `#94a3b8` | `#cbd5e1` | **13,20 : 1** | **12,26 : 1** | Aprovado (AAA) |
| `--apagado` | `#64748b` *(4,11:1 sobre `--bg` e 3,82:1 sobre `--card`)* | `#94a3b8` | **7,69 : 1** | **7,14 : 1** | Aprovado (AA/AAA) |
| `--destaque` | `#38bdf8` | `#38bdf8` | **9,45 : 1** | **8,78 : 1** | Aprovado (AAA) |
| `--pos` | `#34d399` | `#34d399` | **11,63 : 1** | **10,80 : 1** | Aprovado (AAA) |
| `--neg` | `#fb7185` | `#fb7185` | **7,33 : 1** | **6,81 : 1** | Aprovado (AA) |

### 5.2 Classes CSS Adicionadas e Classes Órfãs Tratadas

- **Classes adicionadas e aplicadas nos templates** (conforme prometido no `PROJECT.md` anterior):
  - `.destaque`: definida em `templates/base.html` e aplicada nos links de tickers em `templates/home.html` e `templates/watchlist.html`.
  - `.badge-destaque`: definida em `templates/base.html` e aplicada nos selos de classe de ativo em `templates/home.html` e `templates/resultado.html`.
- **Classes anteriormente órfãs em `templates/base.html`**:
  - `.badge-pos` e `.badge-neg` estavam declaradas em `base.html` sem nenhum uso nos templates. Passaram a ser utilizadas nos selos visuais de margem de segurança de Graham e Bazin em `templates/resultado.html`.
  - Nenhuma classe órfã resta nos blocos `<style>` (verificado automaticamente por `tests/test_visual_consistency.py::test_4_2_4_3_classes_css_e_templates_sem_orfas`, com exceção documentada apenas para `.ativo` e `.vazio`, que são atribuídas via JavaScript no DOM do autocomplete).

---

## 6. Capturas Antes / Depois (Item 4.4)

> **Nota sobre o ambiente:** O servidor MCP do Chrome DevTools não estava montado neste ambiente (apenas o `science_biomolecular_structure_viewer_mcp` estava ativo), mas utilizamos o navegador **Microsoft Edge em modo Headless** (`msedge.exe --headless`) contra o servidor local `uvicorn` para gerar todas as 21 capturas nas três larguras exigidas (`375×812`, `768×1024` e `1440×900`).

Arquivos gerados em `docs/capturas/depois/`:
- `docs/capturas/depois/home_375x812.png`, `docs/capturas/depois/home_768x1024.png`, `docs/capturas/depois/home_1440x900.png`
- `docs/capturas/depois/resultado_acao_petr4_375x812.png`, `docs/capturas/depois/resultado_acao_petr4_768x1024.png`, `docs/capturas/depois/resultado_acao_petr4_1440x900.png`
- `docs/capturas/depois/resultado_fii_hglg11_375x812.png`, `docs/capturas/depois/resultado_fii_hglg11_768x1024.png`, `docs/capturas/depois/resultado_fii_hglg11_1440x900.png`
- `docs/capturas/depois/busca_ambigua_sanepar_375x812.png`, `docs/capturas/depois/busca_ambigua_sanepar_768x1024.png`, `docs/capturas/depois/busca_ambigua_sanepar_1440x900.png`
- `docs/capturas/depois/login_375x812.png`, `docs/capturas/depois/login_768x1024.png`, `docs/capturas/depois/login_1440x900.png`
- `docs/capturas/depois/cadastro_375x812.png`, `docs/capturas/depois/cadastro_768x1024.png`, `docs/capturas/depois/cadastro_1440x900.png`
- `docs/capturas/depois/termos_375x812.png`, `docs/capturas/depois/termos_768x1024.png`, `docs/capturas/depois/termos_1440x900.png`

---

## 7. Decisões Tomadas por Conta Própria e Achados Extras

1. **Refinamento do `PADRAO_TICKER` para evitar falsos positivos alfanuméricos (`"xyz123"`):**
   - **Achado:** A expressão regular antiga `^[A-Z0-9]{4}\d{1,2}$` aceitava 3 letras seguidas de 3 dígitos (ex.: `"XYZ123"` era lido como raiz `"XYZ1"` + classe `"23"`), fazendo `normalizar_ticker("xyz123")` passar sem consultar o catálogo.
   - **Decisão:** Ajustamos para `^[A-Z][A-Z0-9]{2}[A-Z]\d{1,2}$` (regra oficial da B3: o 4º caractere do código do emissor é sempre uma letra, mesmo em códigos como `B3SA3` ou `G2DI33`). Assim, `"xyz123"` cai na busca e levanta `ErroAtivo` imediatamente.
2. **Ativação de `PRAGMA foreign_keys=ON` no SQLite de testes:**
   - **Decisão:** Em `banco.py`, adicionamos um listener de conexão para o SQLite ativando `PRAGMA foreign_keys=ON`, garantindo que o SQLite valide integridade referencial (`ON DELETE CASCADE` / `ON DELETE SET NULL`) da mesma forma que o PostgreSQL em produção.
3. **Compatibilidade de migrações Alembic com SQLite (`render_as_batch=True` e `batch_alter_table`):**
   - **Decisão:** Como o SQLite não suporta `ALTER TABLE ... DROP CONSTRAINT` diretamente, configuramos `batch_alter_table` nas migrações `c0d49a3075c6` e `a1b2c3d4e5f6` e demos nome explícito à FK em `6c6b96237d69`, permitindo que `alembic upgrade head` e `alembic downgrade -1` rodem tanto no PostgreSQL quanto no SQLite.
4. **Cálculo prévio de largura das barras de margem de segurança (`Barra_Graham_Pct` e `Barra_Bazin_Pct`):**
   - **Decisão:** Para cumprir rigorosamente a regra da Fase 2.a (*"o template nunca faz conta"*), a largura percentual da barra visual de Graham/Bazin é pré-calculada em `servico_analise.py` (e nas fixtures) em vez de usar expressões aritméticas no Jinja.

---

## 8. Propostas de Arquitetura Não Implementadas (Futuras Evoluções)

1. **Redis para Rate Limiting e Cooldown Compartilhados entre Workers:**
   - Hoje o `Limiter` (padrão `memory://`) e o `_LOCKS_POR_TICKER` atuam por processo. Como o `Procfile` sobe 4 workers Gunicorn (`-w 4`), basta provisionar uma instância Redis gratuita (ex.: Upstash) e definir `RATE_LIMIT_STORAGE_URI=redis://...` no `.env` para unificar a contagem entre os 4 workers.
2. **Fila Assíncrona em Background para Relatórios da IA:**
   - Embora o paralelismo com `ThreadPoolExecutor` no `yfinance` tenha reduzido o tempo de coleta de dados da B3, a geração do texto pelo Gemini ainda ocorre durante a requisição HTTP (em cache miss). Uma evolução natural é retornar os indicadores quantitativos imediatamente na página e carregar o parecer da IA via endpoint assíncrono (`HTMX` / `fetch`) em segundo plano.
3. **Provedor HTTP de E-mail Transacional (Resend / SendGrid / Amazon SES):**
   - Muitas plataformas de PaaS bloqueiam portas SMTP de saída (`587`/`465`) em planos gratuitos. Substituir ou complementar `smtplib` por uma chamada HTTPS simples à API do Resend ou SES evita bloqueios de rede em produção.

---

## 9. Situação dos Itens de Segurança ("Fora desta Rodada")

Conforme determinado na especificação (*"Não implemente a seção 6 da revisão... Apenas confirme no relatório, item por item, se cada um continua existindo depois das suas mudanças, e se alguma das suas mudanças o afetou"*):

| # | Item de Segurança (Seção 6 da Revisão) | Continua Existindo? | Foi Afetado pelas Mudanças desta Rodada? |
|---|---|---|---|
| **6.1** | `SECRET_KEY` padrão hardcoded como fallback em `seguranca.py` quando ausente no `.env` | **Sim** | **Não.** Mantido exatamente como estava (com `log.warning`). |
| **6.2** | Link de confirmação / redefinição de senha impresso no console (`stdout`) | **Parcialmente mitigado no item 3.3** | **Sim (positivamente pelo item 3.3):** `enviar_email_transacional` agora só imprime o e-mail/link no terminal quando `not email_configurado()` (modo local sem SMTP). Quando o SMTP está configurado no `.env`, o link não é mais impresso no log. |
| **6.3** | Contas legadas da Fase 1 com `senha_hash == ""` podem ter senha definida por quem souber o e-mail no `/cadastro` | **Sim** | **Não.** O bloco `if not usuario_existente.senha_hash:` em `main.py:/cadastro` permaneceu ativo para compatibilidade com registros legados. |
| **6.4** | Token de redefinição de senha (`itsdangerous`) pode ser reutilizado dentro da janela de 1 hora (stateless) | **Sim** | **Não.** O token continua stateless via `URLSafeTimedSerializer` com `max_age=3600`. |
| **6.5** | Links `javascript:` em Markdown no relatório da IA (`renderizar_markdown` escapa `<` mas o parser Markdown ainda converte `[x](javascript:...)`) | **Sim** | **Não.** `renderizar_markdown` em `main.py` permaneceu inalterada. |
| **6.6** | Ausência de token CSRF nos formulários `POST` (`/watchlist/adicionar`, `/watchlist/remover`, `/conta/excluir`) | **Sim** | **Não.** O cookie de sessão continua com `httponly=True` e `samesite="lax"`, sem token CSRF dedicado nos formulários. |
| **6.7** | Parâmetro `?msg=` / `?erro=` refletido nos banners de alerta da página | **Sim** | **Não.** Continua escapado contra HTML/XSS pelo autoescape do Jinja2, mas o texto passado na query string continua sendo exibido no banner. |

---

## 10. Histórico de Commits da Rodada (`git log --oneline`)

```text
dbcb93e fase 6: correcoes pos-conferencia
e6a5d42 docs: corrige comando alembic stamp --purge no relatorio
26c25f3 docs: relatorio de implementacao completo e capturas visuais
75ed2c1 fase 5: cooldown de atualizacao, paralelismo yfinance e fuso horario
81b5cd9 fase 4: pagina de resultado e consistencia visual wcag
41b473e fase 3: busca com prioridade, caminhos, erros gemini e textos
c446199 fase 2: servico devolve numeros e reestruturacao do banco
08ca5c3 fase 1: erros que derrubam o site e rate limit
151e6a3 fase 0: versionamento e base de testes em sqlite
95e9d89 estado inicial antes da revisao de 20/10
```

---

## 11. Rodada de Correções (Fase 6)

### 11.1 Tabela Item por Item da Fase 6

| Item | Descrição | Status | Arquivos Alterados | Commit | Teste que Cobre | Conferência (Auditor) |
|---|---|---|---|---|---|---|
| **3.1** | Cache de 24h ignora cotações migradas sem `Metadados` via helper `_cotacao_tem_dados_novos` em `_buscar_cache_ativo`, forçando nova coleta e evitando Erro 500 em `resultado.html` | `feito` | `main.py` | `dbcb93e` | `tests/test_fase6_correcoes.py::test_6_1_*` | Assinado |
| **3.2** | Paralelismo yfinance isolado por future com instâncias separadas de `yf.Ticker`, eliminando colisão de concorrência em `PriceHistory` | `feito` | `servico_analise.py` | `dbcb93e` | `tests/test_fase6_correcoes.py::test_6_2_*` | Assinado |
| **3.3** | Script `scripts/checar_banco.py` estritamente somente leitura (SELECT) e reescrita determinística do procedimento de deploy no Neon com branch de ensaio | `feito` | `scripts/checar_banco.py`, `RELATORIO_IMPLEMENTACAO.md` | `dbcb93e` | `tests/test_fase6_correcoes.py::test_6_3_*` | Assinado |
| **3.4** | Inclusão de `uvicorn-worker==0.4.0` em `requirements.txt`, atualização do `Procfile` para `uvicorn_worker.UvicornWorker` e ajuste em `test_5_5` | `feito` | `requirements.txt`, `Procfile`, `tests/test_fase5_melhorias.py` | `dbcb93e` | `tests/test_fase5_melhorias.py::test_5_5_*` | Assinado |
| **3.5** | Declaração de transparência sobre o `.env` (chaves `CONFIAR_PROXY` e `RATE_LIMIT_STORAGE_URI` sincronizadas na rodada anterior; `.env` intocado na fase 6) | `feito` | `RELATORIO_IMPLEMENTACAO.md` | `dbcb93e` | Auditoria estática de metadados | Assinado |
| **3.6** | Substituição de travessão (`—`) por dois pontos (`:`) no rótulo de pregão (`"Pregão em andamento: volume até agora"`) | `feito` | `servico_analise.py` | `dbcb93e` | `tests/test_fase5_melhorias.py::test_5_4_*` | Assinado |

### 11.2 Saídas dos Testes Falhando Antes da Correção e Passando Depois

#### Item 3.1 — `test_6_1_cache_ignora_registro_migrado_no_formato_antigo`

**Falha antes da correção (código original com `_buscar_cache_ativo` devolvendo cotação legada):**
```text
>       <div class="{{ 'pos' if var_dia >= 0 else 'neg' }}" style="font-weight:700;">
E       TypeError: '>=' not supported between instances of 'str' and 'int'

templates\resultado.html:157: TypeError
FAILED tests/test_fase6_correcoes.py::test_6_1_cache_ignora_registro_migrado_no_formato_antigo
```

**Sucesso após a correção:**
```text
tests/test_fase6_correcoes.py::test_6_1_cache_ignora_registro_migrado_no_formato_antigo PASSED [ 33%]
```

---

#### Item 3.2 — `test_6_2_yfinance_paralelo_nao_compartilha_objeto_ticker`

**Falha antes da correção (código original compartilhando a mesma instância de `yf.Ticker`):**
```text
>       assert len(instancias_criadas) >= 2, (
            f"Esperava múltiplas instâncias isoladas de yf.Ticker, mas apenas {len(instancias_criadas)} foi criada."
        )
E       AssertionError: Esperava múltiplas instâncias isoladas de yf.Ticker, mas apenas 1 foi criada.
E       assert 1 >= 2

tests\test_fase6_correcoes.py:222: AssertionError
FAILED tests/test_fase6_correcoes.py::test_6_2_yfinance_paralelo_nao_compartilha_objeto_ticker
```

**Sucesso após a correção:**
```text
tests/test_fase6_correcoes.py::test_6_2_yfinance_paralelo_nao_compartilha_objeto_ticker PASSED [ 66%]
```

---

### 11.3 Saída Completa de `python -m pytest -q`

```text
.....................................                                    [100%]
============================== warnings summary ===============================
.venv\Lib\site-packages\fastapi\testclient.py:1
  C:\Users\pedro.000\OneDrive\Área de Trabalho\app_investimentos\.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
37 passed, 1 warning in 2.38s
```

---

### 11.4 Declaração sobre o `.env` (Item 3.5)

O arquivo `.env` do projeto foi modificado em 20/10 às 11:26 (horário de Brasília) durante a rodada anterior para sincronização com as novas variáveis opcionais documentadas no `.env.example`:
- `CONFIAR_PROXY`: adicionada para instruir o slowapi sobre a leitura do cabeçalho `X-Forwarded-For` atrás de proxies reversos.
- `RATE_LIMIT_STORAGE_URI`: adicionada para parametrização do armazenamento do rate limit em memória ou Redis.

Nenhum valor secreto (senhas de banco, tokens de API ou chaves de criptografia) foi alterado, exposto ou lido. Nesta rodada (Fase 6), o arquivo `.env` não sofreu qualquer alteração, mantendo integridade e sigilo absolutos.

