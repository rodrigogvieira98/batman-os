"""Capability bespoke COMP-008 "relatório de impacto por usuário sem
disclaimer CVM" (Vol.IV Cap.17).

Replica `Batman/scan/rules/compliance.py::ImpactReportWithoutDisclaimer`:
um arquivo é considerado "relatório de impacto por usuário" quando o NOME/
caminho casa o heurístico de relatório OU quando o CONTEÚDO tem pelo menos
2 matches de métricas do §10 do plano de monetização — e dispara quando
falta o disclaimer CVM no mesmo arquivo.

Não generalizada em `regex_sobre_conteudo` — a CONTAGEM de matches
(`len(findall) >= 2`) não existe na Skill genérica (que só sabe presença/
ausência booleana), e o OR entre condição de caminho e condição de
conteúdo também não é expressável lá (`pattern_caminho_incluir` é um AND
independente, mesma limitação que motivou a bespoke FIN-005)."""

from __future__ import annotations

import hashlib
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

AGENTE = "compliance"
CATEGORIA = "cvm-relatorio-impacto"
CODIGO = "COMP-008"

# Heurístico de "é um relatório de impacto por usuário": nome do arquivo
# OU pelo menos 2 métricas do §10 do plano juntas (evita falso positivo
# em uma tela qualquer que só cite "sinais" isoladamente). Regexes
# idênticos aos do legado (`_NOME_RE`/`_METRICA_RE`/`_DISCLAIMER_RE`).
_NOME_RE = re.compile(
    r"relat[oó]rio.*(impacto|valor)|impact.*report|relatorio_(impacto|valor)", re.IGNORECASE
)
_METRICA_RE = re.compile(
    r"sinais\s+recebidos|sinais\s+operados|horas?\s+economizad"
    r"|opera[cç][ãa]o(?:ões)?\s+evitad|disciplina|erros?\s+evitad",
    re.IGNORECASE,
)
_DISCLAIMER_RE = re.compile(
    r"CVM|n[aã]o\s+constitui\s+(promessa|garantia|recomenda[cç][ãa]o)"
    r"|n[aã]o\s+realiza\s+recomenda[cç][ãa]o\s+individualizada",
    re.IGNORECASE,
)


class RegraComp008Spec(BaseModel):
    codigo: str = CODIGO
    agente: str = AGENTE
    severidade: str = "high"
    categoria: str = CATEGORIA
    titulo: str = "Relatório de impacto por usuário sem disclaimer CVM"
    causa: str = (
        "ADR 0023 / plano de monetização §10 (Relatório mensal de valor): "
        "todo relatório de impacto por usuário (sinais recebidos/operados, "
        "disciplina, erros evitados, horas economizadas etc.) é histórico de "
        "uso, NUNCA promessa de rentabilidade (P5) — precisa do disclaimer "
        "CVM fixo no mesmo componente/gerador. Sem ele, cria risco "
        "regulatório (promessa implícita de retorno)."
    )
    remediacao: str = (
        "Incluir o disclaimer CVM padrão (mesmo texto centralizado usado em "
        "outras superfícies do produto) no mesmo componente/gerador do "
        "relatório de impacto. DoD: todo arquivo que casa o heurístico de "
        "relatório de impacto também contém o texto do disclaimer."
    )


class EntradaComp008(BaseModel):
    """`tipo` é o mesmo discriminador estrutural usado em todas as Entradas
    desta migração (ver docstring de `EntradaAst.tipo`)."""

    tipo: Literal["comp008"] = "comp008"
    caminho: str
    conteudo: str | None = None
    regra: RegraComp008Spec = Field(default_factory=RegraComp008Spec)


class AchadoComp008(BaseModel):
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


class SaidaComp008(BaseModel):
    achados: list[AchadoComp008] = Field(default_factory=list)


class EntradaInvalida(Exception):
    """Levantada quando `entrada` não satisfaz `EntradaComp008` — vira
    SCHEMA_REJECTION no Execution Engine (Vol.III Cap.12)."""


def _computar_fingerprint(agente: str, categoria: str, caminho: str, codigo: str) -> str:
    normalizado = caminho.replace("\\", "/")
    bruto = f"{agente}|{categoria}|{normalizado}|{codigo}|"
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()


def avaliar_comp008(entrada: Any, contexto: ExecutionContext) -> Any:
    del contexto
    try:
        dados = EntradaComp008.model_validate(entrada)
    except Exception as exc:
        raise EntradaInvalida(str(exc)) from exc

    if dados.conteudo is None:
        return SaidaComp008(achados=[]).model_dump()

    # O legado avalia `_NOME_RE` sobre o caminho relativo já normalizado
    # com "/" (`ctx.rel(p).replace("\\", "/")`).
    rel = dados.caminho.replace("\\", "/")
    eh_relatorio = bool(_NOME_RE.search(rel)) or len(_METRICA_RE.findall(dados.conteudo)) >= 2
    if not eh_relatorio:
        return SaidaComp008(achados=[]).model_dump()
    if _DISCLAIMER_RE.search(dados.conteudo):
        return SaidaComp008(achados=[]).model_dump()

    regra = dados.regra
    achado = AchadoComp008(
        codigo=regra.codigo,
        agente=regra.agente,
        severidade=regra.severidade,
        categoria=regra.categoria,
        titulo=regra.titulo,
        descricao=f"{rel}: relatório de impacto por usuário sem disclaimer CVM.",
        causa=regra.causa,
        remediacao=regra.remediacao,
        arquivo=dados.caminho,
        fingerprint=_computar_fingerprint(regra.agente, regra.categoria, rel, regra.codigo),
    )
    return SaidaComp008(achados=[achado]).model_dump()


def construir_implementacao() -> CapabilityImplementation:
    definicao = CapabilityDefinition(
        id=CapabilityId("comp008-relatorio-impacto-sem-disclaimer"),
        name="COMP-008: relatório de impacto sem disclaimer CVM",
        version="1.0.0",
        input_schema=EntradaComp008.model_json_schema(),
        output_schema=SaidaComp008.model_json_schema(),
        deterministic=True,
        side_effects=SideEffects.NONE,
        idempotent=True,
    )

    conteudo_dispara = "<p>Sinais recebidos: 12</p>\n<p>Horas economizadas: 3</p>\n"
    conteudo_com_disclaimer = (
        "<p>Sinais recebidos: 12</p>\n"
        "<p>Horas economizadas: 3</p>\n"
        "<p>Este relatório não constitui promessa de rentabilidade (CVM).</p>\n"
    )
    entrada_sucesso = {
        "caminho": "frontend/src/pages/RelatorioImpacto.tsx",
        "conteudo": conteudo_dispara,
        "regra": {},
    }
    entrada_com_disclaimer = {
        "caminho": "frontend/src/pages/RelatorioImpacto.tsx",
        "conteudo": conteudo_com_disclaimer,
        "regra": {},
    }
    entrada_regra_invalida = {
        "caminho": "frontend/src/pages/RelatorioImpacto.tsx",
        "conteudo": conteudo_dispara,
        "regra": {"severidade": 123},
    }

    return CapabilityImplementation(
        definition=definicao,
        handler=avaliar_comp008,
        acceptance_tests=[
            AcceptanceTest(
                name="relatorio-de-impacto-sem-disclaimer-dispara",
                entrada=entrada_sucesso,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: len(saida["achados"]) == 1,
            ),
            AcceptanceTest(
                name="relatorio-com-disclaimer-nao-dispara",
                entrada=entrada_com_disclaimer,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: saida["achados"] == [],
            ),
            AcceptanceTest(
                name="entrada-sem-campo-obrigatorio-e-rejeitada",
                entrada={"conteudo": "x"},
                resultado_esperado=ResultadoEsperado.SCHEMA_REJECTION,
            ),
            AcceptanceTest(
                name="regra-com-tipo-de-campo-invalido-e-tratada-como-falha-de-invocacao",
                entrada=entrada_regra_invalida,
                resultado_esperado=ResultadoEsperado.TIMEOUT,
            ),
        ],
    )
