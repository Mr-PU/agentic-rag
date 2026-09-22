# Local Agentic RAG — Postgres / Redis / MinIO / Kubernetes edition

A fully local Agentic RAG platform: [Ollama](https://ollama.com) for LLMs/embeddings,
[Qdrant](https://qdrant.tech) for vectors, **Postgres** for metadata, **Redis** for the
ingestion job queue, and **MinIO** for raw file storage — runnable with either
**Docker Compose** (single machine) or **Kubernetes** (kind/minikube or a real cluster).

This is the scaled-up version of the app. If you just want the simplest possible thing to
run on one laptop with no infra, an earlier, leaner 3-container version (SQLite + local disk,
no queue) is a simpler starting point — this version trades that simplicity for the pieces
that matter once more than one person or process needs to touch the system: durable job
processing, object storage, and a real path to a cluster.

## What it does

- **Upload documents** (PDF, TXT, MD) through the built-in web UI. The API stores the raw
  file in MinIO, writes a `pending` row to Postgres, and enqueues a job in Redis —
  returning immediately instead of blocking the HTTP request on parsing/embedding.
- **A worker pool** (separate container/pod, scales independently) picks up queued jobs:
  downloads the file from MinIO, extracts text, chunks it, embeds it via Ollama, indexes it
  in Qdrant, and writes chunk metadata to Postgres. The UI polls and shows live status
  (`pending` → `processing` → `ready`/`failed`).
- **Ask questions** in the chat UI. The API runs a real agent loop:
  1. **Planner** (`qwen3:1.7b`) — turns your question into 1–3 search queries.
  2. **Hybrid retrieval** — Qdrant vector search + BM25 keyword search (built from Postgres
     chunk data), fused with Reciprocal Rank Fusion.
  3. **Critic** (`gemma3:12b`) — checks whether the evidence actually answers the question;
     if not, it identifies the gap and the loop re-plans/re-retrieves (up to
     `MAX_ITERATIONS`).
  4. **Generator** (`gemma3:12b`) — writes the final answer, grounded only in retrieved
     evidence, with inline `[n]` citations.
- Every step is recorded and viewable in the UI under **"Show agent trace"**.

## Architecture

```
Browser (built-in UI, served by the api pod/container)
        │
        ▼
   FastAPI (api)
        │
        ├── POST /api/ingest → save to MinIO, write Postgres row (pending),
        │                      enqueue Redis job → return immediately
        ├── GET  /api/documents[/​{id}] → list / poll status
        ├── DELETE /api/documents/{id} → remove Postgres rows + Qdrant vectors + MinIO object
        └── POST /api/chat → agent loop (planner → hybrid retrieval → critic → generator)
        │
        ├── Ollama  (LLM + embeddings)
        └── Qdrant  (vector store)

   Redis (queue) ◄── enqueued by api
        │
        ▼
   worker (1+ replicas, scales independently)
        │
        ├── downloads file from MinIO
        ├── extract → chunk → embed (Ollama) → upsert (Qdrant) → write chunks (Postgres)
        └── updates document status in Postgres

   Postgres   — documents, chunks (also backs BM25 keyword search)
   MinIO      — raw uploaded files
   Qdrant     — chunk embeddings
```

## Option A: Docker Compose (one machine)

### Requirements
- Docker + Docker Compose
- ~10 GB free disk for Ollama models, 16 GB+ RAM recommended for a 12B model

### Run it

```bash
cp .env.example .env        # adjust model names / credentials if you like
docker compose up --build
```

`api` and `worker` both wait on healthchecks for Postgres, Redis, MinIO, Qdrant, and Ollama
(plus the `ollama-pull` job completing) before starting — so a first boot with no errors just
means "still pulling models," not "broken." Check progress with:

```bash
docker compose logs -f ollama-pull
```

Once `api` reports healthy, open **http://localhost:8000**.

### Verify it's working

```bash
./scripts/smoke_test.sh
```

Ingests `samples/leave_policy.txt`, polls until the worker finishes processing it, asks a
question with a known answer ("10 days"), checks the response, and cleans up. This is the
fastest way to confirm the whole pipeline — MinIO, Postgres, Redis, the worker, Qdrant, the
agent loop, and generation — is actually working end to end.

### Scaling workers locally

```bash
docker compose up --scale worker=3
```

## Option B: Kubernetes (kind, minikube, or a real cluster)

### Requirements
- A running cluster and `kubectl` pointed at it (`kubectl config current-context`)
- [kind](https://kind.sigs.k8s.io/) or [minikube](https://minikube.sigs.k8s.io/) for local use
- Docker (to build the api/worker image)

### Deploy it

```bash
./scripts/k8s_deploy.sh kind       # or: ./scripts/k8s_deploy.sh minikube
```

This builds the `agentic-rag-api:local` image, loads it into the cluster, applies
`k8s/overlays/local` (namespace + Postgres + Redis + MinIO + Qdrant + Ollama + api + worker),
waits for Ollama, runs the model-pull Job, and waits for the API to become ready.

Then:

```bash
kubectl port-forward svc/api 8000:8000 -n agentic-rag
```

and open **http://localhost:8000**. Run `./scripts/smoke_test.sh` against it the same as
with Compose.

### What's under `k8s/`

```
k8s/
├── base/
│   ├── namespace.yaml
│   ├── configmap.yaml        # non-secret env vars
│   ├── secret.yaml           # DEV-ONLY default credentials — replace for real use
│   ├── postgres.yaml         # Deployment + PVC + Service
│   ├── redis.yaml
│   ├── minio.yaml
│   ├── qdrant.yaml
│   ├── ollama.yaml
│   ├── ollama-pull-job.yaml  # one-shot Job, applied AFTER ollama is ready (see its header)
│   ├── api.yaml
│   ├── worker.yaml           # 2 replicas by default — scale via `kubectl scale` or the overlay
│   └── kustomization.yaml
└── overlays/
    └── local/
        └── kustomization.yaml  # patches worker down to 1 replica for a single dev machine
```

Everything here runs as `replicas: 1` for the stateful pieces (Postgres/Redis/MinIO/Qdrant/
Ollama) — this is a **local/dev topology**, not an HA one. For production you'd want managed
Postgres, a Redis with persistence/HA, and real object storage (or a properly configured MinIO
cluster) instead of single-replica Deployments with PVCs.

### Scaling workers on a real cluster

```bash
kubectl scale deployment/worker -n agentic-rag --replicas=5
```

### GPU nodes

`ollama.yaml` has a commented-out `nodeSelector`/GPU resource block — uncomment and adjust
for your cluster's GPU node pool + device plugin (e.g. NVIDIA's) if you have one.

### Before using this beyond your own machine

Replace every value in `k8s/base/secret.yaml` (it ships with the same throwaway defaults as
Docker Compose, purely for local convenience) and put real TLS/Ingress in front of the `api`
Service instead of `kubectl port-forward`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GENERATOR_MODEL` | `gemma3:12b` | Final answer generation + critic |
| `ROUTER_MODEL` | `qwen3:1.7b` | Lightweight query planning |
| `EMBED_MODEL` | `nomic-embed-text` | Embeddings |
| `EMBED_DIM` | `768` | Must match the embedding model's output dimension |
| `TOP_K` | `6` | Chunks retrieved per search query |
| `MAX_ITERATIONS` | `3` | Max planner→retrieve→critic loops per question |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `800` / `120` | Chunking (in words) |
| `POSTGRES_USER/PASSWORD/DB` | `rag`/`rag`/`rag` | Metadata store |
| `MINIO_ROOT_USER/PASSWORD` | `minioadmin`/`minioadmin` | Raw file storage |

In Compose, edit `.env` and restart the affected services. In Kubernetes, edit
`k8s/base/configmap.yaml` / `secret.yaml` and re-apply.

## API reference

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Reports API/Ollama/Qdrant/Redis/MinIO reachability |
| `POST` | `/api/ingest` | Multipart upload (`file`) → `202 {doc_id, filename, status: "pending"}` |
| `GET` | `/api/documents` | List documents with status + chunk counts |
| `GET` | `/api/documents/{id}` | Poll a single document's status |
| `DELETE` | `/api/documents/{id}` | Remove a document (Postgres + Qdrant + MinIO) |
| `POST` | `/api/chat` | `{"query": "...", "top_k": 6, "max_iterations": 3}` → answer + citations + trace |

## Project layout

```
agentic-rag/
├── docker-compose.yml
├── .env.example
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py            # FastAPI routes
│       ├── config.py          # env-driven settings
│       ├── models.py          # pydantic schemas
│       ├── llm_gateway.py     # async Ollama client (api process) — chat + embeddings, with retry
│       ├── embeddings.py      # async embedding helper (api process)
│       ├── embeddings_sync.py # sync embedding client (worker process — no event loop there)
│       ├── vectorstore.py     # Qdrant wrapper
│       ├── db.py              # Postgres models + queries (SQLAlchemy)
│       ├── storage.py         # MinIO wrapper
│       ├── queue.py           # Redis/RQ queue (shared by api + worker)
│       ├── chunking.py
│       ├── ingestion.py       # submit_ingest() [api] + process_document() [worker]
│       ├── retrieval.py       # hybrid vector + BM25 search (RRF fusion)
│       ├── agent.py           # planner/retrieval/critic/generator loop
│       └── static/            # built-in web UI (HTML/CSS/JS)
├── k8s/                       # see "Option B" above
├── scripts/
│   ├── smoke_test.sh          # end-to-end test against a running stack
│   └── k8s_deploy.sh          # build + load image + deploy to kind/minikube
└── samples/
    └── leave_policy.txt       # sample doc used by the smoke test
```

## Extending it

- **Reranker**: plug a cross-encoder call into `retrieval.hybrid_search` before the final
  `top_k` slice.
- **More tools**: extend `agent.py`'s loop with more tool calls alongside
  `retrieval.hybrid_search` (a calculator, a web search, etc).
- **Evaluation harness**: `agent.run_agent()` already returns a structured trace. A natural
  next step is a `datasets`/`experiments`/`runs` schema in Postgres and an `evaluator`
  service/Job that replays a golden dataset against it — the original design doc this project
  is based on sketches that out in detail if you want to build it.
- **Separate frontend service**: right now the UI is static files served by the `api`
  container/pod to keep the deployment surface small. Splitting it into its own
  Nginx-served Deployment is straightforward if you want strict separation.

## Troubleshooting

- **First response is slow / times out**: the first call to a given model has to load it
  into memory. If your machine can't hold `gemma3:12b`, switch `GENERATOR_MODEL`/
  `CRITIC_MODEL` to `mistral:latest` or `llama3.1:latest`.
- **Document stuck in `processing` or goes to `failed`**: check worker logs —
  `docker compose logs -f worker` or `kubectl logs -l app=worker -n agentic-rag`. The
  `error` field on the document (surfaced as a tooltip on the status badge in the UI) has
  the exception message.
- **Embedding dimension mismatch**: `EMBED_DIM` must match your embedding model's actual
  output size (768 for `nomic-embed-text`). If you switch embedding models, update
  `EMBED_DIM` and clear the Qdrant volume/PVC to rebuild the collection.
- **Kubernetes: `ImagePullBackOff` on `api`/`worker`**: the image is built locally and must
  be loaded into the cluster (`kind load docker-image` / `minikube image load`) — it's not
  on a registry. `scripts/k8s_deploy.sh` does this for you.
- **No GPU**: everything works on CPU, just slower. See the GPU sections in
  `docker-compose.yml` / `k8s/base/ollama.yaml`.
