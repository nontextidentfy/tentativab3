# Diretrizes e Regras do Projeto — Investidor IA

Este repositório segue regras inegociáveis de produto, segurança e engenharia de software para garantir a melhor experiência ao investidor comum e a integridade da aplicação.

---

## 1. Princípios de Produto e UX (O Investidor Comum em Primeiro Lugar)

### R1. Princípio da Tradução Absoluta (Zero Economês Isolado)
* **Regra:** Nenhum dado financeiro ou múltiplo contábil (`P/L`, `P/VP`, `ROE`, `Graham`, `Bazin`, etc.) pode ser apresentado como um número seco ou isolado.
* **Exigência:** Todo indicador precisa vir acompanhado de:
  1. *O que é* em analogia simples do cotidiano.
  2. *Como a empresa está* (barata, cara, endividada, geradora de caixa) em relação ao seu próprio histórico e aos seus pares setoriais.
  3. *O impacto prático no bolso* do usuário.

### R2. O Detector de Mitos & Simulação Realista (Anti-Guru)
* **Regra:** O leigo é atraído por promessas irrealistas de redes sociais (ex.: "5% ao mês garantido"). O produto deve oferecer um simulador factual de aportes (R$ 1k, R$ 5k, R$ 10k) baseado em dados históricos auditados de proventos e cotação.
* **Exigência:** O sistema confronta visualmente a ilusão da internet com a realidade da Bolsa, Poupança e CDI.

### R3. Busca por Marcas da Vida Real
* **Regra:** O usuário comum pesquisa por marcas comerciais que consome na rua (ex.: "Posto Ipiranga", "Banco do Brasil", "Magalu", "Sicredi").
* **Exigência:** A barra de busca deve mapear sinônimos comerciais para seus respectivos tickers da B3. Para marcas sem capital aberto (como cooperativas de crédito), o sistema deve explicar didaticamente o motivo e sugerir alternativas listadas do setor.

### R4. Estrutura do Dossiê em 4 Blocos
* **Bloco 1 (Aberto):** O que a empresa faz, como ganha dinheiro e quem a controla.
* **Bloco 2 (Aberto):** Simulador Realidade vs. Guru com histórico factual.
* **Bloco 3 (Pro):** Raio-X dos concorrentes diretos e forças macroeconômicas.
* **Bloco 4 (Pro):** Cálculos densos e modelagem de cenários de 1 a 5 anos traduzidos por IA.

---

## 2. Guardrails Técnicos, Segurança e Arquitetura

### R5. Proteção Absoluta do `.env`
* Sob nenhuma hipótese o arquivo `.env` deve ser exposto no chat, versionado no Git ou lido na íntegra. Novas variáveis devem ser adicionadas apenas ao `.env.example` com valores genéricos ou vazios.

### R6. Isolamento do Banco Neon (Produção)
* Nenhuma migração ou comando de alteração estrutural deve ser executado diretamente contra o banco principal sem ensaio prévio em branch temporário no Neon ou SQLite em memória.

### R7. Metodologia TDD Estrita em Correções
* Toda correção de defeito deve iniciar com a criação de um caso de teste automatizado (`pytest`) que reproduz a falha antes de qualquer alteração no código de produção.

### R8. Isolamento de Threads no `yfinance`
* Chamadas concorrentes via `ThreadPoolExecutor` devem instanciar objetos `yf.Ticker` separados para cada thread, evitando sobrescrita de caches internos (`PriceHistory._dividends`).
