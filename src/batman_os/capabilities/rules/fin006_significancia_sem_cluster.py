"""Capability bespoke FIN-006 "significância (t/p) sem agrupar por dia"
(Vol.IV Cap.17).

Replica `Batman/scan/rules/financial_analyst.py::
SignificanceWithoutDayClustering`: presença de um t-ingênuo (t do IC via
raiz(N-3) ou `ttest_1samp` direto na série) E presença de vocabulário de
retorno/mercado E ausência de qualquer evidência de clusterização por dia
— agregado POR ARQUIVO.

Não generalizada em `regex_sobre_conteudo` — são 3 condições independentes
sobre o MESMO arquivo (presença AND presença AND ausência), e a Skill
genérica só combina 2 (`presenca-sem-mitigacao` = presença AND ausência).
Escopo de descoberta inclui `scripts/` além de `src_dirs` (diretório onde
mora a estatística do projeto motivador — ver comentário do legado sobre a
lição da ORA-006)."""

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

AGENTE = "financial-analyst"
CATEGORIA = "validacao"
CODIGO = "FIN-006"

# As formas ingênuas: o t do IC (t = IC*raiz(N-3)) e o t-teste direto na
# série. Case-SENSITIVE como no legado (`_T_INGENUO_RE` sem re.I).
_T_INGENUO_RE = re.compile(
    r"sqrt\(\s*(?:len\([^)]*\)|n|N|nobs)\s*-\s*3\s*\)"
    r"|\(\s*(?:len\([^)]*\)|n|N)\s*-\s*3\s*\)\s*\*\*\s*0?\.5"
    r"|ttest_1samp\s*\("
)
# Evidência de que o autor tratou o cluster. Basta UMA para não disparar.
_CLUSTER_RE = re.compile(
    r"groupby\s*\(\s*[^)]*(?:dat[ae]|dia|day)"
    r"|por_dia|by_day|per_day|daily_mean"
    r"|newey|west|cluster"
    r"|\.dt\.date|resample\s*\(",
    re.IGNORECASE,
)
# Sem retorno na jogada, um t-teste não é sobre mercado — não é escopo
# desta regra. NÃO inclui `return` (palavra-chave do Python, casaria em
# qualquer arquivo — mesma nota do legado).
_RETORNO_RE = re.compile(
    r"retorno|\breturns\b|\bret\b|ret_|_ret\b"
    r"|\bic\b|spearman|excess|pnl|alfa|alpha|slippage|backtest|trade|pre[cç]o|price",
    re.IGNORECASE,
)


class RegraFin006Spec(BaseModel):
    codigo: str = CODIGO
    agente: str = AGENTE
    severidade: str = "high"
    categoria: str = CATEGORIA
    titulo: str = "significância (t/p) calculada sobre N observações sem agrupar por dia"
    causa: str = (
        "Retornos de ativos diferentes NO MESMO DIA não são independentes — o mercado "
        "inteiro se move junto. Tratar N eventos espalhados por D dias como N "
        "observações independentes INFLA o t por um fator ~raiz(N/D), e o achado passa "
        "a parecer significante sem ser. Foi exatamente assim que o sinal de 1d deste "
        "projeto foi reportado com t=5,08 quando o t honesto, por dia, era 0,30 "
        "(S143). Um t inflado é o que autoriza pôr dinheiro real em ruído."
    )
    remediacao: str = (
        "Agregar DENTRO do dia (média/IC por dia) e calcular o t sobre os DIAS; ou usar "
        "erro-padrão robusto a cluster (Newey-West / cluster por data). Régua: fatia "
        "com menos de ~15 dias distintos não sustenta conclusão nenhuma, qualquer que "
        "seja o N. DoD: todo teste de significância sobre retornos agrupa por dia — e "
        "reporta o número de DIAS distintos, não só o N."
    )


class EntradaFin006(BaseModel):
    """`tipo` é o mesmo discriminador estrutural usado em todas as Entradas
    desta migração (ver docstring de `EntradaAst.tipo`)."""

    tipo: Literal["fin006"] = "fin006"
    caminho: str
    conteudo: str | None = None
    regra: RegraFin006Spec = Field(default_factory=RegraFin006Spec)


class AchadoFin006(BaseModel):
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


class SaidaFin006(BaseModel):
    achados: list[AchadoFin006] = Field(default_factory=list)


class EntradaInvalida(Exception):
    """Levantada quando `entrada` não satisfaz `EntradaFin006` — vira
    SCHEMA_REJECTION no Execution Engine (Vol.III Cap.12)."""


def _computar_fingerprint(agente: str, categoria: str, caminho: str, codigo: str) -> str:
    normalizado = caminho.replace("\\", "/")
    bruto = f"{agente}|{categoria}|{normalizado}|{codigo}|"
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()


def avaliar_fin006(entrada: Any, contexto: ExecutionContext) -> Any:
    del contexto
    try:
        dados = EntradaFin006.model_validate(entrada)
    except Exception as exc:
        raise EntradaInvalida(str(exc)) from exc

    if dados.conteudo is None:
        return SaidaFin006(achados=[]).model_dump()

    if not _T_INGENUO_RE.search(dados.conteudo):
        return SaidaFin006(achados=[]).model_dump()
    if not _RETORNO_RE.search(dados.conteudo):
        return SaidaFin006(achados=[]).model_dump()
    if _CLUSTER_RE.search(dados.conteudo):
        return SaidaFin006(achados=[]).model_dump()

    # Replica `grep_lines(text, _T_INGENUO_RE.pattern)` + `fmt_lines` do
    # legado (linhas 1-based onde o padrão casa, ordenadas, sem repetição).
    linhas = [
        i for i, linha in enumerate(dados.conteudo.splitlines(), 1) if _T_INGENUO_RE.search(linha)
    ]
    ls = ",".join(str(x) for x in sorted(set(linhas)))

    regra = dados.regra
    descricao = (
        f"{dados.caminho}: t/p sobre N observações sem agrupar por dia "
        f"(linhas {ls}) — t inflado por ~raiz(N/dias)."
    )
    achado = AchadoFin006(
        codigo=regra.codigo,
        agente=regra.agente,
        severidade=regra.severidade,
        categoria=regra.categoria,
        titulo=regra.titulo,
        descricao=descricao,
        causa=regra.causa,
        remediacao=regra.remediacao,
        arquivo=dados.caminho,
        fingerprint=_computar_fingerprint(
            regra.agente, regra.categoria, dados.caminho, regra.codigo
        ),
    )
    return SaidaFin006(achados=[achado]).model_dump()


def construir_implementacao() -> CapabilityImplementation:
    definicao = CapabilityDefinition(
        id=CapabilityId("fin006-significancia-sem-cluster-por-dia"),
        name="FIN-006: significância sem agrupar por dia",
        version="1.0.0",
        input_schema=EntradaFin006.model_json_schema(),
        output_schema=SaidaFin006.model_json_schema(),
        deterministic=True,
        side_effects=SideEffects.NONE,
        idempotent=True,
    )

    conteudo_dispara = "t = ic * sqrt(len(retornos) - 3)\n"
    conteudo_com_cluster = (
        "por_dia = df.groupby(df['data'])['retorno'].mean()\nt = ic * sqrt(len(por_dia) - 3)\n"
    )
    entrada_sucesso = {"caminho": "scripts/backtest_ic.py", "conteudo": conteudo_dispara}
    entrada_com_cluster = {"caminho": "scripts/backtest_ic.py", "conteudo": conteudo_com_cluster}
    entrada_regra_invalida = {
        "caminho": "scripts/backtest_ic.py",
        "conteudo": conteudo_dispara,
        "regra": {"severidade": 123},
    }

    return CapabilityImplementation(
        definition=definicao,
        handler=avaliar_fin006,
        acceptance_tests=[
            AcceptanceTest(
                name="t-ingenuo-com-retorno-sem-cluster-dispara",
                entrada=entrada_sucesso,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: len(saida["achados"]) == 1,
            ),
            AcceptanceTest(
                name="t-ingenuo-com-cluster-por-dia-nao-dispara",
                entrada=entrada_com_cluster,
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
