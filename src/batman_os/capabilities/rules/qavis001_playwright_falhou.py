"""Capability bespoke `qa-visual` v1 (card `BATMAN_QAVIS01` do
radar-preditivo, cegueira nº1 do Plano Cobertura Total — Onda 1, S162):
roda os specs Playwright do repo-alvo contra o STAGING pós-deploy e mapeia
o relatório JSON (`npx playwright test --reporter=json`) para achados —
cada spec quebrado vira um achado, com severidade/fingerprint, no mesmo
"padrão-regras-subprocesso" das demais Capabilities desta migração.

Prova motivadora (achado real, S162): o menu cortado a 100% de zoom passou
por TODO o scanner estático (282 specs) — ninguém RENDERIZA a tela. Este é
o primeiro agente que efetivamente abre o navegador.

Cuidados obrigatórios (lições registradas no Plano Cobertura Total):
- **Timeout ≠ quebrado**: o subprocess do `npx playwright test` pode
  estourar o teto do scanner (`_rodar_subprocess_cacheado` sentinela `-2`)
  sem que a suíte esteja de fato quebrada — vira achado PRÓPRIO, sempre
  `low` ("suite Playwright lenta demais para o scanner"), nunca `high`
  (mesmo raciocínio da recalibração QA-RUN-001, `execucao_comando_
  interpretada.py`).
- **Nunca contra PRD**: a descoberta (`cli/descoberta_arquivos.py::
  _resultado_playwright`) recusa rodar se `base_url` resolver para o
  domínio de produção nu (`exemplo.group`) — o handler aqui só recebe
  o resultado já bloqueado (`bloqueado_prd=True`) e emite um achado de
  configuração, nunca chega a invocar o navegador contra PRD.
- **Conta de teste isenta**: já existe no repositório alvo (specs do
  radar usam `qa-viewer@exemplo.group`) — responsabilidade dos
  próprios specs `.spec.ts`, não deste handler.

O `subprocess` em si é executado por `cli/descoberta_arquivos.py`
(descoberta tipo `"playwright"`, reaproveitando `_rodar_subprocess_
cacheado` — inclusive o fix de árvore/timeout do Windows) — o handler aqui
só INTERPRETA o relatório JSON já capturado em `stdout`.
"""

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

_CHAVE_TIMEOUT_SCANNER = "timeout-do-scanner"
_CHAVE_BLOQUEADO_PRD = "bloqueado-prd"
_STATUS_FALHOS = {"failed", "timedOut", "interrupted"}


class RegraQaVis001Spec(BaseModel):
    codigo: str
    agente: str
    severidade: str
    categoria: str
    titulo: str
    causa: str
    remediacao: str


class EntradaQaVis001(BaseModel):
    tipo: Literal["qavis001"] = "qavis001"
    caminho: str
    conteudo: str | None = None
    regra: RegraQaVis001Spec


class AchadoQaVis001(BaseModel):
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


class SaidaQaVis001(BaseModel):
    achados: list[AchadoQaVis001] = Field(default_factory=list)


class EntradaInvalida(Exception):
    """Levantada quando `entrada` não satisfaz `EntradaQaVis001` — vira
    SCHEMA_REJECTION no Execution Engine (Vol.III Cap.12)."""


class _ResultadoPlaywright(BaseModel):
    """Formato empacotado por `cli/descoberta_arquivos.py::
    _resultado_playwright` em `EntradaQaVis001.conteudo`."""

    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    frontend_dir_existe: bool | None = None
    bloqueado_prd: bool = False
    base_url: str | None = None


def _computar_fingerprint(
    agente: str, categoria: str, caminho: str, codigo: str, chave: str
) -> str:
    import hashlib

    normalizado = caminho.replace("\\", "/")
    bruto = f"{agente}|{categoria}|{normalizado}|{codigo}|{chave}"
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()


def _achado(
    regra: RegraQaVis001Spec,
    caminho: str,
    descricao: str,
    chave: str,
    *,
    severidade: str | None = None,
) -> AchadoQaVis001:
    fingerprint = _computar_fingerprint(regra.agente, regra.categoria, caminho, regra.codigo, chave)
    return AchadoQaVis001(
        codigo=regra.codigo,
        agente=regra.agente,
        severidade=severidade or regra.severidade,
        categoria=regra.categoria,
        titulo=regra.titulo,
        descricao=descricao,
        causa=regra.causa,
        remediacao=regra.remediacao,
        arquivo=caminho,
        chave=chave,
        fingerprint=fingerprint,
    )


def _mensagem_erro_do_spec(spec: dict[str, Any]) -> str:
    """Extrai uma mensagem curta de erro do 1º resultado com status falho
    dentro de `spec["tests"][*]["results"]` — formato do reporter JSON do
    Playwright (`tests[].results[].error.message`)."""
    for teste in spec.get("tests", []) or []:
        for resultado in teste.get("results", []) or []:
            if resultado.get("status") in _STATUS_FALHOS:
                erro = resultado.get("error") or {}
                msg = erro.get("message") or resultado.get("status") or "falhou"
                return str(msg).splitlines()[0][:300]
    return "falhou (ver relatório completo)"


def _specs_falhos(suite: dict[str, Any], arquivo_pai: str) -> list[tuple[str, dict[str, Any]]]:
    """Percorre `suites` recursivamente (o reporter JSON do Playwright
    aninha suites por describe/arquivo) coletando `(arquivo, spec)` para
    todo spec cujo `ok` seja `False` — `ok` agrega todos os projetos/
    retries daquele spec (só `True` se todos passaram ou falharam como
    esperado)."""
    encontrados: list[tuple[str, dict[str, Any]]] = []
    arquivo_atual = suite.get("file") or arquivo_pai
    for spec in suite.get("specs", []) or []:
        if spec.get("ok") is False:
            encontrados.append((spec.get("file") or arquivo_atual, spec))
    for sub in suite.get("suites", []) or []:
        encontrados.extend(_specs_falhos(sub, arquivo_atual))
    return encontrados


def _interpretar_relatorio(
    regra: RegraQaVis001Spec, caminho: str, relatorio: dict[str, Any]
) -> list[AchadoQaVis001]:
    suites = relatorio.get("suites", []) or []
    especificacoes_falhas: list[tuple[str, dict[str, Any]]] = []
    for suite in suites:
        especificacoes_falhas.extend(_specs_falhos(suite, caminho))

    achados: list[AchadoQaVis001] = []
    for arquivo, spec in especificacoes_falhas:
        titulo_spec = str(spec.get("title", "?"))
        msg = _mensagem_erro_do_spec(spec)
        descricao = f"{arquivo}: spec '{titulo_spec}' falhou — {msg}"
        chave = f"{arquivo}::{titulo_spec}"
        achados.append(_achado(regra, arquivo, descricao, chave))
    return achados


def avaliar_qavis001(entrada: Any, contexto: ExecutionContext) -> Any:
    del contexto
    try:
        dados = EntradaQaVis001.model_validate(entrada)
    except Exception as exc:
        raise EntradaInvalida(str(exc)) from exc

    regra = dados.regra

    if dados.conteudo is None:
        return SaidaQaVis001(achados=[]).model_dump()

    try:
        resultado = _ResultadoPlaywright.model_validate(json.loads(dados.conteudo))
    except Exception:
        return SaidaQaVis001(achados=[]).model_dump()

    if resultado.bloqueado_prd:
        achado = _achado(
            regra,
            dados.caminho,
            "qa-visual configurado com base_url de PRODUÇÃO — execução BLOQUEADA "
            "(qa-visual nunca roda contra exemplo.group; aponte para "
            "staging.exemplo.group ou um ambiente local)",
            _CHAVE_BLOQUEADO_PRD,
            severidade="low",
        )
        return SaidaQaVis001(achados=[achado]).model_dump()

    if resultado.frontend_dir_existe is False:
        return SaidaQaVis001(achados=[]).model_dump()

    if resultado.returncode == -2:
        achado = _achado(
            regra,
            dados.caminho,
            f"npx playwright test excedeu o timeout do scanner ({resultado.stderr}) — suite "
            "lenta demais para o scanner, NAO necessariamente quebrada (rode "
            "'npx playwright test' localmente sem o teto do scanner para confirmar)",
            _CHAVE_TIMEOUT_SCANNER,
            severidade="low",
        )
        return SaidaQaVis001(achados=[achado]).model_dump()

    if resultado.returncode == -1:
        achado = _achado(
            regra,
            dados.caminho,
            f"npx/playwright não encontrado ou não instalado: {resultado.stderr}",
            "playwright-nao-encontrado",
            severidade="low",
        )
        return SaidaQaVis001(achados=[achado]).model_dump()

    try:
        relatorio = json.loads(resultado.stdout) if resultado.stdout else None
    except json.JSONDecodeError:
        relatorio = None

    if not isinstance(relatorio, dict):
        # rc != 0 sem JSON parseável em stdout -- npx/playwright quebrou
        # antes de sequer rodar um teste (config inválida, install ausente,
        # etc.) -- achado próprio, distinto de "spec falhou".
        if resultado.returncode not in (0, None):
            achado = _achado(
                regra,
                dados.caminho,
                f"npx playwright test retornou exit {resultado.returncode} sem relatório "
                f"JSON válido: {resultado.stderr or resultado.stdout}",
                "playwright-sem-relatorio",
            )
            return SaidaQaVis001(achados=[achado]).model_dump()
        return SaidaQaVis001(achados=[]).model_dump()

    achados = _interpretar_relatorio(regra, dados.caminho, relatorio)
    return SaidaQaVis001(achados=achados).model_dump()


def construir_implementacao() -> CapabilityImplementation:
    definicao = CapabilityDefinition(
        id=CapabilityId("qavis001-playwright-falhou"),
        name="QA-VIS-001 specs Playwright falhos contra staging",
        version="1.0.0",
        input_schema=EntradaQaVis001.model_json_schema(),
        output_schema=SaidaQaVis001.model_json_schema(),
        deterministic=True,
        side_effects=SideEffects.NONE,
        idempotent=True,
    )

    _regra_teste = {
        "codigo": "QAVIS-001",
        "agente": "qa-automation",
        "severidade": "high",
        "categoria": "qa-visual",
        "titulo": "t",
        "causa": "c",
        "remediacao": "r",
    }

    relatorio_com_falha = {
        "suites": [
            {
                "file": "e2e/smoke/viewer-nav-desktop.spec.ts",
                "specs": [
                    {
                        "title": "A-01-1 badge de ambiente visível",
                        "ok": False,
                        "tests": [
                            {
                                "results": [
                                    {
                                        "status": "failed",
                                        "error": {"message": "Timed out waiting for selector"},
                                    }
                                ]
                            }
                        ],
                    },
                    {"title": "A-01-2 nav Início carrega", "ok": True, "tests": []},
                ],
                "suites": [],
            }
        ]
    }
    relatorio_tudo_verde = {
        "suites": [
            {
                "file": "e2e/smoke/viewer-nav-desktop.spec.ts",
                "specs": [{"title": "A-01-1", "ok": True, "tests": []}],
                "suites": [],
            }
        ]
    }

    entrada_falha = {
        "caminho": "frontend/e2e/",
        "conteudo": json.dumps(
            {
                "returncode": 1,
                "stdout": json.dumps(relatorio_com_falha),
                "stderr": "",
                "frontend_dir_existe": True,
                "bloqueado_prd": False,
            }
        ),
        "regra": _regra_teste,
    }
    entrada_verde = {
        "caminho": "frontend/e2e/",
        "conteudo": json.dumps(
            {
                "returncode": 0,
                "stdout": json.dumps(relatorio_tudo_verde),
                "stderr": "",
                "frontend_dir_existe": True,
                "bloqueado_prd": False,
            }
        ),
        "regra": _regra_teste,
    }
    entrada_timeout = {
        "caminho": "frontend/e2e/",
        "conteudo": json.dumps(
            {
                "returncode": -2,
                "stdout": "",
                "stderr": "comando excedeu timeout de 300s",
                "frontend_dir_existe": True,
                "bloqueado_prd": False,
            }
        ),
        "regra": _regra_teste,
    }
    entrada_bloqueado_prd = {
        "caminho": "frontend/e2e/",
        "conteudo": json.dumps(
            {
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "bloqueado_prd": True,
                "base_url": "https://exemplo.group",
            }
        ),
        "regra": _regra_teste,
    }
    entrada_relatorio_malformado = {
        "caminho": "frontend/e2e/",
        "conteudo": json.dumps(
            {
                "returncode": 1,
                # "suites" com um elemento que NAO e dict -- `_specs_falhos`
                # chama `.get()` nele e levanta AttributeError (falha de
                # invocacao real, nao coberta por schema/JSON parsing).
                "stdout": json.dumps({"suites": ["nao-e-um-dict"]}),
                "stderr": "",
                "frontend_dir_existe": True,
                "bloqueado_prd": False,
            }
        ),
        "regra": _regra_teste,
    }

    return CapabilityImplementation(
        definition=definicao,
        handler=avaliar_qavis001,
        acceptance_tests=[
            AcceptanceTest(
                name="spec-quebrado-dispara-achado",
                entrada=entrada_falha,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: len(saida["achados"]) == 1,
            ),
            AcceptanceTest(
                name="tudo-verde-nao-dispara",
                entrada=entrada_verde,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: saida["achados"] == [],
            ),
            AcceptanceTest(
                name="timeout-do-scanner-vira-achado-low-proprio",
                entrada=entrada_timeout,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: (
                    len(saida["achados"]) == 1 and saida["achados"][0]["severidade"] == "low"
                ),
            ),
            AcceptanceTest(
                name="bloqueado-prd-nunca-roda-e-vira-achado-low",
                entrada=entrada_bloqueado_prd,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: (
                    len(saida["achados"]) == 1 and saida["achados"][0]["severidade"] == "low"
                ),
            ),
            AcceptanceTest(
                name="entrada-sem-campo-obrigatorio-e-rejeitada",
                entrada={"caminho": "x"},
                resultado_esperado=ResultadoEsperado.SCHEMA_REJECTION,
            ),
            AcceptanceTest(
                name="relatorio-com-suite-malformada-e-tratado-como-falha-de-invocacao",
                entrada=entrada_relatorio_malformado,
                resultado_esperado=ResultadoEsperado.TIMEOUT,
            ),
        ],
    )
