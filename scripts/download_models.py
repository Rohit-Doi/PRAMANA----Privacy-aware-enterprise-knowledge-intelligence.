"""Download every local model once (about 2 GB). No account or token is needed.

    python scripts/download_models.py

After this the platform runs with MODELS_OFFLINE=true (the default): nothing is fetched at
run time, so start-up is fast and the model files cannot change underneath a deployment.
"""

from __future__ import annotations

import os
import sys
import time

os.environ["MODELS_OFFLINE"] = "false"  # this script is the one place that is allowed to download

from pramana.shared.config import get_settings


def main() -> int:
    settings = get_settings()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(f"model cache: {settings.cache_dir}")

    from pramana.graph.extraction import get_graph_extractor
    from pramana.privacy.pii import get_pii_engine
    from pramana.retrieval.local_models import get_retrieval_models
    from pramana.retrieval.reader import get_reader
    from pramana.validation.detectors import get_detectors

    retrieval = get_retrieval_models()
    detectors = get_detectors()
    extractor = get_graph_extractor()
    steps = [
        ("dense embeddings   " + settings.embedding_model, lambda: retrieval.embed_query_sync("ready")),
        ("keyword vectors    " + settings.sparse_model, lambda: retrieval.sparse_query_sync("ready")),
        ("reranker           " + settings.reranker_model, lambda: retrieval.rerank_sync("ready", ["ready"])),
        (
            "entity extraction  " + settings.extraction_model,
            lambda: extractor.extract_full(["Aurelia Systems requires VPN."]),
        ),
        (
            "fast extraction    spaCy " + settings.spacy_model,
            lambda: extractor.extract_fast(["Aurelia Systems requires VPN."]),
        ),
        (
            "grounding          " + settings.grounding_model,
            lambda: detectors.grounding_sync([("The sky is blue.", "The sky is blue.")]),
        ),
        (
            "nli                " + settings.nli_model,
            lambda: detectors.nli_sync([("The sky is blue.", "The sky is blue.")]),
        ),
        (
            "unsupported spans  " + settings.span_model,
            lambda: detectors.spans_sync(["The sky is blue."], "Colour?", "The sky is blue."),
        ),
        (
            "answer reader      " + settings.reader_model,
            lambda: get_reader().read_sync("What colour is the sky?", ["The sky is blue."]),
        ),
        ("personal data      Presidio", lambda: get_pii_engine().analyze("Contact Jane Doe at jane@example.com")),
    ]
    failures = 0
    for label, load in steps:
        started = time.perf_counter()
        try:
            load()
            print(f"  ok    {label}  ({time.perf_counter() - started:.1f}s)")
        except Exception as exc:
            failures += 1
            print(f"  FAIL  {label}: {exc!r}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
