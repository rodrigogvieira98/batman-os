"""F1 — monta o dataset de treino do LLM local do Batman OS.

Duas fontes, um formato (JSONL chat Qwen: system/user/assistant, em que
system = `batman_os.llm.prompts.SYSTEM_PROMPT` e user =
`mensagem_usuario(ponto)` LITERAIS — o que o aluno vê no treino é
byte-a-byte o que o `LocalLlmGateway` envia em produção):

- Fonte A (fatos operacionais do Batman legado do radar-preditivo):
  outcomes resolvidos -> `remediar`; fingerprints suprimidos ->
  `suprimir-fp`; regras em deferred.json -> `adiar`. O gold vem do que
  REALMENTE aconteceu em operação, não de opinião.
- Fonte B (professor): `executar_scan` do próprio batman-os roda nos
  repos reais; cada achado vira um DecisionPoint que o professor Claude
  rotula seguindo `docs/llm_local/rubrica_professor_batman.md`, com
  cache em disco por hash do ponto (reruns não pagam de novo).

Split: holdout = TODOS os exemplos do repo B (generalização
cross-repo, substituto honesto do walk-forward até existir tráfego real)
+ fração estratificada por (fonte, opção gold) do restante.

Uso típico:
  python scripts/llm_local/prepare_dataset.py --fonte-a
  python scripts/llm_local/prepare_dataset.py --fonte-b --professor-max 25
  python scripts/llm_local/prepare_dataset.py --fonte-a --fonte-b --professor-max 0
    (coleta achados e deixa em pending_professor.jsonl, sem gastar API)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from batman_os.foundation.types import DecisionOption, EscalationPolicy, Reversibilidade
from batman_os.kernel.decision_engine import RespostaLlmCandidata
from batman_os.kernel.planning_engine import DecisionPoint
from batman_os.llm.prompts import SYSTEM_PROMPT, mensagem_usuario
from batman_os.llm.schema_utils import schema_resposta_para_ponto

REPO_BATMAN_OS = Path(__file__).resolve().parents[2]
#: Raizes dos repositorios reais usados como corpus da fonte B. Nao ha default
#: valido fora da maquina do autor — passe `--repo-a-root` e `--repo-b-root`,
#: ou exporte BATMAN_CORPUS_A / BATMAN_CORPUS_B.
REPO_A_ROOT_DEFAULT = Path(os.environ.get("BATMAN_CORPUS_A", "../repo-a"))
REPO_B_ROOT_DEFAULT = Path(os.environ.get("BATMAN_CORPUS_B", "../repo-b"))
OUT_DIR_DEFAULT = REPO_BATMAN_OS / "data" / "llm_local"
RUBRICA_PATH = REPO_BATMAN_OS / "docs" / "llm_local" / "rubrica_professor_batman.md"

MODELO_PROFESSOR_DEFAULT = "claude-sonnet-5"
SEED = 42
FRACAO_HOLDOUT = 0.10
MAX_EVIDENCIA = 300

OPCOES_CANONICAS = [
    DecisionOption(id="remediar", descricao="Aplicar a remediacao e resolver o achado"),
    DecisionOption(
        id="suprimir-fp", descricao="Suprimir como falso positivo (fingerprint ignorado)"
    ),
    DecisionOption(id="adiar", descricao="Adiar com justificativa registrada e revisao futura"),
    DecisionOption(
        id="escalar-humano", descricao="Escalar para decisao humana (contexto insuficiente)"
    ),
]
_OPCAO_POR_ID = {opcao.id: opcao for opcao in OPCOES_CANONICAS}

POLITICA_DATASET = EscalationPolicy(
    confidence_threshold=0.8,
    preferred_escalation="llm",
    max_llm_retries=2,
    reversibility=Reversibilidade.REVERSIVEL,
)


def _truncar(texto: str, limite: int = MAX_EVIDENCIA) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else texto[: limite - 3] + "..."


def _ponto(pergunta_ctx: dict[str, Any]) -> DecisionPoint:
    codigo = pergunta_ctx.get("codigo", "?")
    severidade = pergunta_ctx.get("severidade", "?")
    return DecisionPoint(
        pergunta=(
            f"O achado {codigo} (severidade {severidade}) deve ser remediado, "
            "suprimido como falso positivo, adiado ou escalado para um humano?"
        ),
        opcoes=OPCOES_CANONICAS,
        escalation_policy=POLITICA_DATASET,
        dados=pergunta_ctx,
    )


def _exemplo(
    ponto: DecisionPoint,
    opcao_id: str,
    confidence: float,
    evidencia: str,
    fonte: str,
    repo: str,
) -> dict[str, Any]:
    resposta = RespostaLlmCandidata(
        opcao=_OPCAO_POR_ID[opcao_id],
        confidence=confidence,
        evidencia_bruta=_truncar(evidencia),
    )
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": mensagem_usuario(ponto)},
            {"role": "assistant", "content": resposta.model_dump_json()},
        ],
        "meta": {
            "fonte": fonte,
            "repo": repo,
            "codigo": ponto.dados.get("codigo", "?"),
            "opcao_gold": opcao_id,
            "confidence_gold": confidence,
        },
    }


# ---------------------------------------------------------------- Fonte A


def _carregar_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def gerar_fonte_a(repo_a_root: Path) -> list[dict[str, Any]]:
    """Fatos operacionais do Batman legado -> exemplos gold.
    Precedencia por fingerprint: suprimir-fp > remediar (outcome resolvido)
    > adiar (regra deferida). Cada fingerprint entra no maximo uma vez."""
    batman = repo_a_root / "Batman"
    ledger = _carregar_json(batman / "ledger.json").get("entries", {})
    supressoes: list[str] = _carregar_json(batman / "config" / "supressoes.json")
    deferred: dict[str, Any] = _carregar_json(batman / "config" / "deferred.json").get(
        "deferred", {}
    )

    exemplos: list[dict[str, Any]] = []
    usados: set[str] = set()

    def _ctx_base(dados: dict[str, Any]) -> dict[str, Any]:
        return {
            "codigo": dados.get("codigo", "?"),
            "agente": dados.get("agente", "?"),
            "severidade": dados.get("severidade", "?"),
            "titulo": dados.get("titulo", ""),
            "descricao": dados.get("descricao", ""),
        }

    # 1) Supressoes (fingerprints planos; contexto vem do ledger)
    for fp in supressoes:
        entry = ledger.get(fp)
        if entry is None or fp in usados:
            continue
        usados.add(fp)
        exemplos.append(
            _exemplo(
                _ponto(_ctx_base(entry)),
                "suprimir-fp",
                0.9,
                "O DEV revisou este fingerprint e o suprimiu como falso positivo — "
                "o padrao detectado nao se aplica a este projeto.",
                fonte="legado-supressao",
                repo="radar-preditivo",
            )
        )

    # 2) Outcomes resolvidos (fato: remediacao aplicada e verificada)
    outcomes_dir = batman / "config" / "outcomes"
    for arquivo in sorted(outcomes_dir.glob("*.json")):
        outcome = _carregar_json(arquivo)
        fp = outcome.get("fingerprint", "")
        if fp in usados or not outcome.get("resolvido"):
            continue
        usados.add(fp)
        ctx = _ctx_base(outcome)
        ctx["causa"] = _truncar(outcome.get("causa", ""), 400)
        ctx["remediacao"] = _truncar(outcome.get("remediacao", ""), 400)
        evidencia = outcome.get("causa") or "Achado verdadeiro-positivo remediado em operacao."
        confianca = 0.95 if outcome.get("verificacao_passou") else 0.85
        exemplos.append(
            _exemplo(
                _ponto(ctx),
                "remediar",
                confianca,
                evidencia,
                fonte="legado-outcome",
                repo="radar-preditivo",
            )
        )

    # 3) Regras deferidas (por codigo; contexto = entries do ledger dessa regra)
    for codigo, info in deferred.items():
        candidatos = [
            (fp, e) for fp, e in ledger.items() if e.get("codigo") == codigo and fp not in usados
        ]
        for fp, entry in candidatos[:3]:
            usados.add(fp)
            ctx = _ctx_base(entry)
            ctx["decisao_registrada"] = _truncar(str(info.get("reason", "")), 400)
            ctx["revisao_sugerida"] = str(info.get("revisao_sugerida", ""))
            exemplos.append(
                _exemplo(
                    _ponto(ctx),
                    "adiar",
                    0.9,
                    f"Debito deliberado registrado pelo DEV: {info.get('reason', '')} "
                    f"(revisao: {info.get('revisao_sugerida', '?')}).",
                    fonte="legado-deferred",
                    repo="radar-preditivo",
                )
            )
    return exemplos


# ---------------------------------------------------------------- Fonte C


def gerar_fonte_c(pending_path: Path, alvo: int = 70) -> list[dict[str, Any]]:
    """Fonte C — exemplos SINTETICOS de baixo-contexto rotulados
    escalar-humano. Pega achados reais e REMOVE o contexto decisivo
    (agente/descricao/causa/remediacao/arquivo), deixando so
    codigo+severidade+titulo: genuinamente indecidivel, entao a resposta
    correta e DEFERIR (rubrica: 'contexto vago e bloqueante'). Ensina o
    conceito de defer com inputs UNICOS — o oversample de 20 na v2 nao
    ensinou, logprob provou que o modelo nao sabe deferir. Cada (codigo,
    titulo) entra no maximo uma vez. Confianca espalhada na banda de
    escalar-humano (0.60-0.74)."""
    if not pending_path.exists():
        return []
    contextos = [
        json.loads(linha)
        for linha in pending_path.read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]
    rng = random.Random(SEED + 2)
    rng.shuffle(contextos)
    confs = [0.60, 0.64, 0.68, 0.72, 0.74]
    vistos: set[tuple[str, str]] = set()
    exemplos: list[dict[str, Any]] = []
    for item in contextos:
        orig = item["ctx"]
        chave = (orig.get("codigo", "?"), orig.get("titulo", "")[:40])
        if chave in vistos:
            continue
        vistos.add(chave)
        ctx = {  # contexto decisivo removido de proposito
            "codigo": orig.get("codigo", "?"),
            "severidade": orig.get("severidade", "?"),
            "titulo": orig.get("titulo", ""),
        }
        exemplos.append(
            _exemplo(
                _ponto(ctx),
                "escalar-humano",
                confs[len(exemplos) % len(confs)],
                "Contexto insuficiente: so codigo, severidade e titulo — sem causa, "
                "arquivo ou descricao que permita decidir. Escalar para humano.",
                fonte="sintetico-lowctx",
                repo="sintetico",
            )
        )
        if len(exemplos) >= alvo:
            break
    return exemplos


# ---------------------------------------------------------------- Fonte B


def coletar_achados(repos: dict[str, Path], cap_por_regra: int) -> list[dict[str, Any]]:
    """Roda o scan do batman-os em cada repo e devolve contextos de
    DecisionPoint (sem rotulo), com cap por regra para diversidade."""
    from batman_os.cli.scan_command import executar_scan

    contextos: list[dict[str, Any]] = []
    for nome, root in repos.items():
        if not root.exists():
            print(f"[fonte-b] repo ausente, pulando: {nome} ({root})")
            continue
        print(f"[fonte-b] escaneando {nome} ({root}) ...")
        resultado = executar_scan(root, paralelo=True)
        por_regra: Counter[str] = Counter()
        aproveitados = 0
        for achado in resultado.achados:
            dados = asdict(achado)
            codigo = dados.get("codigo", "?")
            if por_regra[codigo] >= cap_por_regra:
                continue
            por_regra[codigo] += 1
            aproveitados += 1
            ctx = {
                "codigo": codigo,
                "agente": dados.get("agente", "?"),
                "severidade": dados.get("severidade", "?"),
                "titulo": dados.get("titulo", ""),
                "descricao": _truncar(dados.get("descricao", ""), 500),
                "causa": _truncar(dados.get("causa", ""), 400),
                "remediacao": _truncar(dados.get("remediacao", ""), 400),
                "arquivo": dados.get("arquivo", ""),
            }
            contextos.append({"repo": nome, "ctx": ctx})
        print(
            f"[fonte-b] {nome}: {len(resultado.achados)} achados, "
            f"{aproveitados} aproveitados (cap {cap_por_regra}/regra)"
        )
    return contextos


def _hash_ponto(ctx: dict[str, Any], modelo: str) -> str:
    base = json.dumps({"ctx": ctx, "m": modelo}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def rotular_com_professor(
    contextos: list[dict[str, Any]],
    out_dir: Path,
    modelo: str,
    max_chamadas: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rotula ate `max_chamadas` contextos (cache em disco conta como
    gratis); o restante vai para pending_professor.jsonl."""
    import anthropic
    from dotenv import load_dotenv

    load_dotenv(REPO_BATMAN_OS / ".env")  # ANTHROPIC_API_KEY do .env do repo
    rubrica = RUBRICA_PATH.read_text(encoding="utf-8")
    cache_dir = out_dir / "professor_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic()

    rng = random.Random(SEED)
    embaralhados = list(contextos)
    rng.shuffle(embaralhados)

    exemplos: list[dict[str, Any]] = []
    pendentes: list[dict[str, Any]] = []
    chamadas_pagas = 0

    for indice, item in enumerate(embaralhados):
        ctx, repo = item["ctx"], item["repo"]
        chave = _hash_ponto(ctx, modelo)
        cache_path = cache_dir / f"{chave}.json"
        bruto: dict[str, Any] | None = None

        if cache_path.exists():
            bruto = _carregar_json(cache_path)
        elif chamadas_pagas < max_chamadas:
            ponto = _ponto(ctx)
            try:
                resposta_api = client.messages.create(
                    model=modelo,
                    max_tokens=1024,
                    system=[
                        {"type": "text", "text": SYSTEM_PROMPT + "\n\n=== RUBRICA ===\n" + rubrica}
                    ],
                    messages=[{"role": "user", "content": mensagem_usuario(ponto)}],
                    tools=[
                        {
                            "name": "resolver_decision_point",
                            "description": "Retorna o gabarito estruturado do DecisionPoint.",
                            "input_schema": schema_resposta_para_ponto(ponto),
                        }
                    ],
                    tool_choice={"type": "tool", "name": "resolver_decision_point"},
                )
            except Exception as exc:
                msg = str(exc).lower()
                if "usage limit" in msg or "regain access" in msg:
                    # LLM_LIMIT01 (mesmo padrao do AnthropicLlmGateway): limite
                    # mensal da conta e permanente ate a virada do ciclo —
                    # re-tentar so espalha 400s. Tudo que sobrou vira pendente.
                    print(f"[professor] LIMITE DE CONTA atingido — abortando rotulagem: {exc}")
                    pendentes.append(item)
                    pendentes.extend(embaralhados[indice + 1 :])
                    break
                print(f"[professor] ERRO na chamada ({chave}): {exc}")
                pendentes.append(item)
                continue
            chamadas_pagas += 1
            bloco = next((b for b in resposta_api.content if b.type == "tool_use"), None)
            if bloco is None:
                pendentes.append(item)
                continue
            bruto = dict(bloco.input)  # type: ignore[arg-type]
            cache_path.write_text(json.dumps(bruto, ensure_ascii=False), encoding="utf-8")
        else:
            pendentes.append(item)
            continue

        try:
            resposta = RespostaLlmCandidata.model_validate(bruto)
        except Exception as exc:
            print(f"[professor] gabarito invalido ({chave}): {exc}")
            pendentes.append(item)
            continue
        if resposta.opcao.id not in _OPCAO_POR_ID:
            print(f"[professor] opcao fora do enum ({chave}): {resposta.opcao.id}")
            pendentes.append(item)
            continue
        exemplos.append(
            _exemplo(
                _ponto(ctx),
                resposta.opcao.id,
                resposta.confidence,
                resposta.evidencia_bruta,
                fonte="professor",
                repo=repo,
            )
        )

    print(
        f"[professor] {len(exemplos)} rotulados ({chamadas_pagas} chamadas pagas), "
        f"{len(pendentes)} pendentes"
    )
    return exemplos, pendentes


# ------------------------------------------------------------------ split


def dividir(exemplos: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Holdout estratificado por OPCAO_GOLD — cada classe (inclusive as
    minoritarias suprimir-fp/escalar-humano) fica representada no holdout,
    senao a acuracia por-classe fica sem amostra para medir. Sem caso
    especial de repo (o repo B saiu do dataset — e outro projeto)."""
    rng = random.Random(SEED)
    # Sinteticos (Fonte C) NUNCA entram no holdout — o holdout mede
    # desempenho em achados REAIS. Vao direto pro train.
    train_forcado = [e for e in exemplos if e["meta"]["repo"] == "sintetico"]
    reais = [e for e in exemplos if e["meta"]["repo"] != "sintetico"]
    por_classe: dict[str, list[dict[str, Any]]] = {}
    for exemplo in reais:
        por_classe.setdefault(exemplo["meta"]["opcao_gold"], []).append(exemplo)

    train: list[dict[str, Any]] = list(train_forcado)
    holdout: list[dict[str, Any]] = []
    for grupo in por_classe.values():
        rng.shuffle(grupo)
        if len(grupo) >= 8:
            n_holdout = max(3, round(len(grupo) * 0.15))
        elif len(grupo) >= 4:
            n_holdout = 2
        else:
            n_holdout = 0  # classe minuscula fica toda no train
        holdout.extend(grupo[:n_holdout])
        train.extend(grupo[n_holdout:])
    rng.shuffle(train)
    rng.shuffle(holdout)
    return train, holdout


def rebalancear_train(
    train: list[dict[str, Any]], cap_majoritaria: int, piso_minoritaria: int
) -> list[dict[str, Any]]:
    """Rebalanceia SO o train (o holdout fica intocado — nunca duplicar la,
    seria vazamento). Downsample classes acima de `cap_majoritaria`;
    oversample (duplicando exemplos) classes abaixo de `piso_minoritaria`.
    Ataca o vies de classe majoritaria que fez a v1 colapsar tudo em
    'remediar'. Deterministico (seed)."""
    rng = random.Random(SEED + 1)
    por_classe: dict[str, list[dict[str, Any]]] = {}
    for exemplo in train:
        por_classe.setdefault(exemplo["meta"]["opcao_gold"], []).append(exemplo)

    saida: list[dict[str, Any]] = []
    for grupo in por_classe.values():
        rng.shuffle(grupo)
        if len(grupo) > cap_majoritaria:
            grupo = grupo[:cap_majoritaria]
        elif 0 < len(grupo) < piso_minoritaria:
            base = list(grupo)
            i = 0
            while len(grupo) < piso_minoritaria:
                copia = dict(base[i % len(base)])
                copia["meta"] = {**copia["meta"], "oversampled": True}
                grupo.append(copia)
                i += 1
        saida.extend(grupo)
    rng.shuffle(saida)
    return saida


def _chave_exemplo(exemplo: dict[str, Any]) -> str:
    """Identidade estavel de um exemplo = a mensagem de usuario (o
    DecisionPoint renderizado), unica por achado. Base do anti-leak entre
    train e holdout congelado."""
    for msg in exemplo.get("messages", []):
        if msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def _gravar_jsonl(path: Path, linhas: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for linha in linhas:
            fh.write(json.dumps(linha, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fonte-a", action="store_true", help="gera exemplos do Batman legado")
    parser.add_argument("--fonte-b", action="store_true", help="scan + professor nos repos reais")
    parser.add_argument(
        "--fonte-c",
        action="store_true",
        help="gera exemplos sinteticos de baixo-contexto -> escalar-humano (defer)",
    )
    parser.add_argument("--fonte-c-alvo", type=int, default=70)
    parser.add_argument("--repo-a-root", type=Path, default=REPO_A_ROOT_DEFAULT)
    parser.add_argument("--repo-b-root", type=Path, default=REPO_B_ROOT_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR_DEFAULT)
    parser.add_argument(
        "--professor-max",
        type=int,
        default=0,
        help="maximo de chamadas PAGAS ao professor (0 = so coleta)",
    )
    parser.add_argument("--professor-model", default=MODELO_PROFESSOR_DEFAULT)
    parser.add_argument("--cap-por-regra", type=int, default=6)
    parser.add_argument(
        "--scan-repos",
        default="batman-os",
        help="quais repos escanear na fonte B (lista separada por virgula). "
        "o repo B NAO entra (projeto separado); o repo A e LENTO (.venv/data).",
    )
    parser.add_argument(
        "--balancear",
        action="store_true",
        help="rebalanceia o train (downsample majoritarias / oversample minoritarias)",
    )
    parser.add_argument("--cap-majoritaria", type=int, default=130)
    parser.add_argument("--piso-minoritaria", type=int, default=80)
    parser.add_argument(
        "--holdout-congelado",
        type=Path,
        default=None,
        help="usa este holdout.jsonl exato (freeze) e poe todo o resto no train, "
        "excluindo do train qualquer exemplo identico a um do holdout (anti-leak)",
    )
    parser.add_argument(
        "--pendentes-como-fonte-b",
        type=Path,
        default=None,
        help="pula o scan e usa os contextos deste pending_professor.jsonl "
        "(garante hash identico ao dos rotulos offline ja aplicados)",
    )
    args = parser.parse_args()

    if not args.fonte_a and not args.fonte_b and not args.fonte_c:
        parser.error("informe --fonte-a, --fonte-b e/ou --fonte-c")

    exemplos: list[dict[str, Any]] = []
    if args.fonte_a:
        fonte_a = gerar_fonte_a(args.repo_a_root)
        print(f"[fonte-a] {len(fonte_a)} exemplos ({Counter(e['meta']['fonte'] for e in fonte_a)})")
        exemplos.extend(fonte_a)

    if args.fonte_c:
        pending = args.pendentes_como_fonte_b or (args.out_dir / "pending_professor.jsonl")
        fonte_c = gerar_fonte_c(pending, args.fonte_c_alvo)
        print(f"[fonte-c] {len(fonte_c)} exemplos sinteticos low-context -> escalar-humano")
        exemplos.extend(fonte_c)

    if args.fonte_b:
        if args.pendentes_como_fonte_b is not None:
            contextos = [
                json.loads(linha)
                for linha in args.pendentes_como_fonte_b.read_text(encoding="utf-8").splitlines()
                if linha.strip()
            ]
            print(f"[fonte-b] {len(contextos)} contextos carregados de pendentes (sem scan)")
        else:
            disponiveis = {
                "repo-a": args.repo_a_root,
                "repo-b": args.repo_b_root,
                "batman-os": REPO_BATMAN_OS,
            }
            selecionados = [nome.strip() for nome in args.scan_repos.split(",") if nome.strip()]
            repos = {nome: disponiveis[nome] for nome in selecionados}
            contextos = coletar_achados(repos, args.cap_por_regra)
        rotulados, pendentes = rotular_com_professor(
            contextos, args.out_dir, args.professor_model, args.professor_max
        )
        exemplos.extend(rotulados)
        _gravar_jsonl(args.out_dir / "pending_professor.jsonl", pendentes)

    if not exemplos:
        print("nenhum exemplo gerado — nada a gravar alem de pendentes")
        return 0

    if args.holdout_congelado is not None:
        holdout = [
            json.loads(linha)
            for linha in args.holdout_congelado.read_text(encoding="utf-8").splitlines()
            if linha.strip()
        ]
        chaves_holdout = {_chave_exemplo(e) for e in holdout}
        train = [e for e in exemplos if _chave_exemplo(e) not in chaves_holdout]
        print(f"[split] holdout congelado ({len(holdout)}); train={len(train)} (anti-leak)")
    else:
        train, holdout = dividir(exemplos)

    if args.balancear:
        antes = Counter(e["meta"]["opcao_gold"] for e in train)
        train = rebalancear_train(train, args.cap_majoritaria, args.piso_minoritaria)
        depois = Counter(e["meta"]["opcao_gold"] for e in train)
        print(f"[balancear] train antes={dict(antes)} depois={dict(depois)}")

    _gravar_jsonl(args.out_dir / "train.jsonl", train)
    _gravar_jsonl(args.out_dir / "holdout.jsonl", holdout)

    stats = {
        "total": len(exemplos),
        "train": len(train),
        "holdout": len(holdout),
        "por_fonte": dict(Counter(e["meta"]["fonte"] for e in exemplos)),
        "por_opcao": dict(Counter(e["meta"]["opcao_gold"] for e in exemplos)),
        "por_repo": dict(Counter(e["meta"]["repo"] for e in exemplos)),
        "holdout_por_opcao": dict(Counter(e["meta"]["opcao_gold"] for e in holdout)),
    }
    (args.out_dir / "dataset_stats.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
