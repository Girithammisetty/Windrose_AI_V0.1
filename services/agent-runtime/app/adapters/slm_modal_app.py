"""Modal GPU application for SLM distillation (milestone 3, `modal` backend).

This module is DEPLOYED to Modal, not imported by the running service:

    pip install modal && modal token new          # one-time, your Modal account
    modal deploy services/agent-runtime/app/adapters/slm_modal_app.py

After that a deployed function named ``train_lora`` exists in the Modal app
below, and agent-runtime's ``ModalGpuTrainer`` (adapters/modal_trainer.py) calls
it. Nothing here runs in-cluster: the heavy ML stack (torch/peft/trl/bnb) is
installed into the MODAL image, so agent-runtime only ever needs the thin
``modal`` client SDK.

Why Modal: the distillation job is bursty (minutes, only when enough human
corrections have accumulated), so a per-second serverless GPU is far cheaper
than a standing GPU node pool — and Hetzner, the cost-first target, has no GPUs
at all.

Data flow (deliberately one-way): the caller passes the frozen SFT corpus INLINE
as JSONL, so Modal needs no network path into MinIO/MLflow/Postgres. The trained
LoRA adapter is written to a Modal Volume and its reference returned. Persisting
it into the platform object store + serving it as an ai-gateway rung is the next
increment (see TrainingJobService.promote's note).
"""

from __future__ import annotations

import modal

APP_NAME = "windrose-slm-trainer"

# Pinned so a rebuild is deliberate: this recipe is validated against these
# versions, and silent upstream API drift (TRL in particular) breaks training.
TRAINING_IMAGE = modal.Image.debian_slim(python_version="3.11").pip_install(
    "torch==2.4.1",
    "transformers==4.44.2",
    "peft==0.13.0",
    "trl==0.11.1",
    "datasets==2.21.0",
    "accelerate==0.34.2",
    "bitsandbytes==0.43.3",
)

# Adapters persist here across runs; ModalGpuTrainer returns a modal:// URI into
# this volume as the artifact reference.
ADAPTER_VOLUME = modal.Volume.from_name("windrose-slm-adapters", create_if_missing=True)
ADAPTER_ROOT = "/adapters"

app = modal.App(APP_NAME)


@app.function(
    image=TRAINING_IMAGE,
    gpu="A10G",  # 24GB — enough for QLoRA on a 7-8B student (see design doc)
    timeout=60 * 60,  # a distillation run is minutes; cap the bill at 1h
    volumes={ADAPTER_ROOT: ADAPTER_VOLUME},
)
def train_lora(base_model: str, sft_jsonl: str, params: dict) -> dict:
    """Run one QLoRA fine-tune and return a real artifact reference.

    Returns {adapter_path, checksum, rows, metrics}. Raises on failure — the
    caller maps that to a failed training job rather than a fabricated adapter.
    """
    import hashlib
    import json
    import os

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    rows = [json.loads(line) for line in sft_jsonl.splitlines() if line.strip()]
    if not rows:
        raise ValueError("empty SFT corpus: refusing to train on zero examples")

    # The corpus is the milestone-2 chat-format export: {"messages": [...]}.
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    texts = [
        tokenizer.apply_chat_template(r["messages"], tokenize=False)
        for r in rows
        if r.get("messages")
    ]
    dataset = Dataset.from_dict({"text": texts})

    # 4-bit QLoRA: fits a 7-8B student on a single 24GB card.
    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    peft_config = LoraConfig(
        r=int(params.get("lora_r", 16)),
        lora_alpha=int(params.get("lora_alpha", 32)),
        lora_dropout=float(params.get("lora_dropout", 0.05)),
        bias="none",
        task_type="CAUSAL_LM",
    )

    run_id = hashlib.sha256(sft_jsonl.encode("utf-8")).hexdigest()[:16]
    out_dir = os.path.join(ADAPTER_ROOT, base_model.replace("/", "_"), run_id)

    trainer = SFTTrainer(
        model=base_model,
        train_dataset=dataset,
        peft_config=peft_config,
        args=SFTConfig(
            output_dir=out_dir,
            num_train_epochs=float(params.get("epochs", 3)),
            per_device_train_batch_size=int(params.get("batch_size", 2)),
            gradient_accumulation_steps=int(params.get("grad_accum", 4)),
            learning_rate=float(params.get("learning_rate", 2e-4)),
            bf16=True,
            logging_steps=10,
            save_strategy="no",
            report_to=[],
            dataset_text_field="text",
            max_seq_length=int(params.get("max_seq_length", 2048)),
            model_init_kwargs={"quantization_config": quant, "torch_dtype": torch.bfloat16},
        ),
    )
    result = trainer.train()
    trainer.save_model(out_dir)  # LoRA adapter weights only (small)
    ADAPTER_VOLUME.commit()

    # Checksum the adapter weights so the platform can verify the artifact it
    # later serves is the one this run produced.
    digest = hashlib.sha256()
    for name in sorted(os.listdir(out_dir)):
        path = os.path.join(out_dir, name)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)

    return {
        "adapter_path": os.path.relpath(out_dir, ADAPTER_ROOT),
        "checksum": digest.hexdigest(),
        "rows": len(texts),
        "metrics": {k: float(v) for k, v in (result.metrics or {}).items()},
    }
