"""Descoberta de arquivos no repositório alvo — único ponto de IO real desta
migração (Vol.IV Cap.18: nenhuma Skill/Tool de filesystem foi modelada ainda,
então este módulo lê do disco diretamente em vez de inventar uma abstração
prematura).

Interpreta a seção `descoberta` dos specs em `capabilities/rules/specs/
lote_01/*.json` — 7 tipos: `arquivo_fixo` (um caminho único), `arvore`
(busca recursiva por extensão sob `scope_dirs`), `glob` (padrões glob
relativos à raiz, ou um único padrão recursivo com exclusões), `git` (roda
um comando `git` fixo uma vez, replicando `RepoContext.git()` do legado),
`subprocess` (roda um módulo Python instalado no venv do repo alvo —
pytest/ruff — com cache por comando+root dentro da mesma execução de
`executar_scan`, replicando/corrigindo `_find_python()` + `_ruff_cache`/
ausência de cache de `oracle.py`/`robin.py`), `toml_dependencias` (empacota
`pyproject.toml` + `requirements.txt` + arquivos `.py` de `tests/`/`src/`
como JSON — parsing real via `tomllib` acontece no handler, não aqui),
`de003` (bespoke — empacota `init_db.py`/`tables.py` + `git log -p` sobre
`tables.py` como JSON para a Capability DE-003), `ora004` (bespoke —
empacota todos os `.py` de `scope_dirs` + `enums_path` como JSON para a
Capability ORA-004, que agrega frequência de literais através de TODOS os
arquivos).

Handler da Capability (`capabilities/rules/regex_sobre_conteudo.py`, e as
demais Skills desta migração) permanece puro — este módulo é o único que
toca disco/processo.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from batman_os.capabilities.registry_sdk import registrar
from batman_os.capabilities.rules.a11y002_loader import carregar_especificacoes_a11y002
from batman_os.capabilities.rules.a11y002_onclick_sem_teclado import (
    EntradaA11y002,
    RegraA11y002Spec,
)
from batman_os.capabilities.rules.a11y002_onclick_sem_teclado import (
    construir_implementacao as construir_implementacao_a11y002,
)
from batman_os.capabilities.rules.a11y003_input_sem_label import EntradaA11y003, RegraA11y003Spec
from batman_os.capabilities.rules.a11y003_input_sem_label import (
    construir_implementacao as construir_implementacao_a11y003,
)
from batman_os.capabilities.rules.a11y003_loader import carregar_especificacoes_a11y003
from batman_os.capabilities.rules.arch003_loader import carregar_especificacoes_arch003
from batman_os.capabilities.rules.arch003_pagina_orfa import EntradaArch003, RegraArch003Spec
from batman_os.capabilities.rules.arch003_pagina_orfa import (
    construir_implementacao as construir_implementacao_arch003,
)
from batman_os.capabilities.rules.ast_kwarg_ausente import (
    EntradaKwargAusente,
    RegraKwargAusenteSpec,
)
from batman_os.capabilities.rules.ast_kwarg_ausente import (
    construir_implementacao as construir_implementacao_kwarg_ausente,
)
from batman_os.capabilities.rules.ast_kwarg_ausente_loader import (
    carregar_especificacoes_kwarg_ausente,
)
from batman_os.capabilities.rules.ast_padrao_ausente import EntradaAst, RegraAstSpec
from batman_os.capabilities.rules.ast_padrao_ausente import (
    construir_implementacao as construir_implementacao_ast,
)
from batman_os.capabilities.rules.ast_padrao_ausente_loader import carregar_especificacoes_ast
from batman_os.capabilities.rules.ba004_loader import carregar_especificacoes_ba004
from batman_os.capabilities.rules.ba004_logica_negocio_router import EntradaBa004, RegraBa004Spec
from batman_os.capabilities.rules.ba004_logica_negocio_router import (
    construir_implementacao as construir_implementacao_ba004,
)
from batman_os.capabilities.rules.ba005_divisao_sem_guarda import EntradaBa005, RegraBa005Spec
from batman_os.capabilities.rules.ba005_divisao_sem_guarda import (
    construir_implementacao as construir_implementacao_ba005,
)
from batman_os.capabilities.rules.ba005_loader import carregar_especificacoes_ba005
from batman_os.capabilities.rules.be010_dependencia_nao_declarada import (
    EntradaBe010,
    RegraBe010Spec,
)
from batman_os.capabilities.rules.be010_dependencia_nao_declarada import (
    construir_implementacao as construir_implementacao_be010,
)
from batman_os.capabilities.rules.be010_loader import carregar_especificacoes_be010
from batman_os.capabilities.rules.be013_http200_em_except import EntradaBe013, RegraBe013Spec
from batman_os.capabilities.rules.be013_http200_em_except import (
    construir_implementacao as construir_implementacao_be013,
)
from batman_os.capabilities.rules.be013_loader import carregar_especificacoes_be013
from batman_os.capabilities.rules.comp008_loader import carregar_especificacoes_comp008
from batman_os.capabilities.rules.comp008_relatorio_impacto_sem_disclaimer import (
    EntradaComp008,
    RegraComp008Spec,
)
from batman_os.capabilities.rules.comp008_relatorio_impacto_sem_disclaimer import (
    construir_implementacao as construir_implementacao_comp008,
)
from batman_os.capabilities.rules.cs003_except_pass import EntradaCs003, RegraCs003Spec
from batman_os.capabilities.rules.cs003_except_pass import (
    construir_implementacao as construir_implementacao_cs003,
)
from batman_os.capabilities.rules.cs003_loader import carregar_especificacoes_cs003
from batman_os.capabilities.rules.cs005_erro_sem_request_id import EntradaCs005, RegraCs005Spec
from batman_os.capabilities.rules.cs005_erro_sem_request_id import (
    construir_implementacao as construir_implementacao_cs005,
)
from batman_os.capabilities.rules.cs005_loader import carregar_especificacoes_cs005
from batman_os.capabilities.rules.cto002_loader import carregar_especificacoes_cto002
from batman_os.capabilities.rules.cto002_rota_sem_versao import EntradaCto002, RegraCto002Spec
from batman_os.capabilities.rules.cto002_rota_sem_versao import (
    construir_implementacao as construir_implementacao_cto002,
)
from batman_os.capabilities.rules.cto004_endpoint_sem_doc import EntradaCto004, RegraCto004Spec
from batman_os.capabilities.rules.cto004_endpoint_sem_doc import (
    construir_implementacao as construir_implementacao_cto004,
)
from batman_os.capabilities.rules.cto004_loader import carregar_especificacoes_cto004
from batman_os.capabilities.rules.de003_coluna_sem_migration import EntradaDe003, RegraDe003Spec
from batman_os.capabilities.rules.de003_coluna_sem_migration import (
    construir_implementacao as construir_implementacao_de003,
)
from batman_os.capabilities.rules.de003_loader import carregar_especificacoes_de003
from batman_os.capabilities.rules.doc004_changelog_sem_versao import EntradaDoc004, RegraDoc004Spec
from batman_os.capabilities.rules.doc004_changelog_sem_versao import (
    construir_implementacao as construir_implementacao_doc004,
)
from batman_os.capabilities.rules.doc004_loader import carregar_especificacoes_doc004
from batman_os.capabilities.rules.execucao_comando_interpretada import (
    EntradaExecucaoComando,
    RegraExecucaoComandoSpec,
)
from batman_os.capabilities.rules.execucao_comando_interpretada import (
    construir_implementacao as construir_implementacao_execucao_comando,
)
from batman_os.capabilities.rules.execucao_comando_interpretada_loader import (
    carregar_especificacoes_execucao_comando,
)
from batman_os.capabilities.rules.fe001_export_duplicado import EntradaFe001, RegraFe001Spec
from batman_os.capabilities.rules.fe001_export_duplicado import (
    construir_implementacao as construir_implementacao_fe001,
)
from batman_os.capabilities.rules.fe001_loader import carregar_especificacoes_fe001
from batman_os.capabilities.rules.fe002_loader import carregar_especificacoes_fe002
from batman_os.capabilities.rules.fe002_tofixed_sem_null_safety import (
    EntradaFe002,
    RegraFe002Spec,
)
from batman_os.capabilities.rules.fe002_tofixed_sem_null_safety import (
    construir_implementacao as construir_implementacao_fe002,
)
from batman_os.capabilities.rules.fe007_loader import carregar_especificacoes_fe007
from batman_os.capabilities.rules.fe007_nav_lock import EntradaFe007, RegraFe007Spec
from batman_os.capabilities.rules.fe007_nav_lock import (
    construir_implementacao as construir_implementacao_fe007,
)
from batman_os.capabilities.rules.feapi_loader import carregar_especificacoes_feapi
from batman_os.capabilities.rules.feapi_rota_sem_frontend import EntradaFeApi, RegraFeApiSpec
from batman_os.capabilities.rules.feapi_rota_sem_frontend import (
    construir_implementacao as construir_implementacao_feapi,
)
from batman_os.capabilities.rules.fin005_backtest_sem_oos import EntradaFin005, RegraFin005Spec
from batman_os.capabilities.rules.fin005_backtest_sem_oos import (
    construir_implementacao as construir_implementacao_fin005,
)
from batman_os.capabilities.rules.fin005_loader import carregar_especificacoes_fin005
from batman_os.capabilities.rules.fin006_loader import carregar_especificacoes_fin006
from batman_os.capabilities.rules.fin006_significancia_sem_cluster import (
    EntradaFin006,
    RegraFin006Spec,
)
from batman_os.capabilities.rules.fin006_significancia_sem_cluster import (
    construir_implementacao as construir_implementacao_fin006,
)
from batman_os.capabilities.rules.git_comando_interpretado import (
    EntradaGitInterpretado,
    RegraComparacaoNumericaSpec,
)
from batman_os.capabilities.rules.git_comando_interpretado import (
    construir_implementacao as construir_implementacao_git_interpretado,
)
from batman_os.capabilities.rules.git_comando_interpretado_loader import (
    carregar_especificacoes_git_interpretado,
)
from batman_os.capabilities.rules.govdebt001_finding_sem_decisao import (
    EntradaGovdebt001,
    RegraGovdebt001Spec,
)
from batman_os.capabilities.rules.govdebt001_finding_sem_decisao import (
    construir_implementacao as construir_implementacao_govdebt001,
)
from batman_os.capabilities.rules.govdebt001_loader import carregar_especificacoes_govdebt001
from batman_os.capabilities.rules.janela_contexto_regex import EntradaJanela, RegraJanelaSpec
from batman_os.capabilities.rules.janela_contexto_regex import (
    construir_implementacao as construir_implementacao_janela,
)
from batman_os.capabilities.rules.janela_contexto_regex_loader import carregar_especificacoes_janela
from batman_os.capabilities.rules.lote_01 import carregar_lote_01
from batman_os.capabilities.rules.lote_02 import carregar_lote_02
from batman_os.capabilities.rules.lote_03 import carregar_lote_03
from batman_os.capabilities.rules.metrica_com_limiar import EntradaMetrica, RegraMetricaSpec
from batman_os.capabilities.rules.metrica_com_limiar import (
    construir_implementacao as construir_implementacao_metrica,
)
from batman_os.capabilities.rules.metrica_com_limiar_loader import carregar_especificacoes_metrica
from batman_os.capabilities.rules.ora004_loader import carregar_especificacoes_ora004
from batman_os.capabilities.rules.ora004_status_typo import EntradaOra004, RegraOra004Spec
from batman_os.capabilities.rules.ora004_status_typo import (
    construir_implementacao as construir_implementacao_ora004,
)
from batman_os.capabilities.rules.ora005_fallback_silencioso import EntradaOra005, RegraOra005Spec
from batman_os.capabilities.rules.ora005_fallback_silencioso import (
    construir_implementacao as construir_implementacao_ora005,
)
from batman_os.capabilities.rules.ora005_loader import carregar_especificacoes_ora005
from batman_os.capabilities.rules.ora006_loader import carregar_especificacoes_ora006
from batman_os.capabilities.rules.ora006_proxy_medicao_silenciosa import (
    EntradaOra006,
    RegraOra006Spec,
)
from batman_os.capabilities.rules.ora006_proxy_medicao_silenciosa import (
    construir_implementacao as construir_implementacao_ora006,
)
from batman_os.capabilities.rules.pd001_empty_state_sem_cta import EntradaPd001, RegraPd001Spec
from batman_os.capabilities.rules.pd001_empty_state_sem_cta import (
    construir_implementacao as construir_implementacao_pd001,
)
from batman_os.capabilities.rules.pd001_loader import carregar_especificacoes_pd001
from batman_os.capabilities.rules.pd009_loader import carregar_especificacoes_pd009
from batman_os.capabilities.rules.pd009_rota_nao_descobrivel import EntradaPd009, RegraPd009Spec
from batman_os.capabilities.rules.pd009_rota_nao_descobrivel import (
    construir_implementacao as construir_implementacao_pd009,
)
from batman_os.capabilities.rules.pd010_loader import carregar_especificacoes_pd010
from batman_os.capabilities.rules.pd010_simulador_sem_piso import EntradaPd010, RegraPd010Spec
from batman_os.capabilities.rules.pd010_simulador_sem_piso import (
    construir_implementacao as construir_implementacao_pd010,
)
from batman_os.capabilities.rules.pd011_diversificacao_nao_comunicada import (
    EntradaPd011,
    RegraPd011Spec,
)
from batman_os.capabilities.rules.pd011_diversificacao_nao_comunicada import (
    construir_implementacao as construir_implementacao_pd011,
)
from batman_os.capabilities.rules.pd011_loader import carregar_especificacoes_pd011
from batman_os.capabilities.rules.perf004_arquivo_sem_streaming import (
    EntradaPerf004,
    RegraPerf004Spec,
)
from batman_os.capabilities.rules.perf004_arquivo_sem_streaming import (
    construir_implementacao as construir_implementacao_perf004,
)
from batman_os.capabilities.rules.perf004_loader import carregar_especificacoes_perf004
from batman_os.capabilities.rules.qaauto001_loader import carregar_especificacoes_qaauto001
from batman_os.capabilities.rules.qaauto001_router_sem_teste import (
    EntradaQaAuto001,
    RegraQaAuto001Spec,
)
from batman_os.capabilities.rules.qaauto001_router_sem_teste import (
    construir_implementacao as construir_implementacao_qaauto001,
)
from batman_os.capabilities.rules.qaauto003_loader import carregar_especificacoes_qaauto003
from batman_os.capabilities.rules.qaauto003_smoke_specs_ausentes import (
    EntradaQaAuto003,
    RegraQaAuto003Spec,
)
from batman_os.capabilities.rules.qaauto003_smoke_specs_ausentes import (
    construir_implementacao as construir_implementacao_qaauto003,
)
from batman_os.capabilities.rules.qavis001_loader import carregar_especificacoes_qavis001
from batman_os.capabilities.rules.qavis001_playwright_falhou import (
    EntradaQaVis001,
    RegraQaVis001Spec,
)
from batman_os.capabilities.rules.qavis001_playwright_falhou import (
    construir_implementacao as construir_implementacao_qavis001,
)
from batman_os.capabilities.rules.regex_agregado_multi_arquivo import (
    EntradaAgregada,
    RegraAgregadaSpec,
)
from batman_os.capabilities.rules.regex_agregado_multi_arquivo import (
    construir_implementacao as construir_implementacao_agregadas,
)
from batman_os.capabilities.rules.regex_agregado_multi_arquivo_loader import (
    carregar_especificacoes_agregadas,
)
from batman_os.capabilities.rules.regex_sobre_conteudo import (
    CondicaoAdicional,
    EntradaRegexArquivo,
    ModoAvaliacao,
    RegraSpec,
)
from batman_os.capabilities.rules.regex_sobre_conteudo import (
    construir_implementacao as construir_implementacao_regex,
)
from batman_os.capabilities.rules.rev005_bloco_duplicado import EntradaRev005, RegraRev005Spec
from batman_os.capabilities.rules.rev005_bloco_duplicado import (
    construir_implementacao as construir_implementacao_rev005,
)
from batman_os.capabilities.rules.rev005_loader import carregar_especificacoes_rev005
from batman_os.capabilities.rules.rev006_loader import carregar_especificacoes_rev006
from batman_os.capabilities.rules.rev006_variavel_nome_curto import EntradaRev006, RegraRev006Spec
from batman_os.capabilities.rules.rev006_variavel_nome_curto import (
    construir_implementacao as construir_implementacao_rev006,
)
from batman_os.capabilities.rules.sec005_fstring_sql import EntradaSec005, RegraSec005Spec
from batman_os.capabilities.rules.sec005_fstring_sql import (
    construir_implementacao as construir_implementacao_sec005,
)
from batman_os.capabilities.rules.sec005_loader import carregar_especificacoes_sec005
from batman_os.capabilities.rules.sec007_ddl_no_import import EntradaSec007, RegraSec007Spec
from batman_os.capabilities.rules.sec007_ddl_no_import import (
    construir_implementacao as construir_implementacao_sec007,
)
from batman_os.capabilities.rules.sec007_loader import carregar_especificacoes_sec007
from batman_os.capabilities.rules.sec008_loader import carregar_especificacoes_sec008
from batman_os.capabilities.rules.sec008_role_sem_super_admin import EntradaSec008, RegraSec008Spec
from batman_os.capabilities.rules.sec008_role_sem_super_admin import (
    construir_implementacao as construir_implementacao_sec008,
)
from batman_os.capabilities.rules.sec009_admin_role_script import EntradaSec009, RegraSec009Spec
from batman_os.capabilities.rules.sec009_admin_role_script import (
    construir_implementacao as construir_implementacao_sec009,
)
from batman_os.capabilities.rules.sec009_loader import carregar_especificacoes_sec009
from batman_os.capabilities.rules.sre006_loader import carregar_especificacoes_sre006
from batman_os.capabilities.rules.sre006_timeout_ausente import EntradaSre006, RegraSre006Spec
from batman_os.capabilities.rules.sre006_timeout_ausente import (
    construir_implementacao as construir_implementacao_sre006,
)
from batman_os.capabilities.rules.sup001_excecao_silenciada import (
    EntradaSup001,
    RegraSup001Spec,
)
from batman_os.capabilities.rules.sup001_excecao_silenciada import (
    construir_implementacao as construir_implementacao_sup001,
)
from batman_os.capabilities.rules.sup001_loader import carregar_especificacoes_sup001
from batman_os.capabilities.rules.sweep001_cadencia_quebrada import (
    EntradaSweep001,
    RegraSweep001Spec,
)
from batman_os.capabilities.rules.sweep001_cadencia_quebrada import (
    construir_implementacao as construir_implementacao_sweep001,
)
from batman_os.capabilities.rules.sweep001_loader import carregar_especificacoes_sweep001
from batman_os.capabilities.rules.toml_dependencias import (
    EntradaDependencias,
    RegraDependenciasSpec,
)
from batman_os.capabilities.rules.toml_dependencias import (
    construir_implementacao as construir_implementacao_dependencias,
)
from batman_os.capabilities.rules.toml_dependencias_loader import (
    carregar_especificacoes_dependencias,
)
from batman_os.capabilities.rules.ui002_inline_style_estatico import EntradaUi002, RegraUi002Spec
from batman_os.capabilities.rules.ui002_inline_style_estatico import (
    construir_implementacao as construir_implementacao_ui002,
)
from batman_os.capabilities.rules.ui002_loader import carregar_especificacoes_ui002

_cache_subprocess: dict[tuple[str, ...], tuple[int, str, str]] = {}


# Nomes de diretório podados por padrão em TODA travessia recursiva deste
# módulo — poda a DESCIDA (o diretório nunca é listado), não filtra depois.
# Existe porque `batman scan --root <repo>` varria a árvore INTEIRA: medido
# contra o radar-preditivo real (S161, 2026-07-29) — 90.943 arquivos
# enumerados quando só 1.133 são rastreados pelo git, a maior parte vindo
# de `.venv`/`node_modules`/`data/*.parquet` sob `__pycache__`/`dist`/caches
# de lint e tipo. `Path.rglob()`/`Path.glob()` não têm como podar (sempre
# descem em tudo e só filtram depois de já ter enumerado) — por isso toda
# travessia recursiva aqui passa por `_caminhar_arquivos_podado()` em vez de
# `rglob` direto. Cada entrada é um nome literal (".venv") ou um padrão glob
# simples por segmento ("*.egg-info", via `fnmatch`) — ver `_dir_excluida`.
# CLI (`batman.py::_montar_parser`): `--exclude NOME` soma a esta lista
# (repetível); `--no-default-excludes` é o escape hatch que a zera.
DEFAULT_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        "*.egg-info",
        ".batman-os",
        "unsloth_compiled_cache",
        ".worktrees",
        # Incidente 2026-07-29: data/ do radar-preditivo (parquets, SQLite,
        # .jsonl de dezenas de MB) não é código — varrê-lo custava horas.
        # Regras `arquivo_fixo` que apontam para data/<arquivo> continuam
        # funcionando (leem por caminho direto, não pela travessia).
        "data",
    }
)

_excluir_dirs_atual: frozenset[str] = DEFAULT_EXCLUDED_DIRS


def definir_exclusao_de_diretorios(excluir_dirs: frozenset[str]) -> None:
    """Define o conjunto de nomes de diretório podado por TODAS as
    travessias recursivas deste módulo até a próxima chamada.

    É um setter global — não um parâmetro repassado por toda a cadeia de
    chamada — porque a cadeia passa por ~50 wrappers `entradas_<regra>_
    para_regra` (um por Capability migrada); mudar a assinatura de todos
    não paga o ganho. Seguro sob `executar_scan(paralelo=True)`: é escrito
    uma única vez ANTES do `ThreadPoolExecutor` processar qualquer Missão
    e nunca mutado durante o scan (mesmo raciocínio de `_cache_subprocess`
    acima — leituras concorrentes, escrita serializada). Quem chama:
    `scan_command.py::executar_scan`, no início de cada execução."""
    global _excluir_dirs_atual
    _excluir_dirs_atual = excluir_dirs


def exclusao_de_diretorios_atual() -> frozenset[str]:
    """O conjunto de nomes de diretório podado neste momento — default
    `DEFAULT_EXCLUDED_DIRS` (chamadas diretas de teste a `arquivos_para_
    regra`/`entradas_para_regra`, sem passar por `executar_scan`, herdam a
    poda padrão em vez de variar entre "cru" e "podado" por acidente)."""
    return _excluir_dirs_atual


@contextmanager
def escopo_de_exclusao_de_diretorios(excluir_dirs: frozenset[str]) -> Iterator[None]:
    """Aplica `excluir_dirs` só durante o bloco `with` e restaura o valor
    anterior ao sair (mesmo em exceção) — usado por `executar_scan` para
    nunca vazar a configuração de uma chamada para a próxima, e por testes
    que precisam de um conjunto de exclusão customizado sem afetar os
    demais testes do módulo."""
    anterior = _excluir_dirs_atual
    definir_exclusao_de_diretorios(excluir_dirs)
    try:
        yield
    finally:
        definir_exclusao_de_diretorios(anterior)


def _dir_excluida(nome: str, padroes: frozenset[str]) -> bool:
    """`nome` bate contra `padroes` por igualdade literal (".venv") ou por
    padrão glob simples de um segmento ("*.egg-info", via `fnmatch`)."""
    return any(fnmatch.fnmatch(nome, padrao) for padrao in padroes)


def _caminhar_arquivos_podado(base: Path) -> Iterator[Path]:
    """Enumera todo arquivo sob `base`, recursivamente, podando a DESCIDA
    em qualquer diretório cujo nome bata `exclusao_de_diretorios_atual()`
    — nunca lista o conteúdo de `.venv`/`node_modules`/etc, em vez de
    enumerar tudo e filtrar depois (a diferença de performance que motivou
    `DEFAULT_EXCLUDED_DIRS`: `Path.rglob()` sempre desce em tudo). Usa
    `os.walk` porque é a única API da stdlib que permite podar `dirnames`
    in-place durante a própria travessia — `pathlib` não oferece isso até
    o `Path.walk()` do 3.12 (este projeto mira 3.11)."""
    excluir_dirs = exclusao_de_diretorios_atual()
    if not base.exists():
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not _dir_excluida(d, excluir_dirs)]
        dirpath_path = Path(dirpath)
        for nome_arquivo in filenames:
            yield dirpath_path / nome_arquivo


@dataclass(frozen=True)
class EstatisticasDeDescoberta:
    """Visibilidade de UMA varredura completa de `root` — não o que cada
    Capability efetivamente lê (isso depende do `scope_dirs` de cada
    regra), mas o tamanho real da árvore antes/depois da poda, para expor
    regressão (ex.: um `scope_dirs` novo apontando pra raiz sem querer, ou
    uma exclusão nova quebrada). Logado por `scan_command.py::
    executar_scan` no início de cada execução — silêncio aqui esconde
    regressão de performance."""

    arquivos_enumerados: int
    diretorios_podados: int


def calcular_estatisticas_de_descoberta(
    root: Path, excluir_dirs: frozenset[str] | None = None
) -> EstatisticasDeDescoberta:
    """Uma varredura única e independente de `root` (não usa `scope_dirs`
    de regra nenhuma) só para visibilidade — ver `EstatisticasDeDescoberta`."""
    excludes = excluir_dirs if excluir_dirs is not None else exclusao_de_diretorios_atual()
    arquivos = 0
    podados = 0
    if not root.exists():
        return EstatisticasDeDescoberta(arquivos_enumerados=0, diretorios_podados=0)
    for _dirpath, dirnames, filenames in os.walk(root):
        mantidos = []
        for nome in dirnames:
            if _dir_excluida(nome, excludes):
                podados += 1
            else:
                mantidos.append(nome)
        dirnames[:] = mantidos
        arquivos += len(filenames)
    return EstatisticasDeDescoberta(arquivos_enumerados=arquivos, diretorios_podados=podados)


class TipoDescobertaDesconhecido(Exception):
    """Levantada quando `descoberta["tipo"]` (ou o `tipo` de uma condição
    adicional) não é um dos reconhecidos por este módulo."""


def entradas_cto004_para_regra(
    root: Path, regra: RegraCto004Spec, descoberta: dict[str, Any]
) -> list[EntradaCto004]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke CTO-004 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaCto004(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_cto002_para_regra(
    root: Path, regra: RegraCto002Spec, descoberta: dict[str, Any]
) -> list[EntradaCto002]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke CTO-002 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaCto002(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_cs005_para_regra(
    root: Path, regra: RegraCs005Spec, descoberta: dict[str, Any]
) -> list[EntradaCs005]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke CS-005."""
    return [
        EntradaCs005(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_cs003_para_regra(
    root: Path, regra: RegraCs003Spec, descoberta: dict[str, Any]
) -> list[EntradaCs003]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke CS-003 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaCs003(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_fe007_para_regra(
    root: Path, regra: RegraFe007Spec, descoberta: dict[str, Any]
) -> list[EntradaFe007]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke FE-007 — reaproveita a descoberta genérica `"arquivo_fixo"`."""
    return [
        EntradaFe007(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_rev005_para_regra(
    root: Path, regra: RegraRev005Spec, descoberta: dict[str, Any]
) -> list[EntradaRev005]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke REV-005 — reaproveita a descoberta bespoke `"rev005"`."""
    return [
        EntradaRev005(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_fe002_para_regra(
    root: Path, regra: RegraFe002Spec, descoberta: dict[str, Any]
) -> list[EntradaFe002]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke FE-002 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaFe002(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_ui002_para_regra(
    root: Path, regra: RegraUi002Spec, descoberta: dict[str, Any]
) -> list[EntradaUi002]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke UI-002 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaUi002(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_a11y002_para_regra(
    root: Path, regra: RegraA11y002Spec, descoberta: dict[str, Any]
) -> list[EntradaA11y002]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke A11Y-002 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaA11y002(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_a11y003_para_regra(
    root: Path, regra: RegraA11y003Spec, descoberta: dict[str, Any]
) -> list[EntradaA11y003]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke A11Y-003 — reaproveita a descoberta genérica `"arvore"` já
    existente (nenhuma função de descoberta nova precisa)."""
    return [
        EntradaA11y003(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_govdebt001_para_regra(
    root: Path, regra: RegraGovdebt001Spec, descoberta: dict[str, Any]
) -> list[EntradaGovdebt001]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke GOVDEBT-001."""
    return [
        EntradaGovdebt001(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_sweep001_para_regra(
    root: Path, regra: RegraSweep001Spec, descoberta: dict[str, Any]
) -> list[EntradaSweep001]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke SWEEP-001 — reaproveita a descoberta genérica
    `"arquivo_fixo"`."""
    return [
        EntradaSweep001(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_qavis001_para_regra(
    root: Path, regra: RegraQaVis001Spec, descoberta: dict[str, Any]
) -> list[EntradaQaVis001]:
    """Mesmo espírito de `entradas_sweep001_para_regra`, para a Capability
    bespoke QAVIS-001 — reaproveita a descoberta bespoke `"playwright"`."""
    return [
        EntradaQaVis001(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_para_regra(
    root: Path, regra: RegraSpec, descoberta: dict[str, Any]
) -> list[EntradaRegexArquivo]:
    """Monta as `EntradaRegexArquivo` prontas para invocar a Capability —
    uma por arquivo relevante encontrado, todas compartilhando as mesmas
    `condicoes_adicionais` (resolvidas uma única vez)."""
    condicoes = _condicoes_adicionais_para(root, descoberta)
    return [
        EntradaRegexArquivo(
            caminho=caminho, conteudo=conteudo, condicoes_adicionais=condicoes, regra=regra
        )
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_ast_para_regra(
    root: Path, regra: RegraAstSpec, descoberta: dict[str, Any]
) -> list[EntradaAst]:
    """Mesmo espírito de `entradas_para_regra`, para a Skill AST — sem
    `condicoes_adicionais` (nenhuma regra migrada até agora precisa de
    checagem cruzada entre arquivos nesta Skill)."""
    return [
        EntradaAst(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_kwarg_ausente_para_regra(
    root: Path, regra: RegraKwargAusenteSpec, descoberta: dict[str, Any]
) -> list[EntradaKwargAusente]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Skill "Call com
    kwarg obrigatório ausente"."""
    return [
        EntradaKwargAusente(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_git_interpretado_para_regra(
    root: Path, regra: RegraComparacaoNumericaSpec, descoberta: dict[str, Any]
) -> list[EntradaGitInterpretado]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Skill "comando
    git único interpretado"."""
    return [
        EntradaGitInterpretado(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_execucao_comando_para_regra(
    root: Path, regra: RegraExecucaoComandoSpec, descoberta: dict[str, Any]
) -> list[EntradaExecucaoComando]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Skill "executar
    comando externo, timeout, venv-aware"."""
    return [
        EntradaExecucaoComando(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_dependencias_para_regra(
    root: Path, regra: RegraDependenciasSpec, descoberta: dict[str, Any]
) -> list[EntradaDependencias]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Skill "parsing
    TOML real de pyproject.toml"."""
    return [
        EntradaDependencias(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_janela_para_regra(
    root: Path, regra: RegraJanelaSpec, descoberta: dict[str, Any]
) -> list[EntradaJanela]:
    """Mesmo espírito de `entradas_ast_para_regra` — reaproveita a
    descoberta `"arvore"`/`"glob"`/`"arquivo_fixo"` já existente (1 Missão
    por arquivo), só o handler ("janela de contexto por ocorrência") é
    novo."""
    return [
        EntradaJanela(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_metrica_para_regra(
    root: Path, regra: RegraMetricaSpec, descoberta: dict[str, Any]
) -> list[EntradaMetrica]:
    """Mesmo espírito de `entradas_ast_para_regra` — reaproveita a
    descoberta `"arvore"`/`"glob"`/`"arquivo_fixo"` já existente (1 Missão
    por arquivo), só o handler ("métrica com limiar") é novo."""
    return [
        EntradaMetrica(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_sec005_para_regra(
    root: Path, regra: RegraSec005Spec, descoberta: dict[str, Any]
) -> list[EntradaSec005]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke SEC-005."""
    return [
        EntradaSec005(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_fe001_para_regra(
    root: Path, regra: RegraFe001Spec, descoberta: dict[str, Any]
) -> list[EntradaFe001]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke FE-001 — reaproveita a descoberta `"regex_agregado"` já
    existente (empacota TODOS os arquivos como JSON numa única Missão),
    só o handler (agregação com agrupamento por nome exportado) é novo."""
    return [
        EntradaFe001(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_rev006_para_regra(
    root: Path, regra: RegraRev006Spec, descoberta: dict[str, Any]
) -> list[EntradaRev006]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke REV-006 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaRev006(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_sec008_para_regra(
    root: Path, regra: RegraSec008Spec, descoberta: dict[str, Any]
) -> list[EntradaSec008]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke SEC-008 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaSec008(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_sre006_para_regra(
    root: Path, regra: RegraSre006Spec, descoberta: dict[str, Any]
) -> list[EntradaSre006]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke SRE-006."""
    return [
        EntradaSre006(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_sec009_para_regra(
    root: Path, regra: RegraSec009Spec, descoberta: dict[str, Any]
) -> list[EntradaSec009]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke SEC-009 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaSec009(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_pd001_para_regra(
    root: Path, regra: RegraPd001Spec, descoberta: dict[str, Any]
) -> list[EntradaPd001]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke PD-001 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaPd001(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_pd009_para_regra(
    root: Path, regra: RegraPd009Spec, descoberta: dict[str, Any]
) -> list[EntradaPd009]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke PD-009."""
    return [
        EntradaPd009(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_pd010_para_regra(
    root: Path, regra: RegraPd010Spec, descoberta: dict[str, Any]
) -> list[EntradaPd010]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke PD-010."""
    return [
        EntradaPd010(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_perf004_para_regra(
    root: Path, regra: RegraPerf004Spec, descoberta: dict[str, Any]
) -> list[EntradaPerf004]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke PERF-004 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaPerf004(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_pd011_para_regra(
    root: Path, regra: RegraPd011Spec, descoberta: dict[str, Any]
) -> list[EntradaPd011]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke PD-011."""
    return [
        EntradaPd011(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_qaauto001_para_regra(
    root: Path, regra: RegraQaAuto001Spec, descoberta: dict[str, Any]
) -> list[EntradaQaAuto001]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke QA-AUTO-001."""
    return [
        EntradaQaAuto001(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_fin005_para_regra(
    root: Path, regra: RegraFin005Spec, descoberta: dict[str, Any]
) -> list[EntradaFin005]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke FIN-005 — reaproveita a descoberta genérica `"arvore"`."""
    return [
        EntradaFin005(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_doc004_para_regra(
    root: Path, regra: RegraDoc004Spec, descoberta: dict[str, Any]
) -> list[EntradaDoc004]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke DOC-004."""
    return [
        EntradaDoc004(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_qaauto003_para_regra(
    root: Path, regra: RegraQaAuto003Spec, descoberta: dict[str, Any]
) -> list[EntradaQaAuto003]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke QA-AUTO-003."""
    return [
        EntradaQaAuto003(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_feapi_para_regra(
    root: Path, regra: RegraFeApiSpec, descoberta: dict[str, Any]
) -> list[EntradaFeApi]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke FE-API."""
    return [
        EntradaFeApi(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_arch003_para_regra(
    root: Path, regra: RegraArch003Spec, descoberta: dict[str, Any]
) -> list[EntradaArch003]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke ARCH-003."""
    return [
        EntradaArch003(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_ba004_para_regra(
    root: Path, regra: RegraBa004Spec, descoberta: dict[str, Any]
) -> list[EntradaBa004]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke BA-004."""
    return [
        EntradaBa004(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_ba005_para_regra(
    root: Path, regra: RegraBa005Spec, descoberta: dict[str, Any]
) -> list[EntradaBa005]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke BA-005."""
    return [
        EntradaBa005(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_be010_para_regra(
    root: Path, regra: RegraBe010Spec, descoberta: dict[str, Any]
) -> list[EntradaBe010]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke BE-010."""
    return [
        EntradaBe010(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_be013_para_regra(
    root: Path, regra: RegraBe013Spec, descoberta: dict[str, Any]
) -> list[EntradaBe013]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke BE-013."""
    return [
        EntradaBe013(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_sec007_para_regra(
    root: Path, regra: RegraSec007Spec, descoberta: dict[str, Any]
) -> list[EntradaSec007]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke SEC-007."""
    return [
        EntradaSec007(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_sup001_para_regra(
    root: Path, regra: RegraSup001Spec, descoberta: dict[str, Any]
) -> list[EntradaSup001]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke SUP-001."""
    return [
        EntradaSup001(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_ora005_para_regra(
    root: Path, regra: RegraOra005Spec, descoberta: dict[str, Any]
) -> list[EntradaOra005]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke ORA-005 — reaproveita a descoberta `"arvore"` já existente (1
    Missão por arquivo), só o handler é bespoke."""
    return [
        EntradaOra005(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_ora006_para_regra(
    root: Path, regra: RegraOra006Spec, descoberta: dict[str, Any]
) -> list[EntradaOra006]:
    """Mesmo espírito de `entradas_ora005_para_regra`, para a Capability
    bespoke ORA-006 — mesma descoberta `"arvore"` (1 Missão por arquivo),
    só o handler é bespoke."""
    return [
        EntradaOra006(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_comp008_para_regra(
    root: Path, regra: RegraComp008Spec, descoberta: dict[str, Any]
) -> list[EntradaComp008]:
    """Mesmo espírito de `entradas_a11y003_para_regra`, para a Capability
    bespoke COMP-008 — reaproveita a descoberta genérica `"arvore"` (os 2
    alvos do legado, frontend .ts/.tsx e `api/services` .py, viram 2
    entradas de spec no mesmo diretório `specs/comp008/`)."""
    return [
        EntradaComp008(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_fin006_para_regra(
    root: Path, regra: RegraFin006Spec, descoberta: dict[str, Any]
) -> list[EntradaFin006]:
    """Mesmo espírito de `entradas_fin005_para_regra`, para a Capability
    bespoke FIN-006 — descoberta `"arvore"` com `scripts/` além de
    `src_dirs` (o diretório do caso motivador, ver docstring da regra)."""
    return [
        EntradaFin006(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_ora004_para_regra(
    root: Path, regra: RegraOra004Spec, descoberta: dict[str, Any]
) -> list[EntradaOra004]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke ORA-004."""
    return [
        EntradaOra004(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_agregadas_para_regra(
    root: Path, regra: RegraAgregadaSpec, descoberta: dict[str, Any]
) -> list[EntradaAgregada]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Skill "regex
    agregado sobre múltiplos arquivos"."""
    return [
        EntradaAgregada(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def entradas_de003_para_regra(
    root: Path, regra: RegraDe003Spec, descoberta: dict[str, Any]
) -> list[EntradaDe003]:
    """Mesmo espírito de `entradas_ast_para_regra`, para a Capability
    bespoke DE-003."""
    return [
        EntradaDe003(caminho=caminho, conteudo=conteudo, regra=regra)
        for caminho, conteudo in arquivos_para_regra(root, descoberta)
    ]


def arquivos_para_regra(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Retorna `(caminho_relativo, conteudo_ou_None)` para cada arquivo
    relevante segundo `descoberta["tipo"]`."""
    tipo = descoberta["tipo"]
    if tipo == "arquivo_fixo":
        caminho = descoberta["caminho"]
        conteudo = _ler_ou_marcar_presente(root, caminho)
        # `caminho_relatorio` (opcional) sobrescreve o caminho reportado no
        # achado/fingerprint sem afetar qual caminho e checado no disco -
        # replica DE-002 do motor legado, que checa "alembic"/"migrations"/
        # "alembic.ini" mas reporta o achado com path="." (raiz do repo).
        caminho_relatorio = descoberta.get("caminho_relatorio", caminho)
        return [(caminho_relatorio, conteudo)]
    if tipo == "arvore":
        return _arquivos_em_arvore(root, descoberta)
    if tipo == "glob":
        return _arquivos_via_glob(root, descoberta)
    if tipo == "git":
        return _resultado_de_comando_git(root, descoberta)
    if tipo == "subprocess":
        return _resultado_de_subprocess(root, descoberta)
    if tipo == "playwright":
        return _resultado_playwright(root, descoberta)
    if tipo == "toml_dependencias":
        return _resultado_de_dependencias(root, descoberta)
    if tipo == "de003":
        return _resultado_de003(root, descoberta)
    if tipo == "ora004":
        return _resultado_ora004(root, descoberta)
    if tipo == "regex_agregado":
        return _resultado_regex_agregado(root, descoberta)
    if tipo == "arch003":
        return _resultado_arch003(root, descoberta)
    if tipo == "feapi":
        return _resultado_feapi(root, descoberta)
    if tipo == "be010":
        return _resultado_be010(root, descoberta)
    if tipo == "pd011":
        return _resultado_pd011(root, descoberta)
    if tipo == "qaauto001":
        return _resultado_qaauto001(root, descoberta)
    if tipo == "govdebt001":
        return _resultado_govdebt001(root, descoberta)
    if tipo == "qaauto003":
        return _resultado_qaauto003(root, descoberta)
    if tipo == "doc004":
        return _resultado_doc004(root, descoberta)
    if tipo == "pd009":
        return _resultado_pd009(root, descoberta)
    if tipo == "pd010":
        return _resultado_pd010(root, descoberta)
    if tipo == "sre006":
        return _resultado_sre006(root, descoberta)
    if tipo == "cs005":
        return _resultado_cs005(root, descoberta)
    if tipo == "rev005":
        return _resultado_rev005(root, descoberta)
    raise TipoDescobertaDesconhecido(tipo)


def _resultado_pd011(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """1 única Missão: agrega o texto de `frontend_dirs` (.ts/.tsx) e de
    `src/` (recursivo, .py) SEPARADAMENTE — o handler precisa saber ONDE
    o padrão aparece, não só SE aparece, então os dois textos não podem
    ser misturados numa fonte só (diferente de `regex_agregado`)."""
    caminho_relatorio = descoberta.get("caminho_relatorio", "frontend/src/")
    frontend_dirs = descoberta.get("frontend_dirs", ["frontend/src"])
    backend_dir = descoberta.get("backend_dir", "src")

    frontend_arvore = {"scope_dirs": frontend_dirs, "extensoes": [".ts", ".tsx"]}
    frontend_text = " ".join(
        conteudo or "" for _, conteudo in _arquivos_em_arvore(root, frontend_arvore)
    )

    backend_arvore = {"scope_dirs": [backend_dir], "extensoes": [".py"]}
    backend_text = " ".join(
        conteudo or "" for _, conteudo in _arquivos_em_arvore(root, backend_arvore)
    )

    payload = {"frontend_text": frontend_text, "backend_text": backend_text}
    return [(caminho_relatorio, json.dumps(payload))]


def _resultado_feapi(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """1 Missão por arquivo de rotas (`api/**/*.py`), cada uma carregando o
    PRÓPRIO conteúdo (para extração de rotas via AST no handler) + o texto
    agregado do frontend (`frontend/src/api/**/*.ts(x)`, lido uma única
    vez) — empacotados como JSON. Replica `collect_fastapi_routes` +
    `ctx.ts_files(ctx.frontend_api_dir)` do legado."""
    scope_dirs_api = descoberta.get("scope_dirs", ["api"])
    caminho_frontend_api = descoberta.get("caminho_frontend_api", "frontend/src/api")

    frontend_arvore = {"scope_dirs": [caminho_frontend_api], "extensoes": [".ts", ".tsx"]}
    frontend_text = " ".join(
        conteudo or "" for _, conteudo in _arquivos_em_arvore(root, frontend_arvore)
    )

    api_arvore = {"scope_dirs": scope_dirs_api, "extensoes": [".py"]}
    resultado: list[tuple[str, str | None]] = []
    for rel, conteudo in _arquivos_em_arvore(root, api_arvore):
        payload = {"api_src": conteudo or "", "frontend_text": frontend_text}
        resultado.append((rel, json.dumps(payload)))
    return resultado


def _local_module_names(root: Path) -> list[str]:
    """Replica `RepoContext.local_module_names()`: nomes de topo (diretórios
    e stems de `.py`) direto sob a raiz do repo, mais os nomes hardcoded
    que o legado sempre inclui."""
    nomes: set[str] = set()
    try:
        for p in root.iterdir():
            if p.name.startswith("."):
                continue
            if p.is_dir():
                nomes.add(p.name)
            elif p.suffix == ".py":
                nomes.add(p.stem)
    except OSError:
        pass
    nomes.update({"src", "app", "core", "tests", "test"})
    return sorted(nomes)


def _resultado_cs005(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """1 Missão por arquivo candidato (`api_dir/**/*.py`), cada uma
    carregando o PRÓPRIO conteúdo + o texto agregado do "gate global"
    (`api/middleware/*.py` + `main.py`/`app.py` na raiz e em `api_dir`,
    lido uma única vez) como JSON — replica o `return` antecipado do
    legado quando qualquer candidato do gate já resolve correlation/
    request/trace id globalmente."""
    api_dir = descoberta.get("api_dir", "api")
    middleware_dir_rel = descoberta.get("middleware_dir", "api/middleware")

    textos_gate: list[str] = []
    middleware_dir = root / middleware_dir_rel
    if middleware_dir.exists():
        for mf in sorted(middleware_dir.glob("*.py")):
            textos_gate.append(_ler_texto(mf))
    for nome in ("main.py", "app.py"):
        for base_rel in ("", api_dir):
            caminho_rel = f"{base_rel}/{nome}" if base_rel else nome
            texto = _ler_ou_marcar_presente(root, caminho_rel)
            if texto is not None:
                textos_gate.append(texto)

    middleware_global_texto = " ".join(textos_gate)

    api_arvore = {"scope_dirs": [api_dir], "extensoes": [".py"]}
    resultado: list[tuple[str, str | None]] = []
    for rel, conteudo in _arquivos_em_arvore(root, api_arvore):
        payload = {"api_src": conteudo or "", "middleware_global_texto": middleware_global_texto}
        resultado.append((rel, json.dumps(payload)))
    return resultado


def _resultado_sre006(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """1 única Missão: se `gunicorn.conf.py` existe, empacota SÓ ele
    (prioridade — replica o `return` incondicional do legado após checar
    esse arquivo). Senão, empacota `scripts/*.sh` + `scripts/*.py` (nessa
    ordem, cada grupo ordenado) como JSON."""
    gunicorn_conf_path = descoberta.get("gunicorn_conf_path", "gunicorn.conf.py")
    scripts_dir_rel = descoberta.get("scripts_dir", "scripts")
    caminho_relatorio = descoberta.get("caminho_relatorio", gunicorn_conf_path)

    texto_gunicorn = _ler_ou_marcar_presente(root, gunicorn_conf_path)
    if texto_gunicorn is not None:
        payload = {"gunicorn_conf_texto": texto_gunicorn}
        return [(caminho_relatorio, json.dumps(payload))]

    scripts_dir = root / scripts_dir_rel
    if not scripts_dir.exists():
        return [(caminho_relatorio, None)]

    scripts: list[list[str]] = []
    for caminho in sorted(scripts_dir.glob("*.sh")) + sorted(scripts_dir.glob("*.py")):
        scripts.append([_rel_posix(root, caminho), _ler_texto(caminho)])

    payload_scripts: dict[str, Any] = {"scripts": scripts}
    return [(caminho_relatorio, json.dumps(payload_scripts))]


def _resultado_pd010(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Combina 1 Missão para o arquivo backend fixo (`api/routers/
    sim.py`, se existir) + 1 Missão por arquivo frontend cujo stem
    contém "sim" (case-insensitive) ou "SimuladorFree" (case-sensitive,
    replica o `and` do legado: `"SimuladorFree" not in p.stem and "sim"
    not in p.stem.lower()`)."""
    sim_router_path = descoberta.get("sim_router_path", "api/routers/sim.py")
    frontend_dirs: list[str] = descoberta.get("frontend_dirs", ["frontend/src"])

    resultado: list[tuple[str, str | None]] = []
    texto_backend = _ler_ou_marcar_presente(root, sim_router_path)
    if texto_backend is not None:
        resultado.append((sim_router_path, texto_backend))

    frontend_arvore = {"scope_dirs": frontend_dirs, "extensoes": [".ts", ".tsx"]}
    for rel, conteudo in _arquivos_em_arvore(root, frontend_arvore):
        stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if "SimuladorFree" not in stem and "sim" not in stem.lower():
            continue
        resultado.append((rel, conteudo))

    return resultado


def _resultado_pd009(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """1 única Missão: empacota o conteúdo de `App.tsx` (fonte das
    ocorrências de rota) + os textos de uma LISTA FIXA de arquivos de nav
    como JSON. Se `App.tsx` não existir, `conteudo=None` (replica o
    `return` antecipado do legado)."""
    app_path = descoberta.get("app_path", "frontend/src/App.tsx")
    nav_paths: list[str] = descoberta.get(
        "nav_paths",
        ["frontend/src/components/Layout.tsx", "frontend/src/components/NotifBell.tsx"],
    )
    caminho_relatorio = descoberta.get("caminho_relatorio", app_path)

    app_texto = _ler_ou_marcar_presente(root, app_path)
    if app_texto is None:
        return [(caminho_relatorio, None)]

    nav_textos = [t for p in nav_paths if (t := _ler_ou_marcar_presente(root, p)) is not None]
    payload = {"app_texto": app_texto, "nav_textos": nav_textos}
    return [(caminho_relatorio, json.dumps(payload))]


def _resultado_doc004(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """1 única Missão: empacota `pyproject.toml` + `CHANGELOG.md` como
    JSON. Se qualquer um dos dois não existir, `conteudo=None` (replica
    o `return` antecipado do legado)."""
    pyproject_path = descoberta.get("pyproject_path", "pyproject.toml")
    changelog_path = descoberta.get("changelog_path", "CHANGELOG.md")
    caminho_relatorio = descoberta.get("caminho_relatorio", changelog_path)

    pyproject_texto = _ler_ou_marcar_presente(root, pyproject_path)
    changelog_texto = _ler_ou_marcar_presente(root, changelog_path)
    if pyproject_texto is None or changelog_texto is None:
        return [(caminho_relatorio, None)]

    payload = {"pyproject_texto": pyproject_texto, "changelog_texto": changelog_texto}
    return [(caminho_relatorio, json.dumps(payload))]


def _resultado_qaauto003(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """1 única Missão: checa a existência de cada path em `required_paths`
    e empacota os AUSENTES (na mesma ordem da lista) como JSON."""
    required_paths: list[str] = descoberta.get("required_paths", [])
    caminho_relatorio = descoberta.get("caminho_relatorio", "e2e/smoke")

    missing = [p for p in required_paths if not (root / p).exists()]
    payload = {"missing": missing, "total": len(required_paths)}
    return [(caminho_relatorio, json.dumps(payload))]


#: Quantos dias distintos de abertura equivalem a "1 sessão" para o GOVDEBT-001
#: quando a fonte é o inbox. Ver `_ledger_do_inbox`.
_DIAS_POR_SESSAO_GOVDEBT = 1


def _ledger_do_inbox(root: Path) -> dict[str, Any] | None:
    """Monta o `ledger` do GOVDEBT-001 a partir do `InboxStore`, que é onde a
    fila de achados vive desde o cutover do Batman legado.

    ⚠️ Por que existe (medido em 2026-09-04): o GOVDEBT-001 lia
    `Batman/ledger.json` e `Batman/config/deferred.json`. O diretório `Batman/`
    foi DELETADO no cutover `4d333f21` (2026-07-23). Os arquivos não existem, a
    descoberta devolvia `conteudo=None` e o handler retornava lista vazia — a
    regra que escala a HIGH um achado sem decisão após 2 sessões reportava
    **0 achados enquanto havia 434 aguardando decisão**. A casca da regra foi
    migrada no cutover; a fonte que ela lê, não. É o ponteiro que sobrevive ao
    sistema aposentado.

    O formato de saída é o MESMO que o handler já consome (`entries[fp] =
    {status, sessoes_aberto, codigo, agente, titulo, descricao}`), então o
    handler não muda — só passa a receber dado real.

    ⚠️ **`sessoes_aberto` é uma APROXIMAÇÃO declarada.** O inbox não guarda
    contagem de sessões: guarda `criado_em`. O número aqui são **dias
    distintos** desde a criação, seguindo o precedente da casa (o historian do
    SuperMan contava dias únicos, não execuções, "para evitar inflação" —
    múltiplos scans no mesmo dia contariam como vários). A consequência é que a
    régua fica mais FROUXA que a original, nunca mais apertada: um achado
    criado hoje não escala, mesmo que o scan rode dez vezes.
    """
    caminho = Path(root) / ".batman-os" / "estado.db"
    if not caminho.exists():
        return None

    import sqlite3
    from datetime import UTC, datetime

    try:
        con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        linhas = con.execute(
            "select id, titulo, detalhe, criado_em, status from inbox_achados"
        ).fetchall()
    except sqlite3.Error:
        # Banco existe mas não é do inbox (ou schema antigo): tratar como
        # ausente é melhor que levantar dentro da descoberta.
        return None
    finally:
        con.close()

    agora = datetime.now(UTC)
    entries: dict[str, Any] = {}
    for ident, titulo, detalhe, criado_em, status in linhas:
        if status != "novo":
            continue  # tratado/deferido/resolvido já TÊM decisão
        try:
            nascido = datetime.fromisoformat(str(criado_em))
            if nascido.tzinfo is None:
                nascido = nascido.replace(tzinfo=UTC)
            dias = max(0, (agora.date() - nascido.date()).days)
        except (TypeError, ValueError):
            dias = 0
        titulo_txt = str(titulo or "")
        codigo = titulo_txt.split(" ", 1)[0] if titulo_txt else "?"
        entries[str(ident)] = {
            "status": "open",
            "sessoes_aberto": dias // _DIAS_POR_SESSAO_GOVDEBT,
            "codigo": codigo,
            "agente": "inbox",
            "titulo": titulo_txt,
            "descricao": str(detalhe or ""),
        }
    return {"entries": entries}


def _resultado_govdebt001(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """1 única Missão: empacota o ledger de achados + os códigos com
    deferimento explícito, como JSON.

    A fonte do ledger é, nesta ordem: (1) `ledger_path` legado, se existir —
    preserva o comportamento de quem ainda tem o arquivo; (2) o `InboxStore`
    do próprio repositório escaneado, que é onde a fila vive desde o cutover.
    Sem nenhuma das duas, a Missão carrega `conteudo=None` e o handler devolve
    lista vazia, como antes.
    """
    ledger_path = descoberta.get("ledger_path", "Batman/ledger.json")
    deferred_path = descoberta.get("deferred_path", "Batman/config/deferred.json")
    caminho_relatorio = descoberta.get("caminho_relatorio", ledger_path)

    ledger: dict[str, Any] | None = None
    texto_ledger = _ler_ou_marcar_presente(root, ledger_path)
    if texto_ledger:
        try:
            ledger = json.loads(texto_ledger)
        except json.JSONDecodeError:
            ledger = None

    if ledger is None and descoberta.get("usar_inbox", True):
        ledger = _ledger_do_inbox(root)
        if ledger is not None:
            caminho_relatorio = descoberta.get("caminho_relatorio", ".batman-os/estado.db")

    if ledger is None:
        return [(caminho_relatorio, None)]

    deferred_codes: list[str] = []
    texto_deferred = _ler_ou_marcar_presente(root, deferred_path)
    if texto_deferred:
        try:
            deferred = json.loads(texto_deferred)
            deferred_codes = sorted(deferred.get("deferred", {}).keys())
        except json.JSONDecodeError:
            pass

    payload = {"ledger": ledger, "deferred_codes": deferred_codes}
    return [(caminho_relatorio, json.dumps(payload))]


def _resultado_be010(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """1 única Missão: empacota `pyproject.toml` + nomes de módulo local
    (listagem de diretório, não conteúdo de arquivo) + TODOS os `.py` de
    `scope_dirs` (na ORDEM dada, preservando a ordem de inserção do dict
    — replica `seen.setdefault` do legado, que reporta o PRIMEIRO arquivo
    onde um import de terceiro aparece) como JSON."""
    caminho_pyproject = descoberta.get("caminho_pyproject", "pyproject.toml")
    caminho_relatorio = descoberta.get("caminho_relatorio", caminho_pyproject)
    scope_dirs = descoberta.get("scope_dirs", ["api", "src", "dashboard", "pages"])

    pyproject_texto = _ler_ou_marcar_presente(root, caminho_pyproject)
    if pyproject_texto is None:
        return [(caminho_relatorio, None)]

    arquivos: dict[str, str] = {}
    for escopo in scope_dirs:
        arvore = {"scope_dirs": [escopo], "extensoes": [".py"]}
        for rel, conteudo in _arquivos_em_arvore(root, arvore):
            if conteudo is not None:
                arquivos[rel] = conteudo

    payload = {
        "pyproject_texto": pyproject_texto,
        "local_modules": _local_module_names(root),
        "arquivos": arquivos,
    }
    return [(caminho_relatorio, json.dumps(payload))]


def _resultado_qaauto001(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """1 Missão por router candidato (`api/routers/*.py`), cada uma
    carregando a MESMA lista agregada de stems de teste (`tests/**/
    test_*.py`, prefixo `test_` removido — replica `p.stem.replace(
    "test_", "")` do legado) como JSON. Se `tests/` não existe, nenhuma
    Missão é gerada (replica o `return` antecipado do legado)."""
    tests_dir = descoberta.get("tests_dir", "tests")
    routers_dir = descoberta.get("routers_dir", "api/routers")

    if not (root / tests_dir).is_dir():
        return []

    test_stems = sorted(
        {
            rel.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("test_", "")
            for rel, _ in _arquivos_em_arvore(
                root, {"scope_dirs": [tests_dir], "extensoes": [".py"]}
            )
            if rel.replace("\\", "/").rsplit("/", 1)[-1].startswith("test_")
        }
    )
    payload = json.dumps({"test_stems": test_stems})

    routers_arvore = {"scope_dirs": [routers_dir], "extensoes": [".py"]}
    return [(rel, payload) for rel, _ in _arquivos_em_arvore(root, routers_arvore)]


def _resultado_arch003(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """1 Missão por página candidata (`pages/*.py`), cada uma carregando o
    MESMO conteúdo do agregador (`dashboard/app.py`) como `conteudo` — o
    handler bespoke ARCH-003 só verifica se o próprio nome (stem) da
    página aparece nesse texto compartilhado. Replica `RepoContext.
    dashboard_app`/`pages_dir` do legado."""
    caminho_agregador = descoberta.get("caminho_agregador", "dashboard/app.py")
    scope_dirs = descoberta.get("scope_dirs", ["pages"])
    conteudo_agregador = _ler_ou_marcar_presente(root, caminho_agregador)

    descoberta_arvore = {
        "scope_dirs": scope_dirs,
        "extensoes": descoberta.get("extensoes", [".py"]),
    }
    paginas = _arquivos_em_arvore(root, descoberta_arvore)
    return [(rel, conteudo_agregador) for rel, _ in paginas]


def _resultado_de003(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    tables_path = descoberta.get("tables_path", "api/database/tables.py")
    init_db_path = descoberta.get("init_db_path", "api/database/init_db.py")
    lookback = descoberta.get("lookback", 15)
    caminho_relatorio = descoberta.get("caminho_relatorio", tables_path)

    if not (root / tables_path).exists() or not (root / init_db_path).exists():
        payload: dict[str, Any] = {"aplica": False}
        return [(caminho_relatorio, json.dumps(payload))]

    args = (
        "log",
        f"-{lookback}",
        "--pretty=format:COMMIT:%H",
        "-p",
        "--unified=30",
        "--",
        tables_path,
    )
    try:
        resultado = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=30
        )
        log = resultado.stdout.strip()
    except Exception:
        log = ""

    payload = {
        "aplica": True,
        "init_db_src": _ler_texto(root / init_db_path),
        "tables_src": _ler_texto(root / tables_path),
        "git_log": log,
    }
    return [(caminho_relatorio, json.dumps(payload))]


def _resultado_rev005(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Empacota até `_MAX_FILES` (20) arquivos candidatos — de `api/` depois
    `src/radar/`, nessa ordem, cada diretório varrido em ordem alfabética
    (`rglob` ordenado, replicando `ctx.py_files()`), excluindo caminhos com
    "test"/"migration"/"alembic" e arquivos com menos de 50 linhas — como
    JSON para a Capability bespoke REV-005, que roda o algoritmo de
    detecção de bloco duplicado inteiro numa única invocação."""
    caminho_relatorio = descoberta.get("caminho_relatorio", ".")
    max_files = descoberta.get("max_files", 20)

    arquivos: dict[str, str] = {}
    for escopo in descoberta.get("scope_dirs", []):
        base = root / escopo
        if not base.exists():
            continue
        for caminho in sorted(c for c in _caminhar_arquivos_podado(base) if c.suffix == ".py"):
            rel = str(caminho.relative_to(root))
            if "test" in rel or "migration" in rel or "alembic" in rel:
                continue
            conteudo = _ler_texto(caminho)
            if len(conteudo.splitlines()) < 50:
                continue
            arquivos[rel] = conteudo
            if len(arquivos) >= max_files:
                break
        if len(arquivos) >= max_files:
            break

    return [(caminho_relatorio, json.dumps({"arquivos": arquivos}))]


def _resultado_ora004(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    caminho_relatorio = descoberta.get("caminho_relatorio", ".")
    enums_path = descoberta.get("enums_path")

    descoberta_arvore = {
        "scope_dirs": descoberta.get("scope_dirs", []),
        "extensoes": [".py"],
        # replica oracle.py: `any(part.startswith(("test","conftest")) for
        # part in parts)` — qualquer segmento do caminho, não só o nome do
        # arquivo (arquivo em si coberto por excluir_nome_prefixo). Legado
        # não faz case-fold aqui, só o "tests"/"test_*" minúsculo real.
        "excluir_caminho_contem": ["/test", "/conftest"],
        "excluir_nome_prefixo": ["test", "conftest"],
    }
    arquivos = {
        rel: (conteudo or "") for rel, conteudo in _arquivos_em_arvore(root, descoberta_arvore)
    }

    enums_src = None
    if enums_path and (root / enums_path).exists():
        enums_src = _ler_texto(root / enums_path)

    payload: dict[str, Any] = {"arquivos": arquivos, "enums_src": enums_src}
    return [(caminho_relatorio, json.dumps(payload))]


def _resultado_regex_agregado(
    root: Path, descoberta: dict[str, Any]
) -> list[tuple[str, str | None]]:
    """Empacota o conteúdo de VÁRIOS arquivos (potencialmente de múltiplas
    fontes de tipos DIFERENTES — ex.: VPS-012 usa `glob` não-recursivo
    `scripts/*.sh` porque o legado usa `Path.glob` (só filhos diretos), não
    `Path.rglob`) como JSON para a Skill "regex agregado sobre múltiplos
    arquivos" (`regex_agregado_multi_arquivo.py`). `fontes` (opcional):
    lista de descobertas independentes, cada uma com seu próprio `tipo`
    (`arvore`/`glob`/`arquivo_fixo`, despachadas via `arquivos_para_regra`
    — mesmo dispatcher genérico usado no nível superior); sem `fontes`, a
    própria `descoberta` é usada como fonte única do tipo `arvore` (mesma
    convenção de `scope_dirs`/`extensoes` direto no nível superior)."""
    caminho_relatorio = descoberta.get("caminho_relatorio", ".")
    fontes = descoberta.get("fontes") or [{**descoberta, "tipo": "arvore"}]

    arquivos: dict[str, str] = {}
    for fonte in fontes:
        for rel, conteudo in arquivos_para_regra(root, fonte):
            if conteudo is not None:
                arquivos[rel] = conteudo

    payload: dict[str, Any] = {"arquivos": arquivos}
    return [(caminho_relatorio, json.dumps(payload))]


def _resultado_de_dependencias(
    root: Path, descoberta: dict[str, Any]
) -> list[tuple[str, str | None]]:
    caminho_relatorio = descoberta.get("caminho_relatorio", "pyproject.toml")
    pyproject = root / "pyproject.toml"
    requirements = root / "requirements.txt"

    payload: dict[str, Any] = {
        "pyproject_texto": _ler_texto(pyproject) if pyproject.exists() else None,
        "requirements_texto": _ler_texto(requirements) if requirements.exists() else None,
        "arquivos_tests": _arquivos_py_como_dict(root, root / "tests"),
        "arquivos_src": _arquivos_py_como_dict(root, root / "src"),
    }
    return [(caminho_relatorio, json.dumps(payload))]


def _arquivos_py_como_dict(root: Path, base: Path) -> dict[str, str]:
    if not base.exists():
        return {}
    return {
        str(caminho.relative_to(root)).replace("\\", "/"): _ler_texto(caminho)
        for caminho in sorted(c for c in _caminhar_arquivos_podado(base) if c.suffix == ".py")
    }


def _resultado_de_comando_git(
    root: Path, descoberta: dict[str, Any]
) -> list[tuple[str, str | None]]:
    """Replica `Batman/scan/base.py::RepoContext.git()` — nunca propaga
    falha/timeout como exceção, retorna string vazia (não `None`; `None`
    aqui significaria "arquivo ausente", semântica que não se aplica a saída
    de comando)."""
    args = descoberta["args"]
    caminho_relatorio = descoberta.get("caminho_relatorio", ".git")
    try:
        resultado = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        conteudo = resultado.stdout.strip()
    except Exception:
        conteudo = ""
    return [(caminho_relatorio, conteudo)]


def _python_do_venv(root: Path) -> str:
    """Replica `_find_python()` duplicado em `oracle.py`/`robin.py` —
    prefere o Python do venv do projeto alvo; fallback para o Python que
    está rodando o Batman OS."""
    for candidato in (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
        root / "venv" / "Scripts" / "python.exe",
        root / "venv" / "bin" / "python",
    ):
        if candidato.exists():
            return str(candidato)
    return sys.executable


def _matar_processo_e_arvore(processo: subprocess.Popen[str]) -> None:
    """Mata `processo` e TODA a árvore de descendentes — nunca só
    `Popen.kill()` puro no Windows (bomba latente diagnosticada em
    2026-07-30 investigando hangs do scanner: `subprocess.run()` do CPython,
    ao estourar `timeout`, chama `process.kill()` e ENTÃO chama
    `process.communicate()` de novo para coletar o output remanescente —
    `Lib/subprocess.py`, ramo `_mswindows`. `TerminateProcess` (o que
    `Popen.kill()` faz no Windows) só mata o processo IMEDIATO: um filho
    órfão vivo (ex.: worker do pytest-xdist, processo de navegador do
    Playwright/qa-visual) continua segurando os handles de stdout/stderr
    HERDADOS abertos. Esse segundo `communicate()` bloqueia até os handles
    fecharem — o que nunca acontece com um filho órfão vivo — e um timeout
    de 60s vira uma trava PERMANENTE do scan. `taskkill /F /T /PID` mata a
    árvore inteira (Windows rastreia PPID na criação; `/T` = "tree"),
    garantindo que os handles fecham de verdade."""
    if sys.platform.startswith("win"):
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(processo.pid)],
                capture_output=True,
                timeout=10,
            )
            return
        except Exception:
            pass  # taskkill falhou (ex.: PID já morreu sozinho) -- cai no kill() simples
    processo.kill()


def _rodar_subprocess_cacheado(
    comando: tuple[str, ...],
    root: Path,
    timeout: int,
    *,
    cwd: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Cache por (comando, cwd, env_extra) durante o processo — corrige a
    ausência de cache em `robin.py` (pytest rodava 1x por regra QA-RUN-*;
    aqui roda 1x para as 3) e replica o cache já existente em `oracle.py`
    (`_ruff_cache`, ORA-001/002/003). Nunca propaga timeout/ausência do
    comando como exceção — devolve um returncode sentinela (-2 timeout, -1
    não encontrado), mesmo espírito de `RuffUnavailable`/`PytestUnavailable`.

    `cwd`/`env_extra` (achado da capability `qa-visual`, Onda 1 do Plano
    Cobertura Total): `npx playwright test` precisa rodar dentro de
    `frontend/` (não na raiz do repo) e com `BASE_URL` no ambiente — os
    demais chamadores (pytest/ruff, `python -m modulo`) continuam usando
    `cwd=root` e o ambiente herdado sem mudar nada (`cwd=None` -> `root`;
    `env_extra=None` -> ambiente herdado, mesmo comportamento de sempre).

    Gerencia o `Popen` manualmente (em vez de `subprocess.run(timeout=...)`)
    para poder matar a ÁRVORE inteira no timeout (`_matar_processo_e_arvore`)
    — ver docstring dela para a bomba latente do Windows que isso corrige.
    Após matar a árvore, um segundo `communicate()` com timeout PRÓPRIO
    (nunca ilimitado) tenta coletar o output remanescente; se ainda assim
    não retornar (resíduo teórico — taskkill falhou silenciosamente), desiste
    com output vazio em vez de travar o scan inteiro."""
    cwd_efetivo = cwd if cwd is not None else root
    chave_env = json.dumps(env_extra, sort_keys=True) if env_extra else ""
    chave = (str(cwd_efetivo), chave_env, *comando)
    if chave in _cache_subprocess:
        return _cache_subprocess[chave]
    ambiente = None
    if env_extra:
        ambiente = {**os.environ, **env_extra}
    try:
        processo = subprocess.Popen(
            list(comando),
            cwd=cwd_efetivo,
            env=ambiente,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        valor = (-1, "", "comando não encontrado")
        _cache_subprocess[chave] = valor
        return valor
    try:
        stdout, stderr = processo.communicate(timeout=timeout)
        valor = (processo.returncode or 0, stdout, stderr)
    except subprocess.TimeoutExpired:
        _matar_processo_e_arvore(processo)
        try:
            stdout, stderr = processo.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        valor = (-2, stdout, f"comando excedeu timeout de {timeout}s")
    _cache_subprocess[chave] = valor
    return valor


def _relativizar_filenames_ruff(root: Path, stdout: str) -> str:
    """Ruff (`--output-format json`) reporta `filename` como caminho
    ABSOLUTO — o legado (`_rel()` em `oracle.py`) relativiza contra `root`
    (com fallback para o caminho bruto normalizado se `relative_to`
    falhar) antes de usar no fingerprint. Sem isso, o "rel" usado pelo
    handler (`execucao_comando_interpretada.py`) diverge do legado
    (caminho absoluto vs relativo) — achado real: ORA-002 divergia por
    esse motivo. Aplicado na descoberta para manter o handler puro."""
    try:
        itens = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return stdout
    for item in itens:
        bruto = item.get("filename", "")
        try:
            item["filename"] = str(Path(bruto).relative_to(root)).replace("\\", "/")
        except ValueError:
            item["filename"] = bruto.replace("\\", "/")
    return json.dumps(itens)


def _resultado_de_subprocess(
    root: Path, descoberta: dict[str, Any]
) -> list[tuple[str, str | None]]:
    caminho_relatorio = descoberta.get("caminho_relatorio", ".")
    requer_dir: str | None = descoberta.get("requer_dir")
    dir_requerido_existe = (root / requer_dir).exists() if requer_dir else None

    arquivos_teste_encontrados: list[str] = []
    padroes_teste = descoberta.get("glob_arquivos_teste")
    if padroes_teste and requer_dir and dir_requerido_existe:
        base = root / requer_dir
        arquivos_base = list(_caminhar_arquivos_podado(base))
        for padrao in padroes_teste:
            arquivos_teste_encontrados.extend(
                str(p.relative_to(root)).replace("\\", "/")
                for p in arquivos_base
                if p.match(padrao)
            )

    if requer_dir and dir_requerido_existe is False:
        payload_ausente: dict[str, Any] = {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "dir_requerido_existe": False,
            "arquivos_teste_encontrados": [],
        }
        return [(caminho_relatorio, json.dumps(payload_ausente))]

    args = list(descoberta.get("args", []))
    dirs_condicionais = descoberta.get("dirs_condicionais")
    if dirs_condicionais is not None:
        existentes = [d for d in dirs_condicionais if (root / d).exists()]
        if not existentes:
            # replica oracle.py: nenhum dos dirs-alvo existe -> sucesso
            # vazio, nem roda o comando (`if not targets: rc=0, items=[]`).
            payload_vazio: dict[str, Any] = {"returncode": 0, "stdout": "[]", "stderr": ""}
            return [(caminho_relatorio, json.dumps(payload_vazio))]
        args = [*args, *existentes]

    python = _python_do_venv(root)
    comando = (python, "-m", descoberta["modulo"], *args)
    rc, stdout, stderr = _rodar_subprocess_cacheado(comando, root, descoberta.get("timeout", 60))
    if descoberta["modulo"] == "ruff" and rc == 0:
        stdout = _relativizar_filenames_ruff(root, stdout)

    payload: dict[str, Any] = {
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "dir_requerido_existe": dir_requerido_existe,
        "arquivos_teste_encontrados": arquivos_teste_encontrados,
    }
    return [(caminho_relatorio, json.dumps(payload))]


_NPX_BIN = "npx.cmd" if sys.platform.startswith("win") else "npx"


def _host_de_url(url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower()


def _resultado_playwright(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Descoberta da capability `qa-visual` (QAVIS-001, Onda 1 do Plano
    Cobertura Total): roda `npx playwright test --reporter=json` dentro de
    `frontend_dir` (não na raiz do repo) com `BASE_URL` no ambiente —
    reaproveita `_rodar_subprocess_cacheado` (inclusive o fix de árvore/
    timeout do Windows) via os parâmetros `cwd`/`env_extra`.

    **NUNCA roda contra PRODUÇÃO**: se `base_url` resolver para um domínio
    em `dominios_proibidos` (default: `exemplo.group`, o domínio nu de
    produção), a checagem é recusada ANTES de invocar qualquer subprocess
    — o handler (`qavis001_playwright_falhou.py`) só recebe
    `bloqueado_prd=True` e emite um achado de configuração."""
    caminho_relatorio = descoberta.get("caminho_relatorio", "frontend/e2e/")
    frontend_dir = descoberta.get("frontend_dir", "frontend")
    base_url = descoberta.get("base_url") or os.environ.get(
        descoberta.get("base_url_env", "BATMAN_QAVIS_BASE_URL"), ""
    )
    dominios_proibidos = descoberta.get("dominios_proibidos", ["exemplo.group"])

    if not base_url:
        # sem base_url configurado -- config ausente, nao achado (mesmo
        # espirito de "checagem pulada" do FunctionalMonitor sem credencial).
        payload_sem_url: dict[str, Any] = {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "frontend_dir_existe": None,
            "bloqueado_prd": False,
            "base_url": None,
        }
        return [(caminho_relatorio, json.dumps(payload_sem_url))]

    if _host_de_url(base_url) in {d.lower() for d in dominios_proibidos}:
        payload_bloqueado: dict[str, Any] = {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "frontend_dir_existe": None,
            "bloqueado_prd": True,
            "base_url": base_url,
        }
        return [(caminho_relatorio, json.dumps(payload_bloqueado))]

    frontend_path = root / frontend_dir
    if not frontend_path.exists():
        payload_sem_dir: dict[str, Any] = {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "frontend_dir_existe": False,
            "bloqueado_prd": False,
            "base_url": base_url,
        }
        return [(caminho_relatorio, json.dumps(payload_sem_dir))]

    args = list(descoberta.get("args", ["playwright", "test", "--reporter=json"]))
    comando = (_NPX_BIN, *args)
    rc, stdout, stderr = _rodar_subprocess_cacheado(
        comando,
        root,
        descoberta.get("timeout", 300),
        cwd=frontend_path,
        env_extra={"BASE_URL": base_url},
    )

    payload: dict[str, Any] = {
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "frontend_dir_existe": True,
        "bloqueado_prd": False,
        "base_url": base_url,
    }
    return [(caminho_relatorio, json.dumps(payload))]


def _condicoes_adicionais_para(root: Path, descoberta: dict[str, Any]) -> list[CondicaoAdicional]:
    condicoes: list[CondicaoAdicional] = []
    for item in descoberta.get("condicoes_adicionais", []):
        tipo = item["tipo"]
        if tipo == "arquivo_fixo":
            conteudo = _ler_ou_marcar_presente(root, item["caminho"])
            if conteudo is None and item.get("vazio_se_ausente"):
                # arquivo ausente conta como "sem protecao", nao como
                # "nao ha nada para avaliar" (ver docstring de
                # capabilities/rules/regex_sobre_conteudo.py::
                # _condicao_simples_satisfeita, modo AUSENCIA).
                conteudo = ""
            condicoes.append(
                CondicaoAdicional(
                    caminho=item["caminho"],
                    conteudo=conteudo,
                    checar=ModoAvaliacao(item["checar"]),
                    pattern=item.get("pattern"),
                    ignore_case=item.get("ignore_case", False),
                )
            )
        elif tipo == "glob_existe":
            condicoes.append(_condicao_glob_existe(root, item))
        else:
            raise TipoDescobertaDesconhecido(tipo)
    return condicoes


def _condicao_glob_existe(root: Path, item: dict[str, Any]) -> CondicaoAdicional:
    padrao = item["padrao_recursivo"]
    excludes = item.get("excluir_caminho_contem", [])
    encontrado = any(
        not _excluido_por_substring(_rel_posix(root, caminho), excludes)
        for caminho in _caminhar_arquivos_podado(root)
        if caminho.match(padrao)
    )
    return CondicaoAdicional(
        caminho=padrao,
        conteudo="existe" if encontrado else None,
        checar=ModoAvaliacao(item["checar"]),
    )


def _arquivos_em_arvore(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    resultado: list[tuple[str, str | None]] = []
    extensoes = tuple(descoberta.get("extensoes", []))
    for escopo in descoberta.get("scope_dirs", []):
        base = root / escopo
        if not base.exists():
            continue
        # Uma única travessia podada de `base`, filtrando por extensão em
        # memória, em vez de um `base.rglob(f"*{extensao}")` por extensão
        # (que repetiria a travessia inteira N vezes) — ver
        # `_caminhar_arquivos_podado`.
        candidatos = sorted(
            caminho for caminho in _caminhar_arquivos_podado(base) if caminho.suffix in extensoes
        )
        for caminho in candidatos:
            rel = _rel_posix(root, caminho)
            if _excluido(rel, caminho.name, descoberta):
                continue
            resultado.append((rel, _ler_texto(caminho)))
    return resultado


def _arquivos_via_glob(root: Path, descoberta: dict[str, Any]) -> list[tuple[str, str | None]]:
    resultado: list[tuple[str, str | None]] = []
    padroes = descoberta.get("padroes")
    if padroes:
        candidatos: set[Path] = set()
        for padrao in padroes:
            candidatos.update(root.glob(padrao))
        for caminho in sorted(candidatos):
            if caminho.is_file():
                resultado.append((_rel_posix(root, caminho), _ler_texto(caminho)))
        return resultado

    padrao_recursivo = descoberta.get("padrao_recursivo")
    if padrao_recursivo:
        excludes = descoberta.get("excluir_caminho_contem", [])
        candidatos_recursivos = sorted(
            caminho
            for caminho in _caminhar_arquivos_podado(root)
            if caminho.match(padrao_recursivo)
        )
        for caminho in candidatos_recursivos:
            rel = _rel_posix(root, caminho)
            if _excluido_por_substring(rel, excludes):
                continue
            resultado.append((rel, _ler_texto(caminho)))
        return resultado

    raise TipoDescobertaDesconhecido("glob sem 'padroes' nem 'padrao_recursivo'")


def _excluido(rel_posix: str, nome_arquivo: str, descoberta: dict[str, Any]) -> bool:
    if _excluido_por_substring(
        rel_posix, descoberta.get("excluir_caminho_prefixo", []), prefixo=True
    ):
        return True
    if _excluido_por_substring(rel_posix, descoberta.get("excluir_caminho_contem", [])):
        return True
    if _excluido_por_substring(
        rel_posix.lower(), descoberta.get("excluir_caminho_contem_lower", [])
    ):
        return True
    if _excluido_por_substring(
        nome_arquivo, descoberta.get("excluir_nome_prefixo", []), prefixo=True
    ):
        return True
    return _excluido_por_substring(
        nome_arquivo.lower(), descoberta.get("excluir_nome_contem_lower", [])
    )


def _excluido_por_substring(valor: str, padroes: list[str], prefixo: bool = False) -> bool:
    if prefixo:
        return any(valor.startswith(p) for p in padroes)
    return any(p in valor for p in padroes)


def _ler_ou_marcar_presente(root: Path, caminho_rel: str) -> str | None:
    caminho = root / caminho_rel
    if not caminho.exists():
        return None
    if caminho.is_dir():
        return ""  # presenca confirmada; sem conteudo textual a avaliar
    return _ler_texto(caminho)


MAX_BYTES_LEITURA = 1_000_000
"""Cap de leitura por arquivo (incidente 2026-07-29): sem limite, .jsonl de
28 MB do repo-alvo entravam inteiros no payload das Missões (evento de 4 MB
no estado.db) e alimentavam regex quadráticas. Arquivo acima do cap é tratado
como sem conteúdo textual avaliável — regras de presença não disparam nele."""


def _ler_texto(caminho: Path) -> str:
    try:
        if caminho.stat().st_size > MAX_BYTES_LEITURA:
            return ""
        return caminho.read_text(encoding="utf-8")
    except Exception:
        return ""


def _rel_posix(root: Path, caminho: Path) -> str:
    try:
        return caminho.relative_to(root).as_posix()
    except ValueError:
        return caminho.as_posix()


def registrar_capabilities_conhecidas() -> None:
    """Registra as 50 Capabilities conhecidas (49 bespoke/específicas +
    a genérica `regex-sobre-conteudo-de-arquivo`, alimentada por 3 lotes
    de specs) no `registry_sdk` — chamado uma única vez por
    `cli/scan_command.py::_preparar_capabilities()`. Único ponto de
    registro necessário para uma Capability nova entrar no scan (Fase 1
    do roadmap de plataforma — ver `.claude/plans/
    peaceful-wondering-hearth.md`): antes eram 7 pontos manuais em
    `scan_command.py` + este arquivo; agora é só este bloco.

    Não é auto-discovery via reflexão (`pkgutil`) — isso exigiria mover
    as primitivas de descoberta de arquivo (`arquivos_para_regra` etc.)
    para fora de `cli/`, já que um módulo de Capability em
    `capabilities/rules/*.py` não pode importar deste arquivo sem criar
    import circular (este arquivo já importa de lá). Documentado como
    extensão natural futura, não feito nesta rodada."""
    registrar(
        tipo="generico",
        regra_cls=RegraSpec,
        construir_implementacao=construir_implementacao_regex,
        carregar_especificacoes=lambda: [
            *carregar_lote_01(),
            *carregar_lote_02(),
            *carregar_lote_03(),
        ],
        entradas_para_regra=entradas_para_regra,
    )

    registrar(
        tipo="a11y002",
        regra_cls=RegraA11y002Spec,
        construir_implementacao=construir_implementacao_a11y002,
        carregar_especificacoes=carregar_especificacoes_a11y002,
        entradas_para_regra=entradas_a11y002_para_regra,
    )
    registrar(
        tipo="a11y003",
        regra_cls=RegraA11y003Spec,
        construir_implementacao=construir_implementacao_a11y003,
        carregar_especificacoes=carregar_especificacoes_a11y003,
        entradas_para_regra=entradas_a11y003_para_regra,
    )
    registrar(
        tipo="agregadas",
        regra_cls=RegraAgregadaSpec,
        construir_implementacao=construir_implementacao_agregadas,
        carregar_especificacoes=carregar_especificacoes_agregadas,
        entradas_para_regra=entradas_agregadas_para_regra,
    )
    registrar(
        tipo="arch003",
        regra_cls=RegraArch003Spec,
        construir_implementacao=construir_implementacao_arch003,
        carregar_especificacoes=carregar_especificacoes_arch003,
        entradas_para_regra=entradas_arch003_para_regra,
    )
    registrar(
        tipo="ast",
        regra_cls=RegraAstSpec,
        construir_implementacao=construir_implementacao_ast,
        carregar_especificacoes=carregar_especificacoes_ast,
        entradas_para_regra=entradas_ast_para_regra,
    )
    registrar(
        tipo="ba004",
        regra_cls=RegraBa004Spec,
        construir_implementacao=construir_implementacao_ba004,
        carregar_especificacoes=carregar_especificacoes_ba004,
        entradas_para_regra=entradas_ba004_para_regra,
    )
    registrar(
        tipo="ba005",
        regra_cls=RegraBa005Spec,
        construir_implementacao=construir_implementacao_ba005,
        carregar_especificacoes=carregar_especificacoes_ba005,
        entradas_para_regra=entradas_ba005_para_regra,
    )
    registrar(
        tipo="be010",
        regra_cls=RegraBe010Spec,
        construir_implementacao=construir_implementacao_be010,
        carregar_especificacoes=carregar_especificacoes_be010,
        entradas_para_regra=entradas_be010_para_regra,
    )
    registrar(
        tipo="be013",
        regra_cls=RegraBe013Spec,
        construir_implementacao=construir_implementacao_be013,
        carregar_especificacoes=carregar_especificacoes_be013,
        entradas_para_regra=entradas_be013_para_regra,
    )
    registrar(
        tipo="comp008",
        regra_cls=RegraComp008Spec,
        construir_implementacao=construir_implementacao_comp008,
        carregar_especificacoes=carregar_especificacoes_comp008,
        entradas_para_regra=entradas_comp008_para_regra,
    )
    registrar(
        tipo="cs003",
        regra_cls=RegraCs003Spec,
        construir_implementacao=construir_implementacao_cs003,
        carregar_especificacoes=carregar_especificacoes_cs003,
        entradas_para_regra=entradas_cs003_para_regra,
    )
    registrar(
        tipo="cs005",
        regra_cls=RegraCs005Spec,
        construir_implementacao=construir_implementacao_cs005,
        carregar_especificacoes=carregar_especificacoes_cs005,
        entradas_para_regra=entradas_cs005_para_regra,
    )
    registrar(
        tipo="cto002",
        regra_cls=RegraCto002Spec,
        construir_implementacao=construir_implementacao_cto002,
        carregar_especificacoes=carregar_especificacoes_cto002,
        entradas_para_regra=entradas_cto002_para_regra,
    )
    registrar(
        tipo="cto004",
        regra_cls=RegraCto004Spec,
        construir_implementacao=construir_implementacao_cto004,
        carregar_especificacoes=carregar_especificacoes_cto004,
        entradas_para_regra=entradas_cto004_para_regra,
    )
    registrar(
        tipo="de003",
        regra_cls=RegraDe003Spec,
        construir_implementacao=construir_implementacao_de003,
        carregar_especificacoes=carregar_especificacoes_de003,
        entradas_para_regra=entradas_de003_para_regra,
    )
    registrar(
        tipo="dependencias",
        regra_cls=RegraDependenciasSpec,
        construir_implementacao=construir_implementacao_dependencias,
        carregar_especificacoes=carregar_especificacoes_dependencias,
        entradas_para_regra=entradas_dependencias_para_regra,
    )
    registrar(
        tipo="doc004",
        regra_cls=RegraDoc004Spec,
        construir_implementacao=construir_implementacao_doc004,
        carregar_especificacoes=carregar_especificacoes_doc004,
        entradas_para_regra=entradas_doc004_para_regra,
    )
    registrar(
        tipo="execucao_comando",
        regra_cls=RegraExecucaoComandoSpec,
        construir_implementacao=construir_implementacao_execucao_comando,
        carregar_especificacoes=carregar_especificacoes_execucao_comando,
        entradas_para_regra=entradas_execucao_comando_para_regra,
    )
    registrar(
        tipo="fe001",
        regra_cls=RegraFe001Spec,
        construir_implementacao=construir_implementacao_fe001,
        carregar_especificacoes=carregar_especificacoes_fe001,
        entradas_para_regra=entradas_fe001_para_regra,
    )
    registrar(
        tipo="fe002",
        regra_cls=RegraFe002Spec,
        construir_implementacao=construir_implementacao_fe002,
        carregar_especificacoes=carregar_especificacoes_fe002,
        entradas_para_regra=entradas_fe002_para_regra,
    )
    registrar(
        tipo="fe007",
        regra_cls=RegraFe007Spec,
        construir_implementacao=construir_implementacao_fe007,
        carregar_especificacoes=carregar_especificacoes_fe007,
        entradas_para_regra=entradas_fe007_para_regra,
    )
    registrar(
        tipo="feapi",
        regra_cls=RegraFeApiSpec,
        construir_implementacao=construir_implementacao_feapi,
        carregar_especificacoes=carregar_especificacoes_feapi,
        entradas_para_regra=entradas_feapi_para_regra,
    )
    registrar(
        tipo="fin005",
        regra_cls=RegraFin005Spec,
        construir_implementacao=construir_implementacao_fin005,
        carregar_especificacoes=carregar_especificacoes_fin005,
        entradas_para_regra=entradas_fin005_para_regra,
    )
    registrar(
        tipo="fin006",
        regra_cls=RegraFin006Spec,
        construir_implementacao=construir_implementacao_fin006,
        carregar_especificacoes=carregar_especificacoes_fin006,
        entradas_para_regra=entradas_fin006_para_regra,
    )
    registrar(
        tipo="git_interpretado",
        regra_cls=RegraComparacaoNumericaSpec,
        construir_implementacao=construir_implementacao_git_interpretado,
        carregar_especificacoes=carregar_especificacoes_git_interpretado,
        entradas_para_regra=entradas_git_interpretado_para_regra,
    )
    registrar(
        tipo="govdebt001",
        regra_cls=RegraGovdebt001Spec,
        construir_implementacao=construir_implementacao_govdebt001,
        carregar_especificacoes=carregar_especificacoes_govdebt001,
        entradas_para_regra=entradas_govdebt001_para_regra,
    )
    registrar(
        tipo="janela",
        regra_cls=RegraJanelaSpec,
        construir_implementacao=construir_implementacao_janela,
        carregar_especificacoes=carregar_especificacoes_janela,
        entradas_para_regra=entradas_janela_para_regra,
    )
    registrar(
        tipo="kwarg_ausente",
        regra_cls=RegraKwargAusenteSpec,
        construir_implementacao=construir_implementacao_kwarg_ausente,
        carregar_especificacoes=carregar_especificacoes_kwarg_ausente,
        entradas_para_regra=entradas_kwarg_ausente_para_regra,
    )
    registrar(
        tipo="metrica",
        regra_cls=RegraMetricaSpec,
        construir_implementacao=construir_implementacao_metrica,
        carregar_especificacoes=carregar_especificacoes_metrica,
        entradas_para_regra=entradas_metrica_para_regra,
    )
    registrar(
        tipo="ora004",
        regra_cls=RegraOra004Spec,
        construir_implementacao=construir_implementacao_ora004,
        carregar_especificacoes=carregar_especificacoes_ora004,
        entradas_para_regra=entradas_ora004_para_regra,
    )
    registrar(
        tipo="ora005",
        regra_cls=RegraOra005Spec,
        construir_implementacao=construir_implementacao_ora005,
        carregar_especificacoes=carregar_especificacoes_ora005,
        entradas_para_regra=entradas_ora005_para_regra,
    )
    registrar(
        tipo="ora006",
        regra_cls=RegraOra006Spec,
        construir_implementacao=construir_implementacao_ora006,
        carregar_especificacoes=carregar_especificacoes_ora006,
        entradas_para_regra=entradas_ora006_para_regra,
    )
    registrar(
        tipo="pd001",
        regra_cls=RegraPd001Spec,
        construir_implementacao=construir_implementacao_pd001,
        carregar_especificacoes=carregar_especificacoes_pd001,
        entradas_para_regra=entradas_pd001_para_regra,
    )
    registrar(
        tipo="pd009",
        regra_cls=RegraPd009Spec,
        construir_implementacao=construir_implementacao_pd009,
        carregar_especificacoes=carregar_especificacoes_pd009,
        entradas_para_regra=entradas_pd009_para_regra,
    )
    registrar(
        tipo="pd010",
        regra_cls=RegraPd010Spec,
        construir_implementacao=construir_implementacao_pd010,
        carregar_especificacoes=carregar_especificacoes_pd010,
        entradas_para_regra=entradas_pd010_para_regra,
    )
    registrar(
        tipo="pd011",
        regra_cls=RegraPd011Spec,
        construir_implementacao=construir_implementacao_pd011,
        carregar_especificacoes=carregar_especificacoes_pd011,
        entradas_para_regra=entradas_pd011_para_regra,
    )
    registrar(
        tipo="perf004",
        regra_cls=RegraPerf004Spec,
        construir_implementacao=construir_implementacao_perf004,
        carregar_especificacoes=carregar_especificacoes_perf004,
        entradas_para_regra=entradas_perf004_para_regra,
    )
    registrar(
        tipo="qaauto001",
        regra_cls=RegraQaAuto001Spec,
        construir_implementacao=construir_implementacao_qaauto001,
        carregar_especificacoes=carregar_especificacoes_qaauto001,
        entradas_para_regra=entradas_qaauto001_para_regra,
    )
    registrar(
        tipo="qaauto003",
        regra_cls=RegraQaAuto003Spec,
        construir_implementacao=construir_implementacao_qaauto003,
        carregar_especificacoes=carregar_especificacoes_qaauto003,
        entradas_para_regra=entradas_qaauto003_para_regra,
    )
    registrar(
        tipo="qavis001",
        regra_cls=RegraQaVis001Spec,
        construir_implementacao=construir_implementacao_qavis001,
        carregar_especificacoes=carregar_especificacoes_qavis001,
        entradas_para_regra=entradas_qavis001_para_regra,
    )
    registrar(
        tipo="rev005",
        regra_cls=RegraRev005Spec,
        construir_implementacao=construir_implementacao_rev005,
        carregar_especificacoes=carregar_especificacoes_rev005,
        entradas_para_regra=entradas_rev005_para_regra,
    )
    registrar(
        tipo="rev006",
        regra_cls=RegraRev006Spec,
        construir_implementacao=construir_implementacao_rev006,
        carregar_especificacoes=carregar_especificacoes_rev006,
        entradas_para_regra=entradas_rev006_para_regra,
    )
    registrar(
        tipo="sec005",
        regra_cls=RegraSec005Spec,
        construir_implementacao=construir_implementacao_sec005,
        carregar_especificacoes=carregar_especificacoes_sec005,
        entradas_para_regra=entradas_sec005_para_regra,
    )
    registrar(
        tipo="sec007",
        regra_cls=RegraSec007Spec,
        construir_implementacao=construir_implementacao_sec007,
        carregar_especificacoes=carregar_especificacoes_sec007,
        entradas_para_regra=entradas_sec007_para_regra,
    )
    registrar(
        tipo="sec008",
        regra_cls=RegraSec008Spec,
        construir_implementacao=construir_implementacao_sec008,
        carregar_especificacoes=carregar_especificacoes_sec008,
        entradas_para_regra=entradas_sec008_para_regra,
    )
    registrar(
        tipo="sec009",
        regra_cls=RegraSec009Spec,
        construir_implementacao=construir_implementacao_sec009,
        carregar_especificacoes=carregar_especificacoes_sec009,
        entradas_para_regra=entradas_sec009_para_regra,
    )
    registrar(
        tipo="sre006",
        regra_cls=RegraSre006Spec,
        construir_implementacao=construir_implementacao_sre006,
        carregar_especificacoes=carregar_especificacoes_sre006,
        entradas_para_regra=entradas_sre006_para_regra,
    )
    registrar(
        tipo="sup001",
        regra_cls=RegraSup001Spec,
        construir_implementacao=construir_implementacao_sup001,
        carregar_especificacoes=carregar_especificacoes_sup001,
        entradas_para_regra=entradas_sup001_para_regra,
    )
    registrar(
        tipo="sweep001",
        regra_cls=RegraSweep001Spec,
        construir_implementacao=construir_implementacao_sweep001,
        carregar_especificacoes=carregar_especificacoes_sweep001,
        entradas_para_regra=entradas_sweep001_para_regra,
    )
    registrar(
        tipo="ui002",
        regra_cls=RegraUi002Spec,
        construir_implementacao=construir_implementacao_ui002,
        carregar_especificacoes=carregar_especificacoes_ui002,
        entradas_para_regra=entradas_ui002_para_regra,
    )
