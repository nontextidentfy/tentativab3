# Relatório de Conclusão da Fase 7 — Segurança, E-mail e Preparação para o Render

**Data de conclusão:** 27 de outubro de 2026  
**Status da Suíte de Testes:** **161 testes passando (`161 passed`)**, 0 falhas.

---

## 1. Resumo das Alterações por Passo

### Passo 0 — Higiene da Árvore de Trabalho (Commit `df80bb9`)
- **Mensagem:** `wip: ajustes de 23/10`
- **Arquivos:** `alembic/versions/c3d4e5f6a7b8_adiciona_tabelas_fase2.py`, `banco.py`, `main.py`, `servico_analise.py`, `templates/base.html`, `templates/home.html`, `templates/resultado.html`, `tests/test_e2e_suite.py`, `tests/test_fase2_dados_banco.py`, `tests/test_melhorias_site.py`, `tests/test_revisao_frontend.py`, `tests/test_visual_consistency.py`.
- **Descrição:** Commit das alterações prévias pendentes na árvore de trabalho antes de iniciar as modificações de segurança da Fase 7.
- **Testes antes:** 146 testes passando.

---

### Passo 1 — Proteção contra CSRF nos POSTs (Commit `bbbd640`)
- **Mensagem:** `fase 7.1: verificacao de origem (CSRF) em todos os POSTs`
- **Arquivos modificados/criados:**
  - `main.py`: Implementação da verificação de origem `_origem_permitida(request)` e middleware HTTP `bloquear_post_de_outra_origem`. Avalia prioritariamente o cabeçalho `Sec-Fetch-Site` (`same-origin` ou `none`); em caso de ausência, valida o host do cabeçalho `Origin` contra `request.url.hostname` (e `APP_BASE_URL` em produção). Requisições sem ambos os cabeçalhos (como clientes legados e testes em memória) continuam passando. Bloqueios retornam HTTP 403.
  - `templates/erro.html`: Criação da página padronizada de erro HTTP 403 com a mensagem de segurança: *"Pedido bloqueado por segurança. Abra o site de novo e tente outra vez."*
  - `tests/test_fase7_seguranca_email.py`: Criação do arquivo de testes da Fase 7 cobrindo POST com origem maliciosa (403), mesma origem (303/200), sem cabeçalho de origem (303/200) e Sec-Fetch-Site externo (403).
- **Testes após o passo:** 150 testes passando (+4).

---

### Passo 2 — Sessões Vinculadas ao Carimbo de Senha e Proteção de Contas Antigas (Commit `0b07027`)
- **Mensagem:** `fase 7.2: sessoes vinculadas ao carimbo de senha e protecao de contas antigas`
- **Arquivos modificados:**
  - `seguranca.py`: Atualização de `definir_cookie_sessao` para injetar o campo `"ps"` com `obter_carimbo_senha(usuario.senha_hash)`. Em `obter_usuario_atual`, se o cookie possuir `"ps"`, valida se é idêntico ao carimbo atual do usuário; caso a senha tenha sido redefinida, invalida a sessão automaticamente. Cookies antigos sem `"ps"` permanecem aceitos até a expiração natural.
  - `main.py`:
    - Em `processar_cadastro`: caso o e-mail pertença a uma conta pré-existente sem senha cadastrada (`senha_hash` vazio), o sistema não sobrescreve nem loga o usuário; dispara o link seguro de recuperação e apresenta a mensagem informativa: *"Esta conta já existe. Enviamos ao e-mail cadastrado um link para definir a senha."*
    - Em `processar_redefinir_senha`: após salvar o novo hash de senha no banco, limpa expressamente o cookie de sessão na resposta via `auth.limpar_cookie_sessao(resp)`.
  - `tests/test_fase7_seguranca_email.py`: Adição de testes automatizados para morte da sessão antiga após redefinição de senha, proteção de conta sem senha no cadastro e compatibilidade retroativa com cookies legados.
- **Testes após o passo:** 153 testes passando (+3).

---

### Passo 3 — Resolução do IP Real no Proxy do Render (Commit `08224bf`)
- **Mensagem:** `fase 7.3: resolucao do ip real no proxy do render`
- **Arquivos modificados:**
  - `main.py`: Em `obter_ip_cliente`, com `CONFIAR_PROXY=1`, passa a extrair o último endereço IP da lista `X-Forwarded-For` (`xff.split(",")[-1].strip()`), que é o endereço anexado de forma confiável pelo proxy do Render (eliminando risco de spoofing por cabeçalhos injetados na ponta cliente).
  - `tests/test_sprint_imediata.py` e `tests/test_fase1_erros_ratelimit.py`: Ajuste dos testes existentes (`test_t04` e `test_1_1`) para validar a seleção do último IP da cadeia.
  - `tests/test_fase7_seguranca_email.py`: Adição de teste unitário validando a imunidade contra falsificação de cabeçalho com múltiplos IPs.
- **Testes após o passo:** 154 testes passando (+1).

---

### Passo 4 — Envio Assíncrono de E-mails e Suporte a SSL 465 (Commit `03f6649`)
- **Mensagem:** `fase 7.4: envio assincrono de emails e suporte ssl 465`
- **Arquivos modificados/criados:**
  - `seguranca.py`:
    - Suporte ao formato `Investidor IA <email@dominio>` em `SMTP_FROM` via `EmailMessage`.
    - Suporte a conexão criptografada direta via `smtplib.SMTP_SSL` quando `SMTP_PORT=465` (mantendo `smtplib.SMTP` + `starttls()` em 587).
    - Timeout padrão mantido em 10 segundos para todas as conexões SMTP.
    - Falhas no envio SMTP são capturadas e registradas exclusivamente em log (`log.exception`), nunca propagando exceções ao usuário.
    - Função `validar_configuracao_smtp` que registra `log.error` na inicialização em produção listando apenas os nomes das variáveis faltantes.
    - Atualização de `disparar_confirmacao_email` e `disparar_recuperacao_senha` para aceitar `background_tasks: Optional[BackgroundTasks]`.
  - `main.py`:
    - As rotas `/cadastro`, `/esqueci-senha` e `/conta/reenviar-confirmacao` recebem `BackgroundTasks` do FastAPI e agendam o envio fora do ciclo síncrono da requisição HTTP.
    - Endpoint `/saude` atualizado para retornar `{"status": "ok", "email_configurado": bool}`.
    - Evento de inicialização `@app.on_event("startup")` validando a integridade das variáveis SMTP.
  - `scripts/testar_email.py`: Novo utilitário CLI para testar conectividade SMTP (`python scripts/testar_email.py destinatario@exemplo.com`), exibindo o erro real do smtplib sem vazar credenciais.
  - `.env.example`: Adicionadas as variáveis vazias com comentário explicativo de uma linha: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`.
  - `alembic/env.py`: Configuração de `fileConfig(..., disable_existing_loggers=False)` para evitar que migrações mutem os loggers da aplicação nos testes.
  - `tests/test_fase7_seguranca_email.py`: Adicionados 5 testes cobrindo BackgroundTasks, SMTP_FROM formatado, status de `/saude`, seleção de porta 465 (SSL) e log de produção sem SMTP.
- **Testes após o passo:** 159 testes passando (+5).

---

### Passo 5 — Configuração do Render e Cabeçalho Permissions-Policy (Commit `a4fe084`)
- **Mensagem:** `fase 7.5: configuracao do render e cabecalho permissions-policy`
- **Arquivos modificados/criados:**
  - `render.yaml`: Criação do manifesto de blueprint do Render Web Service no plano gratuito (`plan: free`):
    - `buildCommand: pip install -r requirements.txt`
    - `startCommand: alembic upgrade head && gunicorn main:app -w 2 -k uvicorn_worker.UvicornWorker --bind 0.0.0.0:$PORT`
    - `healthCheckPath: /saude`
    - Configuração de 2 workers (adequados para o limite de 512 MB de RAM).
    - Mapeamento de 13 variáveis de ambiente com `sync: false` e nenhum valor exposto (`DATABASE_URL`, `SECRET_KEY`, `APP_BASE_URL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, `CONFIAR_PROXY`, `EMPRESA_RODAPE`, `PYTHON_VERSION`).
  - `main.py`: Inclusão do cabeçalho `Permissions-Policy: camera=(), microphone=(), geolocation=()` no middleware `adicionar_cabecalhos_seguranca`.
  - `tests/test_fase7_seguranca_email.py`: Testes automatizados validando a existência e conformidade estrita do `render.yaml` e a emissão do cabeçalho `Permissions-Policy`.
- **Testes após o passo:** 161 testes passando (+2).

---

## 2. Contagem de Testes Automatizados

| Etapa | Testes Aprovados | Falhas | Variação |
| :--- | :---: | :---: | :---: |
| **Linha de Base (wip)** | 146 | 0 | - |
| **Passo 1 (CSRF)** | 150 | 0 | +4 |
| **Passo 2 (Sessões & Senhas)** | 153 | 0 | +3 |
| **Passo 3 (IP Proxy Render)** | 154 | 0 | +1 |
| **Passo 4 (E-mail & SMTP)** | 159 | 0 | +5 |
| **Passo 5 (Render & Headers)** | 161 | 0 | +2 |
| **Total Final** | **161 passed** | **0** | **+15 testes** |

---

## Para o Rodrigo

Instruções para deploy e configuração final no Render:

1. **Repositório e Blueprint:**
   - O repositório já conta com o arquivo `render.yaml` configurado para o plano gratuito, com 2 workers (adequados à memória de 512 MB da instância), health check em `/saude` e comando de inicialização com migração automática:
     ```bash
     alembic upgrade head && gunicorn main:app -w 2 -k uvicorn_worker.UvicornWorker --bind 0.0.0.0:$PORT
     ```

2. **Variáveis de Ambiente no Painel do Render (Environment Variables):**
   No painel do serviço no Render, preencha as variáveis marcadas com `sync: false`:
   - `DATABASE_URL`: Connection string do banco Neon (com `?sslmode=require`).
   - `SECRET_KEY`: Frase longa e aleatória (obrigatória em produção; a aplicação recusa iniciar sem ela).
   - `APP_BASE_URL`: Endereço HTTPS público gerado pelo Render (ex.: `https://investidor-ia.onrender.com`).
   - `GEMINI_API_KEY`: Chave do Google AI Studio.
   - `GEMINI_MODEL`: `gemini-2.5-flash`.
   - `CONFIAR_PROXY`: `1` (necessário para que o rate limit capture o IP real no proxy do Render).
   - `SMTP_HOST`: Host do provedor SMTP (ex.: `smtp.resend.com` ou `smtp.sendgrid.net`).
   - `SMTP_PORT`: `587` (STARTTLS) ou `465` (SSL direto).
   - `SMTP_USER`: Usuário ou API Key do provedor de e-mail.
   - `SMTP_PASS`: Senha ou chave secreta do provedor de e-mail.
   - `SMTP_FROM`: Nome e endereço de remetente (ex.: `Investidor IA <contato@seudominio.com.br>`).
   - `EMPRESA_RODAPE`: (Opcional) Identificação legal da empresa no rodapé.
   - `PYTHON_VERSION`: `3.13.5` (ou `3.11+`).

3. **Validação do E-mail após Configurar:**
   Para verificar se o provedor SMTP está entregando as mensagens corretamente antes de liberar o site aos usuários, acesse a aba "Shell" no painel do Render (ou teste localmente com o `.env` preenchido) e execute:
   ```bash
   python scripts/testar_email.py seu-email@exemplo.com
   ```
   O script imprimirá `enviado` em caso de sucesso ou o detalhamento do erro do servidor SMTP caso haja divergência de credenciais.

4. **Monitoramento e Saúde:**
   O endpoint `/saude` responde em JSON informando o status da aplicação e a disponibilidade do envio de e-mails:
   ```json
   {
     "status": "ok",
     "email_configurado": true
   }
   ```
