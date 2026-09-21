"""Capability bespoke QA-AUTO-001 "router sem arquivo de teste
correspondente" (Vol.IV Cap.17).

Não generalizada numa Skill — cardinalidade invertida (mesmo princípio de
ARCH-003): 1 Missão por arquivo candidato (`api/routers/*.py`), cada uma
carregando a MESMA lista agregada de stems de teste (`tests/**/test_*.py`,
com o prefixo `test_` removido — replica `p.stem.replace("test_", "")`
do legado, incluindo o comportamento de `.replace()` remover TODAS as
ocorrências, não só o prefixo) — o handler só verifica se o próprio nome
(stem) do router aparece nessa lista compartilhada."""

from __future__ import annotations

import json
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


class RegraQaAuto001Spec(BaseModel):
    codigo: str
    agente: str
    severidade: str
    categoria: str
    titulo: str
    causa: str
    remediacao: str


class EntradaQaAuto001(BaseModel):
    tipo: Literal["qaauto001"] = "qaauto001"
    caminho: str
    conteudo: str | None = None
    regra: RegraQaAuto001Spec


class AchadoQaAuto001(BaseModel):
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


class SaidaQaAuto001(BaseModel):
    achados: list[AchadoQaAuto001] = Field(default_factory=list)


class EntradaInvalida(Exception):
    """Levantada quando `entrada` não satisfaz `EntradaQaAuto001` — vira
    SCHEMA_REJECTION no Execution Engine (Vol.III Cap.12)."""


def _computar_fingerprint(agente: str, categoria: str, caminho: str, codigo: str) -> str:
    import hashlib

    normalizado = caminho.replace("\\", "/")
    bruto = f"{agente}|{categoria}|{normalizado}|{codigo}|"
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()


def _stem(caminho: str) -> str:
    nome = caminho.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in nome:
        return nome.rsplit(".", 1)[0]
    return nome


def _tem_teste(name: str, test_stems: set[str]) -> bool:
    """O router `name` e coberto por algum arquivo de teste?

    ⚠️ Ate 2026-09-04 a comparacao era IGUALDADE — `test_<router>.py` e nada
    mais. Isso mede o NOME DO ARQUIVO, nao a cobertura, e num projeto que nomeia
    o teste pelo CASO em vez de pelo modulo produz falso positivo em massa:
    medido no radar-preditivo, **44 dos 45 routers** eram acusados enquanto 125
    arquivos de teste exercitavam routers. `admin` era dado como sem teste tendo
    `test_admin_kpis_exclusao.py`, `test_admin_plan_change.py` e outros.

    Agora aceita tambem o prefixo com separador (`admin` casa
    `admin_kpis_exclusao`, e nao casa `administracao`). Medido no mesmo
    repositorio: **44 -> 19 acusados**, e as 25 exclusoes todas com um teste
    nomeado que as justifica.

    ⚠️ LIMITE QUE PERMANECE, e esta declarado de proposito: isto continua sendo
    heuristica sobre NOME. Um router testado num arquivo que nao o nomeia segue
    acusado. Medir cobertura de verdade exigiria ler os testes (imports, rotas
    exercitadas), que e outra ordem de custo.
    """
    if name in test_stems or f"test_{name}" in test_stems:
        return True
    prefixo = f"{name}_"
    return any(stem.startswith(prefixo) for stem in test_stems)


def avaliar_qaauto001(entrada: Any, contexto: ExecutionContext) -> Any:
    del contexto
    try:
        dados = EntradaQaAuto001.model_validate(entrada)
    except Exception as exc:
        raise EntradaInvalida(str(exc)) from exc

    name = _stem(dados.caminho)
    if name == "__init__":
        return SaidaQaAuto001(achados=[]).model_dump()

    payload = json.loads(dados.conteudo) if dados.conteudo else {"test_stems": []}
    test_stems: set[str] = set(payload.get("test_stems", []))

    if _tem_teste(name, test_stems):
        return SaidaQaAuto001(achados=[]).model_dump()

    regra = dados.regra
    fingerprint = _computar_fingerprint(regra.agente, regra.categoria, dados.caminho, regra.codigo)
    achado = AchadoQaAuto001(
        codigo=regra.codigo,
        agente=regra.agente,
        severidade=regra.severidade,
        categoria=regra.categoria,
        titulo=regra.titulo,
        descricao=f"{dados.caminho}: sem arquivo de teste correspondente.",
        causa=regra.causa,
        remediacao=regra.remediacao,
        arquivo=dados.caminho,
        fingerprint=fingerprint,
    )
    return SaidaQaAuto001(achados=[achado]).model_dump()


def construir_implementacao() -> CapabilityImplementation:
    definicao = CapabilityDefinition(
        id=CapabilityId("qaauto001-router-sem-teste"),
        name="QA-AUTO-001 router sem arquivo de teste correspondente",
        version="1.0.0",
        input_schema=EntradaQaAuto001.model_json_schema(),
        output_schema=SaidaQaAuto001.model_json_schema(),
        deterministic=True,
        side_effects=SideEffects.NONE,
        idempotent=True,
    )

    _regra_teste = {
        "codigo": "QA-AUTO-001",
        "agente": "qa-automation",
        "severidade": "medium",
        "categoria": "cobertura",
        "titulo": "t",
        "causa": "c",
        "remediacao": "r",
    }
    entrada_sucesso = {
        "caminho": "api/routers/pedidos.py",
        "conteudo": json.dumps({"test_stems": ["outra_coisa"]}),
        "regra": _regra_teste,
    }
    entrada_ok = {
        "caminho": "api/routers/pedidos.py",
        "conteudo": json.dumps({"test_stems": ["pedidos"]}),
        "regra": _regra_teste,
    }

    return CapabilityImplementation(
        definition=definicao,
        handler=avaliar_qaauto001,
        acceptance_tests=[
            AcceptanceTest(
                name="router-sem-teste-correspondente-dispara",
                entrada=entrada_sucesso,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: len(saida["achados"]) == 1,
            ),
            AcceptanceTest(
                name="router-com-teste-correspondente-nao-dispara",
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
                    "caminho": "api/routers/x.py",
                    "conteudo": "x",
                    "regra": {"severidade": 123},
                },
                resultado_esperado=ResultadoEsperado.TIMEOUT,
            ),
        ],
    )
