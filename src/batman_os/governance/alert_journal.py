"""Registro local, estruturado e append-only de todo alerta que passa pela
entrega externa.

Por que existe
--------------
Medido em 2026-09-04: o daemon `observe` rodava havia **8 dias** com
`/var/log/radar/batman_os_observe.log` em **zero byte desde 23/07**. Ele
alertava no Discord e nao deixava rastro nenhum na maquina. Quando o DEV
perguntou "que alertas sairam hoje?", responder exigiu reconstruir o dia pelo
`sar` do sistema e por contagem de linhas do log de OUTRO processo — e ainda
assim nao havia como saber qual valor foi medido, contra que limiar, nem se um
alerta era novo ou o mesmo repetido pela enesima vez.

Ordem do DEV, 2026-09-04: *"cada alerta deve gerar um evento estruturado"*, e
*"tambem precisa existir o evento resolved, ligando comeco e fim do
incidente"*.

Decisoes de desenho, cada uma por um defeito conhecido
------------------------------------------------------
- **Registra os QUATRO desfechos de `enviar()`, nao so o envio.** A entrega tem
  quatro saidas — sem canal, suprimido pela janela de repeticao, falha de
  transporte e entregue — e **tres eram silenciosas**. Registrar so o sucesso
  reproduziria o buraco que este modulo existe para fechar: "nao vi alerta"
  continuaria significando tanto *nao houve problema* quanto *houve e nao
  chegou*.
- **`notification_sent` diz a verdade de cada desfecho.** E o campo que separa
  *o Discord recebeu* de *o Batman OS decidiu* — sem ele, um throttle de 6 h
  e um webhook quebrado ficam indistinguiveis no registro.
- **JSONL append-only.** Cada ciclo do cron e um processo NOVO: estado em
  memoria nasceria vazio toda vez. E o formato le-se com `tail` e `grep`, sem
  ferramenta nenhuma, que e a condicao para servir as 3 h da manha.
- **`incident_key` liga `firing` a `resolved`.** A chave e a ORIGEM da
  evidencia (`feature-monitor:syn-api-health`), que o `functional_monitor`
  emite identica na queda e na recuperacao — nao a assinatura de dedup, que
  muda de proposito entre as duas.
- **Nunca levanta.** `DiscordAlertSink.enviar` "nunca levanta" por contrato; um
  journal que derrubasse o sink trocaria cegueira por mudez, e mudez e pior.

A divida que este modulo declarava, e que SAIDA-003 pagou
---------------------------------------------------------
Ate 2026-09-08 este cabecalho dizia que `measured_value`, `threshold` e `unit`
sairiam `null`, porque o numero chegava ao alerta so como TEXTO dentro de
`evidence` — e extrai-lo por regex faria o registro perseguir a GRAFIA da
mensagem. A saida apontada era "o `ObservabilityEngine` expor a medicao de forma
estruturada", e e o que passou a acontecer: `Evidence.medicao` carrega um
`Medicao` com valor, unidade, janela, capacidade de referencia e qualidade da
coleta, e estes campos saem preenchidos a partir DELE, nunca do texto.

O custo de nao ter pago antes ficou medido: dos 11 alertas de CPU em producao,
nenhum podia ser reexaminado, e a pergunta do DEV — *"meus alertas de infra sao
reais?"* — nao tinha resposta possivel.

⚠️ `error_type` continua `null`: nao ha ainda uma taxonomia de erro estruturada
para preenche-lo, e inventar uma agora so para o campo nao ficar vazio repetiria
o defeito que este paragrafo descreve.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from batman_os.foundation.types import DivergenciaDeAlvo, Medicao, RespostaAvaliada
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    SeveridadeAlerta,
)

logger = logging.getLogger(__name__)

#: Variavel que aponta o arquivo do journal. Em producao vale a pena manda-lo
#: para `/var/log/radar/`, junto do resto — e o primeiro lugar onde se procura.
ENV_CAMINHO = "BATMANOS_ALERT_JOURNAL"

#: Default relativo, ao lado do `alert_dedup.json`: quem ja acha o estado de
#: dedup acha o journal sem precisar saber de mais um caminho.
CAMINHO_PADRAO = Path(".batman-os") / "alertas.jsonl"

#: Fontes que representam VOLTA AO NORMAL. Um evento destes fecha o incidente
#: aberto pela fonte irma, atraves da `incident_key`.
#:
#: ⚠️ Lista explicita, e nao um teste sobre o nome (`"recovered" in fonte`):
#: derivar do texto do enum faria uma fonte futura chamada, digamos,
#: `RECOVERED_BACKLOG` virar resolucao por acidente de grafia.
FONTES_DE_RESOLUCAO: frozenset[FonteAlerta] = frozenset({FonteAlerta.FEATURE_RECOVERED})

#: Familia por fonte — o que faz `feature-down` e `feature-recovered` caírem no
#: MESMO incidente. Fonte sem entrada usa o proprio valor, e aí abre e fecha
#: sozinha (nunca some do registro por falta de mapa).
_FAMILIA_POR_FONTE: dict[FonteAlerta, str] = {
    FonteAlerta.FEATURE_DOWN: "feature",
    FonteAlerta.FEATURE_RECOVERED: "feature",
}

#: Componente por prefixo da origem da evidencia — traduz o identificador
#: interno para o vocabulário do relatorio ("quem originou o evento").
_COMPONENTE_POR_PREFIXO: dict[str, str] = {
    "feature-monitor": "synthetic-monitor",
    "dados-sentinela": "data-sentinela",
    "observe": "observe-daemon",
}

_trava = threading.Lock()


#: O vocabulario COMPLETO de desfechos do journal — `AUD-002`.
#:
#: ⚠️ **Existe porque ele estava espalhado.** Medido em 2026-09-17: os desfechos
#: nasciam em tres lugares -- aqui, como constante em `transiente.py`, e como
#: STRING SOLTA em `functional_monitor.py:871`. Um erro de digitacao criaria um
#: balde novo em silencio, e a conta de conservacao passaria a nao fechar sem
#: ninguem perceber -- o alerta existiria, estaria journalado, e nao seria
#: contado em lugar nenhum.
#:
#: `test_conservacao_do_journal` varre `src/` e exige que todo literal de
#: desfecho esteja aqui. Esquecer passa a reprovar no portao.
OUTCOMES_ENTREGUES = frozenset({"entregue", "editado"})

OUTCOMES_CONHECIDOS = OUTCOMES_ENTREGUES | frozenset(
    {
        # nao houve canal para onde mandar
        "sem_canal",
        # a politica de severidade/janela decidiu nao publicar
        "silenciado_por_politica",
        "suprimido_por_janela",
        # silencio DECLARADO por mecanismo proprio, com o numero preservado
        "silenciado_incidente_curto",
        "silenciado_falha_de_caminho",
        # o transporte ou a edicao falharam -- o alerta EXISTIU e nao chegou
        "falha_de_transporte",
        "falha_de_sink",
        "falha_de_edicao",
        "edicao_impossivel",
    }
)


class Conservacao(NamedTuple):
    """A conta que o `AUD-002` pede: nada emitido pode sumir.

    ⚠️ *"Diferenca de um alerta ja e divergencia."* Um registro que nao casa
    significa desfecho fora do vocabulario, ou entrega que nao bate com o
    desfecho -- e os dois querem dizer que o journal deixou de ser um censo.
    """

    total: int
    por_outcome: dict[str, int]
    entregues: int
    #: `notification_sent=True` — o que o SINK diz que saiu.
    marcados_como_enviados: int
    desconhecidos: dict[str, int]

    @property
    def soma_fecha(self) -> bool:
        return sum(self.por_outcome.values()) == self.total

    @property
    def entrega_bate(self) -> bool:
        """O desfecho e a marca de envio contam a MESMA historia?"""
        return self.entregues == self.marcados_como_enviados

    @property
    def ok(self) -> bool:
        return self.soma_fecha and self.entrega_bate and not self.desconhecidos

    def linhas(self) -> list[str]:
        saida = [f"registros no journal: {self.total}"]
        for k, v in sorted(self.por_outcome.items(), key=lambda kv: -kv[1]):
            saida.append(f"  {v:7}  {k}")
        saida.append(
            f"soma dos desfechos = {sum(self.por_outcome.values())} "
            f"({'FECHA' if self.soma_fecha else 'NAO FECHA'})"
        )
        saida.append(
            f"entregues por desfecho = {self.entregues}, marcados como enviados = "
            f"{self.marcados_como_enviados} ({'BATEM' if self.entrega_bate else 'DIVERGEM'})"
        )
        if self.desconhecidos:
            saida.append(f"⚠️ DESFECHOS FORA DO VOCABULARIO: {self.desconhecidos}")
        return saida


def conservacao(linhas: Iterable[dict[str, Any]]) -> Conservacao:
    """Conta os desfechos de uma janela e diz se a conta fecha.

    Puro sobre os registros ja lidos: quem le o arquivo decide a janela.
    """
    por_outcome: dict[str, int] = {}
    desconhecidos: dict[str, int] = {}
    total = entregues = enviados = 0
    for linha in linhas:
        total += 1
        desfecho = str(linha.get("outcome") or "")
        por_outcome[desfecho] = por_outcome.get(desfecho, 0) + 1
        if desfecho not in OUTCOMES_CONHECIDOS:
            desconhecidos[desfecho] = desconhecidos.get(desfecho, 0) + 1
        if desfecho in OUTCOMES_ENTREGUES:
            entregues += 1
        if linha.get("notification_sent"):
            enviados += 1
    return Conservacao(total, por_outcome, entregues, enviados, desconhecidos)


def caminho_do_ambiente() -> Path:
    """O arquivo do journal, do ambiente ou do default."""
    bruto = os.getenv(ENV_CAMINHO)
    return Path(bruto) if bruto else CAMINHO_PADRAO


def _origens(alert: GovernanceAlert) -> list[str]:
    return [ev.origem for ev in alert.evidence if ev.origem]


def _componente(origens: Iterable[str]) -> str:
    for origem in origens:
        prefixo = origem.split(":", 1)[0]
        if prefixo in _COMPONENTE_POR_PREFIXO:
            return _COMPONENTE_POR_PREFIXO[prefixo]
    return "governance"


def _check(origens: Iterable[str]) -> str | None:
    """O identificador do check, sem o prefixo de componente."""
    for origem in origens:
        _, _, resto = origem.partition(":")
        if resto:
            return resto
    return None


#: Prefixo do `Evidence.assunto` que carrega um protocolo de incidente.
_PREFIXO_ASSUNTO_INCIDENTE = "incidente:"


def _divergencia_de(alert: GovernanceAlert) -> DivergenciaDeAlvo | None:
    """A divergência de alvo, quando alguma evidencia registrou uma."""
    for ev in alert.evidence:
        if ev.divergencia is not None:
            return ev.divergencia
    return None


def _protocolo_de(alert: GovernanceAlert) -> str | None:
    """O protocolo do incidente, quando alguma evidencia declara um.

    ⚠️ Lido do `assunto` — campo — e nunca extraido do texto. `assunto` tem a
    forma `incidente:BAT-2026-0001`; qualquer outro assunto (`metrica:`,
    `sentinela:`) nao e protocolo de incidente e devolve `None`.
    """
    for ev in alert.evidence:
        if ev.assunto.startswith(_PREFIXO_ASSUNTO_INCIDENTE):
            return ev.assunto[len(_PREFIXO_ASSUNTO_INCIDENTE) :] or None
    return None


def _resposta_de(alert: GovernanceAlert) -> RespostaAvaliada | None:
    """A decisao de resposta automatica que alguma evidencia avaliou.

    ⚠️ Do CAMPO, como a medicao. E registrada em TODO ciclo em que houve
    avaliacao, mesmo quando o alerta e suprimido pelo throttle: o dedupe existe
    para poupar o LEITOR do Discord, nunca para apagar a auditoria. Um bloqueio
    que teria acontecido precisa estar registrado mesmo que ninguem tenha sido
    notificado naquele ciclo.
    """
    for ev in alert.evidence:
        if ev.resposta is not None:
            return ev.resposta
    return None


def _medicao_de(alert: GovernanceAlert) -> Medicao | None:
    """A primeira medicao estruturada que alguma evidencia carregue.

    ⚠️ Ler do CAMPO, e nunca por regex sobre `evidencias`. O cabecalho deste
    modulo ja proibia a segunda via: extrair o numero da frase faria o registro
    perseguir a GRAFIA da mensagem, e qualquer reescrita do texto quebraria o
    campo em silencio -- numero errado e pior que campo vazio.
    """
    for ev in alert.evidence:
        if ev.medicao is not None:
            return ev.medicao
    return None


def _limiar_de(alert: GovernanceAlert) -> float | None:
    """O limiar que a severidade DESTE alerta cruzou.

    Um alerta CRITICAL cruzou o limiar critico; um WARNING, o de aviso. Gravar
    sempre o mesmo dos dois faria o registro dizer que a CPU a 85% violou 90.
    """
    medicao = _medicao_de(alert)
    if medicao is None:
        return None
    if alert.severity is SeveridadeAlerta.CRITICAL:
        return medicao.limiar_critical
    return medicao.limiar_warning


def incident_key(alert: GovernanceAlert) -> str:
    """Chave que liga a queda a recuperacao do MESMO alvo.

    Usa a familia da fonte (`feature-down` e `feature-recovered` compartilham
    `feature`) mais as origens da evidencia, que o `functional_monitor` emite
    identicas nos dois eventos. Sem isso, `firing` e `resolved` seriam duas
    linhas sem relacao e o registro responderia *que caiu*, nunca *por quanto
    tempo ficou caido*.
    """
    familia = _FAMILIA_POR_FONTE.get(alert.source, alert.source.value)
    alvos = ",".join(sorted(_origens(alert))) or "sem-origem"
    tenant = str(alert.related_tenant_id or "global")
    return f"{familia}|{tenant}|{alvos}"


def evento(
    alert: GovernanceAlert,
    *,
    outcome: str,
    notification_sent: bool,
    canal: str | None = None,
    fingerprint: str | None = None,
    detalhe: str | None = None,
) -> dict[str, Any]:
    """Monta o evento estruturado de um alerta. Puro — testavel sem disco."""
    origens = _origens(alert)
    resolvido = alert.source in FONTES_DE_RESOLUCAO
    medicao = _medicao_de(alert)
    resposta = _resposta_de(alert)
    divergencia = _divergencia_de(alert)
    return {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "alert_id": str(alert.id),
        "incident_key": incident_key(alert),
        "component": _componente(origens),
        "check": _check(origens),
        "source": alert.source.value,
        "severity": alert.severity.value,
        "status": "resolved" if resolvido else "firing",
        "outcome": outcome,
        "notification_sent": notification_sent,
        "channel": canal,
        "fingerprint": fingerprint,
        "tenant": str(alert.related_tenant_id or "global"),
        "created_at": alert.created_at.isoformat()
        if hasattr(alert.created_at, "isoformat")
        else str(alert.created_at),
        # SAIDA-003: a medicao passou a ser exposta de forma ESTRUTURADA pela
        # origem (`Evidence.medicao`), entao estes campos deixam de sair `null`
        # quando ha numero.
        #
        # ⚠️ `None` continua sendo `None`, e NUNCA vira zero: zero e leitura
        # saudavel, ausencia e cegueira, e colapsar as duas faria a metrica cega
        # passar por metrica boa no registro que existe para audita-la.
        #
        # ⚠️ E o valor vem do CAMPO, nunca de regex sobre o texto: extrai-lo da
        # frase faria o registro perseguir a GRAFIA da mensagem, e qualquer
        # reescrita quebraria o campo em silencio.
        "measured_value": medicao.valor if medicao else None,
        "threshold": _limiar_de(alert),
        "unit": medicao.unidade if medicao else None,
        # BAT-RESP-001b: o veredito da resposta automatica, em CAMPO. Sai
        # `None` quando nao houve avaliacao neste ciclo -- e `None` significa
        # "nao avaliado", nunca "nao bloquearia": colapsar os dois faria o
        # registro afirmar uma decisao que ninguem tomou.
        "resposta": (
            {
                "ip": resposta.ip,
                "bloquearia": resposta.bloquearia,
                "motivo": resposta.motivo,
                "modo": resposta.modo,
                "ttl_s": resposta.ttl_s,
                "reincidente": resposta.reincidente,
                "recusa_por_teto": resposta.recusa_por_teto,
            }
            if resposta is not None
            else None
        ),
        # O numero que o operador cita. Sai do `assunto` (`incidente:BAT-...`),
        # que e campo, e nunca de regex sobre o texto da evidencia.
        "protocolo": _protocolo_de(alert),
        # ⚠️ Auditavel mesmo sem mensagem no Discord. A guarda de identidade
        # recusa associar a decisao ao alvo errado, e recusar em SILENCIO seria
        # a cegueira que este programa existe para eliminar.
        "divergencia_de_alvo": (
            {
                "alvo_apresentado": divergencia.alvo_apresentado,
                "alvo_avaliado": divergencia.alvo_avaliado,
                "protocolo": divergencia.protocolo,
                "contexto": divergencia.contexto,
            }
            if divergencia is not None
            else None
        ),
        "error_type": None,
        "target": origens[0] if origens else None,
        "detail": detalhe,
        # ⚠️ `historico` passou a ser gravado em 09/09. Ate entao o journal
        # guardava so `evidencias`, e TODA medicao vive em `historico` -- foi
        # por isso que um evento com "247 conexoes" no Discord nao podia ser
        # rastreado no registro: o numero simplesmente nao estava la.
        "evidence": [
            {
                "origem": ev.origem,
                "linhas": list(ev.evidencias),
                "historico": list(ev.historico),
                "confianca": ev.confianca,
                "assunto": ev.assunto,
            }
            for ev in alert.evidence
        ],
    }


def registrar(linha: dict[str, Any], caminho: Path | None = None) -> bool:
    """Anexa um evento ao journal. Devolve se gravou; **nunca levanta**.

    Falha de I/O aqui nao pode derrubar a entrega de alerta — o sink promete
    nao levantar. Mas a falha tambem nao pode ser muda: vai para o `logger` em
    nivel de erro, que e o unico lugar restante.
    """
    destino = caminho or caminho_do_ambiente()
    try:
        with _trava:
            destino.parent.mkdir(parents=True, exist_ok=True)
            with destino.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.error("falha ao registrar alerta no journal (%s): %s", destino, exc)
        return False
