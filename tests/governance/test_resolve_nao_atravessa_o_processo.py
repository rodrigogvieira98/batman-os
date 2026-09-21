"""`resolve` é exigido pela spec e **inutilizável entre ciclos** — `DIV-FIO-004`.

O card dizia "`GovernanceEngine.resolve` existe e ninguém chama". Medido em
2026-09-17, o diagnóstico é mais forte que isso, e muda o que fazer:

**1. A spec EXIGE o método.** `docs/spec/07-governance/01-governance-engine.md:53`
declara `resolve(alertId: AlertId, resolution: string): void` na interface, e a
tabela de implementação confirma *"GovernanceEngine completo
(raise_alert/get_open_alerts/acknowledge/resolve)"*. Pela regra de ouro do
projeto, divergência entre código e spec resolve **a favor da spec** — apagá-lo
exigiria ADR, não um commit.

**2. `RESOLVED` é escrito por `resolve` e lido por NINGUÉM.** Nenhum consumidor
em `src/`, e a API HTTP não o expõe.

**3. ⚠️ E ele é inutilizável ATRAVÉS DE CICLOS, que é o único uso real.** O
`GovernanceEngine` guarda os alertas num `dict` de instância, sem nenhuma
persistência — zero `json`, zero `Path`, zero `open()` no módulo inteiro. O
monitor roda por `cron */5`: **cada ciclo é um processo novo**, o engine nasce
vazio, e resolver um alerta de ciclo anterior levanta `AlertaDesconhecido` por
construção.

Resolução humana acontece *depois*, por definição. Então o único uso que o
método teria é justamente o que ele não suporta.

**Este arquivo existe para que quem for ligar `resolve` um dia descubra a
restrição AQUI, e não em produção**, escrevendo código que levanta em silêncio
três semanas depois. É a armadilha nº 2 do `CLAUDE.md` — contador em memória num
processo que reinicia — pela sexta vez, agora documentada antes de custar.
"""

from __future__ import annotations

import pytest

from batman_os.foundation.types import Evidence, TenantId
from batman_os.governance.governance_engine import (
    AlertaDesconhecido,
    FonteAlerta,
    GovernanceAlert,
    GovernanceEngine,
    SeveridadeAlerta,
    StatusAlerta,
)


def _alerta() -> GovernanceAlert:
    return GovernanceAlert(
        source=FonteAlerta.SECURITY_INTRUSION,
        severity=SeveridadeAlerta.WARNING,
        evidence=[Evidence(origem="observe:teste", evidencias=["x=1"])],
        related_tenant_id=TenantId("acme"),
    )


class TestResolveFuncionaDentroDoMesmoProcesso:
    def test_resolve_marca_o_alerta(self) -> None:
        eng = GovernanceEngine()
        a = _alerta()
        eng.raise_alert(a)
        resolvido = eng.resolve(a.id, "tratado na investigacao")
        assert resolvido.status is StatusAlerta.RESOLVED
        assert resolvido.resolution == "tratado na investigacao"


class TestMasNaoAtravessaOProcesso:
    def test_engine_novo_nao_conhece_o_alerta_do_anterior(self) -> None:
        """⚠️ O fato que decide o destino deste método.

        Cada ciclo do `cron */5` é um processo novo. Resolver um alerta aberto
        num ciclo anterior — que é o ÚNICO caso real, porque humano decide
        depois — levanta `AlertaDesconhecido`.
        """
        anterior = GovernanceEngine()
        a = _alerta()
        anterior.raise_alert(a)

        seguinte = GovernanceEngine()  # o processo do próximo ciclo

        with pytest.raises(AlertaDesconhecido):
            seguinte.resolve(a.id, "decisao humana tomada horas depois")

    def test_o_engine_nao_persiste_nada(self) -> None:
        """A causa, afirmada diretamente: não há estado em disco para carregar.

        ⚠️ Se este teste começar a falhar porque alguém deu persistência ao
        engine, ÓTIMO — mas então `resolve` passa a ser utilizável e este
        arquivo inteiro precisa ser reescrito, não silenciado.
        """
        import inspect

        from batman_os.governance import governance_engine

        fonte = inspect.getsource(governance_engine)
        for pista in ("json.dump", "json.load", "write_text", "read_text", "open("):
            assert pista not in fonte, (
                f"`{pista}` apareceu em governance_engine: o engine ganhou persistência. "
                "Reescreva este arquivo — `resolve` pode ter deixado de ser inutilizável."
            )


class TestOEstadoRESOLVEDNaoTemLeitor:
    def test_nada_em_src_le_RESOLVED(self) -> None:
        """Escrever um estado que ninguém lê é meio caminho de uma feature.

        ⚠️ Não é defeito a consertar apagando: a spec EXIGE `resolve`
        (`07-governance/01-governance-engine.md:53`). É pendência declarada — e
        declarada aqui, para não ser redescoberta por grep daqui a um mês.
        """
        import re
        from pathlib import Path

        import batman_os

        src = Path(batman_os.__file__).parent
        leitores = []
        for arq in src.rglob("*.py"):
            if arq.name == "governance_engine.py":
                continue  # quem escreve
            texto = arq.read_text(encoding="utf-8", errors="replace")
            if re.search(r"StatusAlerta\.RESOLVED", texto):
                leitores.append(arq.name)
        assert leitores == [], (
            f"alguem passou a ler StatusAlerta.RESOLVED ({leitores}) — "
            "atualize o DIV-FIO-004: o laco deixou de ser morto."
        )
