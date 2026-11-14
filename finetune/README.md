# Fine-tuning a small model

Goal: a 1–3B open model that writes SQL for your pipeline, compared honestly against the API models on accuracy, cost and latency.

## 1. Get BIRD train

Download the BIRD **train** set (questions + `train_databases/`). Never train on dev: your dev numbers would stop meaning anything. `prepare_data.py` refuses files named `dev*`.

## 2. (Optional) Find the hard cases

Run the pipeline over a sample of *train* to find questions the API models get wrong:

```bash
python -m eval.run --dataset bird/train/train.json --db-root bird/train/train_databases \
    --limit 1500 --name train-sample
```

## 3. Build the dataset

```bash
python -m finetune.prepare_data --dataset bird/train/train.json \
    --db-root bird/train/train_databases --out data/finetune/train.jsonl \
    --failures runs/train-sample/results.jsonl --max-examples 6000
```

Each row uses the exact prompt the pipeline sends at inference (same retrieval and schema rendering), and the assistant turn is the gold SQL.

## 4. Train (Kaggle / Colab, free T4)

```bash
pip install -r finetune/requirements.txt
python -m finetune.train_lora --model Qwen/Qwen2.5-Coder-1.5B-Instruct \
    --data data/finetune/train.jsonl --out models/t2s-lora --epochs 2 --merge
```

Any small instruct/coder model with a chat template works; check the Hugging Face hub for the current best small coder model. For 3B+ on a T4 add `--load-4bit`. A 1.5B model on ~6k examples takes roughly 1–2 hours on a T4.

Things to record in your notes: training loss curve, how many rows were dropped for length, and the hardware and time used.

## 5. Serve it through your gateway

Convert the merged model to GGUF with llama.cpp and load it in Ollama:

```bash
python llama.cpp/convert_hf_to_gguf.py models/t2s-lora-merged --outfile t2s.gguf --outtype q8_0
cat > Modelfile <<'EOF'
FROM ./t2s.gguf
PARAMETER temperature 0
EOF
ollama create t2s-sql -f Modelfile
```

Ollama exposes an OpenAI-compatible API at `http://localhost:11434/v1`. Add it to your LLM Gateway as a provider (no free-tier quota to track), then point a candidate at it in `config.toml`:

```toml
[[generation.candidates]]
model = "t2s-sql"
prompt = "direct"
temperature = 0.0
```

## 6. Compare

Run the same fixed sample with each model as the only candidate (judge off) to compare raw generation quality:

```bash
python -m eval.run --dataset bird/dev.json --db-root bird/dev_databases --limit 300 \
  --name ft-only --set judge=false --set distinguish=false \
  --set 'candidates=[{"model":"t2s-sql","prompt":"direct","temperature":0}]'
```

> `--set candidates=...` passes a JSON list; `run.py` converts it into candidate specs.

Then report a table: model, execution accuracy, cost per 1k questions, p50 latency. A small model that is 10 points worse but free and 5× faster is a legitimate result if you explain where it fits (for example, as one of the five candidates).
