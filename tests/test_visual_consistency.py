"""
Testes reais de consistência visual (Fase 4):
1. Cálculo matemático de contraste WCAG 2.1 sobre --bg e sobre --card (composto com opacidade).
2. Cruzamento bidirecional de classes CSS definidas em <style> vs usadas em class="..." nos templates.
3. Renderização real via TestClient de home.html, resultado.html (ação e FII), watchlist.html e auth.html.
"""
import os
import re
import banco
import seguranca as auth

PASTA_TEMPLATES = os.path.join(banco.PASTA_PROJETO, "templates")
ARQUIVOS_TEMPLATES = ("base.html", "home.html", "resultado.html", "watchlist.html", "auth.html", "legal.html")

# Classes aplicadas dinamicamente via JavaScript no DOM do autocomplete em base.html
EXCECOES_CLASSES_JS = {"ativo", "vazio"}


def _ler_template(nome: str) -> str:
    caminho = os.path.join(PASTA_TEMPLATES, nome)
    with open(caminho, "r", encoding="utf-8") as f:
        return f.read()


def _extrair_variaveis_root(html: str) -> dict[str, str]:
    bloco_root = re.search(r":root\s*\{([^}]+)\}", html, re.DOTALL)
    assert bloco_root, "Bloco :root não encontrado em base.html"
    variaveis: dict[str, str] = {}
    for m in re.finditer(r"(--[a-zA-Z0-9_-]+)\s*:\s*([^;]+);", bloco_root.group(1)):
        variaveis[m.group(1).strip()] = m.group(2).strip()
    return variaveis


def _parse_cor(valor: str) -> tuple[float, float, float, float]:
    """Converte #RRGGBB ou rgba(r, g, b, a) em (R, G, B, A) com R,G,B em 0..255 e A em 0..1."""
    v = valor.strip()
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16)), 1.0
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([0-9.]+))?\s*\)", v)
    if m:
        r, g, b = float(m.group(1)), float(m.group(2)), float(m.group(3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return r, g, b, a
    raise ValueError(f"Formato de cor não suportado: {valor}")


def _compor_sobre_fundo(fg_rgba: tuple[float, float, float, float], bg_rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b, a = fg_rgba
    br, bg_c, bb = bg_rgb
    return (
        r * a + br * (1.0 - a),
        g * a + bg_c * (1.0 - a),
        b * a + bb * (1.0 - a),
    )


def _luminancia_relativa(rgb: tuple[float, float, float]) -> float:
    def _canal(c: float) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r_lin, g_lin, b_lin = _canal(rgb[0]), _canal(rgb[1]), _canal(rgb[2])
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def _razao_contraste(rgb1: tuple[float, float, float], rgb2: tuple[float, float, float]) -> float:
    l1 = _luminancia_relativa(rgb1)
    l2 = _luminancia_relativa(rgb2)
    maior, menor = (l1, l2) if l1 >= l2 else (l2, l1)
    return (maior + 0.05) / (menor + 0.05)


def test_4_2_4_3_contraste_wcag_variaveis_root():
    """
    Calcula a razão de contraste WCAG de --texto, --suave, --apagado, --destaque, --pos e --neg
    sobre --bg e sobre a cor resolvida de --card (composta sobre --bg pela opacidade) e exige >= 4.5:1.
    """
    base_html = _ler_template("base.html")
    vars_root = _extrair_variaveis_root(base_html)

    bg_r, bg_g, bg_b, _ = _parse_cor(vars_root["--bg"])
    cor_bg = (bg_r, bg_g, bg_b)
    cor_card = _compor_sobre_fundo(_parse_cor(vars_root["--card"]), cor_bg)

    tokens_texto = ("--texto", "--suave", "--apagado", "--destaque", "--pos", "--neg")
    for token in tokens_texto:
        assert token in vars_root, f"Variável {token} ausente em :root"
        r, g, b, _ = _parse_cor(vars_root[token])
        cor_fg = (r, g, b)
        cr_bg = _razao_contraste(cor_fg, cor_bg)
        cr_card = _razao_contraste(cor_fg, cor_card)
        assert cr_bg >= 4.5, f"{token} ({vars_root[token]}) tem contraste {cr_bg:.2f}:1 sobre --bg (< 4.5:1)"
        assert cr_card >= 4.5, f"{token} ({vars_root[token]}) tem contraste {cr_card:.2f}:1 sobre --card (< 4.5:1)"


def _extrair_classes_css(html: str) -> set[str]:
    classes: set[str] = set()
    for bloco_style in re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL | re.IGNORECASE):
        sem_comentarios = re.sub(r"/\*.*?\*/", "", bloco_style, flags=re.DOTALL)
        # Remove blocos de propriedades { ... } mantendo apenas seletores (incluindo dentro de @media)
        # Substitui declarações internas { propriedade: valor; } sem chaves aninhadas
        apenas_seletores = sem_comentarios
        while True:
            novo = re.sub(r"\{[^{}]*\}", " ", apenas_seletores)
            if novo == apenas_seletores:
                break
            apenas_seletores = novo
        for m in re.finditer(r"\.([a-zA-Z_-][a-zA-Z0-9_-]*)", apenas_seletores):
            classes.add(m.group(1))
    return classes


def _extrair_classes_html(html: str) -> set[str]:
    classes: set[str] = set()
    for attr in re.findall(r'\bclass\s*=\s*"([^"]+)"', html):
        # Remove expressões Jinja {{ ... }} e {% ... %}, mas extrai literais entre aspas simples dentro delas
        for lit in re.findall(r"'([a-zA-Z_-][a-zA-Z0-9_-]*)'", attr):
            classes.add(lit)
        limpo = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", attr)
        for token in limpo.split():
            if re.match(r"^[a-zA-Z_-][a-zA-Z0-9_-]*$", token):
                classes.add(token)
    return classes


def test_4_2_4_3_classes_css_e_templates_sem_orfas():
    """Verifica que toda classe usada nos templates existe no CSS e que não há classes CSS órfãs."""
    classes_definidas_css: set[str] = set()
    classes_usadas_html: set[str] = set()

    for nome in ARQUIVOS_TEMPLATES:
        conteudo = _ler_template(nome)
        classes_definidas_css |= _extrair_classes_css(conteudo)
        classes_usadas_html |= _extrair_classes_html(conteudo)

    faltando_no_css = classes_usadas_html - classes_definidas_css
    assert not faltando_no_css, f"Classes usadas no HTML mas não definidas no CSS: {sorted(faltando_no_css)}"

    orfas_no_css = (classes_definidas_css - classes_usadas_html) - EXCECOES_CLASSES_JS
    assert not orfas_no_css, f"Classes definidas no CSS mas nunca usadas nos templates: {sorted(orfas_no_css)}"


def test_4_1_4_3_renderizacao_todos_templates_acao_e_fii(client, sessao_db):
    """Renderiza home.html, resultado.html (ação e FII), watchlist.html, auth.html e legal.html via TestClient."""
    # 1. Home
    r_home = client.get("/")
    assert r_home.status_code == 200

    # 2. Resultado de Ação (PETR4) - verifica novos elementos da Fase 4.1
    r_acao = client.get("/analisar?ticker=PETR4")
    assert r_acao.status_code == 200
    assert "Adicionar à Watchlist" in r_acao.text
    assert "Site Oficial" in r_acao.text
    assert "Resumo Institucional" in r_acao.text
    assert "Dívida Líquida" in r_acao.text
    assert "Payout" in r_acao.text
    assert "Margem:" in r_acao.text

    # 3. Resultado de FII (HGLG11)
    r_fii = client.get("/analisar?ticker=HGLG11")
    assert r_fii.status_code == 200
    assert "CSHG Logística FII" in r_fii.text
    assert "Não se aplica a FII/ETF" in r_fii.text

    # 4. Auth (login, cadastro, esqueci)
    for rota in ("/login", "/cadastro", "/esqueci-senha", "/termos"):
        resp = client.get(rota)
        assert resp.status_code == 200

    # 5. Watchlist autenticada
    u = banco.Usuario(
        nome="Investidora Visual",
        email="visual@exemplo.com",
        senha_hash=auth.gerar_hash_senha("SenhaForte123!"),
        email_confirmado=True,
    )
    sessao_db.add(u)
    sessao_db.commit()
    sessao_db.refresh(u)

    token = auth.criar_token({"uid": u.id}, salt="sessao-usuario")
    client.cookies.set(auth.COOKIE_SESSAO, token)
    r_wl = client.get("/watchlist")
    assert r_wl.status_code == 200
    assert "Minha Watchlist" in r_wl.text

    # Resultado de Ação para usuária logada deve exibir formulário POST para /watchlist/adicionar
    r_acao_logada = client.get("/analisar?ticker=PETR4")
    assert r_acao_logada.status_code == 200
    assert 'action="/watchlist/adicionar"' in r_acao_logada.text
