# Relatório de Ajustes no Dossiê em 4 Blocos (`dossie v2`)

**Data de conclusão:** 21 de outubro de 2026

---

## 1. Resumo das Alterações por Passo

### Passo 0 — Higiene do repositório (Commit `c8e7e4e`)
Adicionamos `.claude/` ao `.gitignore`, removemos a worktree temporária `.claude/worktrees/friendly-davinci-72a4a7` junto com a branch auxiliar `claude/friendly-davinci-72a4a7`, e registramos o estado base do repositório no commit `dossie v1: 4 blocos, busca por marcas, paywall` (alterando `.gitignore`, `alembic/versions/b2c3d4e5f6a7_adiciona_plano_em_usuarios.py`, `banco.py`, `main.py`, `servico_analise.py`, `templates/home.html`, `templates/nao_listada.html`, `templates/resultado.html`, `tests/conftest.py`, `tests/fixtures/ativos.py`, `tests/test_e2e_suite.py` e `tests/test_visual_consistency.py`).

### Passo 1 — Simulador com histórico real no Bloco 2 (Commit `7ed3542`)
Substituímos a conta de juros compostos com cotação fixa por uma simulação histórica real sobre os últimos 5 anos do ativo em `servico_analise.py` (`_coletar_historico_mensal_5a`, executada em instância própria de `yf.Ticker` dentro do `ThreadPoolExecutor` de `extrair_raio_x_b3`, e `calcular_simulador_realidade_vs_guru`), exibindo quantas cotas teriam sido compradas há 1, 2 ou 5 anos, quanto foi efetivamente pago em proventos no período e a frase factual *"Se você tivesse investido R$ X em {mês/ano}, hoje teria R$ Y, sendo R$ Z em proventos"* (inclusive quando o retorno é negativo) ou *"listada há menos de N anos"* para empresas com histórico curto. Integramos também a consulta pública ao SGS do Banco Central (`obter_serie_sgs_bcb`, séries `12` para CDI e `195` para Poupança, com cache em memória de 24h e timeout de 5s) e fallback transparente rotulado como *"aproximação com taxa fixa de X% a.a."*, atualizando `templates/resultado.html`, `tests/conftest.py`, `tests/fixtures/ativos.py` e `tests/test_e2e_suite.py`.

### Passo 2 — Rivais com dados reais no Bloco 3 (Commit `bd02596`)
Apagamos o dicionário estático `DADOS_REFERENCIA_RIVAIS`, o fallback genérico `["ITUB4", "VALE3", "PETR4"]` e a geração de rivais fictícios (`preco: 30.0, p_l: 8.0`) em `servico_analise.py`. Mantivemos apenas `PARES_SETORIAIS` (listas de tickers reais por setor) e implementamos `_coletar_rival_yfinance` para buscar cotação, `P/L`, `P/VP`, `DY`, `ROE`, `Margem Líquida` e `Dív. Líq./EBITDA` em paralelo via `ThreadPoolExecutor` com instâncias isoladas de `yf.Ticker` e timeout de 5s. Em `templates/resultado.html` e `tests/test_e2e_suite.py`, removemos a coluna opinativa *"Veredito Estratégico"*, exibimos *"indisponível no momento"* caso a coleta de algum rival falhe e mostramos *"Ainda não mapeamos os concorrentes desta empresa."* quando o ticker não possui pares mapeados.

### Passo 3 — De "Cenários da IA" para "Simule o futuro com as suas premissas" no Bloco 4 (Commit `4153bc3`)
Removemos a função `calcular_cenarios_ia_1_a_5_anos`, os valores inventados de fallback (`preco = 30.0`, `LPA = preco / 8.0`, `ROE = 0.15`) e os três cartões pré-montados (*"Cenário Pessimista"*, *"Cenário Realista"* e *"Cenário Otimista"*) em `servico_analise.py`, `main.py` e `templates/resultado.html`. Em seu lugar, implementamos `simular_premissas_usuario` e a calculadora interativa **"Simule o futuro com as suas premissas"**, na qual o próprio usuário escolhe a hipótese de crescimento anual do lucro (`-5%`, `0%`, `+10% ao ano`), o múltiplo `P/L` futuro (`6`, `10`, `15`) e o prazo (`1 ano`, `5 anos`), bloqueando a simulação com explicação clara quando `LPA <= 0` (*"não dá para simular: a empresa não teve lucro nos últimos 12 meses"*) ou sem cotação, exibindo o aviso fixo *"Isto não é previsão nem recomendação. É só a conta das premissas que você escolheu."* e trocando o selo dos múltiplos para *"Leitura"* em `traduzir_multiplos_ativo`.

### Passo 4 — Acesso aos Blocos 3 e 4 para conta gratuita logada sem `blur` (Commit `ed75aa2`)
Removemos o falso paywall baseado em CSS `filter: blur(8px)` e transformamos os Blocos 3 e 4 em conteúdo aberto para qualquer usuário autenticado com conta gratuita em `templates/resultado.html` e `tests/test_e2e_suite.py`. Visitantes anônimos não recebem o HTML dos Blocos 3 e 4 no código-fonte da página (`{% if usuario %}` no Jinja2) e visualizam apenas o cartão limpo *"Crie sua conta gratuita para ver a comparação com os concorrentes e o simulador de premissas"* com botões para `/cadastro` e `/login`, enquanto usuários logados (inclusive `plano="free"`) visualizam os 4 blocos completos. A coluna `usuarios.plano` e a propriedade `eh_pro` foram preservadas no código Python para uso futuro quando houver checkout integrado.

### Passo 5 — Textos curados com data e fallback honesto no Bloco 1 (Commit `c190a82`)
Revisamos todas as entradas de `MAPA_PERFIL_DIDATICO` em `servico_analise.py`, removendo porcentagens fixas de participação acionária em `estrutura_controle`, corrigindo as marcas de `ITUB4` (retirando a menção incorreta ao C6 Bank) e adicionando `"revisado_em": "2026-10"` com exibição discreta de *"Perfil revisado em outubro de 2026"* em `templates/resultado.html`. Para tickers fora de `MAPA_PERFIL_DIDATICO`, `obter_dossie_didatico` deixou de gerar as quatro frases genéricas de enchimento e passou a exibir o aviso honesto *"Ainda não escrevemos o perfil em linguagem simples desta empresa."* acompanhado do `longBusinessSummary` oficial rotulado como *"Descrição oficial da empresa (em inglês, vinda do Yahoo Finance)"*.

### Passo 6 — Testes, capturas e relatório
Atualizamos `tests/test_fase2_dados_banco.py` para validar `command.upgrade(alembic_cfg, "head")` incluindo a coluna `"plano"` em `usuarios` (mantendo o teste `b24` em `tests/test_e2e_suite.py`), executamos a suíte completa de testes automatizados em SQLite isolado, geramos as novas capturas visuais em `docs/capturas/dossie_v2/` nas três resoluções (`375x812`, `768x1024` e `1440x900`) e consolidamos este relatório em `docs/RELATORIO_AJUSTES_DOSSIE.md`.

---

## 2. Contagem de Testes Automatizados

- **Total da suíte (`pytest` em SQLite em memória/arquivo temporário):** **109 testes passando (`109 passed`)**, `0 falhas`.
  - Inclui os 15 novos testes TDD dos Passos 1 a 5 em `tests/test_e2e_suite.py`, o teste de migração `b24` (`b2c3d4e5f6a7`), o teste de `upgrade head` com `"plano"` em `tests/test_fase2_dados_banco.py` e a auditoria de contraste WCAG + classes CSS sem órfãs em `tests/test_visual_consistency.py`.

---

## 3. Lista de Palavras e Rótulos Removidos da Interface

Foram eliminados da interface (`templates/resultado.html` e retornos de `servico_analise.py` para os Blocos 1 a 4):
- `"Preço Alvo"` / `"preço-alvo"` / `"preco_alvo"`
- `"Projeções Matemáticas da IA em 3 Cenários"` / `"cenários preditivos da IA"` / `"Modelagem Preditiva"` / `"simulações preditivas"`
- `"Cenário Pessimista"` / `"Cenário Realista"` / `"Cenário Otimista"`
- `"Veredito Estratégico"` (na tabela de concorrentes do Bloco 3)
- `"👑 Camada Pro"` / `"Camada Pro Exclusiva"` / `"👑 Membro Pro"` / `"⚡ Desbloquear Versão Pro"` / `"Já sou Pro (Entrar)"` / classes `.paywall-*` e `.conteudo-borrado`
- Menção incorreta a `"C6 Bank (participação)"` em `ITUB4` e porcentagens societárias fixas em `MAPA_PERFIL_DIDATICO`

---

## 4. Instrução Única para o Banco Real antes de Subir o Servidor

Antes de iniciar o servidor apontando para o banco principal, execute apenas:

```powershell
alembic upgrade head
```

*(Esse comando aplica a revisão `b2c3d4e5f6a7`, que adiciona a coluna `usuarios.plano` com `server_default='free'`.)*
