"""ObserveWatcher — loop/agendamento do subsistema de observacao.

Anexo ao Vol.VII Cap.27/30 (subsistema `batman_os.observe`). Une o
`daemon.py` + `patrol.py` do Batman legado numa peca so, testavel:

- `run_once(snapshot)` — deterministico: alimenta o `ObservabilityEngine`
  com as metricas do snapshot, chama `avaliar_regras` (que ja entrega os
  alertas de metrica via sink), roda as `watch_rules` de EVENTO e chama
  `governance.raise_alert` para cada uma (entrega via sink). Retorna o
  conjunto completo de `GovernanceAlert` do ciclo.
- `run_forever(intervalo, coletar)` — loop best-effort: uma excecao num
  ciclo e logada e o loop CONTINUA (nunca derruba). Parada por numero de
  ciclos ou por evento de parada (para nao rodar infinito em teste).
- `heartbeat(...)` — batimento periodico "tudo limpo OU atencao" (paridade
  com a patrulha) + self-heartbeat do proprio watcher a cada N ciclos: a
  melhoria ✗→✓ contra o "daemon mudo 22 dias sem ninguem notar".

Disciplina arquitetural (contrato de paridade): este modulo importa
`governance` + `foundation` + stdlib. NAO importa `batman_os.kernel`
(mesma disciplina do sink; coerente com ADR-0012). Entrega e sempre
best-effort — o `GovernanceEngine.raise_alert` ja engole falha de sink.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

from batman_os.foundation.types import Evidence, TenantId, Timestamp, agora
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    GovernanceEngine,
    SeveridadeAlerta,
)
from batman_os.governance.observability_engine import ObservabilityEngine
from batman_os.observe import watch_rules
from batman_os.observe.snapshot import ObserveSnapshot

logger = logging.getLogger(__name__)


class ColetorSnapshotComoAssinatura(Protocol):
    """Fonte de snapshots do loop — testes injetam um fake (inclusive um
    que levanta, para provar o best-effort)."""

    def __call__(self) -> ObserveSnapshot: ...


class EventoDeParadaComoAssinatura(Protocol):
    """Sinal de parada do loop (ex.: `threading.Event`)."""

    def is_set(self) -> bool: ...


class DormirComoAssinatura(Protocol):
    def __call__(self, segundos: float) -> object: ...


class ObserveWatcher:
    """Orquestra coleta -> avaliacao -> entrega. Deterministico em
    `run_once`; best-effort em `run_forever`."""

    def __init__(
        self,
        governance: GovernanceEngine,
        *,
        tenant_id: TenantId | None = None,
        ciclos_por_heartbeat: int = 60,
        relogio: Callable[[], Timestamp] | None = None,
    ) -> None:
        self._governance = governance
        self._observ = ObservabilityEngine(governance)
        self._tenant_id = tenant_id
        if ciclos_por_heartbeat < 1:
            raise ValueError("ciclos_por_heartbeat deve ser >= 1")
        self._ciclos_por_heartbeat = ciclos_por_heartbeat
        self._relogio = relogio or agora
        # bases de metrica ja registradas como AlertRule (evita duplicar
        # regra a cada ciclo — mantem o loop estavel).
        self._metricas_registradas: set[str] = set()

    # -- ciclo unico --------------------------------------------------------
    def run_once(self, snapshot: ObserveSnapshot) -> list[GovernanceAlert]:
        agora_ = self._relogio()
        alertas: list[GovernanceAlert] = []
        # 1) metricas -> ObservabilityEngine (avaliar_regras ja entrega).
        alertas.extend(self._avaliar_metricas(snapshot, agora_))
        # 2) eventos -> watch_rules (funcoes puras); run_once entrega cada um.
        for alerta in watch_rules.avaliar_eventos(snapshot):
            self._governance.raise_alert(alerta)
            alertas.append(alerta)
        return alertas

    def _avaliar_metricas(
        self, snapshot: ObserveSnapshot, agora_: Timestamp
    ) -> list[GovernanceAlert]:
        tenant = snapshot.tenant_id or self._tenant_id
        leituras = watch_rules.leituras_de_metrica(snapshot)
        # registra regras de bandas ainda desconhecidas (uma vez so)
        for base, banda, _ in leituras:
            if base in self._metricas_registradas:
                continue
            for regra in watch_rules.alert_rules_da_banda(base, banda, tenant):
                self._observ.register_alert_rule(regra)
            self._metricas_registradas.add(base)
        # zera TODAS as series conhecidas (remove ponto obsoleto de ciclo
        # anterior — determinismo) e realimenta as leituras do ciclo atual
        for base in self._metricas_registradas:
            for serie in watch_rules.series_vazias(base):
                self._observ.registrar_serie(serie)
        for base, banda, valor in leituras:
            for serie in watch_rules.series_da_banda(base, banda, valor, agora_):
                self._observ.registrar_serie(serie)
        self._registrar_leituras_em_banda(leituras)
        return self._observ.avaliar_regras(agora_=agora_)

    @staticmethod
    def _registrar_leituras_em_banda(
        leituras: list[tuple[str, watch_rules.LimiarBanda, float | None]],
    ) -> None:
        """Deixa rastro LOCAL da métrica que entrou em banda — o carimbo de
        tempo que faltou no `ALERTA_CPU_LATENCIA_SEM_CARIMBO01`.

        O daemon alertava no Discord e não escrevia nada na máquina: medido em
        2026-08-12, `batman_os_observe.log` com **0 bytes desde 23/07**, com o
        processo vivo há 60 dias. Só se logava exceção, e 20 dias sem exceção
        produzem arquivo vazio — que se lê como "nunca subiu". Sem o carimbo,
        um CRITICAL de `CPU>90%` não pôde ser separado de limiar mal
        calibrado, porque não havia **quando**.

        Só entra no log o que é informativo — leitura a partir da banda de
        WARNING. A trava importa: `/var/log/<app>` **não tem logrotate**
        (medido: 548 MB, arquivos de até 174 MB), e o daemon roda 1.440 ciclos
        por dia. Logar todo ciclo trocaria um crescimento sem teto por outro,
        que é o defeito que este mesmo lote de trabalho está consertando no
        `estado.db`. Numa máquina saudável esta função não escreve nada.
        """
        em_banda = [
            (base, banda, valor)
            for base, banda, valor in leituras
            if valor is not None and valor >= banda.warning
        ]
        if not em_banda:
            return
        for base, banda, valor in em_banda:
            nivel = logging.WARNING if valor >= banda.critical else logging.INFO
            logger.log(
                nivel,
                "observe: %s=%.1f (warning=%.0f critical=%.0f)",
                base,
                valor,
                banda.warning,
                banda.critical,
            )

    # -- heartbeat ----------------------------------------------------------
    def heartbeat(
        self,
        snapshot: ObserveSnapshot,
        *,
        ciclo: int | None = None,
        houve_alerta: bool = False,
    ) -> GovernanceAlert:
        """Emite (e entrega) um `GovernanceAlert` INFO de batimento. Sempre
        emite — inclusive "tudo limpo" — porque monitoramento que so fala
        quando ha problema e indistinguivel de monitoramento morto. O `ciclo`
        e o `batimento_em` ficam na evidencia para DISPLAY (quando foi o
        batimento), mas o sink os trata como campos volateis (`_EVIDENCIA_
        VOLATIL`): fora da assinatura de dedup, o batimento throttla para 1x/dia
        em vez de floodar (a cada `ciclos_por_heartbeat`)."""
        tenant = snapshot.tenant_id or self._tenant_id
        status = "atencao" if houve_alerta else "tudo-limpo"
        linhas = [f"status={status}", "watcher=vivo"]
        if ciclo is not None:
            linhas.append(f"ciclo={ciclo}")
        linhas.append(f"batimento_em={self._relogio().isoformat()}")
        alerta = GovernanceAlert(
            source=FonteAlerta.OBSERVE_HEARTBEAT,
            severity=SeveridadeAlerta.INFO,
            evidence=[Evidence(origem="observe:heartbeat", evidencias=linhas)],
            related_tenant_id=tenant,
        )
        self._governance.raise_alert(alerta)
        return alerta

    # -- loop ---------------------------------------------------------------
    def run_forever(
        self,
        intervalo: float,
        coletar: ColetorSnapshotComoAssinatura,
        *,
        max_ciclos: int | None = None,
        parar: EventoDeParadaComoAssinatura | None = None,
        dormir: DormirComoAssinatura | None = None,
    ) -> int:
        """Coleta um snapshot e roda `run_once` a cada `intervalo`.
        Best-effort: excecao num ciclo (coleta OU avaliacao) e logada e o
        loop continua — nunca derruba. Retorna o numero de ciclos rodados.
        Termina quando `max_ciclos` e atingido ou `parar.is_set()`."""
        dormir_fn: DormirComoAssinatura = dormir if dormir is not None else _dormir_padrao
        ciclo = 0
        while (max_ciclos is None or ciclo < max_ciclos) and not (
            parar is not None and parar.is_set()
        ):
            ciclo += 1
            try:
                snapshot = coletar()
                alertas = self.run_once(snapshot)
                if ciclo % self._ciclos_por_heartbeat == 0:
                    self.heartbeat(snapshot, ciclo=ciclo, houve_alerta=bool(alertas))
            except Exception as exc:  # best-effort: ciclo ruim nao derruba o loop
                logger.error("ciclo %d de observacao falhou (loop continua): %s", ciclo, exc)
            if (max_ciclos is None or ciclo < max_ciclos) and not (
                parar is not None and parar.is_set()
            ):
                dormir_fn(intervalo)
        return ciclo


def _dormir_padrao(segundos: float) -> None:
    time.sleep(segundos)
