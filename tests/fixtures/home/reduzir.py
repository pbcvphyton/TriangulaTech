"""Reduz uma edição real (``data/AAAA-MM-DD.json``) ao que a home precisa.

As fixtures ``edicao-AAAA-MM-DD.json`` desta pasta saíram daqui e alimentam
``tests/test_home_orcamento.py``. Para refazer (a partir da raiz do repositório)::

    python tests/fixtures/home/reduzir.py 2026-11-16 2026-11-22 2026-11-23

O que fica, sem mudança: data, hora e modo da edição, editorial, resumo, manchete,
secundárias, destaques, seções, cotações, clima, estatísticas, racional, as notícias
recentes (``wire``) e, de cada matéria, título, linha fina, seção, foto, hora e os
veículos. O que é cortado, porque a home não mostra e só pesaria:

- corpo das matérias: só o primeiro parágrafo, até 240 caracteres; sem "por que importa";
- fontes das matérias: o nome e o endereço reduzido ao domínio;
- cobertura comparada (das matérias e dos demais assuntos): os dois lados e, de cada
  veículo, só o nome e o lado (sem o enfoque escrito, sem link e sem conclusão);
- índice do dia: só os primeiros assuntos (o total de notícias da página não é o real);
- termômetro: de cada análise, só o que a média do dia e a lista usam (sem sinais,
  citações, enquadramento e checklist).

A regra de corte fica aqui para a fixture poder ser refeita igual; nada é inventado.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

RAIZ = Path(__file__).resolve().parents[3]
AQUI = Path(__file__).resolve().parent
CORPO_MAX = 240
INDICE_ASSUNTOS = 12
INDICE_ITENS = 3
ANALISE = (
    "id", "url", "titulo", "veiculo", "editoria", "secao", "data_publicacao", "analisado_em",
    "versao_calculo", "selo", "genero", "estado", "T", "rotulo", "confianca",
)


def _dominio(url: str | None) -> str | None:
    partes = urlsplit(url or "")
    return f"{partes.scheme}://{partes.netloc}/" if partes.scheme and partes.netloc else None


def _cobertura(c: dict | None) -> dict | None:
    if not c:
        return None
    saida = {
        "topic": c.get("topic", ""),
        "conclusion": "",
        "side_a": c.get("side_a", ""),
        "side_b": c.get("side_b", ""),
        "outlets": [{"name": o.get("name", ""), "stance": o.get("stance", "neutro")} for o in c.get("outlets") or []],
    }
    if c.get("section"):
        saida["section"] = c["section"]
    return saida


def _materia(s: dict) -> dict:
    corpo = [p for p in s.get("body") or [] if p][:1]
    saida = {
        "id": s["id"],
        "section": s["section"],
        "headline": s["headline"],
        "dek": s.get("dek", ""),
        "body": [p[:CORPO_MAX] for p in corpo],
        "sources": [{"name": r.get("name", ""), "url": _dominio(r.get("url"))} for r in s.get("sources") or []],
        "article_ids": [],
        "image": s.get("image"),
        "published": s.get("published"),
    }
    if s.get("coverage"):
        saida["coverage"] = _cobertura(s["coverage"])
    return saida


def _analise(a: dict) -> dict:
    saida = {k: a[k] for k in ANALISE if k in a}
    saida["url"] = _dominio(saida.get("url"))
    qualidade = a.get("qualidade_apuracao") or {}
    saida["qualidade_apuracao"] = {k: qualidade[k] for k in ("Q", "rotulo") if k in qualidade}
    saida["evidencias"] = []
    return saida


def reduzir(edicao: dict) -> dict:
    saida = {k: edicao[k] for k in (
        "date", "date_label", "generated_at", "mode", "model", "editorial", "briefing",
        "lead", "secondary", "highlights", "sections", "quotes", "weather", "stats", "wire", "rationale",
    ) if k in edicao}
    saida["stories"] = [_materia(s) for s in edicao["stories"]]
    saida["compared"] = [_cobertura(c) for c in edicao.get("compared") or []]
    saida["index"] = [
        {**t, "items": [{**i, "url": _dominio(i.get("url"))} for i in t["items"][:INDICE_ITENS]]}
        for t in (edicao.get("index") or [])[:INDICE_ASSUNTOS]
    ]
    termometro = edicao.get("termometro")
    if termometro:
        saida["termometro"] = {
            **{k: v for k, v in termometro.items() if k != "analises"},
            "analises": [_analise(a) for a in termometro.get("analises") or []],
        }
    return saida


def main(dias: list[str]) -> int:
    total = 0
    for dia in dias:
        edicao = json.loads((RAIZ / "data" / f"{dia}.json").read_text(encoding="utf-8"))
        destino = AQUI / f"edicao-{dia}.json"
        destino.write_text(json.dumps(reduzir(edicao), ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        total += destino.stat().st_size
        print(f"{destino.name}: {destino.stat().st_size / 1024:.0f} KB")
    print(f"total: {total / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
