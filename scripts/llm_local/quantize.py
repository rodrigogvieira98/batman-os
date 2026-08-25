# ruff: noqa: I001 — Unsloth precisa ser importado ANTES das libs que ele
# faz patch; ordem de import e deliberada (mesma nota do train.py).
"""F2 — funde o adapter LoRA no Qwen base e exporta GGUF quantizado.

Adaptacao direta do `radar-preditivo/scripts/llm_poc/quantize.py`, com o
default de quantizacao em **q6_k**: no experimento do radar, Q4_K_M
degradou fidelidade de forma mensuravel (impacto 88,7%->50,0%) e o Q6_K
recuperou — nao repetir o teste.

Roda dentro do venv do WSL (a fusao usa GPU; a quantizacao usa llama.cpp,
que o Unsloth baixa/compila na 1a chamada):
    python scripts/llm_local/quantize.py \
        --adapter $HOME/llm_local_batman/checkpoints/final \
        --out $HOME/llm_local_batman/gguf
Depois copiar o .gguf para o Windows e apontar LLM_LOCAL_GGUF_PATH.
"""

from __future__ import annotations

import unsloth  # noqa: F401

import argparse
from pathlib import Path

from unsloth import FastLanguageModel

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "llm_local"
BASE_MODEL = "unsloth/Qwen2.5-3B-Instruct-bnb-4bit"
MAX_SEQ_LENGTH = 2048


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", type=str, default=str(DATA_DIR / "checkpoints" / "final"))
    parser.add_argument("--method", type=str, default="q6_k")
    parser.add_argument("--out", type=str, default=str(DATA_DIR / "gguf"))
    parser.add_argument(
        "--max-mem",
        type=float,
        default=0.75,
        help="maximum_memory_usage do exportador Unsloth (reduzir p/ 0.45 "
        "em maquina com pouca RAM — o merge 16-bit e o pico)",
    )
    args = parser.parse_args()

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL,
        max_seq_length=MAX_SEQ_LENGTH,
        load_in_4bit=True,
    )
    # get_peft_model + set_peft_model_state_dict (nao model.load_adapter) —
    # precisa ser um PeftModel de verdade, senao o exportador GGUF do
    # Unsloth pula o merge do LoRA em silencio.
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        lora_alpha=16,
        lora_dropout=0.0,
        bias="none",
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
        use_gradient_checkpointing="unsloth",
        random_state=3407,
    )
    from peft import set_peft_model_state_dict
    from safetensors.torch import load_file

    adapter_weights = load_file(str(Path(args.adapter) / "adapter_model.safetensors"))
    set_peft_model_state_dict(model, adapter_weights)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Fundindo adapter + exportando GGUF ({args.method}) em {out_dir} ...")
    model.save_pretrained_gguf(
        str(out_dir),
        tokenizer,
        quantization_method=args.method,
        maximum_memory_usage=args.max_mem,
    )
    print("Concluido.")


if __name__ == "__main__":
    main()
