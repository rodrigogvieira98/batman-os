"""QA-AUTO-001 mede COBERTURA, nao nome exato de arquivo.

Ate 2026-09-04 a regra exigia `test_<router>.py` e nada mais. Num projeto que
nomeia o teste pelo CASO em vez de pelo modulo, isso acusa quase tudo: medido no
radar-preditivo, 44 dos 45 routers eram acusados enquanto 125 arquivos de teste
exercitavam routers. Depois do conserto: 19.
"""

from __future__ import annotations

from batman_os.capabilities.rules.qaauto001_router_sem_teste import _tem_teste


class TestTemTeste:
    def test_nome_exato_continua_valendo(self) -> None:
        assert _tem_teste("carteira", {"carteira"})
        assert _tem_teste("carteira", {"test_carteira"})

    def test_prefixo_com_separador_conta(self) -> None:
        """O conserto: `admin` e coberto por `test_admin_kpis_exclusao.py`."""
        assert _tem_teste("admin", {"admin_kpis_exclusao", "outra_coisa"})
        assert _tem_teste("auth", {"auth_endpoints"})

    def test_prefixo_SEM_separador_nao_conta(self) -> None:
        """O par negativo, e ele e o que impede o conserto de virar cegueira.

        Sem exigir o `_`, `sim` casaria `simulacoes` e `admin` casaria
        `administracao` -- a regra pararia de acusar router nenhum e o falso
        positivo viraria falso NEGATIVO, que custa mais caro.
        """
        assert not _tem_teste("sim", {"simulacoes"})
        assert not _tem_teste("admin", {"administracao"})

    def test_sem_nenhum_teste_continua_acusando(self) -> None:
        assert not _tem_teste("oportunidades", {"admin_kpis", "carteira_router"})

    def test_lista_vazia(self) -> None:
        assert not _tem_teste("qualquer", set())
