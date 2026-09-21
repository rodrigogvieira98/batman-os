"""Testes do loader do spec bespoke QAVIS-001 (`qavis001_loader.py`) —
capability qa-visual (Onda 1 do Plano Cobertura Total, S162)."""

from __future__ import annotations

from batman_os.capabilities.rules.qavis001_loader import carregar_especificacoes_qavis001
from batman_os.capabilities.rules.qavis001_playwright_falhou import RegraQaVis001Spec


class TestCarregarEspecificacoesQaVis001:
    def test_carrega_o_codigo_qavis001(self) -> None:
        specs = carregar_especificacoes_qavis001()
        codigos = {item["regra"].codigo for item in specs}
        assert codigos == {"QAVIS-001"}

    def test_toda_regra_e_uma_regraqavis001spec_valida(self) -> None:
        specs = carregar_especificacoes_qavis001()
        for item in specs:
            assert isinstance(item["regra"], RegraQaVis001Spec)

    def test_descoberta_e_do_tipo_playwright(self) -> None:
        specs = carregar_especificacoes_qavis001()
        for item in specs:
            assert item["descoberta"]["tipo"] == "playwright"

    def test_descoberta_nao_aponta_para_producao(self) -> None:
        """qa-visual NUNCA roda contra PRD — a lista de domínios proibidos
        no spec commitado tem que incluir o domínio nu de produção."""
        specs = carregar_especificacoes_qavis001()
        for item in specs:
            assert "exemplo.group" in item["descoberta"].get("dominios_proibidos", [])
