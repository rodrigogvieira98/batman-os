"""F2 — fine-tuning QLoRA do LLM local do Batman OS.

Adaptação direta (hiperparâmetros INTACTOS) da receita validada no
radar-preditivo (`scripts/llm_poc/train.py`, rodadas PoC + R101): Unsloth
+ TRL SFTTrainer, base Qwen2.5-3B-Instruct em 4-bit, LoRA r=16.

Roda dentro do venv do WSL (Unsloth + CUDA), nao no venv Windows:
    cd $HOME/llm_poc && source .venv/bin/activate
    python /mnt/c/.../batman-os/scripts/llm_local/train.py \
        --data-dir ~/llm_local_batman/data \
        --output-dir ~/llm_local_batman/checkpoints [--max-steps 10]

`--data-dir` deve apontar para uma CoPIA local (home do WSL) de
data/llm_local/{train,holdout}.jsonl — I/O de /mnt/c e lento demais para
dataloader (mesma licao da rodada R101).
"""

# ruff: noqa: I001 — Unsloth precisa ser importado ANTES de trl/
# transformers/peft (faz patch dessas libs); ordem de import e deliberada.
from __future__ import annotations

import unsloth  # noqa: F401

import argparse
import json
from pathlib import Path

from datasets import Dataset
from trl import SFTConfig, SFTTrainer
from unsloth import FastLanguageModel, is_bfloat16_supported

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR_DEFAULT = REPO_ROOT / "data" / "llm_local"
BASE_MODEL = "unsloth/Qwen2.5-3B-Instruct-bnb-4bit"
MAX_SEQ_LENGTH = 2048


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-steps", type=int, default=None, help="sanity rapido (ex: 10)")
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR_DEFAULT)
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR_DEFAULT / "checkpoints")
    parser.add_argument("--resume-from", type=str, default=None)
    args = parser.parse_args()

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL,
        max_seq_length=MAX_SEQ_LENGTH,
        load_in_4bit=True,
    )
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

    train_rows = load_jsonl(args.data_dir / "train.jsonl")
    train_ds = Dataset.from_list([{"messages": r["messages"]} for r in train_rows])
    print(f"dataset de treino: {len(train_ds)} exemplos ({args.data_dir})")

    def formatting_func(example: dict) -> list[str]:
        # O patch do Unsloth chama isso ora com 1 linha (messages = lista de
        # dicts), ora em lote (lista de listas) — sempre devolver lista.
        messages = example["messages"]
        if messages and isinstance(messages[0], dict):
            return [
                tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
            ]
        return [
            tokenizer.apply_chat_template(m, tokenize=False, add_generation_prompt=False)
            for m in messages
        ]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=train_ds,
        formatting_func=formatting_func,
        args=SFTConfig(
            max_length=MAX_SEQ_LENGTH,
            dataset_num_proc=2,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=8,
            warmup_steps=10,
            num_train_epochs=args.epochs,
            max_steps=args.max_steps if args.max_steps else -1,
            learning_rate=2e-4,
            fp16=not is_bfloat16_supported(),
            bf16=is_bfloat16_supported(),
            logging_steps=10,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            seed=3407,
            output_dir=str(output_dir),
            report_to="none",
            save_strategy="steps",
            save_steps=25,
            save_total_limit=5,
        ),
    )

    result = trainer.train(resume_from_checkpoint=args.resume_from)
    print("train_runtime_s:", result.metrics.get("train_runtime"))
    print("train_loss:", result.metrics.get("train_loss"))

    final_dir = output_dir / "final"
    model.save_pretrained(str(final_dir))
    tokenizer.save_pretrained(str(final_dir))
    print("Adapter LoRA salvo em:", final_dir)


if __name__ == "__main__":
    main()
