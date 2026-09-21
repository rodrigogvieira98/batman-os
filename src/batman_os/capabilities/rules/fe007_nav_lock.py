"""Capability bespoke FE-007 "NAV_LOCK01 — item canônico de navegação
removido" (Vol.IV Cap.17).

Não generalizada em Skill — compara 3 blocos de rotas canônicas
(NAV_VIEWER/NAV_ADMIN/NAV_ADMIN_PRD) contra o que está de fato presente
em `Layout.tsx`, podendo produzir ATÉ 3 achados por invocação (um por
bloco com rota(s) faltando), cada um com `chave` distinta
(`{bloco}:{rotas_faltando}`) — não é um único padrão regex, é uma
comparação de conjuntos por bloco extraído."""

from __future__ import annotations

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

_ROUTE_PATTERN = re.compile(r"to:\s*['\"]([^'\"]+)['\"]")

_CANONICAL_VIEWER: frozenset[str] = frozenset(
    {"/investidor", "/mercado", "/oportunidades", "/simulacoes", "/newsletter"}
)
_CANONICAL_ADMIN: frozenset[str] = frozenset(
    {"/admin", "/admin/operacao", "/admin/engenharia", "/admin/analise", "/admin/comercial"}
)
_CANONICAL_ADMIN_PRD: frozenset[str] = frozenset(
    {"/admin", "/admin/operacao", "/admin/engenharia", "/admin/analise", "/admin/comercial"}
)

_CHECKS: list[tuple[str, frozenset[str]]] = [
    ("NAV_VIEWER", _CANONICAL_VIEWER),
    ("NAV_ADMIN", _CANONICAL_ADMIN),
    ("NAV_ADMIN_PRD", _CANONICAL_ADMIN_PRD),
]


class RegraFe007Spec(BaseModel):
    codigo: str
    agente: str
    severidade: str
    categoria: str
    titulo: str
    causa: str
    remediacao: str


class EntradaFe007(BaseModel):
    tipo: Literal["fe007"] = "fe007"
    caminho: str
    conteudo: str | None = None
    regra: RegraFe007Spec


class AchadoFe007(BaseModel):
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


class SaidaFe007(BaseModel):
    achados: list[AchadoFe007] = Field(default_factory=list)


class EntradaInvalida(Exception):
    """Levantada quando `entrada` não satisfaz `EntradaFe007` — vira
    SCHEMA_REJECTION no Execution Engine (Vol.III Cap.12)."""


def _computar_fingerprint(
    agente: str, categoria: str, caminho: str, codigo: str, chave: str
) -> str:
    import hashlib

    normalizado = caminho.replace("\\", "/")
    bruto = f"{agente}|{categoria}|{normalizado}|{codigo}|{chave}"
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()


def _extrair_rotas_do_bloco(text: str, block_name: str) -> set[str]:
    pattern = re.compile(rf"const\s+{re.escape(block_name)}\s*=\s*\[(.*?)\]", re.DOTALL)
    m = pattern.search(text)
    if not m:
        return set()
    return {r.group(1) for r in _ROUTE_PATTERN.finditer(m.group(1))}


def avaliar_fe007(entrada: Any, contexto: ExecutionContext) -> Any:
    del contexto
    try:
        dados = EntradaFe007.model_validate(entrada)
    except Exception as exc:
        raise EntradaInvalida(str(exc)) from exc

    if dados.conteudo is None:
        return SaidaFe007(achados=[]).model_dump()

    text = dados.conteudo
    regra = dados.regra
    achados: list[AchadoFe007] = []

    for block, canonical in _CHECKS:
        present = _extrair_rotas_do_bloco(text, block)
        if not present:
            continue
        missing = canonical - present
        if not missing:
            continue
        chave = f"{block}:{','.join(sorted(missing))}"
        fingerprint = _computar_fingerprint(
            regra.agente, regra.categoria, dados.caminho, regra.codigo, chave
        )
        achados.append(
            AchadoFe007(
                codigo=regra.codigo,
                agente=regra.agente,
                severidade=regra.severidade,
                categoria=regra.categoria,
                titulo=regra.titulo,
                descricao=(
                    f"{dados.caminho}: {block} removeu rota(s) canônica(s): {sorted(missing)}."
                ),
                causa=regra.causa,
                remediacao=regra.remediacao,
                arquivo=dados.caminho,
                chave=chave,
                fingerprint=fingerprint,
            )
        )

    return SaidaFe007(achados=achados).model_dump()


def construir_implementacao() -> CapabilityImplementation:
    definicao = CapabilityDefinition(
        id=CapabilityId("fe007-nav-lock"),
        name="FE-007 NAV_LOCK01 item canonico removido",
        version="1.0.0",
        input_schema=EntradaFe007.model_json_schema(),
        output_schema=SaidaFe007.model_json_schema(),
        deterministic=True,
        side_effects=SideEffects.NONE,
        idempotent=True,
    )

    _regra_teste = {
        "codigo": "FE-007",
        "agente": "frontend-engineer",
        "severidade": "high",
        "categoria": "regressao",
        "titulo": "t",
        "causa": "c",
        "remediacao": "r",
    }
    entrada_sucesso = {
        "caminho": "frontend/src/components/Layout.tsx",
        "conteudo": "const NAV_VIEWER = [\n  { to: '/investidor' },\n  { to: '/mercado' },\n];\n",
        "regra": _regra_teste,
    }
    entrada_ok = {
        "caminho": "frontend/src/components/Layout.tsx",
        "conteudo": (
            "const NAV_VIEWER = [\n"
            "  { to: '/investidor' },\n"
            "  { to: '/mercado' },\n"
            "  { to: '/oportunidades' },\n"
            "  { to: '/simulacoes' },\n"
            "  { to: '/newsletter' },\n"
            "];\n"
        ),
        "regra": _regra_teste,
    }

    return CapabilityImplementation(
        definition=definicao,
        handler=avaliar_fe007,
        acceptance_tests=[
            AcceptanceTest(
                name="rota-canonica-removida-dispara",
                entrada=entrada_sucesso,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: len(saida["achados"]) == 1,
            ),
            AcceptanceTest(
                name="todas-rotas-canonicas-presentes-nao-dispara",
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
                    "caminho": "frontend/src/components/Layout.tsx",
                    "conteudo": "x",
                    "regra": {"severidade": 123},
                },
                resultado_esperado=ResultadoEsperado.TIMEOUT,
            ),
        ],
    )
