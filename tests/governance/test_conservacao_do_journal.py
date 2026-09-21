"""Nada emitido pode sumir do journal — `AUD-002`.

*"Para toda janela: entregues + suprimidos + silenciados + sem canal = total
emitido. Diferença de um alerta já é divergência."* E, até 2026-09-17, **a conta
nunca tinha sido conferida por ninguém**.

**MEDIDA em produção em 17/09**, sobre 16 342 registros:

    sem_canal ................ 6 845      entregue .................. 359
    silenciado_por_politica .. 6 289      silenciado_incidente_curto  110
    suprimido_por_janela ..... 2 671      silenciado_falha_de_caminho  54
                                          editado .................... 13
                                          edicao_impossivel ........... 1

    soma = 16 342 = total                                    ✓ FECHA
    entregue + editado = 372 = notification_sent=True        ✓ BATEM

⚠️ **E medindo apareceu o risco que o card não previa: o vocabulário estava
espalhado por três lugares.** Aqui, como constante em `transiente.py`, e como
**string solta** em `functional_monitor.py:871`. Um erro de digitação criaria um
balde novo em silêncio — o alerta existiria, estaria journalado, e não seria
contado em lugar nenhum. A conta pararia de fechar sem ninguém perceber.

Este arquivo fecha as duas pontas: a conta, e o vocabulário que a sustenta.
"""

from __future__ import annotations

import re
from pathlib import Path

import batman_os
from batman_os.governance.alert_journal import (
    OUTCOMES_CONHECIDOS,
    OUTCOMES_ENTREGUES,
    conservacao,
)

SRC = Path(batman_os.__file__).parent

#: Como um desfecho se parece no código: string literal atribuída a `outcome`.
_LITERAL = re.compile(r'"outcome":\s*"([a-z_]+)"|outcome=["\']([a-z_]+)["\']')


def _outcomes_no_codigo() -> set[str]:
    achados: set[str] = set()
    for arq in SRC.rglob("*.py"):
        if arq.name == "alert_journal.py":
            continue  # onde o vocabulário é DECLARADO
        for m in _LITERAL.finditer(arq.read_text(encoding="utf-8", errors="replace")):
            achados.add(m.group(1) or m.group(2))
    return achados


class TestOVocabularioNaoSeEspalha:
    def test_a_varredura_enxerga_alguma_coisa(self) -> None:
        """⚠️ Guarda que não acha nada passa sempre — a armadilha nº 1 aplicada
        ao próprio guarda."""
        assert _outcomes_no_codigo(), "a varredura por desfecho não achou nenhum"

    def test_todo_desfecho_usado_esta_declarado(self) -> None:
        faltando = sorted(_outcomes_no_codigo() - OUTCOMES_CONHECIDOS)
        assert faltando == [], (
            f"desfecho(s) usados em src/ e NÃO declarados em OUTCOMES_CONHECIDOS: {faltando}. "
            "Um desfecho fora do vocabulário vira balde novo em silêncio, e a conta de "
            "conservação para de fechar sem ninguém perceber."
        )


class TestAContaFecha:
    def test_janela_coerente_fecha(self) -> None:
        c = conservacao(
            [
                {"outcome": "entregue", "notification_sent": True},
                {"outcome": "editado", "notification_sent": True},
                {"outcome": "sem_canal", "notification_sent": False},
                {"outcome": "suprimido_por_janela", "notification_sent": False},
            ]
        )
        assert c.total == 4
        assert c.soma_fecha
        assert c.entrega_bate
        assert c.ok

    def test_janela_vazia_fecha(self) -> None:
        assert conservacao([]).ok


class TestAsTresFormasDeDIVERGIR:
    def test_desfecho_desconhecido_e_denunciado(self) -> None:
        """Uma grafia nova — `silenciado_por_politca`, por exemplo — não pode
        passar como se fosse um desfecho legítimo."""
        c = conservacao([{"outcome": "silenciado_por_politca", "notification_sent": False}])
        assert not c.ok
        assert c.desconhecidos == {"silenciado_por_politca": 1}

    def test_entrega_sem_desfecho_de_entrega_e_denunciada(self) -> None:
        """⚠️ O sink diz que enviou, e o desfecho diz que silenciou. Os dois não
        podem estar certos, e a divergência tem de aparecer."""
        c = conservacao([{"outcome": "sem_canal", "notification_sent": True}])
        assert not c.entrega_bate
        assert not c.ok

    def test_desfecho_de_entrega_sem_marca_de_envio_e_denunciado(self) -> None:
        """A ponta oposta: o desfecho diz entregue e o sink não marcou."""
        c = conservacao([{"outcome": "entregue", "notification_sent": False}])
        assert not c.entrega_bate

    def test_registro_sem_outcome_nao_some_da_conta(self) -> None:
        """⚠️ O pior caso: um registro sem desfecho seria invisível numa contagem
        por chave. Ele entra como desfecho vazio, e vazio não está no
        vocabulário — então é denunciado, em vez de sumir."""
        c = conservacao([{"notification_sent": False}])
        assert c.total == 1
        assert c.soma_fecha, "a soma tem de continuar batendo com o total"
        assert not c.ok
        assert "" in c.desconhecidos


class TestOsEntreguesSaoOsDoisQueOSinkMarca:
    def test_apenas_entregue_e_editado_contam_como_entrega(self) -> None:
        assert OUTCOMES_ENTREGUES == {"entregue", "editado"}
        assert OUTCOMES_ENTREGUES <= OUTCOMES_CONHECIDOS
