#!/usr/bin/env python3
"""Publish nevertwice-embed to the Hugging Face Hub - one command, owner-authenticated.

    huggingface-cli login          # once (write token)
    python hf_publish.py           # v2 when models/universal_v2_merged exists, else v1
    python hf_publish.py --v1      # the v1 release, even when a v2 set is staged
    python hf_publish.py --repo someone/other-name

Uploads: the merged checkpoint (sentence-transformers loadable), the f16 GGUF (Ollama
`ollama pull hf.co/<repo>` works off it), the LoRA adapter (retrainable artifact), and the
model card from hf_card/README.md.

The v2 release (LoRA r=4 over bge-m3, Matryoshka widths 1024/512/256) is staged as
models/universal_v2_merged, models/universal_v2 and models/nevertwice-embed-v2-f16.gguf.
A v2 conversion written under the v1 GGUF name (models/nevertwice-embed-f16.gguf) is
accepted as the fallback and published under the name it actually has.
"""
import argparse
import sys
from pathlib import Path

from huggingface_hub import HfApi

HERE = Path(__file__).parent
MODELS = HERE / "models"
CARD = HERE / "hf_card" / "README.md"

#: The two staged releases, newest first. `gguf` lists the names a conversion may carry in
#: preference order; `note` rides in the commit messages, so a published commit says which
#: weights it moved.
RELEASES = {
    "v2": {"merged": MODELS / "universal_v2_merged", "adapter": MODELS / "universal_v2",
           "gguf": (MODELS / "nevertwice-embed-v2-f16.gguf", MODELS / "nevertwice-embed-f16.gguf"),
           "note": "bge-m3 + universal-v2 LoRA (r=4, Matryoshka 1024/512/256)"},
    "v1": {"merged": MODELS / "universal_v1_merged", "adapter": MODELS / "universal_v1",
           "gguf": (MODELS / "nevertwice-embed-f16.gguf",),
           "note": "bge-m3 + universal-v1 LoRA"},
}


def choose_version(force_v1: bool = False) -> str:
    """`v2` when its merged checkpoint is staged, `v1` otherwise; `--v1` always wins.

    models/universal_v2_merged is created only by train_v2.py's promote step, so its presence
    is the signal that a complete v2 set (adapter and merged checkpoint) exists.
    """
    if force_v1:
        return "v1"
    return "v2" if RELEASES["v2"]["merged"].is_dir() else "v1"


def artifacts(version: str) -> dict:
    """The paths to publish for `version`, with the first GGUF name that exists on disk."""
    spec = RELEASES[version]
    gguf = next((p for p in spec["gguf"] if p.exists()), spec["gguf"][0])
    return {"merged": spec["merged"], "adapter": spec["adapter"], "gguf": gguf, "note": spec["note"]}


def check_ready(a: dict, version: str) -> None:
    """Refuse to publish a set that would load wrong downstream, naming the missing piece."""
    for p in (a["merged"], a["adapter"], a["gguf"], CARD):
        if not p.exists():
            sys.exit(f"missing artifact: {p}")
    # existence is not loadability (review 2026-08 D12): without the ST pooling files
    # the published repo loads MEAN-pooled downstream - refuse to publish a bare
    # checkpoint (the merge step saves and verifies them: merge_and_verify.py for v1,
    # train_v2.py for v2)
    for req in ("modules.json", "1_Pooling"):
        if not (a["merged"] / req).exists():
            sys.exit(f"{a['merged']} lacks {req} - it would load mean-pooled "
                     f"downstream; run the merge step for {version} first")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None, help="target repo id (default <you>/nevertwice-embed)")
    ap.add_argument("--v1", action="store_true",
                    help="publish the v1 release (default: v2 when models/universal_v2_merged exists)")
    args = ap.parse_args()
    version = choose_version(args.v1)
    a = artifacts(version)
    check_ready(a, version)
    if version == "v2" and a["gguf"].name == "nevertwice-embed-f16.gguf":
        print("note: no models/nevertwice-embed-v2-f16.gguf; using the v1-named GGUF and "
              "publishing it under that name. Confirm it was converted from "
              "models/universal_v2_merged before you push.")
    api = HfApi()
    user = api.whoami()["name"]
    repo = args.repo or f"{user}/nevertwice-embed"
    print(f"publishing {version} ({a['note']}) to {repo} as {user}")
    api.create_repo(repo, repo_type="model", exist_ok=True)
    # folders first, curated card LAST and README.md excluded from the folder upload:
    # sentence-transformers' .save() drops its own auto-generated README.md into the
    # checkpoint dir, and uploading the folder after the card silently clobbered the
    # curated card at repo root (review 2026-08 Dc7)
    api.upload_folder(folder_path=str(a["merged"]), repo_id=repo,
                      ignore_patterns=["README.md"],
                      commit_message=f"merged checkpoint ({a['note']}, verified identical to adapter model)")
    api.upload_folder(folder_path=str(a["adapter"]), repo_id=repo, path_in_repo="lora_adapter",
                      ignore_patterns=["README.md"],
                      commit_message=f"LoRA adapter ({version}, retrainable artifact)")
    api.upload_file(path_or_fileobj=str(a["gguf"]), path_in_repo=a["gguf"].name,
                    repo_id=repo, commit_message=f"f16 GGUF ({version}, ollama-importable)")
    api.upload_file(path_or_fileobj=str(CARD), path_in_repo="README.md",
                    repo_id=repo, commit_message="model card")
    print(f"DONE: https://huggingface.co/{repo}")


if __name__ == "__main__":
    main()
