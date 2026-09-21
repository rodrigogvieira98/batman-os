"""Testes de comportamento do spec COMP-007 (`specs/lote_03/COMP-007.json`)
contra o handler genérico `avaliar_regra_regex` — replica
`Batman/scan/rules/compliance.py::ForbiddenPricingCopy`: comparação com
Selic/CDI/poupança OU (patrimônio% E palavra de preço) NA MESMA LINHA.

O legado avalia linha a linha; o spec preserva essa semântica com
`[^\\S\\n]+` no lugar de `\\s+` (espaço que não cruza linha) e com o grupo
`(?m:^(?=...)(?=...))` para o AND-na-mesma-linha."""

from __future__ import annotations

from typing import Any

from batman_os.capabilities.operator import ExecutionContext
from batman_os.capabilities.rules.lote_03 import carregar_lote_03
from batman_os.capabilities.rules.regex_sobre_conteudo import RegraSpec, avaliar_regra_regex
from batman_os.cli.descoberta_arquivos import arquivos_para_regra
from batman_os.foundation.types import MissionId, StepId, TenantId, agora


def _contexto() -> ExecutionContext:
    return ExecutionContext(
        mission_id=MissionId("m-1"),
        tenant_id=TenantId("t-1"),
        step_id=StepId("s-1"),
        deadline=agora(),
    )


def _spec_comp007() -> dict[str, Any]:
    [item] = [i for i in carregar_lote_03() if i["regra"].codigo == "COMP-007"]
    return {"regra": item["regra"], "descoberta": item["descoberta"]}


def _avaliar(conteudo: str) -> Any:
    regra: RegraSpec = _spec_comp007()["regra"]
    entrada = {
        "caminho": "frontend/src/pages/Planos.tsx",
        "conteudo": conteudo,
        "regra": regra.model_dump(),
    }
    return avaliar_regra_regex(entrada, _contexto())


class TestComparacaoComRendaFixa:
    def test_dispara_para_rende_mais_que(self) -> None:
        saida = _avaliar("<p>Rende mais que a poupança</p>\n")
        assert len(saida["achados"]) == 1

    def test_dispara_para_supera_a_selic(self) -> None:
        saida = _avaliar("<p>Nosso radar supera a Selic todo mês</p>\n")
        assert len(saida["achados"]) == 1

    def test_dispara_para_deixar_seu_dinheiro_parado(self) -> None:
        saida = _avaliar("<p>Pare de deixar seu dinheiro parado</p>\n")
        assert len(saida["achados"]) == 1

    def test_nao_dispara_para_comparacao_quebrada_em_duas_linhas(self) -> None:
        # o legado avalia LINHA a LINHA — um match que só existiria
        # atravessando o \n não pode disparar.
        saida = _avaliar("<p>rende\nmais que a Selic</p>\n")
        assert saida["achados"] == []

    def test_nao_dispara_para_mencao_neutra_a_selic(self) -> None:
        saida = _avaliar("<p>Taxa Selic atual: 10,5%</p>\n")
        assert saida["achados"] == []


class TestPatrimonioPercentualMaisPalavraDePreco:
    def test_dispara_quando_pct_patrimonio_e_preco_na_mesma_linha(self) -> None:
        saida = _avaliar("<p>Assinatura equivale a 1% do seu patrimônio</p>\n")
        assert len(saida["achados"]) == 1

    def test_dispara_com_a_palavra_percentual(self) -> None:
        saida = _avaliar("<p>selo definido por percentual do patrimônio</p>\n")
        assert len(saida["achados"]) == 1

    def test_nao_dispara_sem_palavra_de_preco_na_linha(self) -> None:
        # stat tile de equity/patrimônio simulado — falso positivo que o
        # legado evita de propósito.
        saida = _avaliar("<p>Evolução do patrimônio (%)</p>\n")
        assert saida["achados"] == []

    def test_nao_dispara_quando_preco_esta_em_outra_linha(self) -> None:
        saida = _avaliar("<p>1% do patrimônio</p>\n<p>assinatura mensal</p>\n")
        assert saida["achados"] == []

    def test_nao_dispara_para_gestao_de_patrimonio_sem_percentual(self) -> None:
        saida = _avaliar("<p>plano de gestão de patrimônio</p>\n")
        assert saida["achados"] == []


class TestDescobertaExcluiAdminETestes:
    def test_descoberta_exclui_pages_admin_e_arquivos_de_teste(self, tmp_path: Any) -> None:
        base = tmp_path / "frontend" / "src" / "pages"
        (base / "admin").mkdir(parents=True)
        (base / "Planos.tsx").write_text("supera a Selic", encoding="utf-8")
        (base / "admin" / "Motor.tsx").write_text("supera a Selic", encoding="utf-8")
        (base / "Planos.test.tsx").write_text("supera a Selic", encoding="utf-8")

        descoberta = _spec_comp007()["descoberta"]
        caminhos = {rel for rel, _ in arquivos_para_regra(tmp_path, descoberta)}

        assert "frontend/src/pages/Planos.tsx" in caminhos
        assert "frontend/src/pages/admin/Motor.tsx" not in caminhos
        assert "frontend/src/pages/Planos.test.tsx" not in caminhos
