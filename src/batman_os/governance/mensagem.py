"""Traduz um `GovernanceAlert` para o que uma pessoa precisa saber.

Ordem do DEV (Radar Preditivo), 2026-09-05: *"nem sempre eu, que sou leigo,
consigo entender"*. O alerta precisa responder cinco perguntas — o que
aconteceu, qual foi o impacto, se o sistema continua funcionando, o que deve
ser feito, e onde consultar as evidencias.

**O que o template antigo entregava**, na integra, num CRITICAL real:

    🔴 [CRITICAL] infra-saturation
    AlertRule:observe.cpu:crit
        metric=observe.cpu:crit
        condition=above
        threshold=90.0

Diz o limiar e **nunca diz quanto mediu** — o valor existia em
`Evidence.historico` e o renderizador nao lia esse campo. Sem impacto, sem
acao, e com o identificador interno no lugar do titulo.

⚠️ **Este modulo NAO altera o alerta, so o texto.** A assinatura de dedupe
(`_assinatura` em `alert_sinks`) e calculada sobre `origem + evidencias` do
`GovernanceAlert`, antes de qualquer renderizacao. Reescrever a mensagem nao
move o fingerprint — foi por isso que o redesenho coube sem tocar no throttle.

⚠️ **As MEDICOES vem de `Evidence.historico`**, que e volatil de proposito e
fica fora da assinatura. Publicar esse campo e o conserto do "limiar sem valor";
move-lo para `evidencias` para aparecer teria reintroduzido o flood que a
separacao por campo resolveu.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    SeveridadeAlerta,
)

#: Fuso de exibicao. O DEV opera em BRT; os carimbos internos sao UTC. Mostrar
#: os dois evita a conversao de cabeca no meio de um incidente.
_BRT = timedelta(hours=-3)


class EstadoDoServico:
    """As cinco respostas possiveis a "o sistema continua funcionando?".

    `INCONCLUSIVO` e a mais importante: e o que impede o monitor de afirmar
    saude quando nao mediu nada. Sem ela, "nao encontrei problema" e "nao
    consegui procurar" chegam iguais.
    """

    INDISPONIVEL = "🔴 Indisponível"
    DEGRADADO = "🟠 Degradado"
    SOB_RISCO = "⚠️ Funcionando, mas sob risco"
    NORMAL = "🟢 Funcionando normalmente"
    INCONCLUSIVO = "❔ Monitoramento inconclusivo"


class Acao:
    NENHUMA = "Nenhuma ação necessária"
    ACOMPANHAR = "Acompanhar"
    INVESTIGAR = "Investigar"
    ESCALAR = "Escalar para intervenção humana"


#: (titulo, estado, impacto, acao) por ORIGEM da evidencia.
#:
#: A origem e mais fina que a fonte, e essa diferenca e o que permite escrever
#: um texto util: as oito regras de seguranca emitem `SECURITY_INTRUSION`, entao
#: a fonte sozinha nao distingue "porta do n8n" de "brute-force SSH".
_POR_ORIGEM: dict[str, tuple[str, str, str, str, str]] = {
    "observe:ssh-bruteforce": (
        "Possível ataque de força bruta no SSH",
        "Muitas tentativas de login no servidor falharam vindas do mesmo endereço.",
        EstadoDoServico.SOB_RISCO,
        "Se a senha for descoberta, o atacante ganha acesso ao servidor.",
        Acao.INVESTIGAR,
    ),
    "observe:ssh-auth-bypass": (
        "Login SSH aceito de um IP que vinha falhando",
        "Um login foi aceito de um endereço que vinha errando a senha repetidamente.",
        EstadoDoServico.SOB_RISCO,
        "Pode indicar que um ataque de força bruta teve sucesso.",
        Acao.ESCALAR,
    ),
    "observe:bruteforce-404": (
        "Varredura de endereços no site",
        "Alguém está pedindo muitos endereços que não existem no site.",
        EstadoDoServico.SOB_RISCO,
        "Alguém procura páginas e arquivos que não deveria conhecer.",
        Acao.INVESTIGAR,
    ),
    "observe:nao-autorizado": (
        "Tentativas de acesso negadas em sequência",
        "Várias tentativas de acesso foram recusadas em sequência.",
        EstadoDoServico.SOB_RISCO,
        "Pode ser alguém tentando alcançar área restrita.",
        Acao.INVESTIGAR,
    ),
    "observe:porta-inesperada": (
        "Porta de rede aberta sem ser prevista",
        "Uma porta de rede não prevista está aceitando conexões.",
        EstadoDoServico.SOB_RISCO,
        "Porta não declarada pode ser um serviço instalado sem autorização.",
        Acao.INVESTIGAR,
    ),
    "observe:porta-manutencao": (
        "Porta de manutenção em escuta",
        "Uma porta interna de manutenção está em escuta, como esperado.",
        EstadoDoServico.NORMAL,
        "Nenhum. É infraestrutura interna esperada.",
        Acao.NENHUMA,
    ),
    "observe:processo-suspeito": (
        "Ferramenta de ataque rodando no servidor",
        "Um programa típico de invasão ou mineração está rodando no servidor.",
        EstadoDoServico.SOB_RISCO,
        "Programa típico de invasão ou mineração está em execução.",
        Acao.ESCALAR,
    ),
    "observe:endpoint-exposto": (
        "Endereço sensível acessível sem senha",
        "Um endereço que deveria pedir senha está respondendo sem ela.",
        EstadoDoServico.SOB_RISCO,
        "Dados ou funções internas podem estar abertos ao público.",
        Acao.INVESTIGAR,
    ),
    "observe:headers-ausentes": (
        "Site sem cabeçalhos de proteção",
        "O site parou de enviar cabeçalhos que protegem o navegador do usuário.",
        EstadoDoServico.SOB_RISCO,
        "O navegador do usuário fica menos protegido contra ataques comuns.",
        Acao.INVESTIGAR,
    ),
    "observe:nginx-invalida": (
        "Configuração do servidor web inválida",
        "A configuração do servidor web está com erro e não seria aceita numa recarga.",
        EstadoDoServico.SOB_RISCO,
        "Uma recarga do servidor pode falhar e derrubar o site.",
        Acao.INVESTIGAR,
    ),
    "observe:nginx-mudanca": (
        "Configuração do servidor web foi alterada",
        "O arquivo de configuração do servidor web mudou sem que uma alteração fosse prevista.",
        EstadoDoServico.SOB_RISCO,
        "Alteração não prevista pode indicar acesso indevido.",
        Acao.ESCALAR,
    ),
    "observe:servico-caido": (
        "Serviço esperado não está rodando",
        "Um serviço que deveria estar rodando não foi encontrado.",
        EstadoDoServico.DEGRADADO,
        "A função que depende desse serviço para de acontecer.",
        Acao.INVESTIGAR,
    ),
    "observe:caminho-degradado": (
        "Site inacessível pela internet",
        "O site não respondeu pela internet, embora a aplicação responda por dentro.",
        EstadoDoServico.INDISPONIVEL,
        "O usuário não consegue abrir o site, mesmo com a aplicação de pé.",
        Acao.INVESTIGAR,
    ),
    "observe:endpoint-down": (
        "Site fora do ar",
        "O site não respondeu nem pela internet nem por dentro do servidor.",
        EstadoDoServico.INDISPONIVEL,
        "O site não responde nem por dentro nem por fora.",
        Acao.ESCALAR,
    ),
    "observe:heartbeat": (
        "Monitoramento ativo",
        "O monitor está rodando e completou o ciclo.",
        EstadoDoServico.NORMAL,
        "Nenhum. É a confirmação de que o monitor está vivo.",
        Acao.NENHUMA,
    ),
    "observe:fonte-inacessivel": (
        "Monitor sem acesso ao log de segurança",
        "O monitor não conseguiu ler os arquivos de log que usa para checar segurança.",
        EstadoDoServico.INCONCLUSIVO,
        "As checagens de segurança dessas fontes não rodaram. Não sabemos se "
        "houve ataque, a ausência de alerta aqui não é sinal de que está tudo bem.",
        Acao.INVESTIGAR,
    ),
    "observe:fonte-restabelecida": (
        "Monitor voltou a enxergar",
        "O monitor voltou a ler os arquivos de log de segurança.",
        EstadoDoServico.NORMAL,
        "As checagens de segurança que estavam inconclusivas voltaram a valer.",
        Acao.NENHUMA,
    ),
    "feature-monitor:sessao": (
        "Monitor sem conseguir entrar no sistema",
        "O monitor não conseguiu entrar no sistema, então várias verificações não rodaram.",
        EstadoDoServico.INCONCLUSIVO,
        "Várias verificações deixaram de rodar. As funções podem estar boas, "
        "elas não foram testadas.",
        Acao.INVESTIGAR,
    ),
    # ------------------------------------------------------------------
    # Fechamentos e resumos. Ate 09/09 TODOS caiam em `_PADRAO` e chegavam ao
    # Discord como "Evento de governanca ... sem classificacao automatica" com
    # acao "Investigar" — pedindo investigacao de um incidente que acabara de
    # ser ENCERRADO.
    # ------------------------------------------------------------------
    "observe:ssh-recuperado": (
        "Incidente de SSH encerrado",
        "As tentativas de acesso pararam e o incidente foi encerrado.",
        EstadoDoServico.NORMAL,
        "Nenhum agora. O registro fica guardado pelo número de protocolo.",
        Acao.NENHUMA,
    ),
    "observe:web-recuperado": (
        "Incidente no site encerrado",
        "As requisições suspeitas pararam e o incidente foi encerrado.",
        EstadoDoServico.NORMAL,
        "Nenhum agora. O registro fica guardado pelo número de protocolo.",
        Acao.NENHUMA,
    ),
    "observe:resumo-diario": (
        "Resumo diário de segurança",
        "O balanço do dia, com o que ficou aberto e o que foi encerrado.",
        EstadoDoServico.NORMAL,
        "Nenhum. É a prova de vida diária do monitor.",
        Acao.NENHUMA,
    ),
    "observe:autenticacao-aceita": (
        "Alguém ENTROU no servidor",
        "Uma autenticação foi aceita, alguém conseguiu entrar.",
        EstadoDoServico.SOB_RISCO,
        "Se não foi você, há acesso ativo agora. O bloqueio não encerra sessão aberta.",
        Acao.ESCALAR,
    ),
    "observe:credencial-interna": (
        "Credencial interna usada de fora",
        "Uma credencial de uso interno apareceu vindo da internet.",
        EstadoDoServico.SOB_RISCO,
        "Credencial interna exposta permite acesso sem forçar senha.",
        Acao.ESCALAR,
    ),
    "observe:teto-de-incidentes": (
        "O monitor parou de registrar incidentes novos",
        "O limite de incidentes simultâneos foi atingido.",
        EstadoDoServico.INCONCLUSIVO,
        "A partir daqui, atacantes novos são detectados mas NÃO ficam registrados.",
        Acao.INVESTIGAR,
    ),
    "observe:varredura-web": (
        "Varredura de vulnerabilidades no site",
        "Alguém pediu caminhos típicos de sistemas que este site não usa.",
        EstadoDoServico.SOB_RISCO,
        "É reconhecimento automatizado procurando falha conhecida.",
        Acao.ACOMPANHAR,
    ),
}

#: Texto por FONTE, para o que a origem nao cobre.
_POR_FONTE: dict[FonteAlerta, tuple[str, str, str, str, str]] = {
    FonteAlerta.INFRA_SATURATION: (
        "Servidor sem folga de recursos",
        "O uso de recursos da máquina passou do limite configurado.",
        EstadoDoServico.SOB_RISCO,
        "Com a máquina no limite, o site pode ficar lento ou parar de responder.",
        Acao.ACOMPANHAR,
    ),
    FonteAlerta.ENDPOINT_LATENCY: (
        "Site respondendo lentamente",
        "As páginas estão demorando mais que o tempo aceitável para responder.",
        EstadoDoServico.DEGRADADO,
        "As páginas demoram mais para abrir.",
        Acao.ACOMPANHAR,
    ),
    FonteAlerta.ENDPOINT_ERROR_RATE: (
        "Site devolvendo muitos erros",
        "Uma parte grande das requisições está terminando em erro.",
        EstadoDoServico.DEGRADADO,
        "Parte das ações do usuário falha.",
        Acao.INVESTIGAR,
    ),
    FonteAlerta.FEATURE_DOWN: (
        "Uma função do site parou de responder",
        "Uma função verificada pelo monitor deixou de responder como esperado.",
        EstadoDoServico.DEGRADADO,
        "Quem tentar usar essa função encontra erro.",
        Acao.INVESTIGAR,
    ),
    FonteAlerta.FEATURE_RECOVERED: (
        "Função restabelecida",
        "Uma função que estava com problema voltou a responder.",
        EstadoDoServico.NORMAL,
        "Nenhum. A função voltou a responder.",
        Acao.NENHUMA,
    ),
    FonteAlerta.MONITOR_CEGO: (
        "Monitoramento incompleto",
        "Parte das verificações do monitor não pôde ser executada neste ciclo.",
        EstadoDoServico.INCONCLUSIVO,
        "Parte das verificações não rodou. Ausência de alerta aqui não prova saúde.",
        Acao.INVESTIGAR,
    ),
    FonteAlerta.SERVICE_DOWN: (
        "Serviço parado",
        "Um serviço esperado não está em execução.",
        EstadoDoServico.DEGRADADO,
        "A função que depende dele para de acontecer.",
        Acao.INVESTIGAR,
    ),
    FonteAlerta.ORIGEM_ALCANCAVEL: (
        "Origem alcancavel sem passar pela borda",
        "O servidor respondeu a pedidos que nao vieram pelo proxy da nossa zona.",
        EstadoDoServico.INCONCLUSIVO,
        "Quem souber o endereco do servidor pode contornar a protecao da borda.",
        Acao.INVESTIGAR,
    ),
    FonteAlerta.MANIFEST_DRIFT: (
        "Lista de verificações desatualizada",
        "A lista de verificações não corresponde mais às rotas reais do site.",
        EstadoDoServico.INCONCLUSIVO,
        "O monitor pode estar checando rota que não existe mais.",
        Acao.ACOMPANHAR,
    ),
    FonteAlerta.LLM_CIRCUIT_BREAKER: (
        "Assistente de IA temporariamente desligado",
        "O limite de uso da IA foi atingido e o assistente foi desligado por segurança.",
        EstadoDoServico.DEGRADADO,
        "A Iris não responde até o limite ser reposto.",
        Acao.ACOMPANHAR,
    ),
    FonteAlerta.SLA_BREACH: (
        "Prazo de atendimento estourado",
        "O tempo de resposta acordado foi ultrapassado.",
        EstadoDoServico.DEGRADADO,
        "O compromisso de tempo de resposta foi ultrapassado.",
        Acao.INVESTIGAR,
    ),
    FonteAlerta.OBSERVE_HEARTBEAT: (
        "Monitoramento ativo",
        "O monitor está rodando e completou o ciclo.",
        EstadoDoServico.NORMAL,
        "Nenhum. É a confirmação de que o monitor está vivo.",
        Acao.NENHUMA,
    ),
    # ------------------------------------------------------------------
    # Sentinela de dados. As quatro fontes DATA_* nao estavam aqui, e nenhuma
    # origem `dados-sentinela:<id>` esta em `_POR_ORIGEM` (o id e dinamico) —
    # entao TODO alerta da sentinela caia em `_PADRAO`. Medido em producao:
    # 307 alertas de `prices-20y` chegaram a #infra como "Evento de governanca
    # ... sem classificacao automatica", parecendo degradacao de infra sem
    # nunca dizer que era dado obsoleto.
    # ------------------------------------------------------------------
    FonteAlerta.DATA_SOURCE_STALE: (
        "Dado parou de ser atualizado",
        "Um arquivo de dados está mais velho que a cadência esperada.",
        EstadoDoServico.DEGRADADO,
        "O produto segue de pé, mas responde com número velho.",
        Acao.INVESTIGAR,
    ),
    FonteAlerta.DATA_SOURCE_MISSING: (
        "Arquivo de dados desapareceu",
        "Um arquivo que deveria existir não foi encontrado.",
        EstadoDoServico.INDISPONIVEL,
        "O que depende desse arquivo não tem como funcionar.",
        Acao.ESCALAR,
    ),
    FonteAlerta.DATA_PIPELINE_ERROR: (
        "A rotina de dados falhou",
        "A última execução da rotina terminou com erro.",
        EstadoDoServico.DEGRADADO,
        "Os dados podem estar incompletos ou desatualizados.",
        Acao.INVESTIGAR,
    ),
    FonteAlerta.DATA_ROW_COUNT_DROP: (
        "Volume de dados caiu",
        "A rotina rodou, mas trouxe bem menos linhas que o normal.",
        EstadoDoServico.DEGRADADO,
        "Rodar sem erro e trazer pouco dado é pior que falhar: passa despercebido.",
        Acao.INVESTIGAR,
    ),
    # Rede de seguranca por FONTE: origem nova de seguranca ou de tenant que
    # ninguem classificou cai aqui, e nao mais no texto de governanca.
    FonteAlerta.SECURITY_INTRUSION: (
        "Atividade suspeita observada",
        "O monitor registrou atividade de segurança que merece leitura.",
        EstadoDoServico.SOB_RISCO,
        "Ver o detalhe técnico: o tipo exato está na evidência.",
        Acao.INVESTIGAR,
    ),
    FonteAlerta.TENANT_ISOLATION_INCIDENT: (
        "Isolamento entre clientes violado",
        "Um acesso cruzou a fronteira entre dois clientes.",
        EstadoDoServico.SOB_RISCO,
        "Dado de um cliente pode ter ficado visível para outro.",
        Acao.ESCALAR,
    ),
    FonteAlerta.ENDPOINT_DOWN: (
        "Endereço fora do ar",
        "Um endereço monitorado parou de responder.",
        EstadoDoServico.INDISPONIVEL,
        "Quem depende desse endereço não consegue usá-lo.",
        Acao.INVESTIGAR,
    ),
    # As tres fontes de governanca. Sao eventos de governanca DE VERDADE — mas
    # dizer "sem classificacao automatica" sobre elas tambem mente, porque
    # cada uma tem significado proprio e conhecido.
    FonteAlerta.HUMAN_REVIEW_BACKLOG: (
        "Fila de revisão humana acumulando",
        "Há decisões esperando revisão de uma pessoa há mais tempo que o previsto.",
        EstadoDoServico.DEGRADADO,
        "Decisão represada atrasa o que depende dela.",
        Acao.ACOMPANHAR,
    ),
    FonteAlerta.RULE_DRIFT: (
        "Regra divergiu da especificação",
        "Uma regra passou a se comportar de forma diferente do que a spec declara.",
        EstadoDoServico.INCONCLUSIVO,
        "O que a regra afirma pode não corresponder ao que foi aprovado.",
        Acao.INVESTIGAR,
    ),
    FonteAlerta.ADDENDUM_REVIEW_REQUEST: (
        "Adendo aguardando aprovação",
        "Uma mudança de especificação está pendente de aprovação humana.",
        EstadoDoServico.NORMAL,
        "Nenhum agora. A mudança só vale depois de aprovada.",
        Acao.ACOMPANHAR,
    ),
}

#: Ultimo recurso. Nunca deixa a mensagem sem as cinco respostas.
_PADRAO = (
    "Evento de governança",
    "Um evento de governança foi registrado sem classificação automática.",
    EstadoDoServico.INCONCLUSIVO,
    "Não classificado automaticamente, ver detalhe técnico.",
    Acao.INVESTIGAR,
)

_EMOJI = {
    SeveridadeAlerta.CRITICAL: "🔴",
    SeveridadeAlerta.WARNING: "🟠",
    SeveridadeAlerta.INFO: "🔵",
}


#: Texto de SSH por POSTURA do servidor. A origem sozinha nao basta: as mesmas
#: tentativas significam coisas diferentes conforme o servidor aceite senha.
#:
#: ⚠️ Medido em 2026-09-06, num ataque real: o alerta dizia "Se a senha for
#: descoberta, o atacante ganha acesso ao servidor" num servidor com
#: `PasswordAuthentication no`. O impacto e a primeira frase que uma pessoa
#: nao-tecnica le -- e ela mandava investigar um vetor que nao existia.
_SSH_POR_POSTURA: dict[str, tuple[str, str, str, str, str]] = {
    "somente-chave": (
        "Varredura de acesso no SSH",
        "Alguém tentou entrar no servidor várias vezes e falhou.",
        EstadoDoServico.NORMAL,
        "Baixo: o servidor só aceita chave, então estas tentativas não podem"
        " suceder. É reconhecimento, ruído comum na internet.",
        Acao.ACOMPANHAR,
    ),
    "senha-habilitada": (
        "Possível ataque de força bruta no SSH",
        "Muitas tentativas de login falharam vindas do mesmo endereço.",
        EstadoDoServico.SOB_RISCO,
        "Se a senha for descoberta, o atacante ganha acesso ao servidor.",
        Acao.INVESTIGAR,
    ),
    "desconhecida": (
        "Tentativas de acesso no SSH, postura não confirmada",
        "Muitas tentativas de login falharam, e não foi possível medir se o servidor aceita senha.",
        EstadoDoServico.INCONCLUSIVO,
        "Indeterminado: sem saber a postura do servidor, não dá para dizer se"
        " estas tentativas podem suceder. Não presuma que está seguro.",
        Acao.INVESTIGAR,
    ),
}


#: O cartao de `observe:endpoint-down`, escolhido pelo `status=` DECLARADO —
#: `DIV-MENSAGEM-001`.
#:
#: ⚠️ **Existe porque a frase fixa contradizia a propria evidencia.** Medido em
#: 2026-09-16, no cartao entregue no Discord: titulo "Site fora do ar", resumo
#: *"O site nao respondeu nem pela internet nem por dentro do servidor"* -- e a
#: evidencia do MESMO cartao dizendo `status=403`. **403 e uma resposta.**
#:
#: E a distincao muda para onde se olha: sem resposta aponta rede e processo
#: caido; respondeu com erro aponta aplicacao, upstream e nginx. Uma frase so
#: para as duas manda o plantao para o lugar errado metade das vezes.
#:
#: ⚠️ A frase continua vindo do MAPA, escrita a mao -- a evidencia so ESCOLHE
#: qual entrada usar, que e o mesmo padrao de `_SSH_POR_POSTURA`. Tirar a frase
#: da evidencia e o que `resumo()` proibe, e por bons motivos medidos.
_ENDPOINT_DOWN_POR_RESPOSTA: dict[str, tuple[str, str, str, str, str]] = {
    "sem-resposta": (
        "Site fora do ar",
        "O site não respondeu nem pela internet nem por dentro do servidor.",
        EstadoDoServico.INDISPONIVEL,
        "O site não responde nem por dentro nem por fora.",
        Acao.ESCALAR,
    ),
    "respondeu-com-erro": (
        "Site respondendo com erro",
        "O site respondeu, mas com um código de erro, quem acessa recebe erro no lugar da página.",
        EstadoDoServico.INDISPONIVEL,
        "Quem abre o site recebe erro em vez do conteúdo. O servidor está de pé;"
        " o problema está na aplicação ou na configuração que responde por ela.",
        Acao.ESCALAR,
    ),
    #: ⚠️ O DEFAULT e o texto NEUTRO, de proposito. Se o `status=` mudar de
    #: grafia e o parsing falhar, a degradacao tem de cair no que NAO afirma
    #: demais -- afirmar "nao respondeu" por falha de leitura seria repetir o
    #: defeito que este mapa conserta.
    "nao-declarado": (
        "Site não respondeu como esperado",
        "O monitor não obteve resposta válida do site. O código exato não foi declarado.",
        EstadoDoServico.INDISPONIVEL,
        "Quem acessa pode não conseguir usar o site.",
        Acao.ESCALAR,
    ),
}


def _resposta_do_endpoint(alert: GovernanceAlert) -> str:
    """Houve resposta HTTP? Le o `status=` que `regra_endpoint_down` declara.

    `status=timeout` e a forma que a regra usa para "nao houve resposta"; um
    numero significa que o servidor respondeu, ainda que com erro.
    """
    for ev in alert.evidence:
        for linha in ev.evidencias:
            if not linha.startswith("status="):
                continue
            bruto = linha.split("=", 1)[1].strip()
            if bruto == "timeout":
                return "sem-resposta"
            return "respondeu-com-erro" if bruto.isdigit() else "nao-declarado"
    return "nao-declarado"


def _postura_declarada(alert: GovernanceAlert) -> str | None:
    """Le `postura_ssh=` da evidencia. `None` quando a regra nao a declarou."""
    for ev in alert.evidence:
        for linha in ev.evidencias:
            if linha.startswith("postura_ssh="):
                return linha.split("=", 1)[1].strip()
    return None


#: O cartao do resumo diario, DERIVADO do veredito do corpo — `DIV-ENVELOPE-001`.
#:
#: ⚠️ Ate 11/09 a entrada de `observe:resumo-diario` era fixa em NORMAL /
#: "Nenhuma ação necessária" / "Impacto: Nenhum", porque a mensagem foi
#: desenhada como PROVA DE VIDA. Mas ela tambem e o RELATORIO do dia, e o corpo
#: calcula um veredito de verdade. Medido em producao em 11/09 00:00 BRT: o
#: corpo dizia "2 incidente(s) aberto(s)" e "há incidente de segurança em curso"
#: enquanto o cartao dizia "Funcionando normalmente / Nenhuma ação necessária".
#:
#: ⚠️ Quem le para no cartao. Um relatorio que se contradiz nao confunde: ele
#: treina o leitor a nao ler o corpo, e ai o corpo pode dizer qualquer coisa.
_RESUMO_POR_VEREDITO: dict[str, tuple[str, str, str, str, str]] = {
    "limpo": (
        "Resumo diário de segurança",
        "O balanço do dia, com o que ficou aberto e o que foi encerrado.",
        EstadoDoServico.NORMAL,
        "Nenhum. É a prova de vida diária do monitor.",
        Acao.NENHUMA,
    ),
    "incidente": (
        "Resumo diário, há incidente em curso",
        "O balanço do dia, e ele NÃO fechou limpo: há incidente de segurança aberto.",
        EstadoDoServico.SOB_RISCO,
        "Há incidente aberto agora. O detalhe abaixo diz qual, e desde quando.",
        Acao.INVESTIGAR,
    ),
    "inconclusivo": (
        "Resumo diário, monitoramento inconclusivo",
        "O balanço do dia não pôde ser fechado: uma fonte essencial não foi lida.",
        EstadoDoServico.INCONCLUSIVO,
        "Ausência de dado NÃO é ausência de ataque: o Batman não sabe o que houve.",
        Acao.INVESTIGAR,
    ),
}


def _veredito_do_dia(alert: GovernanceAlert) -> str | None:
    """Le `veredito_do_dia=` da evidencia. Mesma convencao de `postura_ssh=`."""
    for ev in alert.evidence:
        for linha in ev.evidencias:
            if linha.startswith("veredito_do_dia="):
                return linha.split("=", 1)[1].strip()
    return None


def _texto_base(alert: GovernanceAlert) -> tuple[str, str, str, str, str]:
    for ev in alert.evidence:
        if ev.origem == "observe:resumo-diario":
            # ⚠️ Veredito ausente ou desconhecido cai no cartao antigo, e nao em
            # erro: uma versao antiga do emissor continua produzindo mensagem
            # valida. Degradacao, nunca excecao no caminho de entrega.
            veredito = _veredito_do_dia(alert)
            if veredito in _RESUMO_POR_VEREDITO:
                return _RESUMO_POR_VEREDITO[veredito]
        if ev.origem == "observe:ssh-bruteforce":
            postura = _postura_declarada(alert) or "desconhecida"
            return _SSH_POR_POSTURA.get(postura, _SSH_POR_POSTURA["desconhecida"])
        if ev.origem == "observe:endpoint-down":
            # `DIV-MENSAGEM-001`: "nao respondeu" so quando nao houve resposta.
            return _ENDPOINT_DOWN_POR_RESPOSTA[_resposta_do_endpoint(alert)]
        if ev.origem in _POR_ORIGEM:
            return _POR_ORIGEM[ev.origem]
    return _POR_FONTE.get(alert.source, _PADRAO)


def titulo(alert: GovernanceAlert) -> str:
    """Titulo humano. O identificador interno some daqui e vive no detalhe.

    Recuperacao ganha marca propria: um `FEATURE_RECOVERED` com o emoji de
    severidade do incidente confundiria fim de problema com problema novo.
    """
    if alert.source is FonteAlerta.FEATURE_RECOVERED:
        return f"🟢 {_texto_base(alert)[0]}"
    return f"{_EMOJI.get(alert.severity, '🔵')} {_texto_base(alert)[0]}"


def estado_do_servico(alert: GovernanceAlert) -> str:
    return _texto_base(alert)[2]


def impacto(alert: GovernanceAlert) -> str:
    return _texto_base(alert)[3]


def acao_recomendada(alert: GovernanceAlert) -> str:
    return _texto_base(alert)[4]


def resumo(alert: GovernanceAlert) -> str:
    """Uma frase sem jargão explicando o ocorrido.

    ⚠️ Vem do MAPA, nunca da evidência. Duas versões anteriores tentaram tirar a
    frase da evidência e as duas escolheram errado: primeiro exibiu
    `metric=observe.cpu:crit`, depois — num alerta de ataque SSH — exibiu
    *"parte das falhas não teve IP extraído"*, verdade técnica irrelevante,
    escolhida por acaso de pontuação.

    A evidência serve ao detalhe técnico. O resumo é escrito para quem não vai
    ler o detalhe, e por isso é redigido à mão, por tipo de evento.
    """
    return _texto_base(alert)[1]


def medicoes(alert: GovernanceAlert) -> list[str]:
    """Valores medidos — de `historico`, o campo que o template antigo ignorava.

    ⚠️ E aqui que o "limiar sem valor" se conserta. O prefixo `medicao:` sai do
    texto: ele existe para o regex de volatilidade do sink, nao para quem le.
    """
    saida: list[str] = []
    for ev in alert.evidence:
        for linha in ev.historico:
            limpa = linha.strip()
            for prefixo in ("medicao:", "medição:"):
                if limpa.lower().startswith(prefixo):
                    limpa = limpa[len(prefixo) :].strip()
                    break
            if limpa:
                saida.append(limpa)
    return saida


def identificacao(alert: GovernanceAlert, ambiente: str = "produção") -> str:
    """alert_id, componente, ambiente e horario nos dois fusos.

    Sem link: a pagina autenticada de detalhe e entrega propria (Fase 5), e o
    DEV pediu explicitamente para nao inventar rota provisoria. O `alert_id`
    sozinho ja permite achar o evento no journal.
    """
    origem = alert.evidence[0].origem if alert.evidence else "desconhecido"
    componente = origem.split(":")[0]
    quando = alert.created_at if isinstance(alert.created_at, datetime) else datetime.now(UTC)
    utc = quando.astimezone(UTC)
    brt = utc + _BRT
    protocolo = next(
        (
            ev.assunto.removeprefix("incidente:")
            for ev in alert.evidence
            if ev.assunto.startswith("incidente:")
        ),
        "",
    )
    marca = f"`{protocolo}` · " if protocolo else ""
    return (
        f"{marca}`{alert.id}` · {componente} · {ambiente} · {utc:%d/%m %H:%M} UTC ({brt:%H:%M} BRT)"
    )
