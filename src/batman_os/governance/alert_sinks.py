"""Entrega externa de `GovernanceAlert` (Discord) — Anexo ao Vol.VII Cap.27.

A spec original (Cap.27/Cap.30) define o ciclo INTERNO do alerta
(`registerAlertRule` -> `GovernanceAlert` -> `get_open_alerts`), mas nao
especifica entrega externa — isto e uma capacidade nova, formalizavel via
Anexo (`StatusAnexo`). Fica no plano de governanca e **nao importa
`batman_os.kernel`** (ADR-0012); recebe um `GovernanceAlert` ja pronto.

Replica o alerta Discord do Batman legado (`radar-preditivo/Batman/
observe/discord_alert.py`: embeds por severidade, retry em 429, backoff),
corrigindo os problemas achados na auditoria daquele emissor:

- **Dedupe por ESTADO, nao por cooldown fixo:** o legado reenviava o mesmo
  heartbeat "ATENCAO" com conteudo identico dias seguidos (caso ARCH-007,
  3x). Aqui, um alerta so e enviado se sua ASSINATURA de conteudo mudou
  desde o ultimo envio para o mesmo (source, tenant) — repeticao identica
  e suprimida, mudanca real passa.
- **`@everyone` contido:** o legado marcava `@everyone` para qualquer porta
  nova com bind externo. Aqui `@everyone` so ocorre para fontes numa
  allowlist explicita (default: vazia); CRITICAL usa `@here`.
- **Sem hostname cru:** o footer do legado expunha `socket.gethostname()`
  da VPS em todo embed. Aqui o footer carrega so o tenant.
- **Roteamento por tenant (ADR-0005):** webhook por `TenantId`, jamais
  vazando alerta de um tenant no canal de outro; fallback global opcional.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from batman_os.foundation.types import TenantId
from batman_os.governance import alert_journal, mensagem
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    SeveridadeAlerta,
)

logger = logging.getLogger(__name__)

# Discord (Cloudflare) exige um User-Agent nao-padrao (o do urllib -> 403).
_USER_AGENT = "BatmanOS-AlertSink/1.0 (+https://exemplo.group)"

# Severidade -> (cor do embed, emoji). Cores no padrao do legado.
_ESTILO: dict[SeveridadeAlerta, tuple[int, str]] = {
    SeveridadeAlerta.CRITICAL: (0xF23F43, "🔴"),
    SeveridadeAlerta.WARNING: (0xF0B232, "🟡"),
    SeveridadeAlerta.INFO: (0x58B9FF, "🔵"),
}


class AlertSink(Protocol):
    """Destino de entrega de um `GovernanceAlert`. Implementacoes NUNCA
    levantam para o chamador (best-effort — entrega falha nao pode derrubar
    o Governance Engine)."""

    #: ⚠️ Devolve o DESFECHO (`entregue`, `suprimido_por_janela`, `sem_canal`...),
    #: e nao `None`. `DIV-PROVA-001`: a prova de vida passou a 6/6 h e o sink
    #: seguiu estrangulando INFO em 24 h, entao 3 de cada 4 provas eram
    #: suprimidas -- e o emissor gravava sucesso, porque nao tinha como saber.
    #: Entrega e supressao PRECISAM ser distinguiveis por quem emite.
    def enviar(self, alert: GovernanceAlert) -> str | None: ...


class TransporteWebhook(Protocol):
    """Transporte HTTP injetavel (testes usam fake, sem rede)."""

    def postar(self, webhook_url: str, payload: dict[str, Any]) -> str | None:
        """Envia o payload e devolve o ID da mensagem; levanta em falha.

        ⚠️ O retorno entrou em SAIDA-004. Ate entao `postar` descartava a
        resposta inteira, e sem o ID nao havia o que EDITAR depois -- a decisao
        do DEV de que *"se houve mensagem original, ela e editada com a
        recuperacao"* era impossivel de cumprir. `None` significa "o destino nao
        devolveu ID", e o efeito e honesto: sem ID nao ha edicao.
        """
        ...

    def editar(self, webhook_url: str, message_id: str, payload: dict[str, Any]) -> None:
        """Reescreve uma mensagem ja enviada; levanta em falha."""
        ...


class _TransporteUrllib:
    """Transporte real via urllib (stdlib — sem dependencia nova). Trata
    HTTP 429 lendo `Retry-After` e re-tenta; backoff exponencial em erro de
    rede. Mesmo contrato de risco do `_post` legado."""

    def __init__(self, timeout: float = 10.0, max_tentativas: int = 3) -> None:
        self._timeout = timeout
        self._max_tentativas = max_tentativas

    def _requisitar(self, url: str, payload: dict[str, Any], metodo: str) -> dict[str, Any] | None:
        """POST ou PATCH, com a mesma politica de 429 e backoff."""
        corpo = json.dumps(payload).encode("utf-8")
        for tentativa in range(1, self._max_tentativas + 1):
            req = urllib.request.Request(
                url,
                data=corpo,
                method=metodo,
                headers={
                    "Content-Type": "application/json",
                    # A Cloudflare do Discord responde 403 ao User-Agent
                    # padrao do urllib ("Python-urllib/*"); exige um UA
                    # proprio (confirmado em campo no shadow, 2026-07-22).
                    "User-Agent": _USER_AGENT,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resposta:
                    bruto = resposta.read()
                if not bruto:
                    return None
                devolvido = json.loads(bruto)
                return devolvido if isinstance(devolvido, dict) else None
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    retry_after = float(exc.headers.get("Retry-After", "1") or "1")
                    time.sleep(min(retry_after, 30.0))
                    continue
                raise  # 4xx/5xx permanente
            except (urllib.error.URLError, TimeoutError):
                if tentativa == self._max_tentativas:
                    raise
                time.sleep(2.0**tentativa)
            except ValueError:
                # Corpo nao-JSON: a entrega ocorreu, o ID e que se perdeu.
                return None
        return None

    def postar(self, webhook_url: str, payload: dict[str, Any]) -> str | None:
        # ⚠️ `?wait=true` e o que faz o Discord devolver o objeto da mensagem.
        # Sem ele a resposta e 204 sem corpo, e o ID -- unica forma de editar
        # depois -- nao existe.
        alvo = webhook_url + ("&" if "?" in webhook_url else "?") + "wait=true"
        devolvido = self._requisitar(alvo, payload, "POST")
        identificador = (devolvido or {}).get("id")
        return str(identificador) if identificador else None

    def editar(self, webhook_url: str, message_id: str, payload: dict[str, Any]) -> None:
        base = webhook_url.split("?", 1)[0].rstrip("/")
        self._requisitar(f"{base}/messages/{message_id}", payload, "PATCH")


# Linhas de evidencia VOLATEIS (medicoes que mudam todo ciclo) NAO entram na
# assinatura de dedup — senao a assinatura muda a cada envio e o throttle nunca
# dispara. Bug real (2026-07-23): feature-down re-alertava a cada 5min porque a
# evidencia trazia `latencia=91.7ms`, diferente em cada ciclo. A IDENTIDADE do
# alerta e o que ele E (feature/status/porta), nao quanto tempo levou.
_EVIDENCIA_VOLATIL = re.compile(
    r"^\s*(lat[êe]ncia|latency|dura[çc][ãa]o|tempo|rtt|p50|p95|uptime|"
    r"observed_at|timestamp|\bts\b|corpo\[|ciclo|batimento|idade|contagem)",
    re.I,
)


#: Canal dedicado a AMEACA ACIONAVEL.
CANAL_CYBER = "cyber"
#: Fallback do cyber enquanto o canal antigo existir (ajuste do DEV): a mensagem
#: vai para UM dos dois, nunca para os dois -- duplicar treina a ignorar ambos.
CANAL_SEGURANCA_LEGADO = "security"

#: Origens que sao SEGURANCA mas nao AMEACA — vao para #infra.
#:
#: `nginx-invalida` e configuracao quebrada: problema operacional. Ja
#: `nginx-mudanca` e alteracao detectada no arquivo, que e a "evidencia concreta
#: de alteracao" que o DEV pediu para manter no canal de ameaca.
_ORIGENS_OPERACIONAIS = frozenset({"observe:nginx-invalida", "observe:servico-caido"})

#: Origens que sao do MONITOR, nao do produto — vao para #log qualquer que seja
#: a fonte que as carregue.
#:
#: ⚠️ `fonte-restabelecida` esta aqui porque a recuperacao viaja como
#: `FEATURE_RECOVERED`, que roteia para #performance. Sem esta excecao, a queda
#: da coleta ia para #log e a volta dela para #performance -- o mesmo incidente
#: partido entre dois canais, e ninguem conseguindo fechar a historia.
_ORIGENS_DO_MONITOR = frozenset({"observe:fonte-restabelecida", "observe:fonte-inacessivel"})

#: Origens de manutencao ESPERADA. Nunca viram mensagem: journal e resumo.
#:
#: ⚠️ Medido em 2026-09-05: `porta-manutencao` produziu 2.046 supressoes contra
#: 2 entregas no canal de seguranca. Era ruido esperado (n8n, monarx em
#: loopback) competindo com ameaca real pelo mesmo canal — e canal de seguranca
#: que carrega ruido e canal que se aprende a ignorar.
_ORIGENS_DE_MANUTENCAO = frozenset({"observe:porta-manutencao"})

#: Origens em que a intrusao deixou de ser tentativa e virou FATO. E a unica
#: coisa que justifica acordar todo mundo: nao e mais "alguem esta tentando", e
#: "alguem esta dentro".
_ORIGENS_DE_INTRUSAO_CONFIRMADA = frozenset({"observe:autenticacao-aceita"})

#: Origens de RECUPERACAO de ameaca. Vao ao canal do incidente mesmo sendo INFO.
#:
#: ⚠️ Mesma armadilha que `_ORIGENS_DO_MONITOR` documenta logo acima, e vale
#: repetir porque ela reaparece por caminhos diferentes: sem esta excecao, o
#: ataque ia para `#cyber` e o fim dele para o journal -- o mesmo incidente
#: partido em dois lugares, e ninguem conseguindo fechar a historia. §17.5:
#: *"Recuperacao volta ao canal do incidente."*
_ORIGENS_DE_RECUPERACAO = frozenset(
    {
        "observe:ssh-recuperado",
        "observe:web-recuperado",
        "observe:resumo-diario",
        "observe:metrica-recuperada",
    }
)

_FONTES_DE_AMEACA = frozenset(
    {FonteAlerta.SECURITY_INTRUSION, FonteAlerta.TENANT_ISOLATION_INCIDENT}
)

#: Devolvido por `canal_do_alerta` quando o evento nao deve ir a canal nenhum.
SOMENTE_JOURNAL = None


def _origens(alert: GovernanceAlert) -> set[str]:
    return {ev.origem for ev in alert.evidence}


#: Prefixo de assunto cujo histórico é EVIDÊNCIA, e não pode ser sobrescrito.
#:
#: ⚠️ A distinção é entre ESTADO e TRILHA, e o DEV a apontou em 2026-09-09 ao
#: ver o resultado: *"uma mensagem não pode substituir a outra porque eu tenho
#: que ter o histórico ali daquele protocolo... agora foi a substituição, eu não
#: tenho mais a origem, não sei quando ele começou, o que o Batman
#: identificou"*.
#:
#: Para uma MÉTRICA, editar é certo: "CPU em 95%" seguido de "CPU normal" é o
#: mesmo fato mudando de valor, e a leitura atual substitui a anterior sem
#: perda. Para um INCIDENTE DE SEGURANÇA, não: abertura, acompanhamento e
#: encerramento são eventos distintos, cada um com o que se sabia naquele
#: instante, e o conjunto é a trilha forense. Substituir a abertura pelo
#: encerramento apaga quando começou, o que foi identificado e qual era a
#: decisão — exatamente o que uma tratativa de incidente precisa preservar.
#:
#: A decisão do DEV de 08/09 ("editar a mensagem original") foi tomada sobre
#: MÉTRICAS. Estendê-la a incidentes foi extensão minha, e estava errada.
_ASSUNTOS_COM_HISTORICO = ("incidente:",)


def preserva_historico(assunto: str) -> bool:
    """O fechamento deste assunto deve ser mensagem NOVA, não edição?"""
    return assunto.startswith(_ASSUNTOS_COM_HISTORICO)


def _e_fechamento(alert: GovernanceAlert) -> bool:
    """Este alerta ANUNCIA UM FIM?

    ⚠️ Lista explicita, e nao teste sobre o nome: derivar de `"recuperad" in
    origem` faria uma origem futura virar fechamento por acidente de grafia --
    a mesma armadilha que `FONTES_DE_RESOLUCAO` do journal ja documenta.
    """
    if alert.source is FonteAlerta.FEATURE_RECOVERED:
        return True
    return bool(_origens(alert) & _ORIGENS_DE_RECUPERACAO)


def assunto_do_alerta(alert: GovernanceAlert) -> str:
    """O assunto declarado nas evidencias, ou vazio.

    ⚠️ LE o campo, nunca deduz do texto. Deduzir por heuristica sobre a frase
    faria a ligacao entre abertura e fechamento depender da GRAFIA da mensagem
    -- o mesmo defeito que manteve `measured_value` vazio ate SAIDA-003.
    """
    for ev in alert.evidence:
        if ev.assunto:
            return ev.assunto
    return ""


def impressao_do_destino(webhook_url: str) -> str:
    """Identificador NAO-REVERSIVEL do destino.

    ⚠️ A URL do webhook CONTEM o token: guarda-la no estado seria vazar o
    segredo num arquivo que existe para depuracao e que alguem vai abrir. O que
    o estado precisa saber e apenas *"o ID guardado pertence a este destino?"*,
    e para isso um hash basta.

    Serve a rotacao: se a URL mudar, a impressao muda, o ID antigo fica orfao e
    a edicao nao e nem tentada -- ela e AUDITADA como impossivel.
    """
    return hashlib.sha256(webhook_url.encode("utf-8")).hexdigest()[:16]


#: Os formatadores do Discord. `>` entra porque, no inicio da linha, vira
#: citacao e desloca o bloco inteiro.
_FORMATADORES_DO_DISCORD = "*_~`|>"

_ESCAPE_MARKDOWN = re.compile(r"([\\" + re.escape(_FORMATADORES_DO_DISCORD) + r"])")


def escapar_markdown(texto: str) -> str:
    """Neutraliza a formatacao do Discord sem apagar um caractere sequer.

    ⚠️ **Fatia de escape do `WEB-10`.** O caminho da URL que o cliente pediu
    entra na evidencia e dai no embed. `_sanitizar_caminho` tira query string e
    trunca em 80, mas nao tira `*`, `_`, backtick, `~` nem `|` -- entao
    `GET /**ATAQUE-CONTIDO**` vira NEGRITO dentro do alerta. Nao e ping (embed
    nao dispara mencao); e falsificacao visual de evidencia, que e pior no que
    importa: o atacante escreve, na mensagem do Batman, texto que parece do
    Batman.

    ⚠️ **Escapar nunca e apagar.** Trocar um defeito de formatacao por uma
    cegueira seria pior -- a rota que o atacante pediu E a evidencia. Cada
    caractere continua ali, so que precedido de barra, e o Discord CONSOME a
    barra ao renderizar.

    ⚠️ **Mora no sink, e nao no coletor, de proposito.** Escapar na coleta
    gravaria texto escapado no registro de incidentes e no `alertas.jsonl`,
    corrompendo o que foi REALMENTE pedido. Escape e apresentacao; apresentacao
    e do sink. O journal guarda o caractere cru.

    Medido antes de escolher: dos 255 literais de texto de `watch_rules`, 16 tem
    caractere de Markdown, quase todos underscore em identificador. Como o
    Discord consome a barra, o escape e invisivel para o texto das regras.
    """
    return _ESCAPE_MARKDOWN.sub(r"\\\1", texto)


#: Separador da chave composta de `_mensagens`. Mesmo caractere de
#: `alert_journal.incident_key` (`familia|tenant|alvos`), pelo mesmo motivo.
_SEPARADOR_DE_CHAVE = "|"

#: Tenant de entrada gravada ANTES de `DIV-TENANT-001`, quando a chave não o
#: carregava. Não é um tenant real e não casa nenhum alerta.
TENANT_DESCONHECIDO = "?desconhecido"


def chave_de_mensagem(alert: GovernanceAlert, assunto: str) -> str:
    """A identidade de "a mensagem original DESTE assunto, DESTE tenant".

    ⚠️ `DIV-TENANT-001`. A chave era só o assunto, e `assunto_de_metrica(base)`
    devolve `"metrica:observe.cpu"` para TODOS os tenants. Tenant A abria, tenant
    B fechava, e o `PATCH` ia na mensagem de A com o payload de B -- cujo rodapé
    dizia `tenant=B`. O cabeçalho deste módulo promete *"jamais vazando alerta de
    um tenant no canal de outro"*, e este era o único ponto do sink onde o
    roteamento por tenant era contornado por construção.

    A forma não é inventada aqui: `alert_journal.incident_key` já é
    `f"{familia}|{tenant}|{alvos}"`.
    """
    return f"{alert.related_tenant_id or 'global'}{_SEPARADOR_DE_CHAVE}{assunto}"


def _migrar_chave_de_mensagem(chave: str) -> str:
    """Chave gravada antes do `DIV-TENANT-001` não carregava tenant.

    ⚠️ Ela é PRESERVADA, e não descartada -- jogar fora faria um fechamento em
    curso perder a referência sem deixar rastro. Mas entra sob um tenant
    sentinela, então nunca casa a busca de um tenant real: o vazamento fica
    fechado inclusive para o estado que já estava em disco.

    ⚠️ Custo declarado: no primeiro ciclo depois do deploy, um fechamento em
    curso posta mensagem nova em vez de editar a original. Uma vez, e cosmético.
    O contrário -- casar a entrada antiga com "o tenant que aparecer" -- seria
    exatamente o defeito.
    """
    return chave if _SEPARADOR_DE_CHAVE in chave else f"{TENANT_DESCONHECIDO}|{chave}"


def canal_do_alerta(alert: GovernanceAlert, canal_padrao: str = "log") -> str | None:
    """Canal de destino, ou `None` para registrar sem notificar.

    Roteia por FONTE, e por SEVERIDADE e ORIGEM onde a fonte e grossa demais:
    as OITO regras de seguranca do observe emitem todas `SECURITY_INTRUSION`,
    entao so a fonte nao distingue "porta do n8n, esperada" de "brute-force SSH".

    Mora aqui, e nao espalhado entre `_webhook_para` e `_registrar`, porque os
    dois precisam concordar — se divergirem, a mensagem vai para um canal e o
    journal registra outro, e "isto chegou onde?" deixa de ter resposta.
    """
    origens = _origens(alert)
    if origens & _ORIGENS_DO_MONITOR:
        return "log"
    if alert.source in _FONTES_DE_AMEACA:
        if origens & _ORIGENS_DE_MANUTENCAO:
            return SOMENTE_JOURNAL
        if origens & _ORIGENS_OPERACIONAIS:
            return "infra"
        if origens & _ORIGENS_DE_RECUPERACAO:
            return CANAL_CYBER
        if alert.severity is SeveridadeAlerta.INFO:
            return SOMENTE_JOURNAL
        return CANAL_CYBER
    return CANAL_POR_FONTE.get(alert.source, canal_padrao)


#: Marca que uma regra poe na evidencia para pedir atencao SONORA mesmo abaixo
#: de CRITICAL.
#:
#: ⚠️ Existe porque severidade e mencao viraram o mesmo eixo, e nao sao.
#: Severidade descreve CONSEQUENCIA -- o que acontece se isto for verdade.
#: Mencao descreve URGENCIA DE ATENCAO -- quao rapido um humano precisa olhar.
#:
#: Medido em 2026-09-07 com o ataque real de 329 conexoes: BAT-SSH-006 rebaixa
#: para WARNING em servidor somente-chave (correto -- nenhuma tentativa de senha
#: pode suceder ali), e `_mention` so mencionava em CRITICAL (correto -- nem tudo
#: deve acordar alguem). Juntas, as duas decisoes corretas faziam o ataque que
#: originou o programa chegar ao canal MUDO.
#:
#: A marca vive em `governance` de proposito: o vocabulario e daqui, e quem
#: decide QUANDO usa-lo e a regra em `observe`. O contrario -- `governance`
#: reconhecendo texto de `observe` -- inverteria a dependencia.
MARCA_ATENCAO_IMEDIATA = "atenção: requer olhar humano agora"


def mencao_do_alerta(
    alert: GovernanceAlert,
    *,
    fontes_everyone: frozenset[FonteAlerta] = frozenset(),
) -> str:
    """A politica de mencao, INTEIRA e num lugar so.

    §B.1 do Apendice B: *"A politica anterior nao deve operar em paralelo com
    limiares conflitantes: a versao escolhida para producao deve ser unica e
    registrada."* Por isso e funcao de modulo e nao metodo do sink -- um metodo
    privado criaria uma segunda politica, testavel por fora e diferente da que
    roda.

    | mencao      | quando                                                |
    |-------------|-------------------------------------------------------|
    | `@everyone` | alguem ENTROU -- CRITICAL de autenticacao aceita       |
    | `@here`     | qualquer CRITICAL; e WARNING com a marca de atencao    |
    | (nenhuma)   | INFO, verde diario, recuperacao, ruido esperado        |

    ⚠️ A marca NAO basta sozinha: em INFO ela e ignorada. Se bastasse, uma regra
    distraida faria o verde diario tocar -- e verde que toca todo dia e o jeito
    mais rapido de ensinar o leitor a silenciar o canal inteiro, levando junto o
    `@here` de verdade.
    """
    origens = _origens(alert)
    if alert.severity is SeveridadeAlerta.CRITICAL:
        if alert.source in fontes_everyone or origens & _ORIGENS_DE_INTRUSAO_CONFIRMADA:
            return "@everyone"
        return "@here"
    if alert.severity is SeveridadeAlerta.WARNING and _tem_marca_de_atencao(alert):
        return "@here"
    return ""


def _tem_marca_de_atencao(alert: GovernanceAlert) -> bool:
    return any(
        linha.strip() == MARCA_ATENCAO_IMEDIATA for ev in alert.evidence for linha in ev.evidencias
    )


def _assinatura(alert: GovernanceAlert) -> str:
    """Assinatura de CONTEUDO (nao inclui o id uuid7, sempre unico, NEM campos
    volateis como latencia/timestamp) — base do dedupe por estado."""
    partes = [
        alert.source.value,
        alert.severity.value,
        str(alert.related_tenant_id or ""),
    ]
    for ev in alert.evidence:
        partes.append(ev.origem)
        partes.extend(e for e in ev.evidencias if not _EVIDENCIA_VOLATIL.match(e))
    return "\n".join(partes)


# Roteamento por TEMA: cada fonte de alerta -> nome de canal (reusa os
# canais existentes do legado: security/infra/performance/log). Fonte sem
# entrada aqui cai no `canal_padrao` ("o que mais se aproxima"). Espelha o
# `RULE_CHANNEL` do `discord_alert.py` legado.
CANAL_POR_FONTE: dict[FonteAlerta, str] = {
    FonteAlerta.INFRA_SATURATION: "infra",
    FonteAlerta.SERVICE_DOWN: "infra",
    FonteAlerta.DATA_PIPELINE_ERROR: "infra",
    FonteAlerta.DATA_SOURCE_STALE: "infra",
    FonteAlerta.DATA_SOURCE_MISSING: "infra",
    FonteAlerta.DATA_ROW_COUNT_DROP: "infra",
    FonteAlerta.ENDPOINT_DOWN: "performance",
    FonteAlerta.ENDPOINT_LATENCY: "performance",
    FonteAlerta.ENDPOINT_ERROR_RATE: "performance",
    FonteAlerta.FEATURE_DOWN: "performance",
    FonteAlerta.MONITOR_CEGO: "performance",
    FonteAlerta.FEATURE_RECOVERED: "performance",
    FonteAlerta.SLA_BREACH: "performance",
    FonteAlerta.SECURITY_INTRUSION: "security",
    FonteAlerta.TENANT_ISOLATION_INCIDENT: "security",
    FonteAlerta.OBSERVE_HEARTBEAT: "log",
    FonteAlerta.MANIFEST_DRIFT: "log",
    # Exposicao da origem e postura de SEGURANCA, e nao registro: o WEB-06
    # trata de contornar a borda, e o canal de quem le isso e o de seguranca.
    FonteAlerta.ORIGEM_ALCANCAVEL: "security",
    FonteAlerta.MONITOR_CEGO: "log",
    # Circuit breaker e degradacao de servico, nao registro: vai com os demais
    # sinais de disponibilidade (ajuste do DEV, 2026-09-05).
    FonteAlerta.LLM_CIRCUIT_BREAKER: "performance",
    FonteAlerta.RULE_DRIFT: "log",
    FonteAlerta.HUMAN_REVIEW_BACKLOG: "log",
    FonteAlerta.ADDENDUM_REVIEW_REQUEST: "log",
}


class DiscordAlertSink:
    """Satisfaz `AlertSink`. Constroi o embed, aplica dedupe por estado e
    roteia por TEMA (fonte->canal, `CANAL_POR_FONTE`) quando ha
    `webhooks_por_canal`; senao roteia por tenant. Nunca levanta em
    `enviar`."""

    def __init__(
        self,
        webhooks_por_tenant: dict[TenantId, str] | None = None,
        webhook_global: str | None = None,
        transporte: TransporteWebhook | None = None,
        fontes_everyone: frozenset[FonteAlerta] = frozenset(),
        webhooks_por_canal: dict[str, str] | None = None,
        canal_padrao: str = "log",
        caminho_estado: Path | None = None,
        janela_repeticao_s: float = 86400.0,
        janelas_por_severidade: dict[SeveridadeAlerta, float] | None = None,
        janelas_por_origem: dict[str, float] | None = None,
        caminho_journal: Path | None = None,
    ) -> None:
        # Journal local de alertas. `None` = usa o caminho do ambiente
        # (`BATMANOS_ALERT_JOURNAL`) ou o default ao lado do estado de dedup —
        # nunca "desligado": era o registro AUSENTE que deixou o observe 43
        # dias mudo. Ver `alert_journal`.
        self._caminho_journal = caminho_journal
        self._webhooks = dict(webhooks_por_tenant or {})
        self._webhook_global = webhook_global
        self._transporte: TransporteWebhook = transporte or _TransporteUrllib()
        self._fontes_everyone = fontes_everyone
        self._webhooks_por_canal = dict(webhooks_por_canal or {})
        self._canal_padrao = canal_padrao
        # Dedupe por estado com JANELA (default 24h): (source|tenant) ->
        # {"sig": assinatura, "ts": epoch}. Reenvia se a assinatura MUDOU
        # (transicao real — nova falha OU recuperacao) OU se passou a janela
        # desde o ultimo envio identico. Um alerta CONHECIDO e REPETIDO (ex.:
        # feature caida sob incidente ativo) vira no maximo 1x/dia, em vez de
        # 1x a cada ciclo de 5min. PERSISTIDO em disco: cada run do cron do
        # monitor e um processo NOVO — sem disco, o estado nascia vazio e
        # re-alertava todo ciclo (o flood que o Rodrigo reportou, 2026-07-23).
        self._caminho_estado = caminho_estado
        self._janela_repeticao_s = janela_repeticao_s
        # Janela de re-alerta POR SEVERIDADE (diretriz do Rodrigo, 2026-07-24):
        # so faz sentido re-alertar de HORA EM HORA o que impacta o usuario /
        # derruba feature / e vulnerabilidade (CRITICAL). WARNING espaca mais;
        # INFO/telemetria (heartbeat, porta loopback benigna) vira 1x/dia.
        self._janelas: dict[SeveridadeAlerta, float] = janelas_por_severidade or {
            SeveridadeAlerta.CRITICAL: 3600.0,  # 1h — outage/vulnerabilidade ATIVA
            SeveridadeAlerta.WARNING: 21600.0,  # 6h
            SeveridadeAlerta.INFO: 86400.0,  # 24h — telemetria/benigno
        }
        # ⚠️ Janela por ORIGEM da evidencia, consultada ANTES da severidade.
        # `DIV-PROVA-001`: a prova de vida e INFO e compartilha
        # `FonteAlerta.SECURITY_INTRUSION` com `ssh-bruteforce` e
        # `porta-manutencao`, entao nem a severidade nem a fonte a distinguem --
        # so a origem da evidencia (`observe:resumo-diario`). Baixar a janela de
        # INFO inteira para consertar a prova soltaria o throttle de tudo que e
        # telemetria, e `porta-manutencao` sozinho ja e 90% do journal.
        self._janelas_por_origem = dict(janelas_por_origem or {})
        self._janela_maxima = max(
            [*self._janelas.values(), *self._janelas_por_origem.values()],
            default=janela_repeticao_s,
        )
        bruto = self._ler_estado()
        self._estado: dict[str, dict[str, Any]] = self._podar_dedup(bruto)
        # ⚠️ Referencia da mensagem original por TENANT E ASSUNTO -- ver
        # `chave_de_mensagem` e `DIV-TENANT-001`. Guarda o ID e a impressao do
        # destino, NUNCA a URL, que carrega o token.
        self._mensagens: dict[str, dict[str, str]] = {
            _migrar_chave_de_mensagem(str(k)): {str(kk): str(vv) for kk, vv in v.items()}
            for k, v in (bruto.get("mensagens") or {}).items()
            if isinstance(v, dict)
        }

    def _ler_estado(self) -> dict[str, Any]:
        """O arquivo inteiro, best-effort.

        ⚠️ Aceita as DUAS formas: o formato antigo era um mapa de chave de
        dedupe direto na raiz, e o novo tem `dedup` e `mensagens`. Ler so o novo
        faria todo throttle em curso reiniciar no deploy -- e o flood que ele
        segura voltaria por uma janela inteira.
        """
        if self._caminho_estado is None or not self._caminho_estado.exists():
            return {}
        try:
            dados = json.loads(self._caminho_estado.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(
                "estado de dedup ilegivel (%s), comecando limpo: %s", self._caminho_estado, exc
            )
            return {}
        if not isinstance(dados, dict):
            return {}
        if "dedup" in dados or "mensagens" in dados:
            return dados
        return {"dedup": dados, "mensagens": {}}

    def _podar_dedup(self, bruto: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Poda entradas mais velhas que a janela — mantem o arquivo pequeno."""
        dados = bruto.get("dedup") or {}
        if not isinstance(dados, dict):
            return {}
        agora = time.time()
        return {
            k: v
            for k, v in dados.items()
            if isinstance(v, dict)
            and isinstance(v.get("ts"), (int, float))
            and (agora - float(v["ts"])) < self._janela_maxima
        }

    def _persistir_estado(self) -> None:
        """Grava o estado atomicamente (tmp + replace) — best-effort, uma
        falha de I/O nunca derruba o Governance Engine."""
        if self._caminho_estado is None:
            return
        try:
            self._caminho_estado.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._caminho_estado.with_suffix(self._caminho_estado.suffix + ".tmp")
            tmp.write_text(
                json.dumps({"dedup": self._estado, "mensagens": self._mensagens}),
                encoding="utf-8",
            )
            tmp.replace(self._caminho_estado)
        except OSError as exc:
            logger.warning("falha ao persistir estado de dedup: %s", exc)

    def _webhook_do_assunto(self, alert: GovernanceAlert, assunto: str) -> str | None:
        """O webhook que POSTOU a mensagem original deste assunto.

        ⚠️ Existe por um defeito achado em producao em 2026-09-08, no primeiro
        fechamento real: a abertura de CPU e `INFRA_SATURATION` e roteia para
        `#infra`; o fechamento e `FEATURE_RECOVERED` e roteia para
        `#performance`. Canais diferentes, webhooks diferentes -- e o `PATCH`
        registrava `edicao_impossivel` por destino divergente, sempre. A edicao
        da mensagem original era impossivel POR CONSTRUCAO, e so um fechamento
        de verdade em producao mostrou isso.

        A correcao segue do proprio mecanismo: para editar uma mensagem e
        preciso o MESMO webhook que a criou. Entao o fechamento nao escolhe
        canal por fonte -- ele volta ao destino de quem abriu.
        """
        # ⚠️ Busca pela chave COMPOSTA (`DIV-TENANT-001`). A varredura de
        # webhooks logo abaixo passa por `self._webhooks.values()`, que e o mapa
        # de TODOS os tenants: sem o tenant na chave, o fechamento de um casava
        # a mensagem de outro pela impressao do destino, e o PATCH ia no canal
        # errado.
        guardado = self._mensagens.get(chave_de_mensagem(alert, assunto))
        if guardado is None:
            return None
        impressao = guardado.get("destino")
        for webhook in (*self._webhooks_por_canal.values(), *self._webhooks.values()):
            if webhook and impressao_do_destino(webhook) == impressao:
                return webhook
        if self._webhook_global and impressao_do_destino(self._webhook_global) == impressao:
            return self._webhook_global
        return None

    def _webhook_para(self, alert: GovernanceAlert) -> str | None:
        # ⚠️ Fechamento com mensagem original conhecida volta ao webhook que a
        # postou -- ver `_webhook_do_assunto`. Roteia-lo por fonte mandaria o
        # fim para outro canal que o comeco.
        if _e_fechamento(alert):
            assunto = assunto_do_alerta(alert)
            if assunto:
                do_original = self._webhook_do_assunto(alert, assunto)
                if do_original is not None:
                    return do_original
        # ⚠️ **O SILENCIO DELIBERADO E DECIDIDO ANTES DE QUALQUER ROTEAMENTO** --
        # `DIV-SILENCIO-001`.
        #
        # Esta checagem vivia DENTRO do ramo `if self._webhooks_por_canal:`, e a
        # consequencia era que ela so valia num ambiente que declarasse webhook
        # por canal. Num deploy so com `BATMAN_DISCORD_WEBHOOK` global o ramo nao
        # era tomado, a execucao caia no fallback por tenant/global, e o alerta
        # que a politica mandou SILENCIAR era postado.
        #
        # MEDIDO em 19/09 com as duas origens que a producao mais silencia:
        # `porta-manutencao` e `varredura-web` davam "entregue" com 1 POST cada,
        # contra "silenciado_por_politica" e 0 POST no ambiente por canal.
        #
        # O custo era grande porque a `varredura-web` esta em SHADOW: o journal
        # de producao tem 6.781 registros dessas duas familias, e todos teriam
        # virado mensagem -- uma regra de limiar NAO APROVADO publicando no canal
        # humano, que e precisamente o que o shadow existe para impedir.
        #
        # ⚠️ Silencio por politica nao e "nao achei canal": e uma DECISAO, e
        # decisao nao pode depender de como o ambiente foi configurado.
        #
        # ⚠️ Calculado UMA vez: chamar `canal_do_alerta` duas vezes nao so
        # repetiria trabalho como faria o tipo perder o estreitamento -- depois
        # do `return`, `canal` e garantidamente `str`.
        canal = canal_do_alerta(alert, self._canal_padrao)
        if canal is SOMENTE_JOURNAL:
            return None
        # 1) roteamento por tema (fonte -> canal -> webhook), com fallback
        #    para o canal padrao ("o que mais se aproxima").
        if self._webhooks_por_canal:
            webhook = self._webhooks_por_canal.get(canal)
            if webhook is None and canal == CANAL_CYBER:
                # Fallback explicito, nao silencioso: enquanto o canal novo nao
                # estiver provisionado, a ameaca cai no canal antigo em vez de
                # sumir. Nunca vai aos dois.
                webhook = self._webhooks_por_canal.get(CANAL_SEGURANCA_LEGADO)
            webhook = webhook or self._webhooks_por_canal.get(self._canal_padrao)
            if webhook:
                return webhook
        # 2) fallback: por tenant, depois global.
        tenant = alert.related_tenant_id
        if tenant is not None and tenant in self._webhooks:
            return self._webhooks[tenant]
        return self._webhook_global

    def _mention(self, alert: GovernanceAlert) -> str:
        """Delega para a politica declarada. ⚠️ Nao reimplementar aqui: logica
        propria criaria uma segunda politica, e §B.1 proibe duas em paralelo."""
        return mencao_do_alerta(alert, fontes_everyone=self._fontes_everyone)

    #: A partir de quanto tempo um alerta que se repete deixa de ser "novo".
    #:
    #: ⚠️ `DIV-PRICES-001`. Medido no journal: `prices-20y` esta CRITICAL desde
    #: 08/09, com 284 `suprimido_por_janela` e ~23 entregas, e NUNCA recuperou.
    #: O throttle de 1 h fez exatamente o que foi desenhado para fazer -- e o
    #: efeito colateral foi rebaixar um incidente de tres dias a rumor de fundo,
    #: porque a 24a mensagem era identica a primeira.
    #:
    #: Incidente LONGO e incidente NOVO nao sao a mesma mensagem. 6 h e o ponto
    #: em que "ainda acontecendo" vira informacao propria: abaixo disso a
    #: repeticao ja se explica pelo throttle.
    IDADE_PARA_ANUNCIAR_ARRASTO_S = 6 * 3600.0

    def _linha_de_arrasto(self, chave: str, agora: float) -> str | None:
        """ "Isto nao e novo: dura ha X" — ou `None` quando ainda e recente."""
        desde = self._estado.get(chave, {}).get("desde")
        if desde is None:
            return None
        try:
            idade = agora - float(desde)
        except (TypeError, ValueError):
            return None
        if idade < self.IDADE_PARA_ANUNCIAR_ARRASTO_S:
            return None
        horas = idade / 3600.0
        if horas < 48.0:
            duracao = f"{horas:.0f} h"
        else:
            duracao = f"{horas / 24.0:.0f} dias"
        return (
            f"⚠️ NAO E NOVO: este alerta se repete ha {duracao} sem recuperacao."
            " Repeticao nao e reincidencia, e o MESMO problema, ainda aberto."
        )

    def _payload(self, alert: GovernanceAlert, arrasto: str | None = None) -> dict[str, Any]:
        """Monta o embed que uma pessoa nao-tecnica consegue ler.

        Responde, nesta ordem, as cinco perguntas que o DEV pediu: o que
        aconteceu, quanto foi medido, se o sistema continua funcionando, qual o
        impacto e o que fazer. O detalhe tecnico vai por ultimo, para quem
        precisar dele.

        ⚠️ Renderizacao NAO move o dedupe: `_assinatura` roda sobre
        `origem + evidencias` do proprio alerta, antes de qualquer texto daqui.

        ⚠️ As MEDICOES saem de `Evidence.historico` -- o campo volatil que o
        template antigo nao lia, e a razao de um CRITICAL de CPU anunciar
        `threshold=90.0` sem nunca dizer que mediu 100.
        """
        cor, _ = _ESTILO[alert.severity]
        campos: list[dict[str, Any]] = [
            {
                "name": "O que aconteceu",
                "value": mensagem.resumo(alert)[:1024],
                "inline": False,
            }
        ]
        if arrasto:
            campos.append({"name": "Há quanto tempo", "value": arrasto[:1024], "inline": False})
        medidas = mensagem.medicoes(alert)
        if medidas:
            # ⚠️ `WEB-10`: escapado como a evidencia, e pelo mesmo motivo. Hoje a
            # medicao e texto de regra, mas nada impede uma regra nova de
            # interpolar um valor do cliente aqui -- e a defesa nao pode depender
            # de ninguem lembrar disso.
            campos.append(
                {
                    "name": "Medição",
                    "value": escapar_markdown("\n".join(medidas))[:1024],
                    "inline": False,
                }
            )
        campos.append(
            {
                "name": "Estado do serviço",
                "value": mensagem.estado_do_servico(alert),
                "inline": True,
            }
        )
        campos.append(
            {
                "name": "Ação recomendada",
                "value": mensagem.acao_recomendada(alert),
                "inline": True,
            }
        )
        campos.append({"name": "Impacto", "value": mensagem.impacto(alert)[:1024], "inline": False})
        for ev in alert.evidence:
            # ⚠️ `WEB-10`, e este e o ponto MEDIDO do vetor: o caminho que o
            # cliente pediu entra aqui cru. `GET /**ATAQUE-CONTIDO**` virava
            # negrito dentro da mensagem do Batman -- falsificacao visual de
            # evidencia. Escapa ANTES da linha de confianca, que e italico
            # DELIBERADO do proprio sink e nao pode ser neutralizado junto.
            bruto = "\n".join(ev.evidencias) if ev.evidencias else "(sem detalhe)"
            valor = escapar_markdown(bruto)
            if ev.confianca is not None:
                valor += f"\n_confianca: {ev.confianca:.2f}_"
            campos.append(
                {
                    "name": f"Detalhe técnico · {ev.origem}"[:256],
                    "value": valor[:1024],
                    "inline": False,
                }
            )
        tenant = str(alert.related_tenant_id or "global")
        return {
            "content": self._mention(alert),
            "embeds": [
                {
                    "title": mensagem.titulo(alert)[:256],
                    "color": cor,
                    "fields": campos,
                    "footer": {"text": f"{mensagem.identificacao(alert)} · tenant={tenant}"[:2048]},
                }
            ],
        }

    def _registrar(
        self,
        alert: GovernanceAlert,
        *,
        outcome: str,
        notification_sent: bool,
        fingerprint: str | None = None,
        detalhe: str | None = None,
    ) -> None:
        """Anexa o desfecho ao journal local. Best-effort, nunca levanta.

        ⚠️ Chamado nos QUATRO desfechos de `enviar`, nao so no envio: tres eram
        silenciosos, e por causa disso "nao vi alerta" significava tanto *nao
        houve problema* quanto *houve e nao chegou*. Ver `alert_journal`.
        """
        # ⚠️ `DIV-CANAL-001`. Isto era
        #     `canal_do_alerta(...) if self._webhooks_por_canal else None`,
        # e o `else None` reencenava a ambiguidade que o card denuncia, um campo
        # adiante: `SOMENTE_JOURNAL` **e** `None`, entao num ambiente so com
        # webhook global um CRITICAL de cyber entregue com sucesso era journalado
        # com `channel=None` -- o mesmo valor de um alerta roteado de proposito
        # ao journal. "A politica escolheu o journal" e "ninguem calculou a rota"
        # ficavam indistinguiveis, e quem auditasse o canal de cyber via vazio.
        #
        # O campo responde "para onde a POLITICA mandou", e essa resposta nao
        # depende de haver webhook configurado para la. Quem responde "chegou?"
        # e o `outcome`, que ja distingue os quatro desfechos.
        #
        # E o proprio docstring de `canal_do_alerta` ja exigia isto: ela mora num
        # lugar so "porque os dois precisam concordar -- se divergirem, a
        # mensagem vai para um canal e o journal registra outro".
        canal = canal_do_alerta(alert, self._canal_padrao)
        try:
            alert_journal.registrar(
                alert_journal.evento(
                    alert,
                    outcome=outcome,
                    notification_sent=notification_sent,
                    canal=canal,
                    fingerprint=fingerprint,
                    detalhe=detalhe,
                ),
                caminho=self._caminho_journal,
            )
        except Exception as exc:  # o journal jamais derruba a entrega
            logger.error("journal de alertas falhou: %s", exc)

    def _janela_para(self, alert: GovernanceAlert) -> float:
        """Janela de dedup deste alerta. ORIGEM vence severidade.

        ⚠️ A origem e mais especifica que a severidade e que a fonte, e e a
        unica que distingue a prova de vida do resto do `SECURITY_INTRUSION`
        INFO. Quando um alerta tem varias evidencias, vence a MENOR janela
        configurada: o sobrescrito existe para deixar passar, e o conservador
        aqui e deixar passar, nao calar.
        """
        candidatas = [
            self._janelas_por_origem[ev.origem]
            for ev in alert.evidence
            if ev.origem in self._janelas_por_origem
        ]
        if candidatas:
            return min(candidatas)
        return self._janelas.get(alert.severity, self._janela_repeticao_s)

    def enviar(self, alert: GovernanceAlert) -> str | None:
        webhook = self._webhook_para(alert)
        if webhook is None:
            # No-op de ENTREGA (como o legado), nunca mais de REGISTRO: alerta
            # sem canal para onde ir era justamente o que sumia sem rastro.
            #
            # ⚠️ **Silencio DELIBERADO e falha de rota nao sao a mesma coisa** —
            # `DIV-VARREDURA-001`. Ate 11/09 os dois gravavam `sem_canal` com o
            # detalhe "nenhum webhook resolveu para este alerta", e o journal de
            # producao tinha 6.781 registros assim: `porta-manutencao` (politica
            # de manutencao) e `varredura-web` (regra em shadow, limiar nao
            # aprovado). TODOS deliberados -- e se uma rota quebrasse de verdade,
            # o registro dela seria indistinguivel desses milhares.
            #
            # Quem le o journal precisa separar "o Batman decidiu nao publicar"
            # de "o Batman quis publicar e nao conseguiu". A primeira e politica
            # funcionando; a segunda e defeito.
            if canal_do_alerta(alert, self._canal_padrao) is SOMENTE_JOURNAL:
                self._registrar(
                    alert,
                    outcome="silenciado_por_politica",
                    notification_sent=False,
                    detalhe=(
                        "roteado para SOMENTE_JOURNAL de proposito"
                        " (manutencao conhecida, ou regra em shadow com limiar nao aprovado)"
                    ),
                )
                return "silenciado_por_politica"
            self._registrar(
                alert,
                outcome="sem_canal",
                notification_sent=False,
                detalhe=(
                    "a politica pedia um canal e NENHUM webhook resolveu"
                    ", isto e defeito de configuracao, nao silencio deliberado"
                ),
            )
            return "sem_canal"

        # Chave = hash da ASSINATURA COMPLETA (source+severidade+tenant+toda a
        # evidencia), NAO (source,tenant): dois alertas do MESMO source no mesmo
        # ciclo (ex.: portas 5678 e 5679, ambos security-intrusion) tem chaves
        # DISTINTAS e cada um throttla sozinho — senao se sobrescreviam e ambos
        # re-disparavam todo ciclo (a causa real do flood duplo, 2026-07-23).
        chave = hashlib.sha256(_assinatura(alert).encode("utf-8")).hexdigest()[:16]
        agora = time.time()

        # ⚠️ A EDICAO passa NA FRENTE do throttle, e isso foi aprendido em
        # producao em 2026-09-08: a abertura foi entregue, guardou a referencia,
        # e o fechamento caiu em `suprimido_por_janela` porque um fechamento
        # anterior tinha a mesma assinatura dentro da janela. Resultado: a
        # mensagem original ficava dizendo CRITICAL para sempre, com a metrica
        # ja recuperada.
        #
        # O throttle existe para nao repetir MENSAGEM. Editar nao repete nada --
        # nao cria mensagem, nao notifica, nao faz som. Aplicar a ele a regra do
        # ruido e usar a defesa contra flood para impedir a CORRECAO de uma
        # mensagem errada.
        assunto_cedo = assunto_do_alerta(alert)
        chave_msg = chave_de_mensagem(alert, assunto_cedo) if assunto_cedo else ""
        if (
            assunto_cedo
            and _e_fechamento(alert)
            and chave_msg in self._mensagens
            # ⚠️ Incidente NAO edita: a abertura fica como registro. Ver
            # `_ASSUNTOS_COM_HISTORICO` -- substituir a abertura pelo
            # encerramento apagava quando o ataque comecou e o que foi
            # identificado, que e a trilha que a tratativa precisa.
            and not preserva_historico(assunto_cedo)
        ):
            return self._editar_original(
                alert, webhook, chave_msg, self._mensagens[chave_msg], chave
            )
        if assunto_cedo and _e_fechamento(alert) and preserva_historico(assunto_cedo):
            # O fechamento vira mensagem propria. Libera a chave de dedupe da
            # abertura para que uma REABERTURA futura do mesmo assunto nao seja
            # engolida pelo throttle -- o mesmo motivo pelo qual a edicao a
            # liberava.
            guardado = self._mensagens.pop(chave_msg, None)
            if guardado is not None:
                self._estado.pop(str(guardado.get("chave_abertura", "")), None)
                self._persistir_estado()

        anterior = self._estado.get(chave)
        janela = self._janela_para(alert)
        idade = agora - float(anterior["ts"]) if anterior else None
        if idade is not None and idade < janela:
            logger.debug(
                "alerta repetido suprimido (throttle %.0fs, sev=%s): %s",
                janela,
                alert.severity.value,
                chave,
            )
            # O desfecho MAIS importante de registrar: e ele que distingue
            # "o problema parou" de "o problema continua e a janela o calou".
            # Sem esta linha, um incidente ativo ha 6 h e um resolvido ficam
            # identicos no registro — e o `logger.debug` acima nunca chegou a
            # lugar nenhum, porque o log do observe estava mudo.
            self._registrar(
                alert,
                outcome="suprimido_por_janela",
                notification_sent=False,
                fingerprint=chave,
                detalhe=f"repetido dentro da janela de {janela:.0f}s (idade {idade:.0f}s)",
            )
            return "suprimido_por_janela"

        assunto = assunto_do_alerta(alert)
        anterior_desde = (anterior or {}).get("desde")
        arrasto = self._linha_de_arrasto(chave, agora)

        try:
            message_id = self._transporte.postar(webhook, self._payload(alert, arrasto))
        except Exception as exc:  # entrega best-effort, jamais derruba governanca
            logger.error("falha ao entregar GovernanceAlert no Discord: %s", exc)
            self._registrar(
                alert,
                outcome="falha_de_transporte",
                notification_sent=False,
                fingerprint=chave,
                detalhe=f"{type(exc).__name__}: {exc}",
            )
            return "falha_de_transporte"
        # so marca como enviado apos sucesso — falha permite reenvio no proximo
        # ⚠️ `desde` atravessa os reenvios: e o inicio do EPISODIO, nao do
        # ultimo envio. Sobrescreve-lo a cada entrega apagaria a idade e
        # devolveria o `DIV-PRICES-001` -- um incidente de tres dias lido
        # como se tivesse comecado agora.
        self._estado[chave] = {"ts": agora, "desde": anterior_desde or agora}
        if assunto and message_id and not _e_fechamento(alert):
            # ⚠️ Guarda o ID e a IMPRESSAO do destino -- nunca a URL, que carrega
            # o token do webhook.
            self._mensagens[chave_de_mensagem(alert, assunto)] = {
                "id": message_id,
                "destino": impressao_do_destino(webhook),
                # ⚠️ A chave de dedupe da ABERTURA viaja junto. Quando o assunto
                # encerra, ela e liberada: o episodio acabou, entao a proxima
                # ocorrencia e evento NOVO, nao repeticao. Sem isso a mensagem
                # original fica corrigida para "resolvido" e uma reabertura
                # dentro da janela e engolida -- o operador nao fica sabendo que
                # o problema voltou.
                "chave_abertura": chave,
            }
        self._persistir_estado()
        self._registrar(alert, outcome="entregue", notification_sent=True, fingerprint=chave)
        return "entregue"

    def _editar_original(
        self,
        alert: GovernanceAlert,
        webhook: str,
        # ⚠️ A chave COMPOSTA (`tenant|assunto`), e nao o assunto cru --
        # `DIV-TENANT-001`. O nome antigo (`assunto`) passou a mentir no dia em
        # que a chave ganhou tenant, e parametro com nome errado e como comentario
        # desatualizado: ele continua sendo lido como se fosse verdade.
        chave_mensagem: str,
        original: dict[str, str],
        chave: str,
    ) -> str:
        """Reescreve a mensagem de abertura com a recuperacao — SAIDA-004.

        ⚠️ Decisao do DEV: *"se houve mensagem original, ela e editada com a
        recuperacao"* e *"nao nasce uma nova notificacao sonora para cada
        fechamento"*. Editar nao toca o canal; postar de novo tocaria.

        ⚠️ E se a edicao falhar: *"auditar e usar o resumo existente; nao criar
        mensagem substituta nem impedir o fechamento interno"*. As tres coisas
        acontecem aqui -- o journal registra, nenhum POST e feito, e o estado
        interno ja fechou antes de esta funcao existir no caminho.
        """
        if original.get("destino") != impressao_do_destino(webhook):
            # O webhook rotacionou: o ID pertence a outro destino e a edicao
            # nem e tentada. Orfao AUDITADO e melhor que 404 silencioso.
            self._mensagens.pop(chave_mensagem, None)
            self._persistir_estado()
            self._registrar(
                alert,
                outcome="edicao_impossivel",
                notification_sent=False,
                fingerprint=chave,
                detalhe="o webhook mudou desde a mensagem original; o ID ficou orfao",
            )
            return "edicao_impossivel"
        try:
            self._transporte.editar(webhook, original["id"], self._payload(alert))
        except Exception as exc:
            logger.warning("nao consegui editar a mensagem original: %s", exc)
            self._registrar(
                alert,
                outcome="falha_de_edicao",
                notification_sent=False,
                fingerprint=chave,
                detalhe=(
                    f"{type(exc).__name__}: {exc}, sem mensagem substituta;"
                    " a recuperacao sai no resumo diario"
                ),
            )
            return "falha_de_edicao"
        # O assunto encerrou: o ID deixa de valer, e um assunto novo comeca do
        # zero se a metrica voltar a cruzar o limiar.
        chave_abertura = original.get("chave_abertura")
        if chave_abertura:
            self._estado.pop(chave_abertura, None)
        self._mensagens.pop(chave_mensagem, None)
        self._persistir_estado()
        self._registrar(
            alert,
            outcome="editado",
            notification_sent=True,
            fingerprint=chave,
            detalhe="mensagem original reescrita com a recuperacao, sem novo som",
        )
        return "editado"
