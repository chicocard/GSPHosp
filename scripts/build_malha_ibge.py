"""
build_malha_ibge.py — regenera papahr/ui/mapa/assets/malha_municipios.js
a partir da API de malhas do IBGE (malha vigente, 5.570 municípios).

Uso (na raiz do projeto, com o .venv ativo ou pelo caminho do python):
    python scripts/build_malha_ibge.py
    python scripts/build_malha_ibge.py --qualidade intermediaria   # mais detalhe, arquivo maior

Sem dependências além da biblioteca padrão. Baixa UF por UF (27 requisições)
da API v3 de malhas e os nomes da API v1 de localidades.

Por que existe: o arquivo que vem com o pacote foi gerado a partir de uma
cópia pública da malha de 2010 (5.564 municípios). Faltam nele os seis
municípios criados depois: Nazária (PI), Mojuí dos Campos (PA), Pescaria
Brava (SC), Balneário Rincão (SC), Paraíso das Águas (MS) e Pinto Bandeira (RS).
Rodando este script na sua máquina (com acesso ao IBGE) o arquivo passa a
ter a malha atual. Se a API mudar e o script falhar, o arquivo antigo
continua funcionando — nada é apagado antes de o novo ficar pronto.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "papahr" / "ui" / "mapa" / "assets" / "malha_municipios.js"

UFS = ["11", "12", "13", "14", "15", "16", "17", "21", "22", "23", "24", "25", "26", "27", "28", "29",
       "31", "32", "33", "35", "41", "42", "43", "50", "51", "52", "53"]

URL_MALHA = ("https://servicodados.ibge.gov.br/api/v3/malhas/estados/{uf}"
             "?formato=application/vnd.geo+json&qualidade={q}&intrarregiao=municipio")
URL_NOMES = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado"


def baixar(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "GSPHosp/1.0", "Accept": "application/vnd.geo+json, application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def arredondar(coords, nd=4):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], nd), round(coords[1], nd)]
    return [arredondar(c, nd) for c in coords]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qualidade", default="minima", choices=["minima", "baixa", "intermediaria", "maxima"])
    ap.add_argument("--destino", default=str(DESTINO))
    args = ap.parse_args()

    print("Baixando nomes dos municípios…")
    nomes = {}
    for m in json.loads(baixar(URL_NOMES)):
        cod = str(m.get("municipio-id") or m.get("id"))
        nomes[cod] = m.get("municipio-nome") or m.get("nome")
    print(f"  {len(nomes)} nomes")

    feats = []
    for uf in UFS:
        url = URL_MALHA.format(uf=uf, q=args.qualidade)
        print(f"UF {uf}…", end=" ", flush=True)
        g = json.loads(baixar(url))
        n = 0
        for f in g["features"]:
            p = f.get("properties", {})
            cod7 = str(p.get("codarea") or f.get("id") or "")
            if len(cod7) != 7:
                continue
            feats.append({
                "type": "Feature", "id": cod7[:6],
                "properties": {"cod6": cod7[:6], "cod7": cod7, "nome": nomes.get(cod7, cod7)},
                "geometry": {"type": f["geometry"]["type"], "coordinates": arredondar(f["geometry"]["coordinates"])},
            })
            n += 1
        print(f"{n} municípios")

    if len(feats) < 5500:
        print(f"ERRO: só {len(feats)} municípios — não vou sobrescrever o arquivo atual.", file=sys.stderr)
        return 1

    out = {"type": "FeatureCollection",
           "fonte": f"IBGE API v3 malhas, qualidade={args.qualidade}, {len(feats)} municípios, coordenadas com 4 casas",
           "features": feats}
    destino = Path(args.destino)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text("window.GSP_MALHA=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    if destino.exists():
        destino.replace(destino.with_suffix(".js.bak"))
    tmp.replace(destino)
    print(f"OK: {destino} ({destino.stat().st_size / 1e6:.1f} MB). Cópia anterior em {destino.with_suffix('.js.bak').name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
