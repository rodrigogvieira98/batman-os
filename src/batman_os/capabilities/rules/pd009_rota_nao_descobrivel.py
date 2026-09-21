"""Capability bespoke PD-009 "rota/feature não referenciada em nav ou CTA
de outra tela" (Vol.IV Cap.17).

Não generalizada em Skill — cross-reference entre `App.tsx` (fonte das
ocorrências de rota) e uma LISTA FIXA de arquivos de nav (`Layout.tsx`,
`NotifBell.tsx`), verificando se a keyword da rota aparece em QUALQUER um
deles. Múltiplos achados possíveis (1 por rota órfã ocorrente em
App.tsx), sem `chave` — colapsam ao mesmo fingerprint (mesma equivalência
já estabelecida para outros códigos com `caminho` fixo)."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from batman_os.capabilities.capability_contract import (
    AcceptanceTest,
    CapabilityImplementation,
    ResultadoEsperado,
)
from batman_os.capabilities.operator import ExecutionContext
from batman_os.foundation.types import CapabilityId
from batman_os.runtime.capability_engine import CapabilityDefinition, SideEffects

_ORPHAN_ROUTES = re.compile(
    r'path=["\'](/retrospectiva|/newsletter|/ajuda|/glossario|/propagador|/changelog)["\']',
    re.I,
)


class RegraPd009Spec(BaseModel):
    codigo: str
    agente: str
    severidade: str
    categoria: str
    titulo: str
    causa: str
    remediacao: str


class EntradaPd009(BaseModel):
    tipo: Literal["pd009"] = "pd009"
    caminho: str
    conteudo: str | None = None
    regra: RegraPd009Spec


class AchadoPd009(BaseModel):
    codigo: str
    agente: str
    severidade: str
    categoria: str
    titulo: str
    descricao: str
    causa: str
    remediacao: str
    arquivo: str
    chave: str = ""
    fingerprint: str = ""


class SaidaPd009(BaseModel):
    achados: list[AchadoPd009] = Field(default_factory=list)


class EntradaInvalida(Exception):
    """Levantada quando `entrada` não satisfaz `EntradaPd009` — vira
    SCHEMA_REJECTION no Execution Engine (Vol.III Cap.12)."""


def _computar_fingerprint(agente: str, categoria: str, caminho: str, codigo: str) -> str:
    import hashlib

    normalizado = caminho.replace("\\", "/")
    bruto = f"{agente}|{categoria}|{normalizado}|{codigo}|"
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()


def avaliar_pd009(entrada: Any, contexto: ExecutionContext) -> Any:
    del contexto
    try:
        dados = EntradaPd009.model_validate(entrada)
    except Exception as exc:
        raise EntradaInvalida(str(exc)) from exc

    if dados.conteudo is None:
        return SaidaPd009(achados=[]).model_dump()

    payload = json.loads(dados.conteudo)
    app_texto: str = payload.get("app_texto", "")
    nav_textos: list[str] = payload.get("nav_textos", [])

    regra = dados.regra
    achados: list[AchadoPd009] = []

    for m in _ORPHAN_ROUTES.finditer(app_texto):
        route = m.group(1)
        kw = route.lstrip("/")
        nav_refs = sum(1 for nav_texto in nav_textos if re.search(kw, nav_texto, re.I))
        if nav_refs > 0:
            continue

        ln = app_texto.count("\n", 0, m.start()) + 1
        fingerprint = _computar_fingerprint(
            regra.agente, regra.categoria, dados.caminho, regra.codigo
        )
        achados.append(
            AchadoPd009(
                codigo=regra.codigo,
                agente=regra.agente,
                severidade=regra.severidade,
                categoria=regra.categoria,
                titulo=regra.titulo,
                descricao=(
                    f"App.tsx:{ln}: rota '{route}' não aparece em nenhum nav/layout — "
                    "usuário não consegue descobrir."
                ),
                causa=regra.causa,
                remediacao=regra.remediacao,
                arquivo=dados.caminho,
                fingerprint=fingerprint,
            )
        )

    return SaidaPd009(achados=achados).model_dump()


def construir_implementacao() -> CapabilityImplementation:
    definicao = CapabilityDefinition(
        id=CapabilityId("pd009-rota-nao-descobrivel"),
        name="PD-009 rota nao descobrivel",
        version="1.0.0",
        input_schema=EntradaPd009.model_json_schema(),
        output_schema=SaidaPd009.model_json_schema(),
        deterministic=True,
        side_effects=SideEffects.NONE,
        idempotent=True,
    )

    _regra_teste = {
        "codigo": "PD-009",
        "agente": "product-designer",
        "severidade": "low",
        "categoria": "descobribilidade",
        "titulo": "t",
        "causa": "c",
        "remediacao": "r",
    }
    entrada_sucesso = {
        "caminho": "frontend/src/App.tsx",
        "conteudo": json.dumps(
            {
                "app_texto": '<Route path="/newsletter" element={<Newsletter />} />',
                "nav_textos": ["nada relacionado aqui"],
            }
        ),
        "regra": _regra_teste,
    }
    entrada_ok = {
        "caminho": "frontend/src/App.tsx",
        "conteudo": json.dumps(
            {
                "app_texto": '<Route path="/newsletter" element={<Newsletter />} />',
                "nav_textos": ["<Link to='/newsletter'>Newsletter</Link>"],
            }
        ),
        "regra": _regra_teste,
    }

    return CapabilityImplementation(
        definition=definicao,
        handler=avaliar_pd009,
        acceptance_tests=[
            AcceptanceTest(
                name="rota-orfa-sem-referencia-em-nav-dispara",
                entrada=entrada_sucesso,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: len(saida["achados"]) == 1,
            ),
            AcceptanceTest(
                name="rota-referenciada-em-nav-nao-dispara",
                entrada=entrada_ok,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: saida["achados"] == [],
            ),
            AcceptanceTest(
                name="entrada-sem-campo-obrigatorio-e-rejeitada",
                entrada={"conteudo": "x"},  # falta 'caminho'
                resultado_esperado=ResultadoEsperado.SCHEMA_REJECTION,
            ),
            AcceptanceTest(
                name="regra-com-tipo-de-campo-invalido-e-tratada-como-falha-de-invocacao",
                entrada={
                    "caminho": "frontend/src/App.tsx",
                    "conteudo": "x",
                    "regra": {"severidade": 123},
                },
                resultado_esperado=ResultadoEsperado.TIMEOUT,
            ),
        ],
    )
