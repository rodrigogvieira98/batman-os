"""Testes da Capability bespoke `qa-visual` v1 (QAVIS-001, Onda 1 do Plano
Cobertura Total, S162)."""

from __future__ import annotations

import json

import pytest

from batman_os.capabilities.capability_contract import certificar
from batman_os.capabilities.operator import ExecutionContext
from batman_os.capabilities.rules.qavis001_playwright_falhou import (
    EntradaInvalida,
    avaliar_qavis001,
    construir_implementacao,
)
from batman_os.foundation.types import MissionId, StepId, TenantId, agora
from batman_os.runtime.capability_engine import StatusCapability


def _contexto() -> ExecutionContext:
    return ExecutionContext(
        mission_id=MissionId("m-1"),
        tenant_id=TenantId("t-1"),
        step_id=StepId("s-1"),
        deadline=agora(),
    )


def _regra(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "codigo": "QAVIS-001",
        "agente": "qa-automation",
        "severidade": "high",
        "categoria": "qa-visual",
        "titulo": "titulo",
        "causa": "causa",
        "remediacao": "remediacao",
    }
    base.update(overrides)
    return base


def _entrada(resultado: dict[str, object], caminho: str = "frontend/e2e/") -> dict[str, object]:
    return {"caminho": caminho, "conteudo": json.dumps(resultado), "regra": _regra()}


def _relatorio(
    specs: list[dict[str, object]], file: str = "e2e/smoke/x.spec.ts"
) -> dict[str, object]:
    return {"suites": [{"file": file, "specs": specs, "suites": []}]}


class TestSpecFalho:
    def test_spec_com_ok_false_dispara_achado(self) -> None:
        relatorio = _relatorio(
            [
                {
                    "title": "A-01-1 badge visivel",
                    "ok": False,
                    "tests": [
                        {"results": [{"status": "failed", "error": {"message": "Timed out"}}]}
                    ],
                }
            ]
        )
        entrada = _entrada(
            {
                "returncode": 1,
                "stdout": json.dumps(relatorio),
                "stderr": "",
                "frontend_dir_existe": True,
                "bloqueado_prd": False,
            }
        )
        saida = avaliar_qavis001(entrada, _contexto())
        assert len(saida["achados"]) == 1
        assert "A-01-1" in saida["achados"][0]["descricao"]
        assert "Timed out" in saida["achados"][0]["descricao"]

    def test_spec_com_ok_true_nao_dispara(self) -> None:
        relatorio = _relatorio([{"title": "A-01-1", "ok": True, "tests": []}])
        entrada = _entrada(
            {
                "returncode": 0,
                "stdout": json.dumps(relatorio),
                "stderr": "",
                "frontend_dir_existe": True,
                "bloqueado_prd": False,
            }
        )
        saida = avaliar_qavis001(entrada, _contexto())
        assert saida["achados"] == []

    def test_multiplos_specs_falhos_em_suites_aninhadas(self) -> None:
        relatorio = {
            "suites": [
                {
                    "file": "e2e/a.spec.ts",
                    "specs": [{"title": "a1", "ok": False, "tests": []}],
                    "suites": [
                        {
                            "file": "e2e/a.spec.ts",
                            "specs": [{"title": "a2", "ok": False, "tests": []}],
                            "suites": [],
                        }
                    ],
                }
            ]
        }
        entrada = _entrada(
            {
                "returncode": 1,
                "stdout": json.dumps(relatorio),
                "stderr": "",
                "frontend_dir_existe": True,
                "bloqueado_prd": False,
            }
        )
        saida = avaliar_qavis001(entrada, _contexto())
        assert len(saida["achados"]) == 2
        fingerprints = {a["fingerprint"] for a in saida["achados"]}
        assert len(fingerprints) == 2  # cada spec falho tem fingerprint proprio


class TestTimeoutDoScannerNuncaHigh:
    """Mesma lição da recalibração QA-RUN-001: timeout do subprocess
    (`_rodar_subprocess_cacheado` sentinela -2) NUNCA é 'suite quebrada'."""

    def test_timeout_vira_achado_low_proprio(self) -> None:
        entrada = _entrada(
            {
                "returncode": -2,
                "stdout": "",
                "stderr": "comando excedeu timeout de 300s",
                "frontend_dir_existe": True,
                "bloqueado_prd": False,
            }
        )
        saida = avaliar_qavis001(entrada, _contexto())
        assert len(saida["achados"]) == 1
        assert saida["achados"][0]["severidade"] == "low"
        assert saida["achados"][0]["chave"] == "timeout-do-scanner"


class TestBloqueadoPrd:
    """qa-visual NUNCA roda contra PRD — a descoberta já recusa ANTES de
    invocar qualquer subprocess; o handler só vê `bloqueado_prd=True`."""

    def test_bloqueado_prd_dispara_achado_low_de_configuracao(self) -> None:
        entrada = _entrada(
            {
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "bloqueado_prd": True,
                "base_url": "https://exemplo.group",
            }
        )
        saida = avaliar_qavis001(entrada, _contexto())
        assert len(saida["achados"]) == 1
        assert saida["achados"][0]["severidade"] == "low"
        assert (
            "PRODUÇÃO" in saida["achados"][0]["descricao"]
            or "producao" in saida["achados"][0]["descricao"].lower()
        )


class TestFrontendDirAusente:
    def test_dir_ausente_nao_dispara(self) -> None:
        entrada = _entrada(
            {
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "frontend_dir_existe": False,
                "bloqueado_prd": False,
            }
        )
        saida = avaliar_qavis001(entrada, _contexto())
        assert saida["achados"] == []


class TestPlaywrightIndisponivel:
    def test_comando_nao_encontrado_dispara_achado_low(self) -> None:
        entrada = _entrada(
            {
                "returncode": -1,
                "stdout": "",
                "stderr": "comando não encontrado",
                "frontend_dir_existe": True,
                "bloqueado_prd": False,
            }
        )
        saida = avaliar_qavis001(entrada, _contexto())
        assert len(saida["achados"]) == 1
        assert saida["achados"][0]["severidade"] == "low"

    def test_relatorio_json_invalido_com_rc_nao_zero_dispara_achado(self) -> None:
        entrada = _entrada(
            {
                "returncode": 1,
                "stdout": "nao e json",
                "stderr": "crash de configuracao",
                "frontend_dir_existe": True,
                "bloqueado_prd": False,
            }
        )
        saida = avaliar_qavis001(entrada, _contexto())
        assert len(saida["achados"]) == 1
        assert saida["achados"][0]["chave"] == "playwright-sem-relatorio"


class TestConteudoAusenteOuMalformado:
    def test_conteudo_none_retorna_vazio(self) -> None:
        entrada = {"caminho": "x", "conteudo": None, "regra": _regra()}
        saida = avaliar_qavis001(entrada, _contexto())
        assert saida["achados"] == []

    def test_conteudo_nao_json_retorna_vazio(self) -> None:
        entrada = {"caminho": "x", "conteudo": "nao e json", "regra": _regra()}
        saida = avaliar_qavis001(entrada, _contexto())
        assert saida["achados"] == []


class TestEntradaInvalida:
    def test_levanta_excecao_sem_campo_regra(self) -> None:
        with pytest.raises(EntradaInvalida):
            avaliar_qavis001({"caminho": "x"}, _contexto())


class TestCertificacao:
    def test_implementacao_real_passa_na_certificacao(self) -> None:
        impl = construir_implementacao()
        contexto = _contexto()
        relatorio = _relatorio([{"title": "a", "ok": False, "tests": []}])
        entrada_idempotencia = _entrada(
            {
                "returncode": 1,
                "stdout": json.dumps(relatorio),
                "stderr": "",
                "frontend_dir_existe": True,
                "bloqueado_prd": False,
            }
        )
        definicao_certificada = certificar(
            impl,
            entrada_para_teste_idempotencia=entrada_idempotencia,
            contexto_para_teste_idempotencia=contexto,
        )
        assert definicao_certificada.status == StatusCapability.ACTIVE
