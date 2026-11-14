"""LoRA fine-tuning of a small open code model on the prepared Text-to-SQL data.

    python -m finetune.train_lora --model Qwen/Qwen2.5-Coder-1.5B-Instruct \
        --data data/finetune/train.jsonl --out models/t2s-lora --epochs 2

Runs on one free Kaggle/Colab T4 (16 GB) for 1.5B models; add --load-4bit for 3B+.
Uses the plain transformers Trainer with explicit label masking: loss is computed
only on the assistant's SQL, never on the (long) schema prompt. Without masking,
most of the gradient goes into memorising schemas.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="any chat model with a chat template")
    ap.add_argument("--data", default="data/finetune/train.jsonl")
    ap.add_argument("--out", default="models/t2s-lora")
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=3072)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--load-4bit", action="store_true")
    ap.add_argument("--merge", action="store_true", help="also save merged full weights (for GGUF/Ollama)")
    args = ap.parse_args()

    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer,
                              TrainingArguments)

    tok = AutoTokenizer.from_pretrained(args.model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    rows = [json.loads(l) for l in Path(args.data).read_text(encoding="utf-8").splitlines() if l.strip()]
    features, too_long = [], 0
    for r in rows:
        msgs = r["messages"]
        prompt_ids = tok.apply_chat_template(msgs[:-1], add_generation_prompt=True, tokenize=True)
        full_ids = tok.apply_chat_template(msgs, tokenize=True)
        if len(full_ids) > args.max_len:
            too_long += 1
            continue
        n = len(prompt_ids)
        if full_ids[:n] != prompt_ids:  # template renders differently with the answer appended
            n = _common_prefix(full_ids, prompt_ids)
        labels = [-100] * n + full_ids[n:]
        features.append({"input_ids": full_ids, "labels": labels})
    print(f"{len(features)} training rows ({too_long} dropped for exceeding {args.max_len} tokens)")

    bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    dtype = torch.bfloat16 if bf16 else torch.float16
    kwargs = {"torch_dtype": dtype, "device_map": "auto"}
    if args.load_4bit:
        from transformers import BitsAndBytesConfig
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=dtype)
    model = AutoModelForCausalLM.from_pretrained(args.model, **kwargs)
    if args.load_4bit:
        model = prepare_model_for_kbit_training(model)
    model.gradient_checkpointing_enable()
    model.config.use_cache = False
    model = get_peft_model(model, LoraConfig(
        r=args.lora_r, lora_alpha=2 * args.lora_r, lora_dropout=0.05,
        target_modules="all-linear", task_type="CAUSAL_LM"))
    model.print_trainable_parameters()

    def collate(batch):
        width = max(len(b["input_ids"]) for b in batch)
        ids = [b["input_ids"] + [tok.pad_token_id] * (width - len(b["input_ids"])) for b in batch]
        lab = [b["labels"] + [-100] * (width - len(b["labels"])) for b in batch]
        att = [[1] * len(b["input_ids"]) + [0] * (width - len(b["input_ids"])) for b in batch]
        return {"input_ids": torch.tensor(ids), "labels": torch.tensor(lab),
                "attention_mask": torch.tensor(att)}

    targs = TrainingArguments(
        output_dir=args.out, per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum, num_train_epochs=args.epochs,
        learning_rate=args.lr, lr_scheduler_type="cosine", warmup_ratio=0.03,
        logging_steps=10, save_strategy="epoch", bf16=bf16, fp16=not bf16,
        report_to="none", remove_unused_columns=False, group_by_length=True)
    Trainer(model=model, args=targs, train_dataset=features, data_collator=collate).train()
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print(f"adapter saved to {args.out}")

    if args.merge:
        base = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=dtype)
        from peft import PeftModel
        merged = PeftModel.from_pretrained(base, args.out).merge_and_unload()
        merged.save_pretrained(f"{args.out}-merged")
        tok.save_pretrained(f"{args.out}-merged")
        print(f"merged model saved to {args.out}-merged (convert to GGUF for Ollama, see finetune/README.md)")


def _common_prefix(a: list[int], b: list[int]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


if __name__ == "__main__":
    main()
