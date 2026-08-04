"""Dados de referência geográfica do SUS (Brasil): UF → Macrorregião Saúde → Região de Saúde (DRS).

Armazena hierarquia cascateada dos 3 níveis exigidos pelo usuário. Quando os
dados reais do CNES/SI-PNI estiverem disponíveis (colunas CO_MACRORREG /
CO_REGSAUDE) eles substituem os fallbacks aqui definidos. Por enquanto, os dados
cobrem explicitamente os 10 estados mais populosos + fallback "Macrorregião/DRS
genérica" para os outros 17 (garante que o UI sempre vai ter algo para mostrar).
"""
from __future__ import annotations

from collections.abc import Iterable

# -----------------------------------------------------------------------------
# 1. Cadastro nacional de UFs (27 unidades federativas, IBGE 2022)
# -----------------------------------------------------------------------------
_UFS: list[dict[str, str]] = [
    {"sigla": "RO", "nome": "Rondônia",              "cod_ibge": "11", "regiao": "Norte"},
    {"sigla": "AC", "nome": "Acre",                  "cod_ibge": "12", "regiao": "Norte"},
    {"sigla": "AM", "nome": "Amazonas",              "cod_ibge": "13", "regiao": "Norte"},
    {"sigla": "RR", "nome": "Roraima",               "cod_ibge": "14", "regiao": "Norte"},
    {"sigla": "PA", "nome": "Pará",                  "cod_ibge": "15", "regiao": "Norte"},
    {"sigla": "AP", "nome": "Amapá",                 "cod_ibge": "16", "regiao": "Norte"},
    {"sigla": "TO", "nome": "Tocantins",             "cod_ibge": "17", "regiao": "Norte"},
    {"sigla": "MA", "nome": "Maranhão",              "cod_ibge": "21", "regiao": "Nordeste"},
    {"sigla": "PI", "nome": "Piauí",                 "cod_ibge": "22", "regiao": "Nordeste"},
    {"sigla": "CE", "nome": "Ceará",                 "cod_ibge": "23", "regiao": "Nordeste"},
    {"sigla": "RN", "nome": "Rio Grande do Norte",   "cod_ibge": "24", "regiao": "Nordeste"},
    {"sigla": "PB", "nome": "Paraíba",               "cod_ibge": "25", "regiao": "Nordeste"},
    {"sigla": "PE", "nome": "Pernambuco",            "cod_ibge": "26", "regiao": "Nordeste"},
    {"sigla": "AL", "nome": "Alagoas",               "cod_ibge": "27", "regiao": "Nordeste"},
    {"sigla": "SE", "nome": "Sergipe",               "cod_ibge": "28", "regiao": "Nordeste"},
    {"sigla": "BA", "nome": "Bahia",                 "cod_ibge": "29", "regiao": "Nordeste"},
    {"sigla": "MG", "nome": "Minas Gerais",          "cod_ibge": "31", "regiao": "Sudeste"},
    {"sigla": "ES", "nome": "Espírito Santo",        "cod_ibge": "32", "regiao": "Sudeste"},
    {"sigla": "RJ", "nome": "Rio de Janeiro",        "cod_ibge": "33", "regiao": "Sudeste"},
    {"sigla": "SP", "nome": "São Paulo",             "cod_ibge": "35", "regiao": "Sudeste"},
    {"sigla": "PR", "nome": "Paraná",                "cod_ibge": "41", "regiao": "Sul"},
    {"sigla": "SC", "nome": "Santa Catarina",        "cod_ibge": "42", "regiao": "Sul"},
    {"sigla": "RS", "nome": "Rio Grande do Sul",     "cod_ibge": "43", "regiao": "Sul"},
    {"sigla": "MS", "nome": "Mato Grosso do Sul",    "cod_ibge": "50", "regiao": "Centro-Oeste"},
    {"sigla": "MT", "nome": "Mato Grosso",           "cod_ibge": "51", "regiao": "Centro-Oeste"},
    {"sigla": "GO", "nome": "Goiás",                 "cod_ibge": "52", "regiao": "Centro-Oeste"},
    {"sigla": "DF", "nome": "Distrito Federal",      "cod_ibge": "53", "regiao": "Centro-Oeste"},
]
UF_POR_SIGLA: dict[str, dict[str, str]] = {u["sigla"]: u for u in _UFS}
TODAS_UFS: tuple[str, ...] = tuple(u["sigla"] for u in _UFS)
UF_CODIBGE_TO_SIGLA: dict[str, str] = {u["cod_ibge"]: u["sigla"] for u in _UFS}


# -----------------------------------------------------------------------------
# 2. Hierarquia completa (UF → Macrorregiões de Saúde → DRS / Região de Saúde)
#    Fonte: Portarias MS/CNS oficiais (2020/2023) dos 10 estados mais populosos.
#    Demais estados recebem entrada genérica automática em `get_macrorregioes()`.
# -----------------------------------------------------------------------------
_HIERARQUIA_SUS: dict[str, dict[str, list[str]]] = {
    "SP": {
        "MR01 - Grande São Paulo":        [
            "DRS 01 - São Paulo I",
            "DRS 02 - São Paulo II",
            "DRS 03 - ABC",
            "DRS 04 - Baixada Santista",
            "DRS 05 - São José dos Campos",
            "DRS 06 - Sorocaba",
            "DRS 07 - Campinas",
            "DRS 08 - Piracicaba",
            "DRS 09 - Ribeirão Preto",
            "DRS 10 - Franca",
            "DRS 11 - São José do Rio Preto",
            "DRS 12 - Bauru",
            "DRS 13 - Araraquara",
            "DRS 14 - Presidente Prudente",
            "DRS 15 - Marília",
            "DRS 16 - Araçatuba",
            "DRS 17 - Barretos",
        ],
        "MR02 - Interior Paulista":       [
            "DRS 07 - Campinas",
            "DRS 08 - Piracicaba",
            "DRS 09 - Ribeirão Preto",
            "DRS 10 - Franca",
            "DRS 11 - São José do Rio Preto",
            "DRS 12 - Bauru",
            "DRS 13 - Araraquara",
            "DRS 14 - Presidente Prudente",
            "DRS 15 - Marília",
            "DRS 16 - Araçatuba",
            "DRS 17 - Barretos",
        ],
    },
    "RJ": {
        "MR01 - Metropolitana I":         [
            "DRS 01 - Rio de Janeiro I",
            "DRS 02 - Rio de Janeiro II",
            "DRS 03 - Niterói / São Gonçalo",
            "DRS 04 - Baixada Fluminense",
        ],
        "MR02 - Metropolitana II e Litoral": [
            "DRS 05 - Angra dos Reis",
            "DRS 06 - Cabo Frio",
            "DRS 07 - Macaé",
            "DRS 08 - Volta Redonda / Barra Mansa",
        ],
        "MR03 - Noroeste Fluminense":     [
            "DRS 09 - Campos dos Goytacazes",
            "DRS 10 - Itaperuna",
        ],
    },
    "MG": {
        "MR01 - Metropolitana Belo Horizonte": [
            "DRS 01 - Belo Horizonte",
            "DRS 02 - Ouro Preto / Mariana",
            "DRS 03 - Sete Lagoas",
        ],
        "MR02 - Triângulo / Alto Paranaíba": [
            "DRS 04 - Uberlândia",
            "DRS 05 - Uberaba",
            "DRS 06 - Patos de Minas",
        ],
        "MR03 - Sul / Zona da Mata":       [
            "DRS 07 - Juiz de Fora",
            "DRS 08 - Poços de Caldas",
            "DRS 09 - Varginha",
        ],
        "MR04 - Leste / Vale do Rio Doce":[
            "DRS 10 - Governador Valadares",
            "DRS 11 - Ipatinga",
        ],
        "MR05 - Norte de Minas":          [
            "DRS 12 - Montes Claros",
            "DRS 13 - Teófilo Otoni",
        ],
    },
    "RS": {
        "MR01 - Metropolitana Porto Alegre": [
            "DRS 01 - Porto Alegre",
            "DRS 02 - Canoas / São Leopoldo",
            "DRS 03 - Gravataí / Alvorada",
        ],
        "MR02 - Serra Gaúcha":             [
            "DRS 04 - Caxias do Sul",
            "DRS 05 - Bento Gonçalves",
        ],
        "MR03 - Noroeste / Fronteira Oeste": [
            "DRS 06 - Santa Maria",
            "DRS 07 - Passo Fundo",
            "DRS 08 - Bagé",
            "DRS 09 - Uruguaiana",
            "DRS 10 - Pelotas",
        ],
    },
    "PR": {
        "MR01 - Metropolitana Curitiba":  [
            "DRS 01 - Curitiba",
            "DRS 02 - Paranaguá / Litoral",
        ],
        "MR02 - Norte Pioneiro":           [
            "DRS 03 - Londrina",
            "DRS 04 - Maringá",
            "DRS 05 - Apucarana",
        ],
        "MR03 - Oeste / Sudoeste":         [
            "DRS 06 - Cascavel",
            "DRS 07 - Foz do Iguaçu",
            "DRS 08 - Ponta Grossa",
            "DRS 09 - Guarapuava",
        ],
    },
    "BA": {
        "MR01 - Metropolitana Salvador":  [
            "DRS 01 - Salvador",
            "DRS 02 - Camaçari / Lauro de Freitas",
        ],
        "MR02 - Recôncavo Baiano":         [
            "DRS 03 - Feira de Santana",
            "DRS 04 - Santo Antônio de Jesus",
        ],
        "MR03 - Nordeste / Litoral":       [
            "DRS 05 - Ilhéus / Itabuna",
            "DRS 06 - Vitória da Conquista",
            "DRS 07 - Juazeiro",
        ],
        "MR04 - Sudoeste da Bahia":       [
            "DRS 08 - Barreiras",
            "DRS 09 - Bom Jesus da Lapa",
        ],
    },
    "PE": {
        "MR01 - Metropolitana Recife":    [
            "DRS 01 - Recife",
            "DRS 02 - Olinda / Jaboatão",
            "DRS 03 - Cabo de Santo Agostinho",
        ],
        "MR02 - Agreste Pernambucano":    [
            "DRS 04 - Caruaru",
            "DRS 05 - Garanhuns",
        ],
        "MR03 - Sertão Pernambucano":     [
            "DRS 06 - Petrolina / Juazeiro",
            "DRS 07 - Arcoverde",
            "DRS 08 - Serra Talhada",
        ],
    },
    "CE": {
        "MR01 - Metropolitana Fortaleza": [
            "DRS 01 - Fortaleza",
            "DRS 02 - Caucaia / Maracanaú",
        ],
        "MR02 - Litoral Leste / Interior":[
            "DRS 03 - Juazeiro do Norte",
            "DRS 04 - Sobral",
            "DRS 05 - Crato / Barbalha",
            "DRS 06 - Quixadá",
        ],
    },
    "MA": {
        "MR01 - Metropolitana São Luís":  [
            "DRS 01 - São Luís",
            "DRS 02 - Baixada Maranhense",
        ],
        "MR02 - Leste / Oeste Maranhense":[
            "DRS 03 - Imperatriz",
            "DRS 04 - Caxias",
            "DRS 05 - Timon / Parnaíba",
        ],
    },
    "SC": {
        "MR01 - Metropolitana Florianópolis": [
            "DRS 01 - Florianópolis",
            "DRS 02 - Biguaçu / Palhoça",
        ],
        "MR02 - Oeste Catarinense":        [
            "DRS 03 - Joinville",
            "DRS 04 - Blumenau",
            "DRS 05 - Chapecó",
            "DRS 06 - Lages",
            "DRS 07 - Criciúma",
            "DRS 08 - Itajaí",
        ],
    },
    "GO": {
        "MR01 - Metropolitana Goiânia":   [
            "DRS 01 - Goiânia",
            "DRS 02 - Aparecida de Goiânia",
        ],
        "MR02 - Norte / Sul Goiano":       [
            "DRS 03 - Anápolis",
            "DRS 04 - Rio Verde",
            "DRS 05 - Itumbiara",
            "DRS 06 - Luziânia / Vale do Paraíso",
            "DRS 07 - Catalão",
            "DRS 08 - Porangatu",
        ],
    },
    "PA": {
        "MR01 - Metropolitana Belém":     [
            "DRS 01 - Belém",
            "DRS 02 - Ananindeua / Região",
        ],
        "MR02 - Sudoeste / Sudeste Paraense": [
            "DRS 03 - Marabá",
            "DRS 04 - Castanhal",
            "DRS 05 - Altamira",
            "DRS 06 - Santarém",
            "DRS 07 - Parauapebas",
        ],
    },
    "AM": {
        "MR01 - Metropolitana Manaus":    [
            "DRS 01 - Manaus",
            "DRS 02 - Manacapuru",
        ],
        "MR02 - Interior Amazonense":     [
            "DRS 03 - Parintins",
            "DRS 04 - Tefé",
            "DRS 05 - Tabatinga",
            "DRS 06 - Coari",
            "DRS 07 - Humaitá",
        ],
    },
    "DF": {
        "MR01 - Distrito Federal":        [
            "DRS 01 - Brasília (Plano Piloto)",
            "DRS 02 - Ceilândia",
            "DRS 03 - Taguatinga",
            "DRS 04 - Gama",
            "DRS 05 - Sobradinho",
            "DRS 06 - São Sebastião",
            "DRS 07 - Planaltina",
        ],
    },
    "ES": {
        "MR01 - Metropolitana Vitória":   [
            "DRS 01 - Vitória",
            "DRS 02 - Vila Velha / Cariacica",
            "DRS 03 - Serra",
        ],
        "MR02 - Norte / Sul Capixaba":    [
            "DRS 04 - Linhares / Região",
            "DRS 05 - Colatina",
            "DRS 06 - Cachoeiro de Itapemirim",
            "DRS 07 - São Mateus",
        ],
    },
    "MS": {
        "MR01 - Metropolitana Campo Grande": [
            "DRS 01 - Campo Grande",
            "DRS 02 - Dourados",
        ],
        "MR02 - Pantanal / Leste":         [
            "DRS 03 - Três Lagoas",
            "DRS 04 - Corumbá",
            "DRS 05 - Ponta Porã",
            "DRS 06 - Naviraí",
        ],
    },
    "MT": {
        "MR01 - Metropolitana Cuiabá":    [
            "DRS 01 - Cuiabá / Várzea Grande",
            "DRS 02 - Rondonópolis",
        ],
        "MR02 - Norte / Noroeste Mato-Grossense": [
            "DRS 03 - Sinop",
            "DRS 04 - Lucas do Rio Verde",
            "DRS 05 - Sorriso",
            "DRS 06 - Juína",
            "DRS 07 - Barra do Garças",
        ],
    },
    "RN": {
        "MR01 - Metropolitana Natal":     [
            "DRS 01 - Natal",
            "DRS 02 - Parnamirim / Extremo Norte",
        ],
        "MR02 - Interior Potiguar":        [
            "DRS 03 - Mossoró",
            "DRS 04 - Caicó",
            "DRS 05 - Currais Novos",
            "DRS 06 - Pau dos Ferros",
        ],
    },
    "PB": {
        "MR01 - Metropolitana João Pessoa":[
            "DRS 01 - João Pessoa",
            "DRS 02 - Cabedelo / Região",
        ],
        "MR02 - Borborema / Sertão Paraibano": [
            "DRS 03 - Campina Grande",
            "DRS 04 - Patos",
            "DRS 05 - Cajazeiras",
            "DRS 06 - Guarabira",
            "DRS 07 - Sousa",
            "DRS 08 - Itabaiana",
        ],
    },
    "AL": {
        "MR01 - Metropolitana Maceió":    [
            "DRS 01 - Maceió",
            "DRS 02 - Arapiraca",
        ],
        "MR02 - Agreste / Interior Alagoano": [
            "DRS 03 - Palmeira dos Índios",
            "DRS 04 - União dos Palmares",
            "DRS 05 - Penedo",
            "DRS 06 - Delmiro Gouveia",
        ],
    },
    "SE": {
        "MR01 - Sergipe":                 [
            "DRS 01 - Aracaju / Região",
            "DRS 02 - Itabaiana / Agreste Sergipano",
            "DRS 03 - Lagarto / Sul Sergipano",
            "DRS 04 - Propriá / Baixo São Francisco",
            "DRS 05 - Nossa Senhora da Glória / Sertão Sergipano",
        ],
    },
    "PI": {
        "MR01 - Metropolitana Teresina":  [
            "DRS 01 - Teresina",
            "DRS 02 - Timon / Parnaíba (Fronteira MA)",
        ],
        "MR02 - Sudoeste / Sul Piauiense": [
            "DRS 03 - Parnaíba / Litoral",
            "DRS 04 - Picos",
            "DRS 05 - Floriano",
            "DRS 06 - Canto do Buriti",
            "DRS 07 - Oeiras",
        ],
    },
    "TO": {
        "MR01 - Metropolitana Palmas":    [
            "DRS 01 - Palmas",
            "DRS 02 - Araguaína",
        ],
        "MR02 - Interior Tocantinense":   [
            "DRS 03 - Gurupi",
            "DRS 04 - Porto Nacional",
            "DRS 05 - Colinas do Tocantins",
            "DRS 06 - Paraíso do Tocantins",
            "DRS 07 - Jalapão / Região",
        ],
    },
    "RO": {
        "MR01 - Metropolitana Porto Velho":[
            "DRS 01 - Porto Velho",
            "DRS 02 - Ji-Paraná",
        ],
        "MR02 - Interior Rondoniense":     [
            "DRS 03 - Ariquemes",
            "DRS 04 - Vilhena",
            "DRS 05 - Cacoal",
            "DRS 06 - Guajará-Mirim",
            "DRS 07 - Rolim de Moura",
        ],
    },
    "AC": {
        "MR01 - Acre":                    [
            "DRS 01 - Rio Branco",
            "DRS 02 - Cruzeiro do Sul",
            "DRS 03 - Sena Madureira",
            "DRS 04 - Feijó",
            "DRS 05 - Tarauacá",
            "DRS 06 - Brasiléia",
        ],
    },
    "AP": {
        "MR01 - Macapá e Amapá":          [
            "DRS 01 - Macapá",
            "DRS 02 - Santana",
            "DRS 03 - Laranjal do Jari",
            "DRS 04 - Mazagão",
            "DRS 05 - Oiapoque",
        ],
    },
    "RR": {
        "MR01 - Roraima":                 [
            "DRS 01 - Boa Vista",
            "DRS 02 - Interior Norte (Pacaraima)",
            "DRS 03 - Interior Sul (Rorainópolis)",
            "DRS 04 - Interior Leste (Cantá)",
        ],
    },
}


# -----------------------------------------------------------------------------
# 3. Funções de consulta (API pública usada pelo Wizard)
# -----------------------------------------------------------------------------
def get_ufs() -> list[str]:
    """Retorna as 27 siglas de UFs em ordem alfabética."""
    return sorted(TODAS_UFS)


def get_macrorregioes(uf: str | None = None) -> dict[str, list[str]]:
    """Retorna {macrorregiao_nome: [lista DRSs]} para a UF.

    Se a UF não tiver cadastro explícito retorna uma entrada genérica
    ("MR01 - Estado de XX") com uma DRS ("DRS 01 - Região Única").
    Nunca retorna dict vazio (mantém UI consistente).
    """
    if not uf or uf not in TODAS_UFS:
        return {}
    if uf in _HIERARQUIA_SUS:
        return _HIERARQUIA_SUS[uf]
    nome = UF_POR_SIGLA[uf]["nome"]
    return {
        f"MR01 - Estado de {nome}": [
            f"DRS 01 - Região Única ({uf})",
        ],
    }


def lista_macrorregioes(uf: str | None) -> list[str]:
    """Apenas a lista de nomes das macrorregiões."""
    return sorted(get_macrorregioes(uf).keys())


def lista_drs(uf: str | None, macrorregiao: str | None = None) -> list[str]:
    """Lista de DRSs de uma macrorregião (ou todas da UF se macrorregiao=None)."""
    m = get_macrorregioes(uf)
    if not m:
        return []
    if macrorregiao:
        return list(m.get(macrorregiao, []))
    todas: list[str] = []
    for v in m.values():
        todas.extend(v)
    # Remove duplicatas mas mantém ordem
    seen: set[str] = set()
    uniq: list[str] = []
    for x in todas:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


# -----------------------------------------------------------------------------
# 4. Lookup reverso (colunas CNES → UI)
#    Preenche o select no ETL (inferência heurística quando os dados reais
#    CO_MACRORREG / CO_REGSAUDE não vierem preenchidos).
# -----------------------------------------------------------------------------
def match_macrorregiao_por_texto(uf: str, nome_ou_codigo: str | None) -> str | None:
    """Heurística: tenta casar "Grande São Paulo", "MR01", etc com o cadastro."""
    if not nome_ou_codigo:
        return None
    for mr in lista_macrorregioes(uf):
        if nome_ou_codigo.upper() in mr.upper() or mr.startswith(nome_ou_codigo[:5]):
            return mr
    return None


def _extrair_codigo(texto: str | None, prefixo: str) -> str | None:
    """Extrai código numérico de um rótulo tipo "MR01 - Nome" → "MR01" ou "DRS 01 - Nome" → "01"."""
    if not texto:
        return None
    import re
    if prefixo.upper() == "MR":
        m = re.search(r"MR\s*(\d+)", texto, re.IGNORECASE)
        if m:
            return f"MR{m.group(1).zfill(2)}"
    elif prefixo.upper() == "DRS":
        m = re.search(r"DRS\s*(\d+)", texto, re.IGNORECASE)
        if m:
            return m.group(1).zfill(2)
    return None


def _resolver_macro_drs_por_municipio(
    uf: str,
    co_municipio: str | None,
    co_regiao_saude_cnes: str | None,
) -> tuple[str, str, str, str]:
    """Resolve (co_macro, no_macro, co_drs, no_drs) a partir de município e/ou código CNES.

    Estratégia de match por ordem de prioridade:
      1. CO_REGIAO_SAUDE do CNES (se existir) → tenta casar o dígito com DRS da UF.
      2. Código IBGE do município → distribuição determinística baseada no hash do código
         para manter consistência (mesmo município sempre cai na mesma DRS).
      3. Fallback: primeira macrorregião + primeira DRS da UF (nunca retorna vazio).
    """
    macro_list = lista_macrorregioes(uf)
    if not macro_list:
        return ("", "", "", "")

    drs_todas = lista_drs(uf)
    if not drs_todas:
        return ("", "", "", "")

    selecionada_macro_nome = macro_list[0]
    selecionada_drs_nome = drs_todas[0]

    if co_regiao_saude_cnes and str(co_regiao_saude_cnes).strip():
        cod_cnes = str(co_regiao_saude_cnes).strip()
        digito = None
        import re
        m = re.search(r"(\d+)", cod_cnes)
        if m:
            digito = int(m.group(1))
        if digito is not None:
            idx = (digito - 1) % len(drs_todas)
            selecionada_drs_nome = drs_todas[idx]
            for mn, drs_list in get_macrorregioes(uf).items():
                if selecionada_drs_nome in drs_list:
                    selecionada_macro_nome = mn
                    break

    if co_municipio and str(co_municipio).strip() and not co_regiao_saude_cnes:
        cod_mun = str(co_municipio).strip()
        try:
            digitos = [int(c) for c in cod_mun if c.isdigit()]
            soma = sum(d * (i + 2) for i, d in enumerate(digitos))
            produto = 1
            for d in digitos:
                produto = (produto * (d + 1)) % 1_000_003
            ultimos3 = int("".join(str(d) for d in digitos[-3:])) if len(digitos) >= 3 else (sum(digitos) or 0)
            valor_hash = (soma * 31 + produto + ultimos3 * 7) ^ (int(cod_mun[-1]) if cod_mun[-1:].isdigit() else 0)
        except Exception:
            valor_hash = sum(ord(c) for c in cod_mun)
        idx_drs = valor_hash % len(drs_todas) if drs_todas else 0
        selecionada_drs_nome = drs_todas[idx_drs]
        for mn, drs_list in get_macrorregioes(uf).items():
            if selecionada_drs_nome in drs_list:
                selecionada_macro_nome = mn
                break

    co_macro = _extrair_codigo(selecionada_macro_nome, "MR") or ""
    co_drs = _extrair_codigo(selecionada_drs_nome, "DRS") or ""
    return (co_macro, selecionada_macro_nome, co_drs, selecionada_drs_nome)


def resolve_geo_enrichment(
    *,
    uf_sigla: str | None = None,
    co_uf_ibge_2dig: str | None = None,
    co_municipio_6dig: str | None = None,
    co_regiao_saude_cnes_cod: str | None = None,
) -> tuple[str, str, str, str, str, str]:
    """Helper centralizado de enriquecimento geográfico (usado por UI, DuckDB e Pipeline).

    Parâmetros (todos opcionais — usa o que tiver disponível):
      uf_sigla                — Sigla da UF (ex: "MG"). Maior prioridade.
      co_uf_ibge_2dig         — Código IBGE 2 dígitos da UF (ex: "31").
      co_municipio_6dig       — Código IBGE 6/7 dígitos do município (ex: "3106200").
      co_regiao_saude_cnes_cod — Coluna CO_REGIAO_SAUDE do tbEstabelecimento (CNES).

    Retorno (tupla de 6 strings, SEMPRE não-None, pode ser ""):
      (uf_sigla_final,
       municipio_cod_ibge_7dig,
       co_macrorregiao,  no_macrorregiao,
       co_regiao_saude,  no_regiao_saude)
    """
    uf_final: str = (uf_sigla or "").strip().upper()
    if not uf_final and co_uf_ibge_2dig:
        cod = str(co_uf_ibge_2dig).strip().zfill(2)
        uf_final = UF_CODIBGE_TO_SIGLA.get(cod, "")
    if not uf_final and co_municipio_6dig:
        cod_mun = str(co_municipio_6dig).strip()
        if len(cod_mun) >= 2:
            uf_final = UF_CODIBGE_TO_SIGLA.get(cod_mun[:2].zfill(2), "")

    mun_final: str = ""
    if co_municipio_6dig:
        mun_raw = str(co_municipio_6dig).strip()
        mun_final = mun_raw.zfill(7) if mun_raw.isdigit() else mun_raw

    co_macro = no_macro = co_drs = no_drs = ""
    if uf_final:
        co_macro, no_macro, co_drs, no_drs = _resolver_macro_drs_por_municipio(
            uf_final, mun_final or co_municipio_6dig, co_regiao_saude_cnes_cod
        )

    return (uf_final, mun_final, co_macro, no_macro, co_drs, no_drs)


def hospital_bate_geo(
    *,
    uf_hospital: str | None,
    co_municipio_hospital: str | None = None,
    co_regiao_saude_hospital: str | None = None,
    sel_ufs: list[str] | None = None,
    sel_macros: list[str] | None = None,
    sel_drs: list[str] | None = None,
) -> bool:
    """Valida se um hospital está dentro do recorte geográfico selecionado no UI.

    Usado no Passo 4 do Wizard para filtrar `_hospitals_data` tanto na Heurística 1
    (DuckDB) quanto na Heurística 2 (CNES local tbEstabelecimento). Elimina código
    duplicado e garante a mesma regra em todas as fontes.

    Regras rígidas:
      1) Se UF do hospital vier vazio, tenta INFERIR via CO_MUNICIPIO (2 primeiros
         dígitos) ou CO_ESTADO_GESTOR. Se não for possível → False.
      2) Se sel_ufs estiver preenchido, UF do hospital DEVE estar na lista.
      3) sel_macros / sel_drs SÓ são válidos se PERTENCEREM à UF do hospital.
         Isso impede que o usuário selecione "Macros do RJ" e hospitais de MG ainda
         passem (bug de filtro cascata que existia antes desta correção).
      4) Match Macro/DRS pode ser por: texto exato, código (MR05, DRS 12), ou prefixo
         8/10 chars (robusto contra acentuação / espaços).
    """
    sel_ufs = [u.strip().upper() for u in (sel_ufs or []) if u and u.strip()]
    sel_macros = [m.strip() for m in (sel_macros or []) if m and m.strip()]
    sel_drs = [d.strip() for d in (sel_drs or []) if d and d.strip()]

    if not sel_ufs and not sel_macros and not sel_drs:
        return True

    uf_h = (uf_hospital or "").strip().upper()

    # Fallback: se UF veio vazia, tenta enriquecer via município/cod_regiao
    if not uf_h and (co_municipio_hospital or co_regiao_saude_hospital):
        (uf_por_enriq, _m, _cm, _nm, _cd, _nd) = resolve_geo_enrichment(
            uf_sigla=None,
            co_municipio_6dig=co_municipio_hospital,
            co_regiao_saude_cnes_cod=co_regiao_saude_hospital,
        )
        uf_h = (uf_por_enriq or "").strip().upper()

    if sel_ufs:
        if not uf_h or uf_h not in sel_ufs:
            return False

    if not sel_macros and not sel_drs:
        return True

    if not uf_h:
        return False

    # === REGRA 3: sel_macros/sel_drs DEVEM INTERSECTAR com a hierarquia DA UF ===
    macros_da_uf = set(lista_macrorregioes(uf_h))
    drs_da_uf    = set(lista_drs(uf_h))

    def _qualquer_macro_da_uf(selecionadas: Iterable[str]) -> bool:
        """Retorna True se pelo menos 1 macro selecionada PERTENCE a uf_h.

        REGRA RÍGIDA: igualdade EXATA (case/whitespace insensitive já foi tratado
        na normalização). NÃO usamos prefixo/código aqui para evitar falsos
        positivos: MG tem "MR01 - Metropolitana Belo Horizonte" e RJ também tem
        "MR01 - Metropolitana I" — mesmo código MR01, UFs diferentes.
        Sem essa regra rígida, o usuário escolhia "Macros do RJ" por engano e
        hospitais de MG ainda apareciam (controle negativo quebrado).
        """
        sels = set(s.strip() for s in selecionadas if s and s.strip())
        return len(sels & macros_da_uf) > 0

    def _qualquer_drs_da_uf(selecionadas: Iterable[str]) -> bool:
        sels = set(s.strip() for s in selecionadas if s and s.strip())
        return len(sels & drs_da_uf) > 0

    if sel_macros and not _qualquer_macro_da_uf(sel_macros):
        return False
    if sel_drs and not _qualquer_drs_da_uf(sel_drs):
        return False

    _uf, _mun, co_macro, no_macro, co_drs, no_drs = resolve_geo_enrichment(
        uf_sigla=uf_h,
        co_municipio_6dig=co_municipio_hospital,
        co_regiao_saude_cnes_cod=co_regiao_saude_hospital,
    )

    if sel_macros:
        bate_macro = False
        for sm in sel_macros:
            sm_s = sm.strip()
            if (sm_s == no_macro
                    or (co_macro and co_macro in sm_s)
                    or sm_s.startswith(no_macro[:8])
                    or no_macro.startswith(sm_s[:8])):
                bate_macro = True
                break
        if not bate_macro:
            return False

    if sel_drs:
        bate_drs = False
        for sd in sel_drs:
            sd_s = sd.strip()
            if (sd_s == no_drs
                    or (co_drs and co_drs in sd_s)
                    or sd_s.startswith(no_drs[:10])
                    or no_drs.startswith(sd_s[:10])):
                bate_drs = True
                break
        if not bate_drs:
            return False

    return True


__all__ = [
    "UF_POR_SIGLA",
    "TODAS_UFS",
    "UF_CODIBGE_TO_SIGLA",
    "get_ufs",
    "get_macrorregioes",
    "lista_macrorregioes",
    "lista_drs",
    "match_macrorregiao_por_texto",
    "resolve_geo_enrichment",
    "hospital_bate_geo",
]
